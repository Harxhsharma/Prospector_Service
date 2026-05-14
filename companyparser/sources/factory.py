"""Factory: build a Playwright source connector."""
from __future__ import annotations
from typing import Optional

from .base import BaseSource
from .playwright_source import PlaywrightSource

SOURCE_TYPES = ("playwright",)


def build_source(
    source_type: str,
    url: str,
    *,
    name: str = "adhoc",
    category: str = "uncategorized",
    tier: str = "public",
    wait_for_selector: Optional[str] = None,
    tag_inside_which_to_extract: Optional[str] = None,
) -> BaseSource:
    """Construct a Playwright connector.

    Args:
        source_type:  must be 'playwright'
        url:          page URL to fetch
        name:         label for provenance
        category:     hotels | dining | cultural | nightlife
        tier:         public | insider
    """
    st = source_type.lower().strip()
    if st == "playwright":
        return PlaywrightSource(
            name=name,
            category=category,
            base_url=url,
            tier=tier,
            wait_for_selector=wait_for_selector,
            tag_inside_which_to_extract=tag_inside_which_to_extract,
        )
    raise ValueError(
        f"Unknown source type {source_type!r}. Expected one of: {', '.join(SOURCE_TYPES)}"
    )
