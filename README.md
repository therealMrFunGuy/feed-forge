# FeedForge

Turn any website into an RSS/JSON/Atom feed by monitoring for changes.

## Quick Start

```bash
# Docker
docker compose up -d

# Or local
pip install -r requirements.txt
python server.py
```

API runs on port 8435. MCP server via `python mcp_server.py` (stdio).

## API

- `POST /feeds` — Create feed monitor (url, css_selector, name, check_interval_minutes)
- `GET /feeds` — List all feeds
- `GET /feeds/{id}` — Feed details + recent items
- `GET /feeds/{id}/rss` — RSS 2.0 XML
- `GET /feeds/{id}/atom` — Atom XML
- `GET /feeds/{id}/json` — JSON Feed 1.1
- `DELETE /feeds/{id}` — Delete feed
- `POST /feeds/{id}/check` — Manual check
- `GET /feeds/{id}/diff` — Show last diff

## MCP Tools

- `create_feed` — Monitor a URL with CSS selector
- `check_feed` — Check a feed now
- `get_changes` — Show recent changes
- `extract_content` — One-shot CSS extraction (no monitoring)
- `list_feeds` — List all monitored feeds
