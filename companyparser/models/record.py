"""Core data models. Every record carries provenance (source_url).

Schema design notes
-------------------
The pipeline is tuned for the "Famous Version vs. Insider Version" Japan
luxury-travel ebook. Every venue is scored on two independent 0-6 axes
(``famous_score`` / ``insider_score``) and assigned one of four ``tagging``
buckets (``famous`` / ``insider`` / ``both`` / ``ambiguous``). The scoring
itself lives in :mod:`companyparser.tagging`.

The legacy ``tier`` field (``public`` / ``insider``) is kept for backwards
compatibility with the existing connectors and dedupe logic. New code should
read ``tagging`` instead.

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
# Legacy two-tier classification kept for back-compat with existing connectors.
Tier = Literal["public", "insider"]
# Four-way editorial bucket per the ebook spec.
Tagging = Literal["famous", "insider", "both", "ambiguous"]
PriceTier = Literal["¥", "¥¥", "¥¥¥", "¥¥¥¥"]
ReservationMethod = Literal[
    "walk_in",
    "online_en",
    "online_jp",
    "phone_only",
    "concierge_only",
    "members_only",
    "recommendation_only",
]
Friendliness = Literal["yes", "limited", "no"]


class GeoPoint(BaseModel):
    lat: float
    lng: float


class Record(BaseModel):
    """Generic ebook content record. Specialize via `category` + `extra`."""

    id: str = Field(..., description="Stable hash-based id")
    category: str
    subcategory: Optional[str] = None
    # Legacy 2-tier label. New code should rely on ``tagging`` instead.
    tier: Tier = "public"

    # --- names ------------------------------------------------------------
    # ``name`` stays as a back-compat alias for ``name_en`` (most existing
    # connectors only fill ``name``). ``name_jp`` preserves kanji+kana per
    # spec - never romanize at the data layer.
    name: str
    name_en: Optional[str] = None
    name_jp: Optional[str] = None
    name_local: Optional[str] = None  # legacy alias for name_jp
    description: Optional[str] = None

    # --- location ---------------------------------------------------------
    city: Optional[str] = None  # "Tokyo" | "Kyoto" (this ebook's scope)
    neighborhood: Optional[str] = None  # e.g. "Ginza", "Higashiyama"
    address: Optional[str] = None
    address_jp: Optional[str] = None
    location: Optional[GeoPoint] = None
    phone: Optional[str] = None
    website: Optional[HttpUrl] = None

    # --- pricing & access -------------------------------------------------
    price_tier: Optional[PriceTier] = None
    price_specific: Optional[str] = None  # e.g. "¥30,000-¥50,000 / person"
    reservation_method: Optional[ReservationMethod] = None
    english_friendly: Optional[Friendliness] = None
    foreigner_friendly: Optional[Friendliness] = None  # distinct from english
    lead_time: Optional[str] = None  # free-form, e.g. "2-4 weeks"

    # --- review / awards signals -----------------------------------------
    english_review_count: Optional[int] = None  # Google + Yelp + TripAdvisor
    japanese_review_count: Optional[int] = None  # Tabelog + Ikyu + ...
    tabelog_score: Optional[float] = None
    tabelog_award: Optional[Literal["gold", "silver", "bronze"]] = None
    awards: list[str] = Field(default_factory=list)  # e.g. "Michelin 2*"
    mentioned_in_jp_media: list[str] = Field(default_factory=list)
    mentioned_in_en_media: list[str] = Field(default_factory=list)

    tags: list[str] = Field(default_factory=list)
    images: list[HttpUrl] = Field(default_factory=list)
    photos: list[HttpUrl] = Field(default_factory=list)  # editor reference only

    # --- editorial tagging (Layer 2) -------------------------------------
    tagging: Optional[Tagging] = None
    famous_score: Optional[int] = Field(default=None, ge=0, le=6)
    insider_score: Optional[int] = Field(default=None, ge=0, le=6)
    tagging_signals: list[str] = Field(default_factory=list)

    # --- provenance -------------------------------------------------------
    source_url: HttpUrl
    source_urls: list[HttpUrl] = Field(default_factory=list)
    source_name: str
    source_type: SourceType
    fetched_at: datetime = Field(default_factory=_utcnow)
    last_scraped: datetime = Field(default_factory=_utcnow)
    notes: Optional[str] = None

    # category-specific fields go here untyped
    extra: dict[str, Any] = Field(default_factory=dict)


class RawPayload(BaseModel):
    """Raw fetched payload, persisted before parsing."""

    source_name: str
    source_type: SourceType
    source_url: HttpUrl
    fetched_at: datetime = Field(default_factory=_utcnow)
    content_type: Optional[str] = None
    body: str
