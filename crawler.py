"""HTTP crawler with optional Playwright JS rendering."""

import asyncio
import hashlib
import logging
import time
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger("feedforge.crawler")

# Rate limiting: track last request time per domain
_domain_last_request: dict[str, float] = {}
_domain_lock = asyncio.Lock()
RATE_LIMIT_SECONDS = 10


async def _enforce_rate_limit(url: str):
    """Ensure at most 1 request per domain per RATE_LIMIT_SECONDS."""
    domain = urlparse(url).netloc
    async with _domain_lock:
        now = time.monotonic()
        last = _domain_last_request.get(domain, 0)
        wait = RATE_LIMIT_SECONDS - (now - last)
        if wait > 0:
            logger.debug(f"Rate limiting {domain}: waiting {wait:.1f}s")
            await asyncio.sleep(wait)
        _domain_last_request[domain] = time.monotonic()


async def fetch_html(url: str, js_render: bool = False, timeout: float = 30.0) -> str:
    """Fetch HTML content from a URL.

    Args:
        url: The URL to fetch.
        js_render: If True, use Playwright for JS-heavy pages.
        timeout: Request timeout in seconds.

    Returns:
        Raw HTML string.
    """
    await _enforce_rate_limit(url)

    if js_render:
        return await _fetch_with_playwright(url, timeout)
    return await _fetch_with_httpx(url, timeout)


async def _fetch_with_httpx(url: str, timeout: float) -> str:
    headers = {
        "User-Agent": "FeedForge/1.0 (RSS feed generator; +https://github.com/feedforge)",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
    }
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=timeout,
        headers=headers,
    ) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.text


async def _fetch_with_playwright(url: str, timeout: float) -> str:
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.warning("Playwright not installed, falling back to httpx")
        return await _fetch_with_httpx(url, timeout)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        try:
            page = await browser.new_page(
                user_agent="FeedForge/1.0 (RSS feed generator)"
            )
            await page.goto(url, timeout=int(timeout * 1000), wait_until="networkidle")
            html = await page.content()
            return html
        finally:
            await browser.close()


def content_hash(text: str) -> str:
    """SHA-256 hash of content for change detection."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
