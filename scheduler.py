"""Background scheduler for periodic feed checks."""

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from crawler import fetch_html, content_hash
from parser import extract_content, elements_to_text, compute_diff, generate_item_title, generate_item_content
from db import (
    list_feeds,
    get_latest_snapshot,
    save_snapshot,
    add_item,
    update_feed_last_check,
    get_feed,
)

logger = logging.getLogger("feedforge.scheduler")

_scheduler_task: asyncio.Task | None = None


async def check_feed(feed_id: str) -> dict:
    """Check a single feed for changes.

    Returns a dict with:
        - feed_id: the feed ID
        - changed: bool
        - diff: diff dict if changed, else None
        - error: error message if failed
        - new_items: number of new items created
    """
    feed = get_feed(feed_id)
    if not feed:
        return {"feed_id": feed_id, "changed": False, "error": "Feed not found", "new_items": 0}

    try:
        html = await fetch_html(feed["url"], js_render=bool(feed["js_render"]))
        elements = extract_content(html, feed["css_selector"], base_url=feed["url"])
        current_text = elements_to_text(elements)
        current_hash = content_hash(current_text)

        prev_snapshot = get_latest_snapshot(feed_id)

        # Always save snapshot
        save_snapshot(feed_id, current_hash, current_text)
        update_feed_last_check(feed_id)

        if not prev_snapshot:
            # First check — create initial item
            add_item(
                feed_id=feed_id,
                title=f"{feed['name']}: initial snapshot",
                content=f"<p>Started monitoring. Found {len(elements)} elements matching <code>{feed['css_selector']}</code>.</p>",
                url=feed["url"],
                diff_summary=f"Initial snapshot: {len(elements)} elements",
            )
            return {"feed_id": feed_id, "changed": False, "diff": None, "error": None, "new_items": 0, "elements": len(elements)}

        if current_hash == prev_snapshot["content_hash"]:
            return {"feed_id": feed_id, "changed": False, "diff": None, "error": None, "new_items": 0}

        # Content changed
        diff_result = compute_diff(prev_snapshot["content"], current_text)
        if not diff_result["changed"]:
            return {"feed_id": feed_id, "changed": False, "diff": None, "error": None, "new_items": 0}

        title = generate_item_title(feed["name"], diff_result)
        content = generate_item_content(diff_result, elements)
        add_item(
            feed_id=feed_id,
            title=title,
            content=content,
            url=feed["url"],
            diff_summary=diff_result["summary"],
        )

        # Fire webhook if configured
        if feed.get("webhook_url"):
            await _fire_webhook(feed["webhook_url"], feed_id, feed["name"], diff_result)

        return {
            "feed_id": feed_id,
            "changed": True,
            "diff": diff_result,
            "error": None,
            "new_items": 1,
        }

    except Exception as e:
        logger.exception(f"Error checking feed {feed_id}")
        return {"feed_id": feed_id, "changed": False, "diff": None, "error": str(e), "new_items": 0}


async def _fire_webhook(webhook_url: str, feed_id: str, feed_name: str, diff_result: dict):
    """Send a POST to the webhook URL when changes are detected."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(webhook_url, json={
                "event": "feed_changed",
                "feed_id": feed_id,
                "feed_name": feed_name,
                "summary": diff_result["summary"],
                "added_count": len(diff_result["added"]),
                "removed_count": len(diff_result["removed"]),
            })
    except Exception as e:
        logger.warning(f"Webhook failed for feed {feed_id}: {e}")


async def _scheduler_loop():
    """Main scheduler loop. Checks feeds that are due."""
    logger.info("Scheduler started")
    while True:
        try:
            feeds = list_feeds(active_only=True)
            now = datetime.now(timezone.utc)

            for feed in feeds:
                if feed["last_check"]:
                    last = datetime.fromisoformat(feed["last_check"])
                    if last.tzinfo is None:
                        last = last.replace(tzinfo=timezone.utc)
                    elapsed_min = (now - last).total_seconds() / 60
                    if elapsed_min < feed["interval_min"]:
                        continue

                logger.info(f"Checking feed: {feed['name']} ({feed['id']})")
                result = await check_feed(feed["id"])
                if result.get("changed"):
                    logger.info(f"Feed {feed['name']} changed: {result['diff']['summary']}")
                elif result.get("error"):
                    logger.warning(f"Feed {feed['name']} error: {result['error']}")

        except Exception:
            logger.exception("Scheduler loop error")

        await asyncio.sleep(30)  # Check every 30 seconds which feeds are due


def start_scheduler():
    """Start the scheduler as a background task."""
    global _scheduler_task
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_scheduler_loop())
        logger.info("Scheduler task created")


def stop_scheduler():
    """Stop the scheduler."""
    global _scheduler_task
    if _scheduler_task and not _scheduler_task.done():
        _scheduler_task.cancel()
        _scheduler_task = None
        logger.info("Scheduler stopped")
