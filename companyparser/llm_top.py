"""LLM-backed top-N picker for venue records pulled from MongoDB.

Distinct from :mod:`companyparser.rank.llm` which operates on full ``Record``
objects from a JSON file. This module accepts plain dicts (Mongo docs) and
returns the same dicts trimmed to the top N as decided by the LLM.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

try:
    from huggingface_hub import InferenceClient
except ImportError:
    InferenceClient = None  # type: ignore[assignment,misc]

DEFAULT_MODEL = "Qwen/Qwen3.6-35B-A3B"


class TopPickerError(RuntimeError):
    pass


def _summary(doc: dict[str, Any]) -> dict[str, Any]:
    """Trim a Mongo doc to the fields the LLM actually needs."""
    keys = (
        "id",
        "name",
        "name_en",
        "name_jp",
        "description",
        "subcategory",
        "city",
        "neighborhood",
        "address",
        "price_tier",
        "tabelog_score",
        "awards",
        "tags",
        "tagging",
        "famous_score",
        "insider_score",
        "tagging_signals",
        "source_url",
    )
    out = {k: doc.get(k) for k in keys if doc.get(k) is not None}
    if "description" in out and isinstance(out["description"], str):
        out["description"] = out["description"][:400]
    return out


def _build_prompt(venue_type: str, top_n: int, items: list[dict[str, Any]]) -> str:
    return (
        "You are a luxury-travel curator for a Japan ebook (Famous vs. Insider).\n"
        f"From the JSON list of {venue_type} venues below, pick the TOP {top_n}.\n"
        "Rank by: editorial quality, uniqueness, insider appeal, evidence of awards/press, "
        "and how strongly the venue fits a luxury Japan-travel guide.\n"
        "Return STRICT JSON, no prose, of the form:\n"
        '{"top": [{"source_url": "...", "rank": 1, "reason": "..."}]}\n'
        "Only use source_url values that appear in the input. Do not invent venues.\n\n"
        f"INPUT:\n{json.dumps(items, ensure_ascii=False)}"
    )


def _parse_top(text: str) -> list[dict[str, Any]]:
    match = re.search(r"\{[\s\S]*\"top\"[\s\S]*\}", text)
    if not match:
        raise TopPickerError(f"LLM did not return parseable JSON. Got: {text[:200]}")
    try:
        return json.loads(match.group(0))["top"]
    except (json.JSONDecodeError, KeyError) as e:
        raise TopPickerError(f"Bad LLM JSON: {e}") from e


def _heuristic_pick(records: list[dict[str, Any]], top_n: int) -> list[dict[str, Any]]:
    """Fallback when no HF_TOKEN: rank by insider_score → famous_score → tabelog_score."""
    def _key(d: dict[str, Any]):
        return (
            d.get("insider_score") or 0,
            d.get("famous_score") or 0,
            d.get("tabelog_score") or 0,
            len(d.get("description") or ""),
        )

    ranked = sorted(records, key=_key, reverse=True)[:top_n]
    return [
        {
            "source_url": r.get("source_url"),
            "rank": i + 1,
            "reason": "heuristic (no HF_TOKEN set)",
        }
        for i, r in enumerate(ranked)
    ]


def pick_top_n(
    records: list[dict[str, Any]],
    *,
    venue_type: str,
    top_n: int,
    model: str = DEFAULT_MODEL,
    token: Optional[str] = None,
) -> list[dict[str, Any]]:
    """Return [{source_url, rank, reason}, ...] of length <= top_n."""
    if not records:
        return []
    if len(records) <= top_n:
        return [
            {"source_url": r.get("source_url"), "rank": i + 1, "reason": "only candidate"}
            for i, r in enumerate(records)
        ]

    hf_token = token or os.getenv("HF_TOKEN")
    if not hf_token:
        return _heuristic_pick(records, top_n)

    if InferenceClient is None:
        raise TopPickerError(
            "huggingface_hub not installed. Run: pip install huggingface_hub"
        )

    items = [_summary(r) for r in records]
    prompt = _build_prompt(venue_type, top_n, items)
    client = InferenceClient(model=model, token=hf_token)
    try:
        response = client.text_generation(
            prompt, max_new_tokens=2048, temperature=0.2, return_full_text=False
        )
    except Exception as e:
        raise TopPickerError(f"HF inference failed: {e}") from e

    return _parse_top(response)
