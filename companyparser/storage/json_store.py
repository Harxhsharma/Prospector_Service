"""JSON-based storage. Swap for SQLite later if needed."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from ..config import PROCESSED_DIR, RAW_DIR
from ..models import RawPayload, Record


# Columns the editor wants in the CSV review workflow. Order matters - this
# is the order they'll see in their spreadsheet.
CSV_COLUMNS = [
    "id",
    "category",
    "subcategory",
    "tagging",
    "famous_score",
    "insider_score",
    "name_en",
    "name_jp",
    "city",
    "neighborhood",
    "address_jp",
    "price_tier",
    "price_specific",
    "reservation_method",
    "english_friendly",
    "foreigner_friendly",
    "lead_time",
    "english_review_count",
    "japanese_review_count",
    "tabelog_score",
    "tabelog_award",
    "awards",
    "mentioned_in_jp_media",
    "mentioned_in_en_media",
    "tagging_signals",
    "source_url",
    "source_urls",
    "photos",
    "last_scraped",
    "notes",
]


def save_raw(payload: RawPayload) -> Path:
    out_dir = RAW_DIR / payload.source_name
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{payload.fetched_at.strftime('%Y%m%dT%H%M%S')}.json"
    path = out_dir / fname
    path.write_text(payload.model_dump_json(indent=2), encoding="utf-8")
    return path


def save_records(records: Iterable[Record], filename: str = "records.json") -> Path:
    path = PROCESSED_DIR / filename
    data = [r.model_dump(mode="json") for r in records]
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    return path


def load_records(filename: str = "records.json") -> list[Record]:
    path = PROCESSED_DIR / filename
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Record.model_validate(d) for d in data]


def export_csv(records: Iterable[Record], filename: str = "records.csv") -> Path:
    """Write records to a CSV that the editor can review in a spreadsheet.

    List fields (awards, media, signals, urls, photos) are joined with " | "
    so they survive a round-trip through Excel without being mangled.
    """
    path = PROCESSED_DIR / filename
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            row = r.model_dump(mode="json")
            # Fall back to ``name`` if ``name_en`` wasn't populated.
            row.setdefault("name_en", row.get("name"))
            row.setdefault("name_jp", row.get("name_local"))
            for k in (
                "awards",
                "mentioned_in_jp_media",
                "mentioned_in_en_media",
                "tagging_signals",
                "source_urls",
                "photos",
            ):
                v = row.get(k)
                if isinstance(v, list):
                    row[k] = " | ".join(str(x) for x in v)
            writer.writerow(row)
    return path

