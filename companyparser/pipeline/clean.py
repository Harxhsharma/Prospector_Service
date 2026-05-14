"""Field-level cleaning: whitespace, encoding, phone/url normalization."""
from __future__ import annotations

import re

from ..models import Record

_WS = re.compile(r"\s+")


def _clean_text(value: str | None) -> str | None:
    if value is None:
        return None
    return _WS.sub(" ", value).strip() or None


def clean(record: Record) -> Record:
    record.name = _clean_text(record.name) or record.name
    record.name_en = _clean_text(record.name_en)
    record.name_jp = _clean_text(record.name_jp)
    record.name_local = _clean_text(record.name_local)
    record.description = _clean_text(record.description)
    record.address = _clean_text(record.address)
    record.address_jp = _clean_text(record.address_jp)
    record.tags = [t.strip().lower() for t in record.tags if t and t.strip()]
    return record
