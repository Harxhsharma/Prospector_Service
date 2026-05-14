"""Data enrichment logic for MongoDB records.

Handles:
- Duplicate detection by name/name_en
- Intelligent merging of venue data
- Enrichment of null and existing values
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional
from bson import ObjectId

logger = logging.getLogger(__name__)


def _is_better_value(existing: Any, new: Any) -> bool:
    """Determine if new value is better than existing."""
    # Prefer non-null over null
    if existing is None and new is not None:
        return True
    if existing is None or new is None:
        return False
    
    # For strings: prefer longer non-whitespace values
    if isinstance(existing, str) and isinstance(new, str):
        existing_clean = existing.strip()
        new_clean = new.strip()
        if len(new_clean) > len(existing_clean):
            return True
    
    # For lists: merge and deduplicate
    if isinstance(existing, list) and isinstance(new, list):
        return False  # Handle lists separately
    
    return False


def _merge_field(field_name: str, existing: Any, new: Any) -> tuple[bool, Any]:
    """Merge a single field. Returns (changed, merged_value)."""
    
    # List fields: merge and deduplicate
    if isinstance(existing, list) and isinstance(new, list):
        merged = list(set(existing) | set(new)) if all(
            isinstance(x, (str, int, float, bool)) for x in existing + new
        ) else existing + new
        changed = len(merged) > len(existing)
        return (changed, merged)
    
    if isinstance(existing, list) and new is not None:
        if new not in existing:
            return (True, existing + [new])
        return (False, existing)
    
    # Scalar fields: prefer non-null and better quality
    if existing is None and new is not None:
        return (True, new)
    
    if existing is not None and new is None:
        return (False, existing)
    
    # For strings: longer is usually better
    if isinstance(existing, str) and isinstance(new, str):
        if len(new.strip()) > len(existing.strip()):
            return (True, new)
    
    # Numbers: prefer newer (LLM might refine)
    if isinstance(existing, (int, float)) and isinstance(new, (int, float)):
        if existing != new:
            return (True, new)  # Assume new is refined
    
    return (False, existing)


def find_duplicate_by_name(
    coll,
    name: Optional[str],
    name_en: Optional[str],
    exclude_id: Optional[ObjectId] = None,
) -> Optional[dict[str, Any]]:
    """Find an existing record with matching name/name_en.
    
    Args:
        coll: MongoDB collection
        name: name field (Japanese)
        name_en: name_en field (English)
        exclude_id: MongoDB ObjectId to exclude (useful when updating)
    
    Returns:
        Matching document or None
    """
    query = {"$or": []}
    
    if name_en:
        query["$or"].append({"name_en": name_en})
    if name:
        query["$or"].append({"name": name})
    
    if not query["$or"]:
        return None
    
    if exclude_id:
        query["_id"] = {"$ne": exclude_id}
    
    return coll.find_one(query)


def enrich_record(
    existing_doc: dict[str, Any],
    new_record: dict[str, Any],
) -> dict[str, Any]:
    """Merge new record data into existing document.
    
    Returns the enriched document with:
    - Updated fields where new data is better
    - Merged lists (deduped)
    - Tracking of what changed
    
    Args:
        existing_doc: Current MongoDB document
        new_record: New extracted Record (as dict)
    
    Returns:
        Enriched document ready for MongoDB update
    """
    enriched = existing_doc.copy()
    changes = {}
    
    # Fields to potentially enrich
    enrichment_fields = [
        "name",
        "name_en",
        "name_jp",
        "name_local",
        "description",
        "city",
        "neighborhood",
        "address",
        "address_jp",
        "phone",
        "website",
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
        "tags",
        "images",
        "photos",
    ]
    
    for field in enrichment_fields:
        existing_val = enriched.get(field)
        new_val = new_record.get(field)
        
        changed, merged_val = _merge_field(field, existing_val, new_val)
        
        if changed:
            enriched[field] = merged_val
            changes[field] = {
                "old": existing_val,
                "new": merged_val,
            }
    
    # Track enrichment metadata
    enriched["enriched_at"] = datetime.now(timezone.utc)
    enriched["enrichment_count"] = enriched.get("enrichment_count", 0) + 1
    enriched["last_enrichment_fields"] = list(changes.keys())
    
    if changes:
        logger.info(
            "Enriched record %s (%s): %d fields updated",
            enriched.get('_id'), enriched.get('name_en'), len(changes),
        )
    
    return enriched


def merge_duplicate_records(
    primary_doc: dict[str, Any],
    duplicate_doc: dict[str, Any],
    keep_primary_name: bool = True,
) -> dict[str, Any]:
    """Merge two duplicate records into one.
    
    The primary document is updated with non-null/better values from duplicate.
    
    Args:
        primary_doc: The document to keep (will be updated)
        duplicate_doc: The document to merge into primary
        keep_primary_name: If True, keep primary's name even if duplicate is better
    
    Returns:
        Merged document
    """
    merged = primary_doc.copy()
    
    enrichment_fields = [
        "description",
        "city",
        "neighborhood",
        "address",
        "address_jp",
        "phone",
        "website",
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
        "tags",
        "images",
        "photos",
    ]
    
    if not keep_primary_name:
        enrichment_fields = ["name", "name_en", "name_jp"] + enrichment_fields
    
    for field in enrichment_fields:
        existing_val = merged.get(field)
        new_val = duplicate_doc.get(field)
        
        if new_val is not None:
            changed, merged_val = _merge_field(field, existing_val, new_val)
            if changed:
                merged[field] = merged_val
    
    # Track merge metadata
    merged["merged_duplicate_ids"] = (
        merged.get("merged_duplicate_ids", []) + [str(duplicate_doc.get("_id", "unknown"))]
    )
    merged["last_merged_at"] = datetime.now(timezone.utc)
    
    return merged
