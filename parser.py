"""HTML parsing, CSS selector extraction, and content diffing."""

import difflib
import logging
from typing import Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup, Tag

logger = logging.getLogger("feedforge.parser")


def extract_content(html: str, css_selector: str, base_url: Optional[str] = None) -> list[dict]:
    """Extract content from HTML using a CSS selector.

    Returns a list of extracted elements, each with:
        - text: stripped text content
        - html: inner HTML
        - links: list of absolute URLs found in the element
        - tag: the tag name of the matched element
    """
    soup = BeautifulSoup(html, "lxml")
    elements = soup.select(css_selector)

    results = []
    for el in elements:
        text = el.get_text(separator=" ", strip=True)
        if not text:
            continue

        links = []
        for a in el.find_all("a", href=True):
            href = a["href"]
            if base_url:
                href = urljoin(base_url, href)
            links.append({"text": a.get_text(strip=True), "url": href})

        results.append({
            "text": text,
            "html": str(el),
            "links": links,
            "tag": el.name,
        })

    return results


def elements_to_text(elements: list[dict]) -> str:
    """Convert extracted elements to a flat text representation for hashing/diffing."""
    lines = []
    for i, el in enumerate(elements):
        lines.append(f"[{i+1}] {el['text']}")
        for link in el.get("links", []):
            lines.append(f"    -> {link['text']}: {link['url']}")
    return "\n".join(lines)


def compute_diff(old_text: str, new_text: str) -> dict:
    """Compute a human-readable diff between two text snapshots.

    Returns:
        dict with keys:
            - added: list of added lines
            - removed: list of removed lines
            - changed: bool indicating if there was any change
            - unified_diff: full unified diff string
            - summary: one-line summary of changes
    """
    old_lines = old_text.splitlines()
    new_lines = new_text.splitlines()

    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm="", n=2))

    added = [line[1:] for line in diff if line.startswith("+") and not line.startswith("+++")]
    removed = [line[1:] for line in diff if line.startswith("-") and not line.startswith("---")]

    changed = len(added) > 0 or len(removed) > 0

    summary_parts = []
    if added:
        summary_parts.append(f"{len(added)} added")
    if removed:
        summary_parts.append(f"{len(removed)} removed")
    summary = ", ".join(summary_parts) if summary_parts else "no changes"

    return {
        "added": added,
        "removed": removed,
        "changed": changed,
        "unified_diff": "\n".join(diff),
        "summary": summary,
    }


def generate_item_title(feed_name: str, diff_result: dict) -> str:
    """Generate a title for a feed item based on the diff."""
    return f"{feed_name}: {diff_result['summary']}"


def generate_item_content(diff_result: dict, elements: list[dict]) -> str:
    """Generate HTML content for a feed item showing what changed."""
    parts = []

    if diff_result["added"]:
        parts.append("<h3>Added</h3><ul>")
        for line in diff_result["added"][:20]:
            parts.append(f"<li>{_escape_html(line)}</li>")
        if len(diff_result["added"]) > 20:
            parts.append(f"<li>... and {len(diff_result['added']) - 20} more</li>")
        parts.append("</ul>")

    if diff_result["removed"]:
        parts.append("<h3>Removed</h3><ul>")
        for line in diff_result["removed"][:20]:
            parts.append(f"<li><del>{_escape_html(line)}</del></li>")
        if len(diff_result["removed"]) > 20:
            parts.append(f"<li>... and {len(diff_result['removed']) - 20} more</li>")
        parts.append("</ul>")

    return "\n".join(parts) if parts else "<p>Content updated</p>"


def _escape_html(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
