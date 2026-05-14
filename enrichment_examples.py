"""Example usage of the MongoDB enrichment system.

This demonstrates how to use the new enrichment features to:
1. Save venue records with automatic duplicate detection
2. Track enrichment history
3. Query enriched data
4. Handle multi-source venues
"""

from datetime import datetime
from bson import ObjectId
from companyparser.storage.mongo_store import get_records_collection, save_venue_record
from companyparser.storage.query_utils import (
    get_enrichment_status,
    find_records_by_name,
    get_highly_enriched_records,
    get_merged_duplicates,
    export_enrichment_report,
    get_incomplete_records,
)


def example_1_save_with_enrichment():
    """Example 1: Save a venue record from Tabelog with enrichment."""
    
    # First extraction from Tabelog
    tabelog_record = {
        "name_en": "Chidori Sushi",
        "name_jp": "ちどり鮨",
        "name": "Chidori Sushi",  # For backward compat
        "phone": "042-321-0108",
        "address": "3-15-5 Minamimachi, Kokubunji",
        "address_jp": "東京都国分寺市南町3-15-5",
        "city": "Tokyo",
        "neighborhood": "Kokubunji",
        "category": "dining",
        "subcategory": "Sushi",
        "tabelog_score": 3.06,
        "price_tier": "¥¥¥",
        "source_url": "https://tabelog.com/en/tokyo/A1325/A132502/13092322/dtlrvwlst/",
        "source_type": "playwright",
        "tags": ["sushi", "family friendly"],
    }
    
    # Save with automatic duplicate detection
    result = save_venue_record("dining", tabelog_record)
    
    print(f"✓ Created record: {result['name_en']} (MongoDB ID: {result['_id']})")
    print(f"  - Source: {result['source_urls']}")
    print(f"  - Enrichments: {result['enrichment_count']}")


def example_2_enrichment_from_multiple_sources():
    """Example 2: Same venue discovered from another source gets enriched."""
    
    # Later, same venue found from Google
    google_record = {
        "name_en": "Chidori Sushi",
        "name_jp": "ちどり鮨",
        "phone": "042-321-0108",  # Same phone
        "website": "https://chidori-sushi.example.com",  # NEW INFO
        "address_jp": "東京都国分寺市南町3-15-5",
        "reservation_method": "online_jp",  # NEW INFO
        "english_friendly": "limited",  # NEW INFO
        "source_url": "https://www.google.com/maps/place/Chidori+Sushi/",
        "source_type": "playwright",
    }
    
    # Save - system detects duplicate by name_en and ENRICHES instead of creating new
    result = save_venue_record("dining", google_record)
    
    print(f"✓ Enriched record: {result['name_en']} (MongoDB ID: {result['_id']})")
    print(f"  - Sources: {result['source_urls']} (now {len(result['source_urls'])} sources!)")
    print(f"  - Enrichments: {result['enrichment_count']}")
    print(f"  - Website: {result.get('website')} (from Google)")
    print(f"  - Reservation: {result.get('reservation_method')} (from Google)")


def example_3_track_enrichment_history():
    """Example 3: Check enrichment history of a record."""
    
    coll = get_records_collection("dining")
    
    # Find Chidori Sushi records
    records = find_records_by_name(coll, "Chidori")
    
    if records:
        for record in records:
            status = get_enrichment_status(coll, record["_id"])
            
            print(f"\n{'='*60}")
            print(f"Venue: {status['name']}")
            print(f"{'='*60}")
            print(f"MongoDB ID:        {status['record_id']}")
            print(f"Enrichments:       {status['enrichment_count']}")
            print(f"Data Sources:      {status['source_count']}")
            print(f"  {status['source_urls']}")
            print(f"Created:           {status['created_at']}")
            print(f"Last Updated:      {status['updated_at']}")
            if status['enriched_at']:
                print(f"Last Enriched:     {status['enriched_at']}")
                print(f"Fields Updated:    {', '.join(status['last_enrichment_fields'])}")
            if status['merged_duplicate_ids']:
                print(f"Merged Duplicates: {status['merged_duplicate_ids']}")


def example_4_find_high_confidence_records():
    """Example 4: Find venues enriched multiple times (high confidence)."""
    
    coll = get_records_collection("dining")
    
    # Get records enriched 2+ times
    high_conf = get_highly_enriched_records(coll, min_enrichments=2, limit=10)
    
    print(f"\n{'='*60}")
    print(f"HIGH-CONFIDENCE VENUES (enriched 2+ times)")
    print(f"{'='*60}")
    
    for i, record in enumerate(high_conf, 1):
        print(f"{i}. {record['name_en']}")
        print(f"   - Enrichments: {record['enrichment_count']}")
        print(f"   - Sources: {len(record.get('source_urls', []))}")
        print(f"   - Score: {record.get('tabelog_score', 'N/A')}")
        print()


def example_5_find_incomplete_records():
    """Example 5: Find venues that need more enrichment."""
    
    coll = get_records_collection("dining")
    
    # Get records with many missing fields
    incomplete = get_incomplete_records(coll, limit=5, fields_threshold=4)
    
    print(f"\n{'='*60}")
    print(f"INCOMPLETE RECORDS (need enrichment)")
    print(f"{'='*60}")
    
    for i, record in enumerate(incomplete, 1):
        missing = sum(1 for field in ['phone', 'website', 'description', 'price_tier', 'reservation_method']
                     if not record.get(field))
        print(f"{i}. {record['name_en']} - Missing {missing} key fields")
        print(f"   ID: {record['_id']}")
        print(f"   Current sources: {len(record.get('source_urls', []))}")
        print()


def example_6_enrichment_statistics():
    """Example 6: Generate enrichment report."""
    
    coll = get_records_collection("dining")
    
    report = export_enrichment_report(coll)
    
    print(f"\n{'='*60}")
    print(f"ENRICHMENT STATISTICS")
    print(f"{'='*60}")
    print(f"Total Records:           {report['total_records']}")
    print(f"\nEnrichment Distribution:")
    print(f"  Never enriched:        {report['enrichment_distribution']['never_enriched']['count']} ({report['enrichment_distribution']['never_enriched']['percentage']}%)")
    print(f"  Once enriched:         {report['enrichment_distribution']['once_enriched']['count']} ({report['enrichment_distribution']['once_enriched']['percentage']}%)")
    print(f"  Multi-enriched (2+):   {report['enrichment_distribution']['multi_enriched']['count']} ({report['enrichment_distribution']['multi_enriched']['percentage']}%)")
    print(f"\nMulti-source Records:    {report['multi_source_records']['count']} ({report['multi_source_records']['percentage']}%)")
    print(f"Merged Duplicates:       {report['merged_records']['count']} ({report['merged_records']['percentage']}%)")
    print(f"Average Enrichments:     {report['average_enrichments']}")


def example_7_handle_duplicate_detection():
    """Example 7: Demonstrate duplicate detection and merging."""
    
    # Scenario: Two sources discover the same venue with slightly different data
    
    # Source 1: Wikipedia-style guide
    source1_record = {
        "name_en": "Shun Sushi",
        "name_jp": "旬寿司",
        "phone": "03-1234-5678",
        "address": "1-2-3 Ginza, Chuo",
        "city": "Tokyo",
        "neighborhood": "Ginza",
        "description": "Premium sushi omakase experience",
        "category": "dining",
        "subcategory": "Sushi",
        "price_tier": "¥¥¥¥",
        "source_url": "https://luxury-guide.example.com/venues/shun",
        "source_type": "playwright",
    }
    
    print(f"\n{'='*60}")
    print(f"DUPLICATE DETECTION EXAMPLE")
    print(f"{'='*60}")
    print(f"Saving source 1: {source1_record['name_en']} from {source1_record['source_url']}")
    
    result1 = save_venue_record("dining", source1_record)
    print(f"✓ Created: {result1['_id']}")
    
    # Source 2: Travel blog (same venue, slightly different data)
    source2_record = {
        "name_en": "Shun Sushi",  # Same name!
        "name_jp": "旬寿司",
        "website": "https://shun-sushi.jp",  # NEW
        "reservation_method": "online_jp",  # NEW
        "english_friendly": "limited",  # NEW
        "tags": ["omakase", "premium"],  # NEW
        "source_url": "https://travel-blog.example.com/shun-sushi-review",
        "source_type": "playwright",
    }
    
    print(f"\nSaving source 2: {source2_record['name_en']} from {source2_record['source_url']}")
    print("System detects duplicate by name_en...")
    
    result2 = save_venue_record("dining", source2_record)
    print(f"✓ Enriched: {result2['_id']} (same record as source 1!)")
    print(f"\nResult:")
    print(f"  - Single MongoDB record with data from BOTH sources")
    print(f"  - source_urls: {result2['source_urls']}")
    print(f"  - website: {result2.get('website')} (from source 2)")
    print(f"  - reservation_method: {result2.get('reservation_method')} (from source 2)")
    print(f"  - enrichment_count: {result2['enrichment_count']}")


if __name__ == "__main__":
    print("MongoDB Enrichment System Examples\n")
    
    # Run examples
    try:
        example_1_save_with_enrichment()
        example_2_enrichment_from_multiple_sources()
        example_3_track_enrichment_history()
        example_4_find_high_confidence_records()
        example_5_find_incomplete_records()
        example_6_enrichment_statistics()
        example_7_handle_duplicate_detection()
        
    except Exception as e:
        print(f"\n✗ Error: {e}")
        import traceback
        traceback.print_exc()
    
    print(f"\n{'='*60}")
    print("Examples completed!")
