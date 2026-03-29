"""FeedForge MCP Server — tools for feed monitoring via Model Context Protocol."""

import asyncio
import json
import logging
import sys
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import run_server
from mcp.types import Tool, TextContent

# Ensure project root is on path
sys.path.insert(0, str(Path(__file__).parent))

from db import init_db, create_feed, get_feed, list_feeds, get_items
from crawler import fetch_html
from parser import extract_content, elements_to_text, compute_diff
from scheduler import check_feed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("feedforge.mcp")

server = Server("feedforge")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="create_feed",
            description="Monitor a URL for changes using a CSS selector. Returns a feed ID you can use to check for updates. The feed will be checked automatically at the specified interval.",
            inputSchema={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Human-readable name for this feed"},
                    "url": {"type": "string", "description": "URL to monitor"},
                    "css_selector": {"type": "string", "description": "CSS selector to extract content (e.g., '.article-title', '#main-content li')"},
                    "check_interval_minutes": {"type": "integer", "description": "How often to check (min 5)", "default": 15},
                    "js_render": {"type": "boolean", "description": "Use Playwright for JS-heavy pages", "default": False},
                },
                "required": ["name", "url", "css_selector"],
            },
        ),
        Tool(
            name="check_feed",
            description="Immediately check a monitored feed for changes. Returns whether content changed and a diff if it did.",
            inputSchema={
                "type": "object",
                "properties": {
                    "feed_id": {"type": "string", "description": "The feed ID to check"},
                },
                "required": ["feed_id"],
            },
        ),
        Tool(
            name="get_changes",
            description="Show what changed in a feed since the last check. Returns the diff with added/removed content.",
            inputSchema={
                "type": "object",
                "properties": {
                    "feed_id": {"type": "string", "description": "The feed ID"},
                    "limit": {"type": "integer", "description": "Max items to return", "default": 10},
                },
                "required": ["feed_id"],
            },
        ),
        Tool(
            name="extract_content",
            description="One-shot extraction: fetch a URL and extract content using a CSS selector. No monitoring — just returns what's there now.",
            inputSchema={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to fetch"},
                    "css_selector": {"type": "string", "description": "CSS selector to extract"},
                    "js_render": {"type": "boolean", "description": "Use Playwright for JS-heavy pages", "default": False},
                },
                "required": ["url", "css_selector"],
            },
        ),
        Tool(
            name="list_feeds",
            description="List all monitored feeds with their status and last check time.",
            inputSchema={
                "type": "object",
                "properties": {},
            },
        ),
    ]


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent]:
    try:
        if name == "create_feed":
            feed = create_feed(
                name=arguments["name"],
                url=arguments["url"],
                css_selector=arguments["css_selector"],
                interval_min=arguments.get("check_interval_minutes", 15),
                js_render=arguments.get("js_render", False),
            )
            # Do initial check
            result = await check_feed(feed["id"])
            return [TextContent(
                type="text",
                text=json.dumps({
                    "feed": feed,
                    "initial_check": result,
                    "message": f"Feed '{feed['name']}' created with ID {feed['id']}. Monitoring {feed['url']} every {feed['interval_min']} minutes.",
                }, indent=2, default=str),
            )]

        elif name == "check_feed":
            result = await check_feed(arguments["feed_id"])
            return [TextContent(type="text", text=json.dumps(result, indent=2, default=str))]

        elif name == "get_changes":
            feed_id = arguments["feed_id"]
            limit = arguments.get("limit", 10)
            feed = get_feed(feed_id)
            if not feed:
                return [TextContent(type="text", text="Feed not found")]

            items = get_items(feed_id, limit=limit)
            output = {
                "feed": {"id": feed["id"], "name": feed["name"], "url": feed["url"]},
                "recent_changes": [
                    {
                        "title": item["title"],
                        "summary": item.get("diff_summary", ""),
                        "detected_at": item["detected_at"],
                        "content": item["content"][:500],
                    }
                    for item in items
                ],
            }
            return [TextContent(type="text", text=json.dumps(output, indent=2, default=str))]

        elif name == "extract_content":
            url = arguments["url"]
            css_selector = arguments["css_selector"]
            js_render = arguments.get("js_render", False)

            html = await fetch_html(url, js_render=js_render)
            elements = extract_content(html, css_selector, base_url=url)

            output = {
                "url": url,
                "selector": css_selector,
                "element_count": len(elements),
                "elements": [
                    {"text": el["text"][:500], "links": el["links"][:10], "tag": el["tag"]}
                    for el in elements[:50]
                ],
            }
            return [TextContent(type="text", text=json.dumps(output, indent=2, default=str))]

        elif name == "list_feeds":
            feeds = list_feeds()
            output = {
                "total": len(feeds),
                "feeds": [
                    {
                        "id": f["id"],
                        "name": f["name"],
                        "url": f["url"],
                        "selector": f["css_selector"],
                        "interval_min": f["interval_min"],
                        "last_check": f["last_check"],
                        "active": bool(f["active"]),
                    }
                    for f in feeds
                ],
            }
            return [TextContent(type="text", text=json.dumps(output, indent=2, default=str))]

        else:
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    except Exception as e:
        logger.exception(f"Tool {name} failed")
        return [TextContent(type="text", text=f"Error: {str(e)}")]


async def main():
    init_db()
    logger.info("FeedForge MCP server starting")
    await run_server(server)


if __name__ == "__main__":
    asyncio.run(main())
