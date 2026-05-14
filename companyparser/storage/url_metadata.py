from datetime import datetime, timezone
from typing import Optional
from pymongo.collection import Collection

def update_lastfoundurl_metadata(coll: Collection, parent_url: str, child_url: str, *, lastCrawled: Optional[datetime], lastStatus: Optional[str]):
    """
    Update the metadata for a specific child_url in lastFoundURLs for a given parent_url document.
    """
    update_fields = {}
    if lastCrawled is not None:
        update_fields["lastFoundURLs.$.lastCrawled"] = lastCrawled
    if lastStatus is not None:
        update_fields["lastFoundURLs.$.lastStatus"] = lastStatus
    if update_fields:
        coll.update_one(
            {"website": parent_url, "lastFoundURLs.url": child_url},
            {"$set": update_fields}
        )
