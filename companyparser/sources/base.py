"""Base interface for all source connectors."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Iterable

from ..models import RawPayload, Record, SourceType


class BaseSource(ABC):
    """A connector responsible for one external data source."""

    name: str
    source_type: SourceType
    category: str
    tier: str = "public"

    def __init__(self, name: str, category: str, tier: str = "public") -> None:
        self.name = name
        self.category = category
        self.tier = tier

    @abstractmethod
    def fetch(self) -> Iterable[RawPayload]:
        """Fetch raw payloads. Must be polite (rate-limited, cached)."""

    @abstractmethod
    def parse(self, payload: RawPayload) -> Iterable[Record]:
        """Parse a raw payload into structured records."""

    def run(self) -> Iterable[Record]:
        """Convenience: fetch + parse."""
        for payload in self.fetch():
            yield from self.parse(payload)
