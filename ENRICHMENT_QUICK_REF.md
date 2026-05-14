# Quick Reference - Enrichment System

## Save Records

```python
from companyparser.storage.mongo_store import save_venue_record

# Save with automatic enrichment (default)
result = save_venue_record("dining", record)

# Save WITHOUT checking for duplicates (faster, use for initial bulk import)
result = save_venue_record("dining", record, check_duplicates=False)

# Save WITHOUT enriching existing records (replace instead of merge)
result = save_venue_record("dining", record, enrich_existing=False)
```

## Query Records

```python
from companyparser.storage.mongo_store import get_records_collection
from companyparser.storage.query_utils import *

coll = get_records_collection("dining")

# Find by name
records = find_records_by_name(coll, "Sushi")

# Get enrichment status of one record
status = get_enrichment_status(coll, record_id)

# Get high-confidence records (enriched 2+ times)
high_conf = get_highly_enriched_records(coll, min_enrichments=2)

# Get records from multiple sources
multi_src = get_records_with_multiple_sources(coll)

# Get incomplete records (need enrichment)
incomplete = get_incomplete_records(coll, fields_threshold=5)

# Get all merged duplicates
merged = get_merged_duplicates(coll)

# Never enriched
unenriched = get_unenriched_records(coll, limit=100)
```

## Reports

```python
# Get overall statistics
report = export_enrichment_report(coll)

# Print nicely formatted report
print(f"Total: {report['total_records']}")
print(f"Multi-enriched: {report['enrichment_distribution']['multi_enriched']['percentage']}%")
print(f"Multi-source: {report['multi_source_records']['percentage']}%")
```

## Check Single Record Status

```python
status = get_enrichment_status(coll, ObjectId("..."))
print(status['enrichment_count'])       # How many times enriched
print(status['source_count'])           # How many sources
print(status['source_urls'])            # Which URLs contributed
print(status['last_enrichment_fields']) # What was updated
```

## MongoDB Structure

```json
{
  "_id": ObjectId("..."),           // ← Use this as unique venue ID
  "name_en": "Venue Name",
  "name_jp": "会場名",
  "source_urls": ["url1", "url2"],  // ← All discovery sources
  "source_count": 2,                // ← How many sources
  "enrichment_count": 1,            // ← How many times enriched
  "enriched_at": "datetime",        // ← When last enriched
  "created_at": "datetime",         // ← When created
  "updated_at": "datetime",         // ← When last updated
  "last_enrichment_fields": [],     // ← What changed
  "merged_duplicate_ids": [],       // ← Duplicates merged
  // ... venue data fields
}
```

## Enrichment Strategy

| Field Type | Rule |
|-----------|------|
| null → value | ✓ Use value |
| string | ✓ Use longer |
| number | ✓ Use newer |
| list | ✓ Merge & dedupe |
| existing | ✓ Keep unless better |

## Common Operations

### Create or Enrich a Venue
```python
# Same code for new or existing - system handles it
result = save_venue_record("dining", {
    "name_en": "Sushi Place",
    "phone": "123-4567",
    "source_url": "https://...",
})
```

### Find All Venues by City
```python
results = list(coll.find({"city": "Tokyo"}))
```

### Find Venues with Multiple Sources
```python
from companyparser.storage.query_utils import get_records_with_multiple_sources
multi = get_records_with_multiple_sources(coll, limit=50)
```

### Find High-Quality Venues
```python
# Enriched 2+ times = high confidence
high_quality = get_highly_enriched_records(coll, min_enrichments=2, limit=20)
```

### Check if Venue is Complete
```python
record = coll.find_one({"name_en": "Venue"})

required_fields = ["phone", "address", "price_tier"]
is_complete = all(record.get(f) for f in required_fields)
```

### Find Venues Needing More Data
```python
# Find incomplete records
incomplete = get_incomplete_records(coll, fields_threshold=4)

for v in incomplete:
    print(f"{v['name_en']} - needs enrichment")
```

## Debugging

```python
# Check if enrichment is working
status = get_enrichment_status(coll, record_id)
assert status['enrichment_count'] > 0  # Should be > 0 after enrichment

# Find duplicates that should be merged
from companyparser.storage.query_utils import get_merged_duplicates
merged = get_merged_duplicates(coll)  # Records that absorbed duplicates

# Check data quality
report = export_enrichment_report(coll)
print(f"Quality: {report['enrichment_distribution']['multi_enriched']['percentage']}% multi-enriched")
```

## Bulk Operations

```python
# Mark all records from a source as enriched
source_url = "https://tabelog.com/..."
records = coll.find({"source_urls": source_url})
print(f"Found {records.count()} records from {source_url}")

# Get enrichment statistics
report = export_enrichment_report(coll)
```

## Response Structure

When you save a record, you get back:

```python
result = save_venue_record("dining", record)

result['_id']                    # ObjectId - use this as unique ID
result['name_en']               # Enriched name
result['source_urls']           # All sources that contributed
result['enrichment_count']       # How many times enriched
result['created_at']            # When created
result['updated_at']            # When last updated
result['last_enrichment_fields'] # What changed
```

## Notes

- `_id` is MongoDB's auto-generated unique ID for each venue
- `source_url` in the input record tracks where data came from
- `source_urls` in the saved record lists ALL sources for that venue
- Enrichment happens automatically on save (no extra call needed)
- Duplicates are merged, not replaced
- All enrichments are tracked for audit trail
