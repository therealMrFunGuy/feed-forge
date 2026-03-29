"""Feed generation: RSS, Atom, and JSON Feed formats."""

from datetime import datetime, timezone
from typing import Optional

from feedgen.feed import FeedGenerator

from db import get_feed, get_items


def _build_feedgen(feed_id: str, base_url: str) -> tuple[Optional[FeedGenerator], Optional[dict]]:
    """Build a FeedGenerator instance populated with feed info and items."""
    feed = get_feed(feed_id)
    if not feed:
        return None, None

    fg = FeedGenerator()
    fg.id(f"{base_url}/feeds/{feed_id}")
    fg.title(feed["name"])
    fg.link(href=feed["url"], rel="alternate")
    fg.link(href=f"{base_url}/feeds/{feed_id}/rss", rel="self")
    fg.description(f"FeedForge monitor for {feed['url']} ({feed['css_selector']})")
    fg.language("en")
    fg.generator("FeedForge")

    items = get_items(feed_id, limit=50)
    for item in reversed(items):  # oldest first for feed ordering
        fe = fg.add_entry()
        fe.id(f"{base_url}/feeds/{feed_id}/items/{item['id']}")
        fe.title(item["title"])
        fe.content(content=item["content"], type="html")
        if item.get("url"):
            fe.link(href=item["url"])
        else:
            fe.link(href=feed["url"])
        detected = datetime.fromisoformat(item["detected_at"])
        if detected.tzinfo is None:
            detected = detected.replace(tzinfo=timezone.utc)
        fe.published(detected)
        fe.updated(detected)

    return fg, feed


def generate_rss(feed_id: str, base_url: str = "http://localhost:8430") -> Optional[str]:
    """Generate RSS 2.0 XML for a feed."""
    fg, feed = _build_feedgen(feed_id, base_url)
    if not fg:
        return None
    return fg.rss_str(pretty=True).decode("utf-8")


def generate_atom(feed_id: str, base_url: str = "http://localhost:8430") -> Optional[str]:
    """Generate Atom XML for a feed."""
    fg, feed = _build_feedgen(feed_id, base_url)
    if not fg:
        return None
    fg.link(href=f"{base_url}/feeds/{feed_id}/atom", rel="self", type="application/atom+xml")
    return fg.atom_str(pretty=True).decode("utf-8")


def generate_json_feed(feed_id: str, base_url: str = "http://localhost:8430") -> Optional[dict]:
    """Generate JSON Feed 1.1 format for a feed."""
    feed = get_feed(feed_id)
    if not feed:
        return None

    items = get_items(feed_id, limit=50)

    json_items = []
    for item in items:
        json_item = {
            "id": item["id"],
            "title": item["title"],
            "content_html": item["content"],
            "url": item.get("url") or feed["url"],
            "date_published": item["detected_at"],
        }
        if item.get("diff_summary"):
            json_item["summary"] = item["diff_summary"]
        json_items.append(json_item)

    return {
        "version": "https://jsonfeed.org/version/1.1",
        "title": feed["name"],
        "home_page_url": feed["url"],
        "feed_url": f"{base_url}/feeds/{feed_id}/json",
        "description": f"FeedForge monitor for {feed['url']}",
        "items": json_items,
    }
