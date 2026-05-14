"""MongoDB store for tracking parent/single URLs to crawl.

DB:  ``websites`` (configurable via MONGO_DB)
Collections, keyed by venue type:
    hotel      -> Hotels
    dining     -> Dining
    cultural   -> Cultural & Unique Experiences
    nightlife  -> NightLife & Bars

Document shape::

    {
        "website":         <url>,                     # primary identifier (parent/listing page)
        "venue_type":      "hotel" | "dining" | ...,
        "lastCrawlTime":   datetime | None,
        "lastCrawlStatus": str | None,
        "lastFoundURLs":   list[dict],                # venue URLs discovered on this page
        # Each dict: {"url": str, "lastCrawled": datetime|None, "lastStatus": str|None, "createdAt": datetime}
    }
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pymongo import MongoClient
from pymongo.collection import Collection
from ..config import settings
from .enrichment import (
    find_duplicate_by_name,
    enrich_record,
    merge_duplicate_records,
)

logger = logging.getLogger(__name__)

VenueType = Literal["hotel", "dining", "cultural", "nightlife"]

VENUE_TYPE_TO_COLLECTION: dict[str, str] = {
    "hotel": "Hotels",
    "hotels": "Hotels",
    "dining": "Dining",
    "cultural": "Cultural & Unique Experiences",
    "nightlife": "NightLife & Bars",
}

_client: Optional[MongoClient] = None


def get_client() -> MongoClient:
    global _client
    if _client is None:
        _client = MongoClient(settings.MONGO_URI, serverSelectionTimeoutMS=5000)
    return _client


def get_collection(venue_type: str) -> Collection:
    key = venue_type.lower().strip()
    if key not in VENUE_TYPE_TO_COLLECTION:
        raise ValueError(
            f"Unknown venue type {venue_type!r}. "
            f"Expected one of: {', '.join(sorted(set(VENUE_TYPE_TO_COLLECTION)))}"
        )
    db = get_client()[settings.MONGO_DB]
    return db[VENUE_TYPE_TO_COLLECTION[key]]


def get_records_collection(venue_type: str) -> Collection:
    """Collection in the Records DB (extracted venue records)."""
    key = venue_type.lower().strip()
    if key not in VENUE_TYPE_TO_COLLECTION:
        raise ValueError(
            f"Unknown venue type {venue_type!r}. "
            f"Expected one of: {', '.join(sorted(set(VENUE_TYPE_TO_COLLECTION)))}"
        )
    db = get_client()[settings.MONGO_RECORDS_DB]
    return db[VENUE_TYPE_TO_COLLECTION[key]]


def save_venue_record(
    venue_type: str,
    record: dict[str, Any],
    *,
    check_duplicates: bool = True,
    enrich_existing: bool = True,
) -> dict[str, Any]:
    """Save or enrich a venue record into the Records DB.
    
    Implements intelligent duplicate detection and data enrichment:
    - Checks for existing records with matching name/name_en
    - If duplicate found and enrich_existing=True, merges data intelligently
    - If new record, uses MongoDB's _id as the unique identifier
    - Tracks enrichment metadata for audit trail
    
    Args:
        venue_type: The venue category
        record: The venue record (as dict)
        check_duplicates: Whether to check for duplicates by name
        enrich_existing: Whether to enrich existing records with new data
    
    Returns:
        The stored/updated MongoDB document (with _id)
    """
    coll = get_records_collection(venue_type)
    
    # Primary key lookup
    source_url = record.get("source_url") or record.get("id")
    if not source_url:
        raise ValueError("record must have 'source_url' or 'id'")
    
    # Check if record already exists by source_url
    existing_by_url = coll.find_one({"source_url": str(source_url)})
    
    if existing_by_url:
        # Record exists by source_url - perform enrichment
        if enrich_existing:
            enriched = enrich_record(existing_by_url, record)
            coll.update_one(
                {"_id": existing_by_url["_id"]},
                {"$set": enriched},
            )
            logger.info(
                "Enriched existing record: %s (id: %s)",
                source_url, existing_by_url['_id'],
            )
            return coll.find_one({"_id": existing_by_url["_id"]})
        else:
            # Just update with new record
            coll.update_one(
                {"source_url": str(source_url)},
                {"$set": record},
            )
            return coll.find_one({"source_url": str(source_url)})
    
    # Check for duplicates by name (only if check_duplicates=True)
    if check_duplicates:
        name_en = record.get("name_en")
        name = record.get("name")
        
        duplicate = find_duplicate_by_name(coll, name, name_en)
        
        if duplicate:
            # Found duplicate record - merge instead of creating new
            if enrich_existing:
                merged = merge_duplicate_records(duplicate, record)
                
                # Add source_url to tracking list
                merged["source_urls"] = (
                    merged.get("source_urls", []) + [str(source_url)]
                )
                merged["updated_at"] = datetime.now(timezone.utc)
                
                coll.update_one(
                    {"_id": duplicate["_id"]},
                    {"$set": merged},
                )
                
                logger.info(
                    "Merged duplicate record: %s (existing id: %s, new source: %s)",
                    name_en or name, duplicate['_id'], source_url,
                )
                return coll.find_one({"_id": duplicate["_id"]})
    
    # New record - insert with auto-generated _id
    record["source_urls"] = [str(source_url)]
    record["created_at"] = datetime.now(timezone.utc)
    record["updated_at"] = datetime.now(timezone.utc)
    record["enrichment_count"] = 0
    
    result = coll.insert_one(record)
    
    logger.info(
        "Created new record: %s (id: %s, source: %s)",
        record.get('name_en'), result.inserted_id, source_url,
    )
    
    return coll.find_one({"_id": result.inserted_id})


def fetch_records(venue_type: str, limit: int) -> list[dict[str, Any]]:
    """Return up to `limit` venue docs from the Records DB collection.

    Strips heavy fields (raw_response, _id) to keep payloads small for the LLM.
    """
    coll = get_records_collection(venue_type)
    cursor = coll.find(
        {},
        projection={"raw_response": 0, "_id": 0},
    ).limit(max(1, limit))
    return list(cursor)


def register_website(
    url: str,
    *,
    venue_type: str,
    tag_selector_to_wait_for: Optional[str] = None,
    tag_inside_which_to_extract: Optional[str] = None,
) -> dict[str, Any]:
    """Insert (or upsert) a parent URL entry in the appropriate collection.

    Returns the stored document.
    """
    coll = get_collection(venue_type)
    doc = {
        "website": url,
        "venue_type": venue_type.lower().strip(),
        "lastCrawlTime": None,
        "lastCrawlStatus": None,
        "lastFoundURLs": [],
        "tag_selector_to_wait_for": tag_selector_to_wait_for,
        "tag_inside_which_to_extract": tag_inside_which_to_extract,
        "createdAt": datetime.now(timezone.utc),
    }
    coll.update_one(
        {"website": url},
        {
            "$setOnInsert": doc,
        },
        upsert=True,
    )
    return coll.find_one({"website": url}) or doc


def find_pending(venue_type: str) -> list[dict[str, Any]]:
    """Return all docs in the venue's collection where lastCrawlTime is null."""
    coll = get_collection(venue_type)
    return list(coll.find({"lastCrawlTime": None}))


def mark_crawled(
    venue_type: str,
    url: str,
    *,
    status: str,
    found_urls: Optional[list[dict]] = None,
    raw_response: Optional[str] = None,
) -> None:
    """Update lastCrawlTime / lastCrawlStatus / lastFoundURLs / raw_response.
    found_urls should be a list of dicts: {"url", "lastCrawled", "lastStatus", "createdAt"}
    """
    coll = get_collection(venue_type)
    update: dict[str, Any] = {
        "lastCrawlTime": datetime.now(timezone.utc),
        "lastCrawlStatus": status,
        "lastFoundURLs": found_urls or [],
    }
    if raw_response is not None:
        update["raw_response"] = raw_response
    coll.update_one(
        {"website": url},
        {"$set": update},
    )


def save_llm_prompt(
    url: str,
    prompt: str,
    *,
    prompt_type: str = "extraction",  # "extraction" or "url_extraction"
    category: Optional[str] = None,
    response: Optional[str] = None,
    html: Optional[str] = None,
) -> None:
    """Save LLM prompt and optional response to MongoDB LLMPrompts collection.
    
    Args:
        url: The page URL being processed
        prompt: The prompt sent to the LLM
        prompt_type: Type of prompt ("extraction" for venue, "url_extraction" for URLs)
        category: The venue category
        response: Optional LLM response
        html: Optional raw HTML content
    """
    db = get_client()[settings.MONGO_RECORDS_DB]
    coll = db["LLMPrompts"]
    
    doc = {
        "url": url,
        "prompt": prompt,
        "prompt_type": prompt_type,
        "category": category,
        "response": response,
        "html": html,
        "timestamp": datetime.now(timezone.utc),
    }
    
    # Insert new document for each prompt call (not upsert, to preserve history)
    coll.insert_one(doc)
