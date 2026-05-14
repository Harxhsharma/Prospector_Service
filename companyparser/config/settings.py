"""Global settings for the pipeline."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
CACHE_DIR = DATA_DIR / "cache"
PROCESSED_DIR = DATA_DIR / "processed"

for _d in (RAW_DIR, CACHE_DIR, PROCESSED_DIR):
    _d.mkdir(parents=True, exist_ok=True)

USER_AGENT = "CompanyParserBot/0.1 (+contact: set-me-in-config)"
REQUEST_TIMEOUT = 30  # seconds
REQUEST_DELAY = 1.0   # polite delay between requests (seconds)

# Delay between crawling each child URL in the /run endpoint (seconds).
CRAWL_DELAY_SECONDS = int(os.environ.get("CRAWL_DELAY_SECONDS", "23"))

# Re-scrape cadence: anything older than this is "stale" per the spec.
FRESHNESS_DAYS = 90

# v1 ebook geographic scope. v2 will add Osaka, Hokkaido, Okinawa, Hakone, etc.
CITIES = ["Tokyo", "Kyoto"]

# Editorial categories for the "Famous vs. Insider" Japan ebook.
# Legacy aliases (restaurants/attractions/experiences/transport/shopping) are
# still accepted by the inclusion + dedupe layers for back-compat with
# existing connectors.
CATEGORIES = ["hotels", "dining", "cultural", "nightlife"]

SUB_CATEGORIES: dict[str, list[str]] = {
    "hotels": [
        "ultra_luxury_icons",
        "ryokan",
        "design_boutique",
        "view_driven",
        "private_exclusive",
        "location_strategy",
    ],
    "dining": [
        "kaiseki",
        "sushi",
        "omakase",
        "michelin_vs_local",
        "counter_vs_private_room",
        "cultural_depth",
        "reservation_difficulty",
    ],
    "cultural": [
        "temple_shrine_access",
        "geisha_maiko",
        "sumo",
        "tea_ceremony",
        "kimono_styling",
        "traditional_crafts",
        "connection_only",
    ],
    "nightlife": [
        "luxury_hotel_bars",
        "speakeasy_hidden",
        "cocktail_craft",
        "local_chaos",
        "late_night_fine_dining",
        "access_entry_rules",
    ],
}

# Per-category price-tier thresholds in JPY (per person, per night, etc.).
# Editor can tune; the Layer 1 inclusion module does the hard cutoffs.
PRICE_TIER_THRESHOLDS: dict[str, dict[str, int]] = {
    "hotels": {"¥": 0, "¥¥": 30_000, "¥¥¥": 60_000, "¥¥¥¥": 120_000},
    "dining": {"¥": 0, "¥¥": 8_000, "¥¥¥": 15_000, "¥¥¥¥": 40_000},
    "cultural": {"¥": 0, "¥¥": 5_000, "¥¥¥": 15_000, "¥¥¥¥": 50_000},
    "nightlife": {"¥": 0, "¥¥": 1_500, "¥¥¥": 3_500, "¥¥¥¥": 8_000},
}

# Volume targets for the first pass of the ebook (informational).
VOLUME_TARGETS = {
    "hotels": (30, 40),
    "dining": (60, 80),
    "cultural": (30, 40),
    "nightlife": (40, 50),
}

# Legacy two-tier classification (kept for back-compat with existing
# connectors). New code should use ``Record.tagging`` instead.
TIERS = ["public", "insider"]

# --- MongoDB ---------------------------------------------------------------
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017/")
MONGO_DB = os.environ.get("MONGO_DB", "websites")
MONGO_RECORDS_DB = os.environ.get("MONGO_RECORDS_DB", "Records")

# --- Anthropic (LLM extraction) -------------------------------------------
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")


