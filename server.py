"""FeedForge — FastAPI REST API server."""

import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, HttpUrl, Field

from auth_client import require_auth

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

AUTH_SERVICE_URL = os.environ.get("AUTH_SERVICE_URL", "http://localhost:8499")
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

LANDING_PAGE_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>FeedForge — Turn Any Website Into an RSS Feed</title>
  <script src="https://cdn.tailwindcss.com"></script>
  <script>
    tailwind.config = {
      theme: {
        extend: {
          colors: {
            brand: {
              50: '#fffbeb',
              100: '#fef3c7',
              200: '#fde68a',
              300: '#fcd34d',
              400: '#fbbf24',
              500: '#f59e0b',
              600: '#d97706',
              700: '#b45309',
              800: '#92400e',
              900: '#78350f',
            }
          }
        }
      }
    }
  </script>
  <style>
    html { scroll-behavior: smooth; }
    pre code { font-size: 0.85rem; }
  </style>
</head>
<body class="bg-gray-950 text-gray-100 font-sans antialiased">

  <!-- Nav -->
  <nav class="sticky top-0 z-50 bg-gray-950/80 backdrop-blur border-b border-gray-800">
    <div class="max-w-6xl mx-auto flex items-center justify-between px-6 py-4">
      <a href="#" class="flex items-center gap-2 text-xl font-bold text-brand-400">
        <svg class="w-7 h-7" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 5c7.18 0 13 5.82 13 13M6 11a7 7 0 017 7M6 17a1 1 0 100 2 1 1 0 000-2z"/>
        </svg>
        FeedForge
      </a>
      <div class="hidden md:flex items-center gap-8 text-sm text-gray-400">
        <a href="#features" class="hover:text-brand-400 transition">Features</a>
        <a href="#pricing" class="hover:text-brand-400 transition">Pricing</a>
        <a href="#docs" class="hover:text-brand-400 transition">Docs</a>
        <a href="https://github.com/therealMrFunGuy/feed-forge" target="_blank"
           class="hover:text-brand-400 transition">GitHub</a>
      </div>
    </div>
  </nav>

  <!-- Hero -->
  <section class="relative overflow-hidden">
    <div class="absolute inset-0 bg-gradient-to-br from-brand-600/20 via-transparent to-orange-900/10 pointer-events-none"></div>
    <div class="max-w-4xl mx-auto px-6 pt-28 pb-20 text-center relative">
      <div class="inline-block px-4 py-1.5 mb-6 rounded-full bg-brand-500/10 border border-brand-500/20 text-brand-400 text-sm font-medium">
        v1.0 &mdash; Open Source &amp; MCP-native
      </div>
      <h1 class="text-5xl md:text-6xl font-extrabold leading-tight tracking-tight">
        Turn Any Website Into an
        <span class="text-transparent bg-clip-text bg-gradient-to-r from-brand-400 to-orange-400">RSS Feed</span>
      </h1>
      <p class="mt-6 text-lg md:text-xl text-gray-400 max-w-2xl mx-auto leading-relaxed">
        Monitor any page for changes using CSS selectors. Get instant RSS, Atom, or JSON Feed output.
        Works as a standalone API or as an MCP server for Claude&nbsp;Desktop.
      </p>
      <div class="mt-10 flex flex-col sm:flex-row items-center justify-center gap-4">
        <a href="#docs"
           class="px-8 py-3 rounded-lg bg-brand-500 hover:bg-brand-600 text-gray-950 font-semibold text-sm transition shadow-lg shadow-brand-500/25">
          Get Started
        </a>
        <a href="https://github.com/therealMrFunGuy/feed-forge" target="_blank"
           class="px-8 py-3 rounded-lg border border-gray-700 hover:border-brand-500/50 text-gray-300 hover:text-brand-400 font-semibold text-sm transition">
          View on GitHub
        </a>
      </div>
    </div>
  </section>

  <!-- Code Examples -->
  <section id="examples" class="max-w-5xl mx-auto px-6 py-20">
    <h2 class="text-3xl font-bold text-center mb-4">See It in Action</h2>
    <p class="text-center text-gray-500 mb-12 max-w-xl mx-auto">Three commands from zero to a live RSS feed.</p>
    <div class="grid md:grid-cols-2 gap-6">
      <!-- Create a feed -->
      <div class="rounded-xl bg-gray-900 border border-gray-800 overflow-hidden">
        <div class="px-4 py-2 bg-gray-800/60 text-xs text-gray-400 font-mono flex items-center gap-2">
          <span class="w-2 h-2 rounded-full bg-brand-400"></span> Create a feed
        </div>
        <pre class="p-4 overflow-x-auto text-sm text-gray-300 leading-relaxed"><code>curl -X POST http://localhost:8435/feeds \\
  -H "Content-Type: application/json" \\
  -d '{
    "name": "HN Front Page",
    "url": "https://news.ycombinator.com",
    "css_selector": ".titleline > a",
    "check_interval_minutes": 15
  }'</code></pre>
      </div>
      <!-- Get RSS -->
      <div class="rounded-xl bg-gray-900 border border-gray-800 overflow-hidden">
        <div class="px-4 py-2 bg-gray-800/60 text-xs text-gray-400 font-mono flex items-center gap-2">
          <span class="w-2 h-2 rounded-full bg-green-400"></span> Get RSS / Atom / JSON
        </div>
        <pre class="p-4 overflow-x-auto text-sm text-gray-300 leading-relaxed"><code># RSS 2.0
curl http://localhost:8435/feeds/{id}/rss

# Atom
curl http://localhost:8435/feeds/{id}/atom

# JSON Feed 1.1
curl http://localhost:8435/feeds/{id}/json</code></pre>
      </div>
      <!-- MCP config -->
      <div class="md:col-span-2 rounded-xl bg-gray-900 border border-gray-800 overflow-hidden">
        <div class="px-4 py-2 bg-gray-800/60 text-xs text-gray-400 font-mono flex items-center gap-2">
          <span class="w-2 h-2 rounded-full bg-purple-400"></span> Claude Desktop &mdash; MCP config
        </div>
        <pre class="p-4 overflow-x-auto text-sm text-gray-300 leading-relaxed"><code>{
  "mcpServers": {
    "feedforge": {
      "command": "uvx",
      "args": ["mcp-server-feedforge"],
      "env": { "PORT": "8435" }
    }
  }
}</code></pre>
      </div>
    </div>
  </section>

  <!-- Features -->
  <section id="features" class="bg-gray-900/40 border-y border-gray-800">
    <div class="max-w-6xl mx-auto px-6 py-20">
      <h2 class="text-3xl font-bold text-center mb-4">Features</h2>
      <p class="text-center text-gray-500 mb-14 max-w-xl mx-auto">Everything you need to monitor the web and pipe changes into your workflow.</p>
      <div class="grid sm:grid-cols-2 lg:grid-cols-4 gap-6">
        <!-- Card 1 -->
        <div class="rounded-xl bg-gray-900 border border-gray-800 p-6 hover:border-brand-500/40 transition">
          <div class="w-10 h-10 rounded-lg bg-brand-500/10 flex items-center justify-center mb-4">
            <svg class="w-5 h-5 text-brand-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M10 20l4-16m4 4l4 4-4 4M6 16l-4-4 4-4"/>
            </svg>
          </div>
          <h3 class="font-semibold text-white mb-2">CSS Selector Monitoring</h3>
          <p class="text-sm text-gray-400 leading-relaxed">Target exactly the content you care about with precise CSS selectors. Ignore noise, track signal.</p>
        </div>
        <!-- Card 2 -->
        <div class="rounded-xl bg-gray-900 border border-gray-800 p-6 hover:border-brand-500/40 transition">
          <div class="w-10 h-10 rounded-lg bg-brand-500/10 flex items-center justify-center mb-4">
            <svg class="w-5 h-5 text-brand-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M6 5c7.18 0 13 5.82 13 13M6 11a7 7 0 017 7M6 17a1 1 0 100 2 1 1 0 000-2z"/>
            </svg>
          </div>
          <h3 class="font-semibold text-white mb-2">RSS / Atom / JSON Output</h3>
          <p class="text-sm text-gray-400 leading-relaxed">Every feed is available in RSS 2.0, Atom, and JSON Feed 1.1 formats. Plug into any reader or automation.</p>
        </div>
        <!-- Card 3 -->
        <div class="rounded-xl bg-gray-900 border border-gray-800 p-6 hover:border-brand-500/40 transition">
          <div class="w-10 h-10 rounded-lg bg-brand-500/10 flex items-center justify-center mb-4">
            <svg class="w-5 h-5 text-brand-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z"/>
            </svg>
          </div>
          <h3 class="font-semibold text-white mb-2">Change Detection</h3>
          <p class="text-sm text-gray-400 leading-relaxed">Snapshot diffing highlights exactly what changed between checks. Never miss an update again.</p>
        </div>
        <!-- Card 4 -->
        <div class="rounded-xl bg-gray-900 border border-gray-800 p-6 hover:border-brand-500/40 transition">
          <div class="w-10 h-10 rounded-lg bg-brand-500/10 flex items-center justify-center mb-4">
            <svg class="w-5 h-5 text-brand-400" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
              <path stroke-linecap="round" stroke-linejoin="round" d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z"/>
            </svg>
          </div>
          <h3 class="font-semibold text-white mb-2">Scheduled Checks</h3>
          <p class="text-sm text-gray-400 leading-relaxed">Configurable check intervals from 5 minutes to daily. Set it and forget it.</p>
        </div>
      </div>
    </div>
  </section>

  <!-- Pricing -->
  <section id="pricing" class="max-w-5xl mx-auto px-6 py-20">
    <h2 class="text-3xl font-bold text-center mb-4">Pricing</h2>
    <p class="text-center text-gray-500 mb-14 max-w-lg mx-auto">Start free. Scale when you need to.</p>
    <div class="grid md:grid-cols-3 gap-6">
      <!-- Free -->
      <div class="rounded-xl bg-gray-900 border border-gray-800 p-8 flex flex-col">
        <h3 class="text-lg font-semibold text-white">Free</h3>
        <div class="mt-4 mb-6">
          <span class="text-4xl font-extrabold text-white">$0</span>
          <span class="text-gray-500 text-sm">/mo</span>
        </div>
        <ul class="space-y-3 text-sm text-gray-400 mb-8 flex-1">
          <li class="flex items-start gap-2"><span class="text-brand-400 mt-0.5">&#10003;</span> 5 feeds</li>
          <li class="flex items-start gap-2"><span class="text-brand-400 mt-0.5">&#10003;</span> 1-hour check interval</li>
          <li class="flex items-start gap-2"><span class="text-brand-400 mt-0.5">&#10003;</span> RSS, Atom &amp; JSON output</li>
          <li class="flex items-start gap-2"><span class="text-brand-400 mt-0.5">&#10003;</span> Community support</li>
        </ul>
        <a href="#docs" class="block text-center py-2.5 rounded-lg border border-gray-700 hover:border-brand-500/50 text-sm font-medium text-gray-300 hover:text-brand-400 transition">
          Get Started
        </a>
      </div>
      <!-- Pro -->
      <div class="rounded-xl bg-gray-900 border-2 border-brand-500 p-8 flex flex-col relative shadow-lg shadow-brand-500/10">
        <div class="absolute -top-3 left-1/2 -translate-x-1/2 px-3 py-0.5 bg-brand-500 text-gray-950 text-xs font-bold rounded-full">
          POPULAR
        </div>
        <h3 class="text-lg font-semibold text-white">Pro</h3>
        <div class="mt-4 mb-6">
          <span class="text-4xl font-extrabold text-white">$14</span>
          <span class="text-gray-500 text-sm">/mo</span>
        </div>
        <ul class="space-y-3 text-sm text-gray-400 mb-8 flex-1">
          <li class="flex items-start gap-2"><span class="text-brand-400 mt-0.5">&#10003;</span> 100 feeds</li>
          <li class="flex items-start gap-2"><span class="text-brand-400 mt-0.5">&#10003;</span> 5-minute check intervals</li>
          <li class="flex items-start gap-2"><span class="text-brand-400 mt-0.5">&#10003;</span> Webhook notifications</li>
          <li class="flex items-start gap-2"><span class="text-brand-400 mt-0.5">&#10003;</span> JavaScript rendering</li>
          <li class="flex items-start gap-2"><span class="text-brand-400 mt-0.5">&#10003;</span> Priority support</li>
        </ul>
        <a href="#docs" class="block text-center py-2.5 rounded-lg bg-brand-500 hover:bg-brand-600 text-gray-950 text-sm font-semibold transition shadow-lg shadow-brand-500/25">
          Upgrade to Pro
        </a>
      </div>
      <!-- Enterprise -->
      <div class="rounded-xl bg-gray-900 border border-gray-800 p-8 flex flex-col">
        <h3 class="text-lg font-semibold text-white">Enterprise</h3>
        <div class="mt-4 mb-6">
          <span class="text-4xl font-extrabold text-white">Custom</span>
        </div>
        <ul class="space-y-3 text-sm text-gray-400 mb-8 flex-1">
          <li class="flex items-start gap-2"><span class="text-brand-400 mt-0.5">&#10003;</span> Unlimited feeds</li>
          <li class="flex items-start gap-2"><span class="text-brand-400 mt-0.5">&#10003;</span> Custom check intervals</li>
          <li class="flex items-start gap-2"><span class="text-brand-400 mt-0.5">&#10003;</span> Dedicated infrastructure</li>
          <li class="flex items-start gap-2"><span class="text-brand-400 mt-0.5">&#10003;</span> SLA &amp; uptime guarantee</li>
          <li class="flex items-start gap-2"><span class="text-brand-400 mt-0.5">&#10003;</span> SSO &amp; team management</li>
        </ul>
        <a href="mailto:hello@rjctdlabs.xyz" class="block text-center py-2.5 rounded-lg border border-gray-700 hover:border-brand-500/50 text-sm font-medium text-gray-300 hover:text-brand-400 transition">
          Contact Sales
        </a>
      </div>
    </div>
  </section>

  <!-- API Reference -->
  <section id="docs" class="bg-gray-900/40 border-y border-gray-800">
    <div class="max-w-5xl mx-auto px-6 py-20">
      <h2 class="text-3xl font-bold text-center mb-4">API Reference</h2>
      <p class="text-center text-gray-500 mb-14 max-w-lg mx-auto">All endpoints return JSON. Feed output available in RSS, Atom, and JSON Feed formats.</p>
      <div class="space-y-4">
        <!-- POST /feeds -->
        <div class="rounded-xl bg-gray-900 border border-gray-800 p-6">
          <div class="flex items-center gap-3 mb-3">
            <span class="px-2.5 py-0.5 rounded text-xs font-bold bg-green-500/10 text-green-400 border border-green-500/20">POST</span>
            <code class="text-sm text-white font-mono">/feeds</code>
          </div>
          <p class="text-sm text-gray-400 mb-3">Create a new feed monitor. Returns the feed object and results of the initial check.</p>
          <div class="text-xs text-gray-500 font-mono bg-gray-800/50 rounded-lg p-3">
            Body: { "name": string, "url": string, "css_selector": string, "check_interval_minutes"?: int, "js_render"?: bool, "webhook_url"?: string }
          </div>
        </div>
        <!-- GET /feeds/{id}/rss -->
        <div class="rounded-xl bg-gray-900 border border-gray-800 p-6">
          <div class="flex items-center gap-3 mb-3">
            <span class="px-2.5 py-0.5 rounded text-xs font-bold bg-blue-500/10 text-blue-400 border border-blue-500/20">GET</span>
            <code class="text-sm text-white font-mono">/feeds/{id}/rss</code>
          </div>
          <p class="text-sm text-gray-400">Returns the feed as RSS 2.0 XML. Also available as <code class="text-brand-400">/atom</code> and <code class="text-brand-400">/json</code>.</p>
        </div>
        <!-- GET /feeds/{id}/json -->
        <div class="rounded-xl bg-gray-900 border border-gray-800 p-6">
          <div class="flex items-center gap-3 mb-3">
            <span class="px-2.5 py-0.5 rounded text-xs font-bold bg-blue-500/10 text-blue-400 border border-blue-500/20">GET</span>
            <code class="text-sm text-white font-mono">/feeds/{id}/json</code>
          </div>
          <p class="text-sm text-gray-400">Returns the feed as JSON Feed 1.1 format, ideal for programmatic consumption and modern feed readers.</p>
        </div>
        <!-- POST /feeds/{id}/check -->
        <div class="rounded-xl bg-gray-900 border border-gray-800 p-6">
          <div class="flex items-center gap-3 mb-3">
            <span class="px-2.5 py-0.5 rounded text-xs font-bold bg-green-500/10 text-green-400 border border-green-500/20">POST</span>
            <code class="text-sm text-white font-mono">/feeds/{id}/check</code>
          </div>
          <p class="text-sm text-gray-400">Manually trigger a check for new content. Returns whether changes were detected and any new items found.</p>
        </div>
        <!-- GET /feeds/{id}/diff -->
        <div class="rounded-xl bg-gray-900 border border-gray-800 p-6">
          <div class="flex items-center gap-3 mb-3">
            <span class="px-2.5 py-0.5 rounded text-xs font-bold bg-blue-500/10 text-blue-400 border border-blue-500/20">GET</span>
            <code class="text-sm text-white font-mono">/feeds/{id}/diff</code>
          </div>
          <p class="text-sm text-gray-400">Show a diff of changes between the last two snapshots. Useful for debugging and auditing content changes.</p>
        </div>
      </div>
      <p class="mt-8 text-center text-sm text-gray-500">
        Full interactive docs available at <a href="/docs" class="text-brand-400 hover:underline">/docs</a> (Swagger UI) and <a href="/redoc" class="text-brand-400 hover:underline">/redoc</a> (ReDoc).
      </p>
    </div>
  </section>

  <!-- Footer -->
  <footer class="border-t border-gray-800">
    <div class="max-w-6xl mx-auto px-6 py-12 flex flex-col md:flex-row items-center justify-between gap-6">
      <div class="flex items-center gap-2 text-sm text-gray-500">
        <svg class="w-5 h-5 text-brand-500" fill="none" viewBox="0 0 24 24" stroke="currentColor" stroke-width="2">
          <path stroke-linecap="round" stroke-linejoin="round" d="M6 5c7.18 0 13 5.82 13 13M6 11a7 7 0 017 7M6 17a1 1 0 100 2 1 1 0 000-2z"/>
        </svg>
        <span>FeedForge v1.0.0</span>
        <span class="mx-2">&middot;</span>
        <span>Powered by <a href="https://rjctdlabs.xyz" target="_blank" class="text-brand-400 hover:underline">rjctdlabs.xyz</a></span>
      </div>
      <div class="flex items-center gap-6 text-sm text-gray-500">
        <a href="https://github.com/therealMrFunGuy/feed-forge" target="_blank" class="hover:text-brand-400 transition">GitHub</a>
        <a href="https://pypi.org/project/mcp-server-feedforge/" target="_blank" class="hover:text-brand-400 transition">PyPI</a>
        <a href="/docs" class="hover:text-brand-400 transition">API Docs</a>
      </div>
    </div>
  </footer>

</body>
</html>
"""


@app.get("/", response_class=HTMLResponse)
async def root():
    return HTMLResponse(content=LANDING_PAGE_HTML)


@app.post("/feeds", status_code=201)
async def create_feed_endpoint(req: CreateFeedRequest, auth: dict = Depends(require_auth)):
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
async def list_feeds_endpoint(auth: dict = Depends(require_auth)):
    feeds = list_feeds()
    return {"feeds": feeds, "total": len(feeds), "max": MAX_FEEDS}


@app.get("/feeds/{feed_id}")
async def get_feed_endpoint(feed_id: str, auth: dict = Depends(require_auth)):
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
async def get_rss(feed_id: str, auth: dict = Depends(require_auth)):
    xml = generate_rss(feed_id, base_url=BASE_URL)
    if not xml:
        raise HTTPException(status_code=404, detail="Feed not found")
    return Response(content=xml, media_type="application/rss+xml; charset=utf-8")


@app.get("/feeds/{feed_id}/atom")
async def get_atom(feed_id: str, auth: dict = Depends(require_auth)):
    xml = generate_atom(feed_id, base_url=BASE_URL)
    if not xml:
        raise HTTPException(status_code=404, detail="Feed not found")
    return Response(content=xml, media_type="application/atom+xml; charset=utf-8")


@app.get("/feeds/{feed_id}/json")
async def get_json_feed(feed_id: str, auth: dict = Depends(require_auth)):
    data = generate_json_feed(feed_id, base_url=BASE_URL)
    if not data:
        raise HTTPException(status_code=404, detail="Feed not found")
    return data


@app.delete("/feeds/{feed_id}")
async def delete_feed_endpoint(feed_id: str, auth: dict = Depends(require_auth)):
    if not delete_feed(feed_id):
        raise HTTPException(status_code=404, detail="Feed not found")
    return {"deleted": True, "feed_id": feed_id}


@app.post("/feeds/{feed_id}/check")
async def check_feed_endpoint(feed_id: str, auth: dict = Depends(require_auth)):
    feed = get_feed(feed_id)
    if not feed:
        raise HTTPException(status_code=404, detail="Feed not found")
    result = await check_feed(feed_id)
    return result


@app.get("/feeds/{feed_id}/diff")
async def get_diff(feed_id: str, auth: dict = Depends(require_auth)):
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
