"""FeedForge — FastAPI REST API server."""

import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, HttpUrl, Field

from db import (
    init_db,
    create_feed,
    get_feed,
    list_feeds,
    delete_feed,
    count_feeds,
    get_items,
    get_last_two_snapshots,
)
from feed_gen import generate_rss, generate_atom, generate_json_feed
from parser import compute_diff
from scheduler import start_scheduler, stop_scheduler, check_feed

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("feedforge.server")

MAX_FEEDS = int(os.getenv("MAX_FEEDS_FREE", "50"))
DEFAULT_INTERVAL = int(os.getenv("CHECK_INTERVAL_DEFAULT", "15"))
PORT = int(os.getenv("PORT", "8435"))
BASE_URL = os.getenv("BASE_URL", f"http://localhost:{PORT}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    start_scheduler()
    logger.info(f"FeedForge started on port {PORT}")
    yield
    stop_scheduler()


app = FastAPI(
    title="FeedForge",
    description="Turn any website into an RSS/JSON/Atom feed by monitoring for changes.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# --- Request/Response Models ---

class CreateFeedRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    url: str = Field(..., min_length=1)
    css_selector: str = Field(..., min_length=1)
    check_interval_minutes: int = Field(default=DEFAULT_INTERVAL, ge=5, le=1440)
    js_render: bool = False
    webhook_url: Optional[str] = None


class FeedResponse(BaseModel):
    id: str
    name: str
    url: str
    css_selector: str
    interval_min: int
    js_render: int
    webhook_url: Optional[str]
    last_check: Optional[str]
    created_at: str
    active: int


class CheckResult(BaseModel):
    feed_id: str
    changed: bool
    diff: Optional[dict] = None
    error: Optional[str] = None
    new_items: int


# --- Routes ---

@app.get("/")
async def root():
    return {
        "service": "FeedForge",
        "version": "1.0.0",
        "description": "Turn any website into an RSS/JSON/Atom feed",
        "endpoints": {
            "POST /feeds": "Create a new feed monitor",
            "GET /feeds": "List all feeds",
            "GET /feeds/{id}": "Get feed details + recent items",
            "GET /feeds/{id}/rss": "RSS 2.0 XML output",
            "GET /feeds/{id}/atom": "Atom XML output",
            "GET /feeds/{id}/json": "JSON Feed 1.1 output",
            "DELETE /feeds/{id}": "Delete a feed",
            "POST /feeds/{id}/check": "Manually trigger a check",
            "GET /feeds/{id}/diff": "Show changes since last check",
        },
    }


@app.post("/feeds", status_code=201)
async def create_feed_endpoint(req: CreateFeedRequest):
    if count_feeds() >= MAX_FEEDS:
        raise HTTPException(status_code=429, detail=f"Maximum {MAX_FEEDS} feeds allowed")

    feed = create_feed(
        name=req.name,
        url=req.url,
        css_selector=req.css_selector,
        interval_min=req.check_interval_minutes,
        js_render=req.js_render,
        webhook_url=req.webhook_url,
    )

    # Do initial check immediately
    result = await check_feed(feed["id"])

    return {
        "feed": feed,
        "initial_check": result,
        "rss_url": f"{BASE_URL}/feeds/{feed['id']}/rss",
        "atom_url": f"{BASE_URL}/feeds/{feed['id']}/atom",
        "json_url": f"{BASE_URL}/feeds/{feed['id']}/json",
    }


@app.get("/feeds")
async def list_feeds_endpoint():
    feeds = list_feeds()
    return {"feeds": feeds, "total": len(feeds), "max": MAX_FEEDS}


@app.get("/feeds/{feed_id}")
async def get_feed_endpoint(feed_id: str):
    feed = get_feed(feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    items = get_items(feed_id, limit=50)
    return {
        "feed": feed,
        "items": items,
        "item_count": len(items),
        "rss_url": f"{BASE_URL}/feeds/{feed_id}/rss",
        "atom_url": f"{BASE_URL}/feeds/{feed_id}/atom",
        "json_url": f"{BASE_URL}/feeds/{feed_id}/json",
    }


@app.get("/feeds/{feed_id}/rss")
async def get_rss(feed_id: str):
    xml = generate_rss(feed_id, base_url=BASE_URL)
    if not xml:
        raise HTTPException(status_code=404, detail="Feed not found")
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8")


@app.get("/feeds/{feed_id}/atom")
async def get_atom(feed_id: str):
    xml = generate_atom(feed_id, base_url=BASE_URL)
    if not xml:
        raise HTTPException(status_code=404, detail="Feed not found")
    return Response(content=xml, media_type="application/atom+xml; charset=utf-8")


@app.get("/feeds/{feed_id}/json")
async def get_json_feed(feed_id: str):
    data = generate_json_feed(feed_id, base_url=BASE_URL)
    if not data:
        raise HTTPException(status_code=404, detail="Feed not found")
    return data


@app.delete("/feeds/{feed_id}")
async def delete_feed_endpoint(feed_id: str):
    if not delete_feed(feed_id):
        raise HTTPException(status_code=404, detail="Feed not found")
    return {"deleted": True, "feed_id": feed_id}


@app.post("/feeds/{feed_id}/check")
async def check_feed_endpoint(feed_id: str):
    feed = get_feed(feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    result = await check_feed(feed_id)
    return result


@app.get("/feeds/{feed_id}/diff")
async def get_diff(feed_id: str):
    feed = get_feed(feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")

    snapshots = get_last_two_snapshots(feed_id)
    if len(snapshots) < 2:
        return {
            "feed_id": feed_id,
            "has_diff": False,
            "message": "Need at least 2 snapshots to compute a diff",
            "snapshot_count": len(snapshots),
        }

    newer = snapshots[0]
    older = snapshots[1]
    diff = compute_diff(older["content"], newer["content"])

    return {
        "feed_id": feed_id,
        "has_diff": diff["changed"],
        "older_snapshot": older["checked_at"],
        "newer_snapshot": newer["checked_at"],
        "diff": diff,
    }


@app.get("/health")
async def health():
    return {"status": "ok", "service": "feedforge"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("server:app", host="0.0.0.0", port=PORT, reload=False)
