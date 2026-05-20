"""Core data models. Every record carries provenance (source_url).

Schema design notes
-------------------
The pipeline is tuned for the Japan luxury-travel ebook.  Each venue gets a
``tier`` label (``famous`` / ``insider``) assigned directly by the LLM, and
a set of client-facing fields that map 1-to-1 with the delivery spreadsheet.

All new fields are optional - per the spec, the editor would rather see gaps
than fabricated data, so unknown values stay ``None`` / empty.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, HttpUrl


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)

SourceType = Literal["playwright"]
Tier = Literal["famous", "insider"]
PriceTier = Literal["¥", "¥¥", "¥¥¥", "¥¥¥¥"]


class Record(BaseModel):
    """Slim ebook venue record — fields map to the client delivery spreadsheet."""

    id: str = Field(..., description="Stable hash-based id")
    category: str
    subcategory: Optional[str] = None
    tier: Optional[Tier] = None

    # --- names ------------------------------------------------------------
    name: str
    name_en: Optional[str] = None
    name_jp: Optional[str] = None

    # --- content ----------------------------------------------------------
    description: Optional[str] = None
    why_on_list: Optional[str] = None  # editorial justification for inclusion
    signature_dish_or_feature: Optional[str] = None  # key attraction / standout offering
    hype_indicators: Optional[str] = None  # consolidated fame signals string

    # --- location ---------------------------------------------------------
    city: Optional[str] = None  # "Tokyo" | "Kyoto" (this ebook's scope)
    neighborhood: Optional[str] = None  # e.g. "Ginza", "Higashiyama"
    address: Optional[str] = None

    # --- pricing ----------------------------------------------------------
    price_tier: Optional[PriceTier] = None

    # --- media ------------------------------------------------------------
    photos: list[HttpUrl] = Field(default_factory=list)  # editor reference only

    # --- provenance -------------------------------------------------------
    source_url: HttpUrl
    source_name: str
    source_type: SourceType
    fetched_at: datetime = Field(default_factory=_utcnow)
    last_scraped: datetime = Field(default_factory=_utcnow)
    notes: Optional[str] = None


class RawPayload(BaseModel):
    """Raw fetched payload, persisted before parsing."""

    source_name: str
    source_type: SourceType
    source_url: HttpUrl
    fetched_at: datetime = Field(default_factory=_utcnow)
    content_type: Optional[str] = None
    body: str
