# MongoDB Data Enrichment System

## Overview

The new enrichment system automatically detects duplicate venues and intelligently merges data from multiple sources, enriching the MongoDB records over time.

## Key Features

### 1. **Duplicate Detection by Name**
When a new venue record is saved, the system checks for existing records with matching `name_en` or `name` (case-insensitive). If a duplicate is found, data is merged instead of creating a new record.

### 2. **Intelligent Field Merging**
For each field, the system applies intelligent merging rules:

| Field Type | Merge Strategy |
|-----------|-----------------|
| `null` values | Replace with non-null | 
| String fields | Keep longer value (more detailed) |
| Numeric fields | Use newer value (assumed refined) |
| List fields | Merge and deduplicate |
| Objects/Dates | Keep newest |

### 3. **Multiple Source Tracking**
Records track all `source_urls` they were discovered from:
```python
{
  "_id": ObjectId("..."),
  "name_en": "Chidori Zushi",
  "source_urls": [
    "https://tabelog.com/.../13092322/",
    "https://other-source.com/venues/xyz"
  ],
  "source_count": 2
}
```

### 4. **Enrichment Audit Trail**
Every enrichment creates audit metadata:
```python
{
  "enrichment_count": 2,           # How many times enriched
  "last_enrichment_fields": ["phone", "website"],  # What changed
  "enriched_at": datetime.now(),
  "created_at": datetime(...),
  "updated_at": datetime(...),
  "merged_duplicate_ids": ["507f1f77bcf86cd799439011"]  # What was merged
}
```

## Data Structure

### Record with `_id` as Primary Identifier

```json
{
  "_id": {
    "$oid": "69f6230cf53630c5bd716ba9"
  },
  "name": "Chidori Zushi",
  "name_en": "Chidori Zushi",
  "name_jp": "ちどり鮨",
  "source_urls": ["https://tabelog.com/.../13092322/"],
  "source_count": 1,
  "phone": "042-321-0108",
  "address": "3-15-5 Minamimachi, Kokubunji",
  "address_jp": "東京都国分寺市南町3-15-5",
  "city": "Tokyo",
  "neighborhood": "Kokubunji",
  "category": "dining",
  "subcategory": "Sushi",
  "tabelog_score": 3.06,
  "price_tier": "¥¥¥",
  "created_at": "2026-05-02T16:15:08.374923Z",
  "updated_at": "2026-05-02T16:15:08.374923Z",
  "enrichment_count": 0,
  "merged_duplicate_ids": []
}
```

## Usage Examples

### Saving a Record with Enrichment

```python
from companyparser.storage.mongo_store import save_venue_record

# Save with automatic duplicate detection and enrichment
record = {
    "name_en": "Chidori Zushi",
    "name_jp": "ちどり鮨",
    "phone": "042-321-0108",
    "source_url": "https://tabelog.com/.../13092322/",
    # ... other fields
}

result = save_venue_record(
    "dining",
    record,
    check_duplicates=True,      # Detect duplicates by name
    enrich_existing=True        # Merge if duplicate found
)

print(f"Record saved with _id: {result['_id']}")
```

### Querying Enrichment Status

```python
from companyparser.storage.mongo_store import get_records_collection
from companyparser.storage.query_utils import (
    get_enrichment_status,
    find_records_by_name,
    get_highly_enriched_records,
    export_enrichment_report,
)

coll = get_records_collection("dining")

# Check enrichment history for a specific record
status = get_enrichment_status(coll, record_id)
print(f"Enrichment count: {status['enrichment_count']}")
print(f"Sources: {status['source_count']}")
print(f"Last enriched fields: {status['last_enrichment_fields']}")

# Find all records for a venue
records = find_records_by_name(coll, "Chidori")
for r in records:
    print(f"{r['name_en']} ({r['_id']}): {r.get('enrichment_count', 0)} enrichments")

# Get high-confidence records (enriched 2+ times)
high_conf = get_highly_enriched_records(coll, min_enrichments=2)
print(f"Found {len(high_conf)} high-confidence records")

# Export enrichment statistics
report = export_enrichment_report(coll)
print(f"Total records: {report['total_records']}")
print(f"Multi-enriched: {report['enrichment_distribution']['multi_enriched']['percentage']}%")
```

### Handling Duplicates After Discovery

When the same venue is discovered from two different sources:

**Before:**
- Record A: `{_id: ObjectId(...), name_en: "Sushi Abe", phone: "123-4567"}`
- Record B (duplicate from new source)

**After Enrichment:**
- Single Record: `{_id: ObjectId(...), name_en: "Sushi Abe", phone: "123-4567", source_urls: [url1, url2], merged_duplicate_ids: [old_id]}`

## Enrichment Rules

### String Fields
```
existing: "Short name"
new:      "Very detailed restaurant name with full description"
result:   "Very detailed restaurant name with full description"  ✓ (longer = better)
```

### Null Handling
```
existing: None
new:      "Best Western Hotel"
result:   "Best Western Hotel"  ✓ (non-null preferred)
```

### List Fields (awards, tags, etc.)
```
existing: ["Michelin 1*", "Fine Dining"]
new:      ["Michelin 1*", "Modern Cuisine"]
result:   ["Michelin 1*", "Fine Dining", "Modern Cuisine"]  ✓ (merged & deduped)
```

### Numeric Fields (scores, review counts)
```
existing: 3.5
new:      3.6
result:   3.6  ✓ (newer assumed refined)
```

## Bulk Enrichment Operations

### Merging Identified Duplicates

```python
from companyparser.storage.enrichment import merge_duplicate_records

# If you identify duplicates manually
primary = coll.find_one({"_id": ObjectId("...")})
duplicate = coll.find_one({"_id": ObjectId("...")})

merged = merge_duplicate_records(primary, duplicate, keep_primary_name=True)
coll.update_one({"_id": primary["_id"]}, {"$set": merged})

# Mark original as merged
coll.delete_one({"_id": duplicate["_id"]})
```

### Batch Enrichment Report

```python
from companyparser.storage.query_utils import (
    export_enrichment_report,
    get_unenriched_records,
    get_incomplete_records,
)

# Get statistics
report = export_enrichment_report(coll)

# Find records that need attention
unenriched = get_unenriched_records(coll, limit=50)
incomplete = get_incomplete_records(coll, limit=50, fields_threshold=5)

print(f"Unenriched: {len(unenriched)}")
print(f"Incomplete: {len(incomplete)}")
```

## MongoDB Indexes

For optimal performance, create these indexes:

```python
coll.create_index("name_en")
coll.create_index("name")
coll.create_index("name_jp")
coll.create_index("source_url")
coll.create_index("created_at")
coll.create_index("enrichment_count")
coll.create_index([("enrichment_count", -1)])
```

## Configuration

Control enrichment behavior globally:

```python
# Save with enrichment (default)
save_venue_record("dining", record, check_duplicates=True, enrich_existing=True)

# Save without checking duplicates (faster, for initial bulk import)
save_venue_record("dining", record, check_duplicates=False)

# Save without enrichment (replace existing)
save_venue_record("dining", record, enrich_existing=False)
```

## Troubleshooting

### "Record not found" errors
- Check that the source_url or id field is present
- Verify MongoDB connection

### Duplicates not being detected
- Ensure `check_duplicates=True` (default)
- Check that name_en or name fields are populated
- Verify fields are strings (not None or objects)

### Enrichment not happening
- Check that `enrich_existing=True` (default)
- Verify existing record exists in collection
- Review `last_enrichment_fields` to see what changed

## Best Practices

1. **Always check_duplicates=True for production** - Catches venue duplicates across multiple sources
2. **Monitor enrichment_count** - Records with higher counts are more reliable
3. **Use source_urls tracking** - Helps identify which sources contributed
4. **Regular exports** - Generate enrichment reports to monitor data quality
5. **Archive merged records** - Consider keeping deleted duplicate IDs for audit trails
