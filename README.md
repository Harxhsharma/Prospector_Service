# CompanyParser

CompanyParser is a Python pipeline for discovering, extracting, and ranking Japan luxury-travel venues for an ebook-style dataset.

It is built around three jobs:

1. Discover venue URLs from a parent or listing page.
2. Extract one structured venue record from each venue page.
3. Store those records and optionally rank the best records for a given venue type.

The current implementation is API-first and uses Playwright for page rendering, Hugging Face inference for LLM extraction and ranking, MongoDB for the main crawl state and record store, and local JSON/CSV files for export-oriented workflows.

## What The Project Does

The project is tuned for a Japan luxury-travel content workflow with an editorial distinction between famous venues and insider venues.

Supported venue groups:

- `hotel` / `hotels`
- `dining`
- `cultural`
- `nightlife`

The data model is designed around:

- venue identity and names
- city and neighborhood context
- pricing and reservation friction
- review and awards signals
- editorial tagging such as `famous`, `insider`, `both`, or `ambiguous`
- provenance, including the original source URL and fetch timestamps

## Project Structure

```text
companyparser/
  api.py                 FastAPI entry points
  llm_extract.py         LLM extraction for URLs and venue records
  llm_top.py             LLM ranking of top-N records from Mongo
  config/settings.py     Global directories, categories, Mongo settings
  models/record.py       Canonical Pydantic models
  pipeline/clean.py      Field cleanup after extraction
  sources/               Fetch connectors, currently Playwright-based
  storage/json_store.py  JSON and CSV export helpers
  storage/mongo_store.py MongoDB state and record persistence
data/
  raw/                   Raw payload exports when save_raw is used
  cache/                 Reserved cache directory
  processed/             JSON and CSV outputs
```

## Main Flows

### Flow 1: Parent URL To Venue URLs

This flow is exposed by `POST /urls` in [companyparser/api.py](/home/sharhar/GitWorkspace/CompanyParser/companyparser/api.py).

Steps:

1. The API receives a parent URL and a venue type.
2. The URL is registered in MongoDB inside the `websites` database.
3. The page is fetched with the Playwright source connector.
4. The rendered HTML is sent to the URL extractor in [companyparser/llm_extract.py](/home/sharhar/GitWorkspace/CompanyParser/companyparser/llm_extract.py).
5. The extractor returns a list of venue URLs.
6. MongoDB is updated with:
   - `lastCrawlTime`
   - `lastCrawlStatus`
   - `lastFoundURLs`
   - optionally `raw_response`

If `HF_TOKEN` is not set, URL extraction falls back to same-domain `<a href>` discovery using BeautifulSoup.

### Flow 2: Venue URLs To Structured Records

This flow is exposed by `POST /run` in [companyparser/api.py](/home/sharhar/GitWorkspace/CompanyParser/companyparser/api.py).

Steps:

1. The API looks up the parent URL in the Mongo `websites` database.
2. It reads the child URLs from `lastFoundURLs`.
3. Each child URL is fetched with Playwright.
4. The rendered HTML is sent to `extract_record(...)` in [companyparser/llm_extract.py](/home/sharhar/GitWorkspace/CompanyParser/companyparser/llm_extract.py).
5. The resulting `Record` is cleaned in [companyparser/pipeline/clean.py](/home/sharhar/GitWorkspace/CompanyParser/companyparser/pipeline/clean.py).
6. The record is upserted into the Mongo `Records` database, keyed by `source_url`.

Each saved Mongo record also stores the full page HTML in `raw_response`.

If `HF_TOKEN` is not set, the extractor returns a minimal fallback record with only a basic name from the page title when available.

### Flow 3: Rank Top-N Records

This flow is exposed by `POST /top` in [companyparser/api.py](/home/sharhar/GitWorkspace/CompanyParser/companyparser/api.py).

Steps:

1. Pull a pool of records from the Mongo `Records` database for one venue type.
2. Ask the ranking model to choose the top `N` items.
3. Return ranked results with:
   - `source_url`
   - `rank`
   - `reason`
   - full `record` payload attached

If `HF_TOKEN` is not set, ranking falls back to a heuristic sort using:

1. `insider_score`
2. `famous_score`
3. `tabelog_score`
4. description length

### Flow 4: Local JSON And CSV Review

There is also a file-based export path in [companyparser/storage/json_store.py](/home/sharhar/GitWorkspace/CompanyParser/companyparser/storage/json_store.py).

Important detail:

- `POST /run` writes extracted records to MongoDB, not to `data/processed/records.json`.
- `POST /export-csv` and `GET /stale` read from local JSON files in `data/processed/`.

That means the JSON/CSV workflow is separate from the main Mongo crawl workflow unless another script or integration writes records with `save_records(...)`.

## Data Stores

### MongoDB Databases

Configured in [companyparser/config/settings.py](/home/sharhar/GitWorkspace/CompanyParser/companyparser/config/settings.py):

- `MONGO_DB`, default: `websites`
- `MONGO_RECORDS_DB`, default: `Records`

### Collections By Venue Type

Defined in [companyparser/storage/mongo_store.py](/home/sharhar/GitWorkspace/CompanyParser/companyparser/storage/mongo_store.py):

- `hotel` / `hotels` -> `Hotels`
- `dining` -> `Dining`
- `cultural` -> `Cultural & Unique Experiences`
- `nightlife` -> `NightLife & Bars`

### Website Tracking Document Shape

Parent URLs are tracked in the `websites` database with documents shaped like:

```json
{
  "website": "https://example.com/listing-page",
  "venue_type": "hotel",
  "lastCrawlTime": null,
  "lastCrawlStatus": null,
  "lastFoundURLs": [],
  "createdAt": "2026-05-01T00:00:00+00:00"
}
```

After crawling a parent page, fields such as `raw_response` may also be stored.

### Venue Record Storage

Venue records are stored in the `Records` database and upserted by `source_url`.

Heavy fields are stripped when records are fetched for top-N ranking:

- `_id`
- `raw_response`

### Local File Outputs

Configured directories:

- `data/raw/`
- `data/cache/`
- `data/processed/`

Used by [companyparser/config/settings.py](/home/sharhar/GitWorkspace/CompanyParser/companyparser/config/settings.py) and [companyparser/storage/json_store.py](/home/sharhar/GitWorkspace/CompanyParser/companyparser/storage/json_store.py).

Typical outputs:

- `data/processed/records.json`
- `data/processed/records.csv`

## Record Schema

The canonical schema is defined in [companyparser/models/record.py](/home/sharhar/GitWorkspace/CompanyParser/companyparser/models/record.py).

### Core Identity Fields

- `id`: stable hash-based identifier
- `category`
- `subcategory`
- `tier`: legacy `public` or `insider`
- `name`
- `name_en`
- `name_jp`
- `name_local`
- `description`

### Location Fields

- `city`
- `neighborhood`
- `address`
- `address_jp`
- `location` with `lat` and `lng`
- `phone`
- `website`

### Pricing And Access Fields

- `price_tier`
- `price_specific`
- `reservation_method`
- `english_friendly`
- `foreigner_friendly`
- `lead_time`

### Review And Prestige Signals

- `english_review_count`
- `japanese_review_count`
- `tabelog_score`
- `tabelog_award`
- `awards`
- `mentioned_in_jp_media`
- `mentioned_in_en_media`

### Tagging And Editorial Scoring

- `tags`
- `tagging`
- `famous_score`
- `insider_score`
- `tagging_signals`

### Provenance Fields

- `source_url`
- `source_urls`
- `source_name`
- `source_type`
- `fetched_at`
- `last_scraped`
- `notes`
- `extra`

### Supported Editorial Labels

- `tagging`: `famous`, `insider`, `both`, `ambiguous`
- `tier`: legacy `public`, `insider`

The schema is intentionally permissive. Missing values stay `None` or empty lists instead of being fabricated.

## How Extraction Works

### URL Extraction

Implemented in [companyparser/llm_extract.py](/home/sharhar/GitWorkspace/CompanyParser/companyparser/llm_extract.py):

- Input: rendered HTML of a parent or listing page
- Output: JSON array of absolute venue URLs
- Fallback without `HF_TOKEN`: same-domain link extraction with BeautifulSoup

### Record Extraction

Implemented in [companyparser/llm_extract.py](/home/sharhar/GitWorkspace/CompanyParser/companyparser/llm_extract.py):

- Input: rendered HTML of a single venue page
- Output: one structured `Record`
- LLM prompt is constrained to exact schema keys
- invalid LLM output is coerced down to a minimal valid `Record`

### Post-Extraction Cleaning

Implemented in [companyparser/pipeline/clean.py](/home/sharhar/GitWorkspace/CompanyParser/companyparser/pipeline/clean.py):

- normalize whitespace
- clean selected text fields
- lowercase and trim tags

## Source Connector Behavior

The source factory in [companyparser/sources/factory.py](/home/sharhar/GitWorkspace/CompanyParser/companyparser/sources/factory.py) currently builds only one connector: `playwright`.

The connector in [companyparser/sources/playwright_source.py](/home/sharhar/GitWorkspace/CompanyParser/companyparser/sources/playwright_source.py) supports:

- single-page fetches
- sitemap-driven URL discovery
- regex-based link discovery from an index page
- same-domain filtering
- headless Chromium rendering
- browser fingerprint reduction via custom headers and `navigator.webdriver` masking

Each fetch yields a `RawPayload` with:

- `source_name`
- `source_type`
- `source_url`
- `content_type`
- `body`
- `fetched_at`

## API Endpoints

Defined in [companyparser/api.py](/home/sharhar/GitWorkspace/CompanyParser/companyparser/api.py).

### `GET /health`

Returns a basic service health response.

### `POST /urls`

Registers a parent URL, fetches it, extracts venue links, and stores them in Mongo.

Request body:

```json
{
  "url": "https://example.com/hotels",
  "venue_type": "hotel"
}
```

### `POST /run`

Looks up a previously registered parent URL, crawls all `lastFoundURLs`, extracts records, cleans them, and writes them to the Mongo `Records` database.

Request body:

```json
{
  "url": "https://example.com/hotels",
  "venue_type": "hotel"
}
```

### `POST /top`

Ranks the top records for a venue type.

Query parameters:

- `venue_type`
- `count`
- `model`

Example:

```bash
curl -X POST "http://localhost:8000/top?venue_type=hotel&count=10"
```

### `POST /export-csv`

Reads records from `data/processed/<input_file>` and writes a CSV into `data/processed/<output>`.

Request body:

```json
{
  "input_file": "records.json",
  "output": "records.csv",
  "category": null,
  "sort_by": "insider_score"
}
```

### `GET /stale`

Reads local JSON records and returns records older than the freshness window.

Example:

```bash
curl "http://localhost:8000/stale?input_file=records.json&days=90"
```

## Setup

### Requirements

Dependencies are listed in [requirements.txt](/home/sharhar/GitWorkspace/CompanyParser/requirements.txt).

Core packages:

- `fastapi`
- `uvicorn`
- `playwright`
- `beautifulsoup4`
- `lxml`
- `huggingface_hub`
- `pymongo`
- `pydantic`

### Install

```bash
pip install -r requirements.txt
playwright install chromium
```

### Environment Variables

Optional or required depending on the flow:

- `HF_TOKEN`: enables LLM URL extraction, record extraction, and top-N ranking
- `MONGO_URI`: defaults to `mongodb://localhost:27017`
- `MONGO_DB`: defaults to `websites`
- `MONGO_RECORDS_DB`: defaults to `Records`

### Run The API

```bash
uvicorn companyparser.api:app --reload
```

Swagger UI will then be available at:

- `http://localhost:8000/docs`

## End-To-End Example

### 1. Discover venue URLs from a listing page

```bash
curl -X POST "http://localhost:8000/urls" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/hotels",
    "venue_type": "hotel"
  }'
```

### 2. Crawl each discovered venue page and save records

```bash
curl -X POST "http://localhost:8000/run" \
  -H "Content-Type: application/json" \
  -d '{
    "url": "https://example.com/hotels",
    "venue_type": "hotel"
  }'
```

### 3. Ask for the top records

```bash
curl -X POST "http://localhost:8000/top?venue_type=hotel&count=10"
```

## Operational Notes

- `Flow 1` depends on the parent page being fetchable by Playwright.
- `Flow 2` depends on `Flow 1` having already stored `lastFoundURLs`.
- Without `HF_TOKEN`, the system still runs, but extraction and ranking become limited fallback behavior.
- `GET /stale` and `POST /export-csv` work against local JSON files, not MongoDB.
- The configured editorial city scope is currently `Tokyo` and `Kyoto`.

## Current Limitations

- There is no built-in sync from Mongo `Records` to `data/processed/records.json`.
- The API is the main orchestration surface; there is no documented CLI entry point in the current repository.
- The cleaner currently normalizes only a subset of fields.
- Only the Playwright source connector is active.

## Version

Package version in [companyparser/__init__.py](/home/sharhar/GitWorkspace/CompanyParser/companyparser/__init__.py): `0.1.0`