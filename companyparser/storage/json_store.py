"""JSON-based storage. Swap for SQLite later if needed."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Iterable

from ..config import PROCESSED_DIR, RAW_DIR
from ..models import RawPayload, Record


# Columns the client expects in the delivery spreadsheet. Order matches their
# schema: Identity → Content → Location → Pricing → Media → Provenance.
CSV_COLUMNS = [
    "id",
    "category",
    "subcategory",
    "tier",
    "name_en",
    "name_jp",
    "city",
    "neighborhood",
    "address",
    "description",
    "why_on_list",
    "price_tier",
    "signature_dish_or_feature",
    "hype_indicators",
    "photos",
    "source_url",
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
    """Write records to a CSV that the client can review in a spreadsheet.

    List fields (photos) are joined with " | " so they survive a round-trip
    through Excel without being mangled.
    """
    path = PROCESSED_DIR / filename
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for r in records:
            row = r.model_dump(mode="json")
            # Fall back to ``name`` if ``name_en`` wasn't populated.
            row.setdefault("name_en", row.get("name"))
            for k in ("photos",):
                v = row.get(k)
                if isinstance(v, list):
                    row[k] = " | ".join(str(x) for x in v)
            writer.writerow(row)
    return path
