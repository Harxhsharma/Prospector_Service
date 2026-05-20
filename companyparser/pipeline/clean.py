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
    record.description = _clean_text(record.description)
    record.why_on_list = _clean_text(record.why_on_list)
    record.signature_dish_or_feature = _clean_text(record.signature_dish_or_feature)
    record.address = _clean_text(record.address)
    return record
