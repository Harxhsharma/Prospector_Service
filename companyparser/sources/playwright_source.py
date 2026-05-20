"""Playwright connector for JS-rendered / dynamic sites.

Renders `base_url` with headless Chromium and yields a single RawPayload.
"""
from __future__ import annotations


import logging
import random
import time
import traceback
from datetime import datetime, timezone
from typing import Iterable, Optional

from playwright.sync_api import sync_playwright

from ..config import REQUEST_TIMEOUT
from ..models import RawPayload, Record
from .base import BaseSource


class PlaywrightSource(BaseSource):
    logger = logging.getLogger("companyparser.playwright_source")
    source_type = "playwright"

    def __init__(
        self,
        name: str,
        category: str,
        base_url: str,
        tier: str = "public",
        headless: bool = True,
        wait_until: str = "domcontentloaded",
        wait_for_selector: Optional[str] = None,
        tag_inside_which_to_extract: Optional[str] = None,
        **_kwargs,
    ) -> None:
        super().__init__(name=name, category=category, tier=tier)
        self.base_url = base_url
        self.headless = headless
        self.wait_for_selector = wait_for_selector
        self.tag_inside_which_to_extract = tag_inside_which_to_extract
        allowed_waits = {"commit", "domcontentloaded", "load", "networkidle"}
        # Default to 'load' for better reliability on dynamic sites
        if wait_until not in allowed_waits:
            self.logger.warning("wait_until '%s' is not valid, defaulting to 'load'", wait_until)
            self.wait_until = "load"
        else:
            self.wait_until = wait_until or "load"
        self.logger.debug("Initialized PlaywrightSource for %s (category=%s, headless=%s, wait_until=%s)", base_url, category, headless, self.wait_until)

    # ---------- fetch & parse ----------

    # Realistic Chrome-on-macOS UA + sec-ch-* / accept headers help dodge
    # naive bot blocks (Cloudflare, Akamai, etc.). Override via __init__ if
    # the target site fingerprints differently.
    USER_AGENTS = [
        # Chrome on Mac
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        # Chrome on Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        # Firefox on Mac
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7; rv:126.0) Gecko/20100101 Firefox/126.0",
        # Edge on Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36 Edg/126.0.0.0",
    ]
    HEADER_TEMPLATES = [
        {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Sec-Ch-Ua": '"Chromium";v="126", "Google Chrome";v="126", "Not.A/Brand";v="24"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"macOS"',
        },
        {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9,ja;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Sec-Ch-Ua": '"Not.A/Brand";v="24", "Chromium";v="126", "Google Chrome";v="126"',
            "Sec-Ch-Ua-Mobile": "?0",
            "Sec-Ch-Ua-Platform": '"Windows"',
        },
    ]

    def fetch(self) -> Iterable[RawPayload]:
        self.logger.info("Starting fetch for %s", self.base_url)

        max_retries = 3
        base_timeout = max(REQUEST_TIMEOUT, 120)  # at least 60s
        for attempt in range(1, max_retries + 1):
            ua = random.choice(self.USER_AGENTS)
            headers = random.choice(self.HEADER_TEMPLATES)
            self.logger.info("Attempt %d/%d for %s with UA: %s", attempt, max_retries, self.base_url, ua)
            with sync_playwright() as p:
                self.logger.debug("Launching Chromium browser")
                browser = p.chromium.launch(
                    headless=self.headless,
                    slow_mo=100,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--disable-features=IsolateOrigins,site-per-process",
                        
                    ],
                )
                try:
                    context = browser.new_context(
                        user_agent=ua,
                        locale="en-US",
                        timezone_id="Asia/Tokyo",
                        viewport={"width": 1440, "height": 900},
                        extra_http_headers=headers,
                    )
                    # Hide the obvious `navigator.webdriver=true` tell.
                    context.add_init_script(
                        "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                    )
                    page = context.new_page()

                    # Collect page errors and console messages
                    page_errors = []
                    def on_page_error(msg):
                        page_errors.append(f"PAGE ERROR: {msg}")
                    def on_console(msg):
                        page_errors.append(f"CONSOLE: {msg.type}: {msg.text}")
                    page.on("pageerror", on_page_error)
                    page.on("console", on_console)

                    try:
                        self.logger.info("Navigating to %s (timeout=%ds)", self.base_url, base_timeout)
                        page.goto(
                            self.base_url,
                            wait_until=self.wait_until,
                            timeout=base_timeout * 1000,
                        )
                        # Wait for the main content to appear (improves reliability for dynamic sites)
                        try:
                            selector = self.wait_for_selector or "body"
                            page.wait_for_selector(selector, timeout=15000)
                        except Exception as e:
                            self.logger.warning("wait_for_selector('%s') failed: %s", selector, e)
                        if self.tag_inside_which_to_extract:
                            self.logger.info("Extracting content inside selector: %s", self.tag_inside_which_to_extract)
                            try:
                                element = page.query_selector(self.tag_inside_which_to_extract)
                                if element:
                                    html = element.inner_html()
                                else:
                                    self.logger.warning("Selector %s not found, falling back to full page content", self.tag_inside_which_to_extract)
                                    html = page.content()
                            except Exception as e:
                                self.logger.warning("Error extracting with selector %s: %s, falling back to full page content", self.tag_inside_which_to_extract, e)
                        else:
                            html = page.content()
                        self.logger.info("Fetched content from %s (length=%d)", self.base_url, len(html))
                        yield RawPayload(
                            source_name=self.name,
                            source_type=self.source_type,
                            source_url=self.base_url,
                            content_type="text/html",
                            body=html,
                            fetched_at=datetime.now(timezone.utc),
                        )
                        return
                    except Exception as e:

                        tb = traceback.format_exc()
                        error_info = f"Playwright navigation error: {e}\nTraceback:\n{tb}\nPage errors: {page_errors}"
                        self.logger.error("Failed to fetch %s on attempt %d: %s", self.base_url, attempt, error_info)
                        if attempt == max_retries:
                            yield RawPayload(
                                source_name=self.name,
                                source_type=self.source_type,
                                source_url=self.base_url,
                                content_type="text/plain",
                                body=error_info,
                                fetched_at=datetime.now(timezone.utc),
                            )
                            return
                        else:
                            # Randomized backoff before retry
                            delay = random.uniform(2, 5)
                            self.logger.info("Retrying after %.1fs...", delay)
                            time.sleep(delay)
                finally:
                    self.logger.debug("Closing browser")
                    browser.close()

    def parse(self, payload: RawPayload) -> Iterable[Record]:
        # Structured extraction is handled by companyparser.llm_extract.
        self.logger.debug("Parse called for payload from %s", payload.source_url)
        return
        yield  # pragma: no cover  # makes this a generator
