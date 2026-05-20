"""FastAPI surface for the CompanyParser pipeline.

Run with::

    uvicorn companyparser.api:app --reload

Then open http://localhost:8000/docs for Swagger UI.

Four main flows:
  1. POST /crawl/discover  — register parent URL, fetch it, LLM extracts venue URLs → lastFoundURLs
  2. POST /crawl/batch     — take parent URL, read lastFoundURLs, crawl each, LLM extract records → Records DB
  3. POST /crawl/single    — stateless single-URL extraction: fetch → LLM extract → save record (no tracking)
  4. POST /crawl/from-db   — read raw HTML from MongoDB, run LLM extraction only (skip Playwright)
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import settings
from .llm_extract import ExtractorError, extract_record, extract_urls
from .llm_top import TopPickerError, pick_top_n
from .pipeline.clean import clean
from .sources import build_source
from .storage import (
    export_csv,
    fetch_records,
    load_records,
    mark_crawled,
    register_website,
    save_records,
    save_venue_record,
)
from .storage.mongo_store import VENUE_TYPE_TO_COLLECTION, get_collection, get_client
from .storage.url_metadata import update_lastfoundurl_metadata

logger = logging.getLogger("companyparser.api")

app = FastAPI(
    title="CompanyParser API",
    description="Pipeline endpoints for the Japan luxury-travel ebook crawler.",
    version="0.1.0",
)

_VALID_VENUES = sorted(set(VENUE_TYPE_TO_COLLECTION))


# ---------------------------- request/response models ----------------------


class FoundUrlEntry(BaseModel):
    url: str
    lastCrawled: Optional[datetime] = None
    lastStatus: Optional[str] = None
    createdAt: datetime


class AddUrlRequest(BaseModel):
    url: str = Field(..., description="Parent/listing URL to register and crawl for venue links.")
    venue_type: str = Field(
        ...,
        description=f"One of: {', '.join(_VALID_VENUES)}",
        examples=["hotel"],
    )
    tag_selector_to_wait_for: Optional[str] = Field(
        None,
        description=(
            "Optional CSS selector to wait for before extracting the page content. "
            "Improves reliability for dynamic sites. Defaults to 'body'."
        ),
    )
    tag_inside_which_to_extract: Optional[str] = Field(
        None,
        description=(
            "Optional CSS selector to narrow the scope of LLM extraction. "
            "If not provided, the entire page HTML is used."
        ),
    )


class AddUrlResponse(BaseModel):
    collection: str
    url: str
    found_urls: list[FoundUrlEntry]
    status: str


class RunRequest(BaseModel):
    url: str = Field(..., description="Parent URL already registered in the websites DB.")
    venue_type: str = Field(
        ...,
        description=f"One of: {', '.join(_VALID_VENUES)}",
        examples=["hotel"],
    )


class RunUrlResult(BaseModel):
    url: str
    status: str


class RunResponse(BaseModel):
    parent_url: str
    total_child_urls: int
    records_saved: int
    per_url: list[RunUrlResult]


class ExportCsvRequest(BaseModel):
    input_file: str = "records.json"
    output: str = "records.csv"
    category: Optional[str] = None
    sort_by: str = "tier"


class StaleResponse(BaseModel):
    total: int
    stale: int
    days: int
    items: list[dict[str, Any]]


class IndependentRunRequest(BaseModel):
    url: str = Field(..., description="Direct venue URL to fetch and extract a record from.")
    venue_type: str = Field(
        ...,
        description=f"One of: {', '.join(_VALID_VENUES)}",
        examples=["hotel"],
    )


class IndependentRunResponse(BaseModel):
    url: str
    venue_type: str
    status: str
    record: Optional[dict[str, Any]] = None


class FromDbRequest(BaseModel):
    db_name: str = Field(..., description="MongoDB database name where the raw response is stored.")
    collection_name: str = Field(..., description="Collection name inside that database.")
    raw_response_key: str = Field(
        "raw_response",
        description="Key/field name under which the raw HTML is stored in the document.",
    )
    venue_type: str = Field(
        ...,
        description=f"One of: {', '.join(_VALID_VENUES)}",
        examples=["dining"],
    )
    source_url_key: str = Field(
        "source_url",
        description="Key/field name that holds the venue URL in each document.",
    )
    limit: int = Field(
        0,
        description="Max documents to process. 0 = all documents in the collection.",
    )


class FromDbUrlResult(BaseModel):
    url: str
    status: str


class FromDbResponse(BaseModel):
    db_name: str
    collection_name: str
    total_documents: int
    records_saved: int
    skipped: int
    per_url: list[FromDbUrlResult]


# ----------------------------- helpers -------------------------------------


def _validate_venue(vt: str) -> str:
    key = vt.lower().strip()
    if key not in VENUE_TYPE_TO_COLLECTION:
        raise HTTPException(
            status_code=422,
            detail=f"venue_type must be one of: {', '.join(_VALID_VENUES)}; got {vt!r}",
        )
    return key


def _safe_mark_crawled(
    vt: str,
    url: str,
    *,
    status: str,
    found_urls: Optional[list[dict]] = None,
    raw_response: Optional[str] = None,
) -> None:
    """Persist crawl status to MongoDB, swallowing errors (already logged)."""
    try:
        mark_crawled(
            vt, url,
            status=status,
            found_urls=found_urls or [],
            raw_response=raw_response,
        )
    except Exception as e:
        logger.error("Failed to mark_crawled for %s: %s", url, e, exc_info=True)


# ----------------------------- endpoints -----------------------------------


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post(
    "/crawl/discover",
    response_model=AddUrlResponse,
    tags=["crawl"],
    summary="Discover venue URLs from a listing page via LLM",
)
def add_url(req: AddUrlRequest) -> AddUrlResponse:
    """Register a parent/listing URL, fetch the page with Playwright,
    use the LLM to identify venue URLs, and store them in lastFoundURLs."""
    logger.info("Received /urls request: url=%s, venue_type=%s", req.url, req.venue_type)
    vt = _validate_venue(req.venue_type)

    # Register in websites DB
    try:
        logger.debug("Registering website %s for venue_type=%s", req.url, vt)
        register_website(
            req.url,
            venue_type=vt,
            tag_selector_to_wait_for=req.tag_selector_to_wait_for,
            tag_inside_which_to_extract=req.tag_inside_which_to_extract,
        )
    except Exception as e:
        logger.error("Failed to register website %s: %s", req.url, e, exc_info=True)
        raise HTTPException(status_code=500, detail="MongoDB error: %s" % e) from e

    # Fetch parent page
    try:
        logger.info("Building Playwright source for %s", req.url)
        connector = build_source(
            source_type="playwright",
            url=req.url,
            name=req.url,
            category=vt,
            wait_for_selector=req.tag_selector_to_wait_for,
            tag_inside_which_to_extract=req.tag_inside_which_to_extract,
        )
        payloads = list(connector.fetch())
        if not payloads:
            logger.error("Playwright returned no payload for %s", req.url)
            _safe_mark_crawled(
                vt, req.url,
                status="error: playwright returned no payload",
                raw_response=None,
            )
            raise RuntimeError("playwright returned no payload")
        raw_html = payloads[0].body
        # If the payload is an error (from PlaywrightSource), store it and raise
        if payloads[0].content_type == "text/plain":
            logger.error("Playwright error body for %s: %s", req.url, raw_html)
            _safe_mark_crawled(
                vt, req.url,
                status="error: playwright fetch error",
                raw_response=raw_html,
            )
            raise RuntimeError("playwright fetch error: %s" % raw_html[:300])
    except HTTPException:
        raise
    except Exception as e:
        logger.error("Error fetching page for %s: %s", req.url, e, exc_info=True)
        _safe_mark_crawled(vt, req.url, status="error: %s" % e, raw_response=None)
        raise HTTPException(status_code=502, detail="Failed to fetch page: %s" % e) from e

    # LLM extracts venue URLs from the parent page
    try:
        logger.info("Extracting URLs from fetched HTML for %s", req.url)
        found_urls_raw = extract_urls(html=raw_html, url=req.url)
        # Wrap each found URL in a dict with metadata
        now = datetime.now(timezone.utc)
        found_urls = [
            {"url": u, "lastCrawled": None, "lastStatus": None, "createdAt": now}
            for u in found_urls_raw
        ]
    except ExtractorError as e:
        logger.error("LLM URL extraction failed for %s: %s", req.url, e, exc_info=True)
        _safe_mark_crawled(
            vt, req.url,
            status="error: %s" % e,
            raw_response=raw_html,
        )
        raise HTTPException(status_code=502, detail="URL extraction failed: %s" % e) from e

    # Store results
    _safe_mark_crawled(
        vt, req.url,
        status="ok",
        found_urls=found_urls,
        raw_response=raw_html,
    )
    logger.info(
        "Crawl and extraction succeeded for %s, found %d URLs",
        req.url, len(found_urls),
    )

    return AddUrlResponse(
        collection=VENUE_TYPE_TO_COLLECTION[vt],
        url=req.url,
        found_urls=found_urls,
        status="ok",
    )


@app.post(
    "/crawl/batch",
    response_model=RunResponse,
    tags=["crawl"],
    summary="Batch-process discovered venue URLs → extract records",
)
def run_pipeline_endpoint(req: RunRequest) -> RunResponse:
    """Look up a parent URL in the websites DB, take each URL from lastFoundURLs,
    crawl it, extract a Record via LLM, and save to the Records DB."""
    vt = _validate_venue(req.venue_type)

    # Look up the parent URL in websites DB
    coll = get_collection(vt)
    doc = coll.find_one({"website": req.url})
    if not doc:
        raise HTTPException(
            status_code=404,
            detail="URL not found in %s: %s" % (VENUE_TYPE_TO_COLLECTION[vt], req.url),
        )

    found_urls = doc.get("lastFoundURLs") or []
    if not found_urls:
        raise HTTPException(
            status_code=404,
            detail="No URLs in lastFoundURLs. Run POST /urls first to discover venue links.",
        )

    # Filter to only crawl URLs that haven't been crawled yet
    urls_to_crawl = [
        url_entry for url_entry in found_urls
        if url_entry.get("lastCrawled") is None or url_entry.get("lastStatus") is None
    ]
    per_url: list[RunUrlResult] = []
    records_saved = 0

    for url_entry in urls_to_crawl:
        time.sleep(settings.CRAWL_DELAY_SECONDS)
        child_url = url_entry["url"] if isinstance(url_entry, dict) else url_entry
        crawl_time = datetime.now(timezone.utc)
        try:
            child_connector = build_source(
                source_type="playwright",
                url=child_url,
                name=req.url,
                category=vt,
            )
            child_payloads = list(child_connector.fetch())
            if not child_payloads:
                update_lastfoundurl_metadata(
                    coll, req.url, child_url,
                    lastCrawled=crawl_time,
                    lastStatus="error: no payload",
                )
                per_url.append(RunUrlResult(url=child_url, status="error: no payload"))
                continue
            html = child_payloads[0].body
            try:
                rec = extract_record(
                    html,
                    url=child_url,
                    category=vt,
                    source_name=req.url,
                )
            except ExtractorError as e:
                update_lastfoundurl_metadata(
                    coll, req.url, child_url,
                    lastCrawled=crawl_time,
                    lastStatus="error: %s" % e,
                )
                per_url.append(RunUrlResult(url=child_url, status="error: %s" % e))
                continue
            if rec is None:
                update_lastfoundurl_metadata(
                    coll, req.url, child_url,
                    lastCrawled=crawl_time,
                    lastStatus="Junked Page",
                )
                per_url.append(RunUrlResult(url=child_url, status="Junked Page"))
                continue
            rec = clean(rec)
            rec_doc = rec.model_dump(mode="json")
            rec_doc["raw_response"] = html
            save_venue_record(vt, rec_doc)
            update_lastfoundurl_metadata(
                coll, req.url, child_url,
                lastCrawled=crawl_time,
                lastStatus="ok",
            )
            records_saved += 1
            per_url.append(RunUrlResult(url=child_url, status="ok"))
        except Exception as e:
            per_url.append(RunUrlResult(url=child_url, status="error: %s" % e))

    return RunResponse(
        parent_url=req.url,
        total_child_urls=len(found_urls),
        records_saved=records_saved,
        per_url=per_url,
    )


@app.post(
    "/crawl/single",
    response_model=IndependentRunResponse,
    tags=["crawl"],
    summary="Extract a single venue URL — stateless, no tracking",
)
def independent_run(req: IndependentRunRequest) -> IndependentRunResponse:
    """Fetch a single venue URL, extract a structured record via LLM, clean it,
    and save directly to MongoDB. No parent URL registration or URL metadata
    tracking — purely stateless, fire-and-forget extraction."""
    vt = _validate_venue(req.venue_type)
    logger.info(
        "Received /independent/run: url=%s, venue_type=%s",
        req.url, vt,
    )

    # 1. Fetch the page with Playwright
    try:
        connector = build_source(
            source_type="playwright",
            url=req.url,
            name=req.url,
            category=vt,
        )
        payloads = list(connector.fetch())
        if not payloads:
            logger.error("Playwright returned no payload for %s", req.url)
            return IndependentRunResponse(
                url=req.url, venue_type=vt, status="error: playwright returned no payload",
            )
        html = payloads[0].body
        if payloads[0].content_type == "text/plain":
            logger.error("Playwright error for %s: %s", req.url, html[:300])
            return IndependentRunResponse(
                url=req.url, venue_type=vt, status="error: playwright fetch error",
            )
    except Exception as e:
        logger.error("Failed to fetch %s: %s", req.url, e, exc_info=True)
        raise HTTPException(status_code=502, detail="Failed to fetch page: %s" % e) from e

    # 2. LLM extraction
    try:
        rec = extract_record(html, url=req.url, category=vt, source_name=req.url)
    except ExtractorError as e:
        logger.error("LLM extraction failed for %s: %s", req.url, e, exc_info=True)
        raise HTTPException(status_code=502, detail="LLM extraction failed: %s" % e) from e

    if rec is None:
        logger.info("LLM flagged %s as a junk page", req.url)
        return IndependentRunResponse(
            url=req.url, venue_type=vt, status="junked_page",
        )

    # 3. Clean + save
    rec = clean(rec)
    rec_doc = rec.model_dump(mode="json")
    rec_doc["raw_response"] = html
    try:
        save_venue_record(vt, rec_doc)
    except Exception as e:
        logger.error("Failed to save record for %s: %s", req.url, e, exc_info=True)
        raise HTTPException(status_code=500, detail="MongoDB save failed: %s" % e) from e

    # Convert any ObjectId values to strings so Pydantic can serialise the response.
    if "_id" in rec_doc:
        rec_doc["_id"] = str(rec_doc["_id"])

    logger.info("Independent run succeeded for %s — record saved", req.url)
    return IndependentRunResponse(
        url=req.url, venue_type=vt, status="ok", record=rec_doc,
    )


@app.post(
    "/crawl/from-db",
    response_model=FromDbResponse,
    tags=["crawl"],
    summary="Extract records from pre-fetched raw HTML stored in MongoDB",
)
def from_db_run(req: FromDbRequest) -> FromDbResponse:
    """Read raw Playwright HTML from an arbitrary MongoDB collection and run
    only the LLM extraction + clean + save pipeline.  Skips the Playwright
    fetch step entirely — useful when raw responses were saved from earlier
    crawls or external tools.

    Each document in the source collection must contain:
    - A field with the raw HTML (key specified by ``raw_response_key``)
    - A field with the source URL (key specified by ``source_url_key``)
    """
    vt = _validate_venue(req.venue_type)
    logger.info(
        "Received /crawl/from-db: db=%s, collection=%s, venue_type=%s",
        req.db_name, req.collection_name, vt,
    )

    # Connect to the specified DB + collection
    try:
        db = get_client()[req.db_name]
        source_coll = db[req.collection_name]
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail="Failed to connect to %s.%s: %s" % (req.db_name, req.collection_name, e),
        ) from e

    # Fetch documents that have the raw_response_key
    query = {req.raw_response_key: {"$exists": True, "$nin": [None, ""]}}
    cursor = source_coll.find(query)
    if req.limit > 0:
        cursor = cursor.limit(req.limit)

    docs = list(cursor)
    if not docs:
        raise HTTPException(
            status_code=404,
            detail="No documents with '%s' found in %s.%s"
                   % (req.raw_response_key, req.db_name, req.collection_name),
        )

    per_url: list[FromDbUrlResult] = []
    records_saved = 0
    skipped = 0

    for doc in docs:
        html = doc.get(req.raw_response_key, "")
        source_url = str(doc.get(req.source_url_key) or doc.get("website") or doc.get("url") or doc.get("_id"))

        if not html or not html.strip():
            per_url.append(FromDbUrlResult(url=source_url, status="skipped: empty raw_response"))
            skipped += 1
            continue

        # LLM extraction
        try:
            rec = extract_record(html, url=source_url, category=vt, source_name=source_url)
        except ExtractorError as e:
            logger.error("LLM extraction failed for %s: %s", source_url, e)
            per_url.append(FromDbUrlResult(url=source_url, status="error: %s" % e))
            continue

        if rec is None:
            per_url.append(FromDbUrlResult(url=source_url, status="junked_page"))
            skipped += 1
            continue

        # Clean + save
        rec = clean(rec)
        rec_doc = rec.model_dump(mode="json")
        rec_doc["raw_response"] = html
        try:
            save_venue_record(vt, rec_doc)
        except Exception as e:
            logger.error("Failed to save record for %s: %s", source_url, e)
            per_url.append(FromDbUrlResult(url=source_url, status="error: save failed — %s" % e))
            continue

        records_saved += 1
        per_url.append(FromDbUrlResult(url=source_url, status="ok"))
        logger.info("from-db extraction succeeded for %s", source_url)

    return FromDbResponse(
        db_name=req.db_name,
        collection_name=req.collection_name,
        total_documents=len(docs),
        records_saved=records_saved,
        skipped=skipped,
        per_url=per_url,
    )



@app.post(
    "/export/csv",
    tags=["export"],
    summary="Export records to CSV",
)
def export_csv_endpoint(req: ExportCsvRequest) -> dict[str, Any]:
    records = load_records(filename=req.input_file)
    if not records:
        raise HTTPException(
            status_code=404, detail="No records in data/processed/%s" % req.input_file
        )
    if req.category:
        records = [r for r in records if r.category == req.category]

    def _key(r):
        v = getattr(r, req.sort_by, None)
        if isinstance(v, (int, float)):
            return (0, -v)
        if isinstance(v, str):
            return (1, v.lower())
        return (2, "")

    records.sort(key=_key)
    path = export_csv(records, filename=req.output)
    return {"rows": len(records), "saved_path": str(path)}


@app.post(
    "/venues/top",
    tags=["venues"],
    summary="Top-N venues ranked by LLM",
)
def top_endpoint(
    venue_type: str,
    count: int = 10,
    model: str = "mistralai/Mistral-7B-Instruct-v0.3",
) -> dict[str, Any]:
    """Pull `count*10` records from MongoDB Records.<Collection> for the given
    venue type, ask the LLM to pick the top `count`, and return them with
    rank/reason plus the full record payload."""
    vt = _validate_venue(venue_type)
    if count < 1:
        raise HTTPException(status_code=422, detail="count must be >= 1")

    pool_size = count * 10
    try:
        pool = fetch_records(vt, limit=pool_size)
    except Exception as e:
        raise HTTPException(status_code=500, detail="MongoDB error: %s" % e) from e

    if not pool:
        return {
            "venue_type": vt,
            "requested": count,
            "pool_size": 0,
            "top": [],
        }

    try:
        picks = pick_top_n(pool, venue_type=vt, top_n=count, model=model)
    except TopPickerError as e:
        raise HTTPException(status_code=502, detail=str(e)) from e

    by_url = {r.get("source_url"): r for r in pool if r.get("source_url")}
    enriched = [
        {**pick, "record": by_url.get(pick.get("source_url"))}
        for pick in picks
    ]
    return {
        "venue_type": vt,
        "requested": count,
        "pool_size": len(pool),
        "top": enriched,
    }


@app.get(
    "/venues/stale",
    response_model=StaleResponse,
    tags=["venues"],
    summary="List records older than the freshness window",
)
def stale_endpoint(
    input_file: str = "records.json",
    days: int = settings.FRESHNESS_DAYS,
) -> StaleResponse:
    records = load_records(filename=input_file)
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    stale_recs = [
        r for r in records if (r.last_scraped or r.fetched_at) < cutoff
    ]
    return StaleResponse(
        total=len(records),
        stale=len(stale_recs),
        days=days,
        items=[
            {
                "id": r.id,
                "category": r.category,
                "name": r.name_en or r.name,
                "last_scraped": (r.last_scraped or r.fetched_at).isoformat(),
            }
            for r in stale_recs[:200]
        ],
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
