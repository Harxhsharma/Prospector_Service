"""Utility functions for querying and managing enriched venue records."""
from __future__ import annotations

import logging
from typing import Any, Optional
from bson import ObjectId

logger = logging.getLogger(__name__)


def get_enrichment_status(coll, record_id: ObjectId) -> dict[str, Any]:
    """Get enrichment history and status for a record.
    
    Returns metadata about how many times a record has been enriched,
    what fields have been updated, and when.
    """
    doc = coll.find_one({"_id": record_id})
    if not doc:
        return {"error": f"Record {record_id} not found"}
    
    return {
        "record_id": str(record_id),
        "name": doc.get("name_en") or doc.get("name"),
        "enrichment_count": doc.get("enrichment_count", 0),
        "source_urls": doc.get("source_urls", []),
        "source_count": len(doc.get("source_urls", [])),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
        "enriched_at": doc.get("enriched_at"),
        "last_enrichment_fields": doc.get("last_enrichment_fields", []),
        "merged_duplicate_ids": doc.get("merged_duplicate_ids", []),
    }


def find_records_by_name(coll, name: str) -> list[dict[str, Any]]:
    """Find records matching a name (exact or partial).
    
    Searches across name, name_en, and name_jp fields.
    """
    query = {
        "$or": [
            {"name_en": {"$regex": name, "$options": "i"}},
            {"name": {"$regex": name, "$options": "i"}},
            {"name_jp": {"$regex": name, "$options": "i"}},
        ]
    }
    return list(coll.find(query))


def get_unenriched_records(coll, limit: int = 100) -> list[dict[str, Any]]:
    """Get records with enrichment_count == 0 (never enriched).
    
    Useful for identifying which records could benefit from more sources.
    """
    return list(coll.find({"enrichment_count": 0}).limit(max(1, limit)))


def get_highly_enriched_records(
    coll,
    min_enrichments: int = 2,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Get records enriched multiple times (high confidence).
    
    Records enriched multiple times have data from different sources,
    making them higher confidence.
    """
    return list(
        coll.find({"enrichment_count": {"$gte": min_enrichments}})
        .sort("enrichment_count", -1)
        .limit(max(1, limit))
    )


def get_merged_duplicates(coll, limit: int = 100) -> list[dict[str, Any]]:
    """Get records that are results of duplicate merges.
    
    These records have absorbed data from multiple equivalent venues.
    """
    return list(
        coll.find({"merged_duplicate_ids": {"$exists": True, "$ne": []}})
        .sort("last_merged_at", -1)
        .limit(max(1, limit))
    )


def get_records_with_multiple_sources(coll, limit: int = 100) -> list[dict[str, Any]]:
    """Get records discovered from multiple source URLs.
    
    These are high-value records found through different crawl paths.
    """
    return list(
        coll.find({"source_urls": {"$size": {"$gte": 2}}})
        .sort("source_count", -1)
        .limit(max(1, limit))
    )


def get_incomplete_records(
    coll,
    limit: int = 100,
    fields_threshold: int = 5,
) -> list[dict[str, Any]]:
    """Get records with many null/missing fields.
    
    These records could benefit from additional sources and enrichment.
    
    Args:
        coll: MongoDB collection
        limit: Maximum records to return
        fields_threshold: Only return records with this many or fewer non-null optional fields
    """
    # This requires aggregation in MongoDB for efficiency
    return list(
        coll.aggregate([
            {
                "$project": {
                    "_id": 1,
                    "name_en": 1,
                    "name": 1,
                    "null_count": {
                        "$sum": [
                            {"$cond": [{"$eq": ["$description", None]}, 1, 0]},
                            {"$cond": [{"$eq": ["$address", None]}, 1, 0]},
                            {"$cond": [{"$eq": ["$price_tier", None]}, 1, 0]},
                            {"$cond": [{"$eq": ["$why_on_list", None]}, 1, 0]},
                            {"$cond": [{"$eq": ["$signature_dish_or_feature", None]}, 1, 0]},
                            {"$cond": [{"$eq": ["$hype_indicators", None]}, 1, 0]},
                            {"$cond": [{"$eq": ["$tier", None]}, 1, 0]},
                            {"$cond": [{"$eq": ["$subcategory", None]}, 1, 0]},
                            {"$cond": [{"$eq": ["$neighborhood", None]}, 1, 0]},
                        ]
                    }
                }
            },
            {"$match": {"null_count": {"$gte": fields_threshold}}},
            {"$sort": {"null_count": -1}},
            {"$limit": max(1, limit)},
        ])
    )


def export_enrichment_report(coll) -> dict[str, Any]:
    """Generate a report of enrichment statistics across all records."""
    total_records = coll.count_documents({})
    
    if total_records == 0:
        return {"error": "No records found", "total": 0}
    
    # Records by enrichment level
    never_enriched = coll.count_documents({"enrichment_count": 0})
    once_enriched = coll.count_documents({"enrichment_count": 1})
    multi_enriched = coll.count_documents({"enrichment_count": {"$gte": 2}})
    
    # Records with merges
    with_merges = coll.count_documents({"merged_duplicate_ids": {"$exists": True, "$ne": []}})
    
    # Records with multiple sources
    multi_source = coll.count_documents({"source_urls": {"$size": {"$gte": 2}}})
    
    # Average fields filled
    avg_enrichments = coll.aggregate([
        {"$group": {"_id": None, "avg": {"$avg": "$enrichment_count"}}}
    ])
    avg_enrich_value = list(avg_enrichments)[0]["avg"] if avg_enrichments else 0
    
    return {
        "total_records": total_records,
        "enrichment_distribution": {
            "never_enriched": {
                "count": never_enriched,
                "percentage": round(100 * never_enriched / total_records, 2),
            },
            "once_enriched": {
                "count": once_enriched,
                "percentage": round(100 * once_enriched / total_records, 2),
            },
            "multi_enriched": {
                "count": multi_enriched,
                "percentage": round(100 * multi_enriched / total_records, 2),
            },
        },
        "merged_records": {
            "count": with_merges,
            "percentage": round(100 * with_merges / total_records, 2),
        },
        "multi_source_records": {
            "count": multi_source,
            "percentage": round(100 * multi_source / total_records, 2),
        },
        "average_enrichments": round(avg_enrich_value, 2),
    }
