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
    "why_on_list",
    "signature_dish_or_feature",
    "hype_indicators",
    "subcategory",
    "city",
    "neighborhood",
    "address",
    "price_tier",
    "tier",
    "notes",
)


class ExtractorError(RuntimeError):
    pass


# ---------------------------------------------------------------------------
# The 16 locked subcategories with editorial descriptions for LLM prompting
# ---------------------------------------------------------------------------

_SUBCATEGORIES: dict[str, str] = {
    "hotel": """\
1. ultra_luxury_icons — Aman / Bulgari / Four Seasons / Mandarin tier
2. ryokan — Traditional Japanese inns (Tawaraya, Hiiragiya, Hoshinoya Kyoto)
3. design_boutique — Architecturally led, smaller-key, design-forward (Trunk, K5, Hoshinoya Tokyo)
4. private_exclusive — Member-only, villa-style, residences (Soho House, Janu, private compounds)""",

    "dining": """\
1. kaiseki — Seasonal multi-course art dining
2. sushi — All sushi; tier and price_range carry the distinction
3. omakase — All omakase; tier and price_range carry the distinction""",

    "cultural": """\
1. temples_shrines — Fushimi Inari to private temple tea; tier carries the access split
2. geisha_maiko — Tourist-facing dance shows vs real Gion ozashiki bookings
3. sumo — Tournament tickets vs morning training stable visits
4. traditional_crafts_workshops — Tea, kimono, katana, pottery, washi, tofu; tier carries the master-vs-tourist split
5.   — Connection-only experiences: private temple dinners, after-hours museums, specific geiko""",

    "nightlife": """\
1. luxury_hotel_bars — Aman bar, Andaz rooftop, Bulgari, New York Bar at Park Hyatt
2. cocktail_bars — Speakeasies + craft cocktail (Tender, Bar High Five, Star Bar, Benfiddich, unmarked Ginza basements)
3. local_chaos — Golden Gai, Omoide Yokocho, late-night izakaya streets
4. late_night_fine_dining — Post-midnight kitchens, members' dining clubs that operate as nightlife""",
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
            "why_on_list": {
                "type": "string",
                "description": "1-2 sentence editorial justification for why this venue belongs in a luxury Japan travel ebook.",
            },
            "signature_dish_or_feature": {
                "type": "string",
                "description": "The standout offering or key attraction, e.g. '20-course omakase', 'private onsen suite'.",
            },
            "hype_indicators": {
                "type": "string",
                "description": "Consolidated fame/review signals as a pipe-separated string, e.g. 'Tabelog: 4.31 | Michelin: 2★ | TripAdvisor: 1,247 reviews'.",
            },
            "subcategory": {
                "type": "string",
                "enum": [
                    "ultra_luxury_icons", "ryokan", "design_boutique", "private_exclusive",
                    "kaiseki", "sushi", "omakase",
                    "temples_shrines", "geisha_maiko", "sumo", "traditional_crafts_workshops", "access_you_cant_google",
                    "luxury_hotel_bars", "cocktail_bars", "local_chaos", "late_night_fine_dining",
                ],
                "description": "MUST be from the provided subcategory list. Null if none fit.",
            },
            "city": {"type": "string", "description": "City name, e.g. 'Tokyo', 'Kyoto'."},
            "neighborhood": {"type": "string", "description": "District/neighborhood name."},
            "address": {"type": "string", "description": "Full address in English/romanized form."},
            "price_tier": {
                "type": "string",
                "enum": ["¥", "¥¥", "¥¥¥", "¥¥¥¥"],
                "description": "Price tier: ¥=under ¥2000, ¥¥=¥2000-5000, ¥¥¥=¥5000-15000, ¥¥¥¥=over ¥15000.",
            },
            "tier": {
                "type": "string",
                "enum": ["famous", "insider"],
                "description": "'famous' = well-known to international travelers (chains, Michelin, guidebooks, high English reviews). 'insider' = known to locals/connoisseurs (Tabelog favorites, Japanese-only access, hidden gems).",
            },
            "notes": {
                "type": "string",
                "description": "Any additional noteworthy details not captured by other fields.",
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
You are extracting structured venue data from a webpage for a Japan luxury-travel ebook.
You MUST use the `save_venue_record` tool to output the data.

For the subcategory field, you MUST select from this predefined list. Do NOT create new values:

<subcategory_options>
{subcategory_list}
</subcategory_options>

FIELD RULES:

**Control:**
- is_venue: true if the page describes a specific venue; false for category/listing/error/blog pages
- confidence: 0-1. Use 0 if not a venue page

**Identity:**
- name: primary display name (English or romanized)
- name_en: English name if explicitly shown, else null
- name_jp: Japanese name in kanji/kana 
- description: brief 1-2 sentence description from page content, or null
- why_on_list: 1-2 sentence editorial justification for inclusion in a luxury Japan ebook. Focus on what makes it special. Null if insufficient info.
- signature_dish_or_feature: the standout offering (e.g. "20-course omakase", "private onsen suite"). Null if not identifiable.
- hype_indicators: consolidated fame/review signals as pipe-separated string. Combine any Tabelog scores, Michelin stars, TripAdvisor reviews, awards found (e.g. "Tabelog: 4.31 | Michelin: 2★ | TripAdvisor: 1,247 reviews"). Null if none found.
- subcategory: MUST be from the list above. Null if none fit.

**Location:**
- city: e.g. "Tokyo", "Kyoto"
- neighborhood: district name, e.g. "Ginza", "Gion"
- address: full address in English/romanized form

**Pricing:**
- price_tier: ¥ (under ¥2000) | ¥¥ (¥2000-5000) | ¥¥¥ (¥5000-15000) | ¥¥¥¥ (over ¥15000) | null

**Tier (Famous vs Insider):**
- tier: "famous" or "insider"
  * "famous" = well-known internationally: major hotel chains, Michelin stars, featured in Condé Nast / Lonely Planet, high English-language reviews, celebrity chef, iconic landmarks
  * "insider" = known to locals/connoisseurs: high Tabelog scores, Japanese-only booking, hidden gems, featured in Japanese media (Brutus, Dancyu), cult following, hard-to-book among locals
  * If both signals are equally strong, prefer "famous". Use null only if no signal at all.

**Other:**
- notes: any additional noteworthy details not captured above, or null

**General Rules:**
- Use null for any field without clear information on the page
- Do NOT guess, infer, or fabricate data
- You are allowed to convert names to english or japanese if found on the page
- Extract only from the provided page text

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
