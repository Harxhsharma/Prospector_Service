"""LLM-based structured data extraction for venue pages.

Uses the Anthropic Claude API to extract venue records from rendered HTML,
and BeautifulSoup for URL extraction from listing/parent pages.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
from typing import Any, Optional
from urllib.parse import urljoin, urlparse

import anthropic
from bs4 import BeautifulSoup

from .config import settings
from .models import Record
from .storage.mongo_store import get_records_collection, save_llm_prompt

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Anthropic client (lazy-initialised)
# ---------------------------------------------------------------------------

_client: Optional[anthropic.Anthropic] = None


def _get_client() -> anthropic.Anthropic:
    """Return a cached Anthropic client, creating it on first call."""
    global _client
    if _client is None:
        api_key = settings.ANTHROPIC_API_KEY
        if not api_key:
            raise ExtractorError(
                "ANTHROPIC_API_KEY is not set. "
                "Set it in your environment or .env file."
            )
        _client = anthropic.Anthropic(api_key=api_key)
    return _client


# ---------------------------------------------------------------------------
# Junk-page detection
# ---------------------------------------------------------------------------

_TEXT_LIMIT = 100

_JUNK_TITLE_PATTERN = re.compile(
    r"^(トップ|forbidden|coming soon|404|エラー|Not Found"
    r"|Page Not Found|アクセスできません"
    r"|お探しのページは見つかりませんでした)",
    re.IGNORECASE,
)


def is_junk_page(html: str) -> bool:
    """Return True if the page looks like an error / empty / irrelevant page."""
    title = _page_title(html)
    if title and _JUNK_TITLE_PATTERN.search(title):
        return True
    # Strip HTML tags and check remaining text length
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return len(text) < _TEXT_LIMIT


# ---------------------------------------------------------------------------
# Fields the LLM is asked to fill
# ---------------------------------------------------------------------------

# Anything else on Record is derived (id, source_*, fetched_at) or left at
# the model default.
_LLM_FIELDS: tuple[str, ...] = (
    "name",
    "name_en",
    "is_venue",
    "confidence",
    "name_jp",
    "description",
    "subcategory",
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
    "awards",
    "tags",
    # editorial tagging — LLM decides Famous vs Insider
    "tagging",
    "famous_score",
    "insider_score",
    "tagging_signals",
)


class ExtractorError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# Category-specific subcategory options (draft — client reviews May 15)
# ---------------------------------------------------------------------------

_SUBCATEGORIES: dict[str, str] = {
    "hotel": """\
1. Traditional
   - Ryokan (traditional inn with tatami rooms, futon, yukata, kaiseki meals)
   - Minshuku (family-run Japanese B&B, simpler than a ryokan)
   - Shukubo (temple or monastery lodging, often includes meditation or vegetarian meals)

2. Western-style
   - City hotel (full-service, urban, international amenities)
   - Business hotel (compact, affordable, focused on work travelers)
   - Resort hotel (leisure-focused, scenic locations, pools and spas)

3. Budget / Unique
   - Capsule hotel (pod-style sleeping units, very low cost, often gender-separated floors)
   - Manga cafe / Net cafe (cubicle overnight stays with internet access and manga)
   - Love hotel (themed rooms, short-stay or overnight, privacy-focused)

4. Luxury / Hybrid
   - Luxury ryokan (high-end traditional inn with private onsen, premium kaiseki, butler service)
   - International chain hotel (Marriott, Hyatt, Hilton, etc. operating in Japan)
   - Boutique / Design hotel (curated aesthetic, architect-designed, unique identity)

5. Other
   - Hostel (dormitory beds, communal spaces, social atmosphere)
   - Onsen hotel (hot spring baths as the central feature, can be Western or Japanese style)
   - Minpaku (licensed home-sharing / Airbnb-style accommodation under Japan's 2018 law)
   - Pension (European-style B&B, common in ski and mountain resort areas)""",

    "dining": """\
Sushi, Ramen, Izakaya, Kaiseki, Tempura, Yakitori, Teppanyaki, Udon/Soba,
Tonkatsu, Unagi, Okonomiyaki, Yakiniku, French, Italian, Fusion, Café,
Bakery, Dessert/Patisserie, Teahouse, Bar dining, Street food, Bento/Takeout""",

    "cultural": """\
Temple, Shrine, Castle, Garden, Museum, Gallery, Onsen/Sento, Tea ceremony,
Calligraphy, Pottery/Ceramics, Kimono rental, Martial arts, Cooking class,
Festival/Matsuri, Theater (Kabuki/Noh/Bunraku), Geisha district, Nature/Hiking,
Sake brewery, Craft workshop""",

    "nightlife": """\
Cocktail bar, Whisky bar, Sake bar, Wine bar, Jazz bar, Live music venue,
Karaoke, Beer hall/Craft beer, Standing bar (Tachinomi), Club/Dance venue,
Rooftop bar, Hotel bar, Speakeasy, Yokocho/Alley bar, Snack bar""",
}


# ---------------------------------------------------------------------------
# Anthropic tool_use schema for structured extraction
# ---------------------------------------------------------------------------

EXTRACT_TOOL: dict[str, Any] = {
    "name": "save_venue_record",
    "description": (
        "Save the extracted venue record. Call this tool with all fields "
        "populated from the page text analysis."
    ),
    "input_schema": {
        "type": "object",
        "required": ["is_venue", "confidence"],
        "properties": {
            "is_venue": {
                "type": "boolean",
                "description": "True if the page describes a specific venue; false for category/listing/error pages.",
            },
            "confidence": {
                "type": "number",
                "description": "Extraction confidence 0-1. Use 0 if not a venue page.",
            },
            "name": {
                "type": "string",
                "description": "Primary display name (English or romanized).",
            },
            "name_en": {
                "type": "string",
                "description": "English name if explicitly provided.",
            },
            "name_jp": {
                "type": "string",
                "description": "Japanese name in kanji/kana if shown.",
            },
            "description": {
                "type": "string",
                "description": "Brief 1-2 sentence description based on page content.",
            },
            "subcategory": {
                "type": "string",
                "description": "MUST be from the provided subcategory list. Null if none fit.",
            },
            "city": {"type": "string", "description": "City name, e.g. 'Tokyo', 'Kyoto'."},
            "neighborhood": {"type": "string", "description": "District/neighborhood name."},
            "address": {"type": "string", "description": "Full address in English/romanized form."},
            "address_jp": {"type": "string", "description": "Full address in Japanese characters."},
            "phone": {"type": "string", "description": "Phone number as shown on page."},
            "website": {"type": "string", "description": "Official website URL."},
            "price_tier": {
                "type": "string",
                "enum": ["¥", "¥¥", "¥¥¥", "¥¥¥¥"],
                "description": "Price tier: ¥=under ¥2000, ¥¥=¥2000-5000, ¥¥¥=¥5000-15000, ¥¥¥¥=over ¥15000.",
            },
            "price_specific": {
                "type": "string",
                "description": "Specific price info, e.g. '¥8,000 per person'.",
            },
            "reservation_method": {
                "type": "string",
                "enum": [
                    "walk_in", "online_en", "online_jp", "phone_only",
                    "concierge_only", "members_only", "recommendation_only",
                ],
                "description": "How reservations are made.",
            },
            "english_friendly": {
                "type": "string",
                "enum": ["yes", "limited", "no"],
                "description": "English accessibility level.",
            },
            "foreigner_friendly": {
                "type": "string",
                "enum": ["yes", "limited", "no"],
                "description": "Tourist-friendliness level.",
            },
            "lead_time": {
                "type": "string",
                "description": "How far in advance reservations needed, e.g. '2 months'.",
            },
            "awards": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Awards/recognitions, e.g. ['Michelin 1 star']. Empty array if none.",
            },
            "tags": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Descriptive tags from page content. Empty array if none.",
            },
            "tagging": {
                "type": "string",
                "enum": ["famous", "insider", "both", "ambiguous"],
                "description": "Editorial bucket based on score rules.",
            },
            "famous_score": {
                "type": "integer",
                "description": "0-6 indicating international/mainstream fame.",
            },
            "insider_score": {
                "type": "integer",
                "description": "0-6 indicating local/connoisseur recognition.",
            },
            "tagging_signals": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Evidence phrases for scores. Empty array if none.",
            },
        },
    },
}



def _stable_id(url: str) -> str:
    return hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]


def _get_scoped_elements(soup, selector: str):
    """Helper to find scoped elements using class names or CSS selectors."""
    try:
        # Try simple class-based lookup first (e.g., 'js-rstlist-info')
        if selector.startswith('.') and ' ' not in selector:
            class_name = selector.lstrip('.')
            element = soup.find('div', class_=class_name)
            return [element] if element else []
        # Fall back to CSS selector
        return soup.select(selector)
    except Exception:
        return []


def _strip_html(html: str, max_chars: int = 12000, selector: Optional[str] = None) -> str:
    """Crude tag-stripper so we keep the LLM context window small.

    If selector is provided, extracts text only from that scope.
    """
    try:
        soup = BeautifulSoup(html, "lxml")

        # If selector provided, scope to matched elements
        if selector:
            scoped_elements = _get_scoped_elements(soup, selector)
            if not scoped_elements:
                return ""
            # Combine all matched elements
            soup = BeautifulSoup("\n".join(str(el) for el in scoped_elements), "lxml")

        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        text = soup.get_text(" ", strip=True)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
    return text[:max_chars]


def _page_title(html: str) -> Optional[str]:
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    return re.sub(r"\s+", " ", m.group(1)).strip() or None


# ---------------------------------------------------------------------------
# LLM prompt for record extraction
# ---------------------------------------------------------------------------

def _get_system_prompt(category: str) -> str:
    """Return the static system prompt for a given venue category.

    Placed in the ``system`` parameter of the Anthropic API call so it is
    automatically cached across requests (~30 % input-token savings).
    """
    subcategory_list = _SUBCATEGORIES.get(category, _SUBCATEGORIES["hotel"])
    return (f"""
You are extracting structured venue data from a webpage for a Japan luxury-travel ebook. Your task is to extract information about a single venue.
You MUST use the `save_venue_record` tool to output the data.

For the subcategory field, you MUST select from this predefined list. Do NOT create new subcategory values:

<subcategory_options>
{subcategory_list}
</subcategory_options>

FIELD DEFINITIONS AND RULES:

**Basic Identification:**
- is_venue: true if the page describes a specific hotel/restaurant/bar/cafe/experience venue; false if it's a category page, 404, login page, generic article, listing page, blog post, or other non-venue page
- confidence: a number between 0 and 1 indicating extraction confidence. Use 0 if not a venue page, 1 if very confident. This helps filter out junk pages
- name: the venue's primary display name (use the most prominent English or romanized name shown)
- name_en: English name if explicitly provided, else null
- name_jp: Japanese name in kanji/kana if shown, else null
- description: a brief 1-2 sentence description of the venue based on page content, or null if insufficient information
- subcategory: MUST be selected from the subcategory_options list provided above. Choose the single most specific match. If none fit, use null. Do NOT create new values.

**Location:**
- city: city name (e.g., "Tokyo", "Kyoto", "Gifu")
- neighborhood: neighborhood/district name if mentioned (e.g., "Shibuya", "Gion")
- address: full address in English/romanized form
- address_jp: full address in Japanese characters if provided, else null

**Contact & Booking:**
- phone: phone number in any format shown on page, or null
- website: official website URL if provided, else null
- reservation_method: one of: walk_in | online_en | online_jp | phone_only | concierge_only | members_only | recommendation_only | null (choose based on reservation information on page)
- lead_time: how far in advance reservations are needed (e.g., "1 week", "2 months"), or null

**Pricing:**
- price_tier: one of '¥', '¥¥', '¥¥¥', '¥¥¥¥' or null. Infer from price ranges: ¥ = under ¥2000, ¥¥ = ¥2000-5000, ¥¥¥ = ¥5000-15000, ¥¥¥¥ = over ¥15000
- price_specific: specific price information mentioned (e.g., "¥8,000 per person", "lunch ¥1,500"), or null

**Accessibility:**
- english_friendly: yes | limited | no | null (based on English menu, English-speaking staff mentions)
- foreigner_friendly: yes | limited | no | null (based on tourist-friendliness signals, foreign customer mentions)

**Quality Signals:**
- tabelog_score: the numeric Tabelog rating if shown (e.g., 3.18), or null
- awards: array of awards/recognitions (e.g., ["Michelin 1 star", "Tabelog Gold"]), empty array [] if none
- tags: array of descriptive tags from page content (e.g., ["stylish", "family-friendly", "breakfast"]), empty array [] if none

**Scoring (CRITICAL - Read Carefully):**

Before assigning scores, use the scratchpad below to think through the evidence.

- famous_score: integer 0-6 indicating how well-known the venue is to international/mainstream travelers
  
  **What makes a venue FAMOUS (high famous_score):**
  * International hotel/restaurant chains (Hyatt, Marriott, Hilton, Park Hyatt, Mandarin Oriental)
  * Michelin stars (especially 2-3 stars)
  * Featured in international media (Condé Nast Traveler, Travel + Leisure, NYT Travel)
  * High volume of English-language reviews (hundreds on TripAdvisor/Google)
  * Celebrity chef or internationally recognized brand
  * Iconic tourist destinations everyone knows (Tokyo Tower, Fushimi Inari)
  * Appears in major English guidebooks (Lonely Planet, Fodor's)
  
  **What does NOT make a venue famous:**
  * Being on a booking aggregator site (rlx.jp, Veltra, Viator)
  * Having a website or taking online reservations
  * Being expensive or luxury-positioned
  * Generic tour operators or bus tours
  * Local chains unknown outside Japan
  
  **Scoring guide:**
  * 0 = no fame signals at all
  * 1-2 = minimal fame (mentioned in one English blog, small number of foreign reviews, generic tour operator)
  * 3-4 = moderate fame (featured in major international guides, 100+ English reviews, recognized brand in travel circles, 1 Michelin star)
  * 5-6 = very famous (2-3 Michelin stars, international hotel brand, iconic landmark, celebrity chef, appears in every major guidebook)
  * Use null ONLY if page provides absolutely no signal

- insider_score: integer 0-6 indicating how known the venue is to Japan locals/industry professionals/connoisseurs
  
  **What makes a venue INSIDER (high insider_score):**
  * Featured in Japanese gourmet/lifestyle magazines (Brutus, Pen, Dancyu, Hanako)
  * High Tabelog score (3.5+) with many Japanese reviews
  * Described as "hidden gem," "locals only," "known to connoisseurs"
  * Requires Japanese language to book (phone only, Japanese website only)
  * No English menu or English-speaking staff
  * Featured in Japanese TV shows or by Japanese food critics
  * Small, independent, family-run establishments with cult following
  * Mentioned as difficult to get reservations among locals
  * Regional specialties known to Japanese food enthusiasts
  * Traditional crafts/experiences valued by Japanese culture enthusiasts
  
  **What does NOT make a venue insider:**
  * Being on an English booking site
  * Having English-friendly service
  * Being a tourist attraction
  * Generic commercial tours marketed to tourists
  
  **Scoring guide:**
  * 0 = no insider signals at all
  * 1-2 = minimal insider appeal (standard commercial venue, tourist-focused, widely accessible to foreigners)
  * 3-4 = moderate insider appeal (local favorite, featured in Japanese media, respected by locals, high Tabelog score, some barriers to foreign access)
  * 5-6 = strong insider appeal (hidden gem, industry secret, featured in Japanese connoisseur publications, Japanese-only access, cult following among locals)
  * Use null ONLY if page provides absolutely no signal

- tagging: Assign based on the scores using EXACTLY these rules:
  * 'famous': famous_score ≥ 3 AND insider_score ≤ 2
  * 'insider': insider_score ≥ 3 AND famous_score ≤ 2
  * 'both': BOTH famous_score ≥ 3 AND insider_score ≥ 3
  * 'ambiguous': BOTH famous_score < 3 AND insider_score < 3
  * null: only if page provides no meaningful signal for either score (both scores are null)

- tagging_signals: array of short phrases citing evidence for the scores (e.g., ["Michelin 2 star", "340 Tabelog reviews", "featured in Brutus magazine", "locals only", "no English menu", "Park Hyatt brand", "500+ TripAdvisor reviews"]), empty array [] if none

**General Rules:**
- Use null for any field where the page does not provide clear information
- Do NOT guess or infer information not present on the page
- Do NOT make up data
- For arrays (awards, tags, tagging_signals), use empty array [] if there are none, not null
- Extract information only from the provided page text
- For subcategory, you MUST choose from the provided list - do not invent new categories

Call the save_venue_record tool with your findings."""
    )


def _parse_json_object(text: str) -> dict[str, Any]:
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        raise ExtractorError("LLM did not return JSON. Got: %s" % text[:200])
    try:
        return json.loads(match.group(0))
    except json.JSONDecodeError as e:
        raise ExtractorError("Bad LLM JSON: %s" % e) from e


def _coerce_to_record(
    data: dict[str, Any],
    *,
    url: str,
    category: str,
    source_name: str,
) -> Record:
    """Build a :class:`Record`, dropping fields Pydantic would reject."""
    payload: dict[str, Any] = {
        k: data.get(k)
        for k in _LLM_FIELDS
        if data.get(k) is not None and k not in ("is_venue", "confidence")
    }
    payload.setdefault("name", _page_title(url) or url)
    payload.update(
        id=_stable_id(url),
        category=category,
        source_url=url,
        source_name=source_name,
        source_type="playwright",
    )
    try:
        return Record(**payload)
    except Exception:
        # Strip any fields that failed validation and retry with required-only.
        minimal = {
            "id": payload["id"],
            "category": category,
            "name": payload.get("name") or url,
            "source_url": url,
            "source_name": source_name,
            "source_type": "playwright",
            "notes": "LLM payload failed validation: %r" % payload,
        }
        return Record(**minimal)


# ---------------------------------------------------------------------------
# Public API: extract_record
# ---------------------------------------------------------------------------

def extract_record(
    html: str,
    *,
    url: str,
    category: str,
    source_name: str = "playwright",
) -> Optional[Record]:
    """Extract a :class:`Record` from rendered HTML using the Anthropic LLM.

    Returns ``None`` for junk / non-venue pages.
    """
    if is_junk_page(html):
        return None

    text = _strip_html(html)
    # 1. Pre-flight Deduplication Check
    try:
        coll = get_records_collection(category)
        existing = coll.find_one({"source_url": url})
        if existing:
            logger.info("Deduplication: Skipping extraction, record exists for %s", url)
            return _coerce_to_record(existing, url=url, category=category, source_name=source_name)
    except Exception as e:
        logger.warning("Failed pre-flight duplicate check: %s", e)

    # 2. Extract using tool
    user_prompt = f"<page_text>\n{text}\n</page_text>"
    system_prompt = _get_system_prompt(category)

    try:
        response = _get_client().messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
            tools=[EXTRACT_TOOL],
            tool_choice={"type": "tool", "name": "save_venue_record"},
        )
    except Exception as e:
        # Save the failed prompt for debugging
        save_llm_prompt(
            url, user_prompt,
            prompt_type="extraction",
            category=category,
            response="ERROR: %s" % e,
        )
        raise ExtractorError("Claude API inference failed: %s" % e) from e

    tool_calls = [c for c in response.content if getattr(c, "type", None) == "tool_use"]
    if not tool_calls:
        raise ExtractorError("Claude did not call the save_venue_record tool.")
    
    data = tool_calls[0].input

    # Save the successful prompt + response for audit
    save_llm_prompt(
        url, user_prompt,
        prompt_type="extraction",
        category=category,
        response=json.dumps(data),
    )

    if not data.get("is_venue", True) or (
        data.get("confidence") is not None and data.get("confidence", 0) < 0.5
    ):
        # Treat low-confidence or non-venue extractions as junk.
        return None

    return _coerce_to_record(data, url=url, category=category, source_name=source_name)


# ---------------------------------------------------------------------------
# Public API: extract_urls (pure HTML, no LLM)
# ---------------------------------------------------------------------------

# Anchor-text patterns that indicate navigation / non-venue links.
# Matched case-insensitively against the visible text of each <a> tag.
_NAV_LINK_PATTERN = re.compile(
    r"^("
    # --- English navigation ---
    r"home|top|back|menu|sitemap|search|login|log ?in|sign ?in|sign ?up|register"
    r"|contact|about|about us|help|faq|support|feedback"
    r"|privacy|privacy policy|terms|terms of service|terms of use|cookie policy|disclaimer"
    r"|cart|checkout|my ?account|my ?page|profile|settings|dashboard"
    r"|subscribe|newsletter|unsubscribe"
    r"|next|prev|previous|more|see more|show more|load more|view all|read more"
    r"|share|tweet|follow|like"
    r"|copyright|all rights reserved"
    # --- Japanese navigation ---
    r"|ホーム|トップ|トップページ|戻る|メニュー"
    r"|ログイン|ログアウト|新規登録|会員登録|マイページ"
    r"|お問い合わせ|お問合せ|問い合わせ|連絡|サポート"
    r"|プライバシー|プライバシーポリシー|利用規約|個人情報|免責事項"
    r"|サイトマップ|ヘルプ|よくある質問"
    r"|検索|探す|カート|会計"
    r"|もっと見る|一覧|次へ|前へ|詳しくはこちら"
    r"|採用情報|会社概要|企業情報|運営会社|特定商取引法"
    r"|ページトップ|ページの先頭"
    r")$",
    re.IGNORECASE,
)

# URL path segments that typically indicate non-venue pages.
_NAV_PATH_PATTERN = re.compile(
    r"/(login|signin|signup|register|cart|checkout|account|search|contact"
    r"|about|privacy|terms|faq|help|sitemap|feed|rss|api|admin|wp-admin"
    r"|tag/|tags/|category/|author/|page/\d+|#)",
    re.IGNORECASE,
)


def _is_nav_link(anchor_text: str, href_path: str) -> bool:
    """Return True if a link looks like navigation rather than a venue link."""
    text = anchor_text.strip()

    # Very short text is usually an icon/arrow link
    if len(text) <= 1:
        return True

    # Check anchor text against known nav patterns
    if _NAV_LINK_PATTERN.match(text):
        return True

    # Check URL path for non-venue segments
    if _NAV_PATH_PATTERN.search(href_path):
        return True

    return False


def extract_urls(
    html: str,
    *,
    url: str,
) -> list[str]:
    """Extract venue URLs from a parent/listing page using href extraction.

    Extracts all same-domain links from <a href> attributes using BeautifulSoup.
    Filters out navigation, footer, and other non-venue links using anchor text
    and URL path heuristics. No LLM is used — this is pure HTML parsing.
    """
    try:
        soup = BeautifulSoup(html, "html.parser")
        base_host = urlparse(url).netloc
        seen: set[str] = set()
        urls: list[str] = []

        for a in soup.find_all("a", href=True):
            href = urljoin(url, a["href"])
            parsed = urlparse(href)
            if parsed.netloc != base_host:
                continue
            clean_url = f"{parsed.scheme}://{parsed.netloc}{parsed.path}"
            if clean_url in seen or clean_url == url:
                continue

            # Filter out nav / non-venue links by anchor text + path
            anchor_text = a.get_text(separator=" ", strip=True)
            if _is_nav_link(anchor_text, parsed.path):
                continue

            seen.add(clean_url)
            urls.append(clean_url)
        return urls
    except Exception:
        return []
