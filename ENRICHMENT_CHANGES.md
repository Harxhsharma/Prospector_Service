# MongoDB Enrichment System - Implementation Summary

## What Was Changed

Your MongoDB data structure has been enhanced with intelligent duplicate detection and data enrichment. Here's what was implemented:

### 1. **Files Created**

#### `companyparser/storage/enrichment.py`
- Core enrichment logic for merging duplicate records
- Intelligent field merging based on data type (strings, lists, numbers)
- Duplicate detection by name matching
- Enrichment metadata tracking

#### `companyparser/storage/query_utils.py`
- Query utilities to find and analyze enriched records
- Reporting functions to understand enrichment status
- Functions to find incomplete/high-confidence records

#### `ENRICHMENT_SYSTEM.md`
- Complete documentation of the enrichment system
- Usage examples and best practices
- MongoDB index recommendations

#### `enrichment_examples.py`
- 7 practical examples showing how to use the system
- Duplicate detection scenarios
- Enrichment tracking

### 2. **Files Modified**

#### `companyparser/storage/mongo_store.py`
- Updated `save_venue_record()` function to:
  - Check for duplicates by name/name_en
  - Intelligently merge data from multiple sources
  - Track enrichment history and metadata
  - Support multiple source URLs per record

## How It Works

### Current Behavior (Before)
```
Mongo Record 1: name="Chidori Zushi", source_url="tabelog.com/..."
                (Insert/Replace on save)
                
New Data from Google Maps with same name
                ↓
Mongo Record 2: name="Chidori Zushi", source_url="google.com/..."
                (Creates DUPLICATE record)
```

### New Behavior (After)
```
Mongo Record 1: name="Chidori Zushi", source_urls=["tabelog.com/..."]
                _id: ObjectId("69f6230cf53630c5bd716ba9")
                enrichment_count: 0
                
New Data from Google Maps with same name
                ↓ (Duplicate Detection)
Single Enriched Record:
  {
    "_id": "69f6230cf53630c5bd716ba9",
    "name_en": "Chidori Zushi",
    "phone": "042-321-0108",
    "website": "chidori.com",        // ← NEW from Google
    "source_urls": [
      "tabelog.com/.../13092322/",
      "google.com/maps/place/..."
    ],
    "enrichment_count": 1,
    "enriched_at": datetime,
    "last_enrichment_fields": ["website"],
  }
```

## Key Changes to Your Data

### MongoDB Document Structure

**Before (with duplicates):**
```json
{
  "_id": ObjectId("69f6230cf53630c5bd716ba9"),
  "name": "Chidori Zushi",
  "source_url": "https://tabelog.com/.../13092322/",
  "phone": "042-321-0108"
}
// If discovered again from Google:
{
  "_id": ObjectId("507f191e810c19729de860ea"),  // ← DIFFERENT ID (duplicate!)
  "name": "Chidori Zushi",
  "source_url": "https://google.com/...",
  "phone": "042-321-0108"  // ← SAME phone but new record
}
```

**After (with enrichment):**
```json
{
  "_id": ObjectId("69f6230cf53630c5bd716ba9"),
  "name": "Chidori Zushi",
  "name_en": "Chidori Zushi",
  "name_jp": "ちどり鮨",
  "source_urls": [
    "https://tabelog.com/.../13092322/",
    "https://google.com/..."
  ],
  "source_count": 2,
  "phone": "042-321-0108",
  "website": "chidori.example.com",      // ← Enriched from new source
  "reservation_method": "online_jp",    // ← Enriched from new source
  "created_at": "2026-05-02T16:15:08Z",
  "updated_at": "2026-05-02T17:30:45Z",  // ← Updated when enriched
  "enriched_at": "2026-05-02T17:30:45Z",
  "enrichment_count": 1,
  "last_enrichment_fields": ["website", "reservation_method"],
  "merged_duplicate_ids": []
}
```

## How to Use in Your Code

### Option 1: Simple Save with Auto-Enrichment (Recommended)

```python
from companyparser.storage.mongo_store import save_venue_record

# When you have new venue data:
record = {
    "name_en": "Chidori Sushi",
    "name_jp": "ちどり鮨",
    "phone": "042-321-0108",
    "website": "chidori.example.com",  # NEW field
    "source_url": "https://new-source.com/...",
    # ... other fields
}

# This automatically:
# 1. Checks if "Chidori Sushi" already exists
# 2. If yes → enriches existing record with new data
# 3. If no → creates new record
result = save_venue_record("dining", record)
print(f"Saved/enriched: {result['name_en']} (ID: {result['_id']})")
```

### Option 2: Find What You've Enriched

```python
from companyparser.storage.mongo_store import get_records_collection
from companyparser.storage.query_utils import (
    get_enrichment_status,
    find_records_by_name,
)

coll = get_records_collection("dining")

# Find all "Chidori Sushi" records
records = find_records_by_name(coll, "Chidori")

for record in records:
    status = get_enrichment_status(coll, record["_id"])
    print(f"{record['name_en']}")
    print(f"  Sources: {status['source_count']}")
    print(f"  Enrichments: {status['enrichment_count']}")
    print(f"  URLs: {status['source_urls']}")
```

### Option 3: Monitor Data Quality

```python
from companyparser.storage.query_utils import export_enrichment_report

report = export_enrichment_report(coll)
print(f"Total venues: {report['total_records']}")
print(f"Multi-enriched: {report['enrichment_distribution']['multi_enriched']['percentage']}%")
print(f"High-confidence venues: {report['multi_source_records']['percentage']}%")
```

## Merging Strategy by Field Type

| Situation | Strategy |
|-----------|----------|
| Existing: `None`, New: `"Value"` | Use new value ✓ |
| Existing: `"Short"`, New: `"Much longer value"` | Use longer value ✓ |
| Existing: `["tag1"]`, New: `["tag1", "tag2"]` | Merge to `["tag1", "tag2"]` ✓ |
| Existing: `3.5`, New: `3.6` | Use new (refined estimate) ✓ |
| Existing: `"Complete"`, New: `None` | Keep existing ✓ |

## Field Tracking

All enrichments are tracked with metadata:

```python
record = {
    "enrichment_count": 2,              # Record has been enriched 2 times
    "enriched_at": datetime(...),       # When last enriched
    "created_at": datetime(...),        # Original creation
    "updated_at": datetime(...),        # Last update (enrichment)
    "source_urls": [url1, url2, ...],   # All sources that contributed
    "source_count": 3,                  # How many sources
    "last_enrichment_fields": ["website", "phone"],  # What changed
    "merged_duplicate_ids": [...],      # Duplicate IDs that were merged
}
```

## Migration from Old Data

If you have existing duplicate records (same venue name but different MongoDB IDs), you can merge them:

```python
from companyparser.storage.enrichment import merge_duplicate_records

# Find duplicates manually or by inspection
primary = coll.find_one({"name_en": "Chidori Sushi"})
duplicate = coll.find_one({"name_en": "Chidori Sushi", "_id": {"$ne": primary["_id"]}})

if duplicate:
    # Merge duplicate into primary
    merged = merge_duplicate_records(primary, duplicate)
    coll.update_one({"_id": primary["_id"]}, {"$set": merged})
    
    # Delete the duplicate
    coll.delete_one({"_id": duplicate["_id"]})
    print(f"Merged {duplicate['_id']} into {primary['_id']}")
```

## Benefits of This System

1. **No Duplicates** - Same venue discovered from multiple sources = 1 record
2. **Rich Data** - Each record gets enriched with data from all sources
3. **Audit Trail** - Know exactly what changed, when, and from where
4. **Confidence Scores** - Records enriched multiple times = higher quality
5. **Source Tracking** - Every data point knows where it came from

## What Changed in MongoDB

When you run enrichment for the second time on a venue:

```diff
{
  "_id": ObjectId("69f6230cf53630c5bd716ba9"),
  "name_en": "Chidori Sushi",
  "phone": "042-321-0108",
- "source_urls": ["https://tabelog.com/.../13092322/"]
+ "source_urls": ["https://tabelog.com/.../13092322/", "https://google.com/..."]
- "website": null,
+ "website": "https://chidori.example.com",
+ "enriched_at": "2026-05-02T17:30:45Z",
- "enrichment_count": 0
+ "enrichment_count": 1,
+ "last_enrichment_fields": ["website"],
}
```

## Next Steps

1. **Review** the new enrichment modules:
   - `companyparser/storage/enrichment.py`
   - `companyparser/storage/query_utils.py`

2. **Run the examples** to see it in action:
   - `python enrichment_examples.py`

3. **Update your extraction code** to use:
   - `from companyparser.storage.mongo_store import save_venue_record`

4. **Monitor data quality** with:
   - `export_enrichment_report(coll)`

5. **Check the documentation** for:
   - Advanced queries
   - Bulk operations
   - Custom merge rules

---

**Questions?** Review `ENRICHMENT_SYSTEM.md` for complete documentation or check `enrichment_examples.py` for working code samples.
