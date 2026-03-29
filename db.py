"""SQLite database layer for FeedForge."""

import sqlite3
import uuid
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).parent / "data" / "feedforge.db"


def get_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    conn = get_db()
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS feeds (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            url TEXT NOT NULL,
            css_selector TEXT NOT NULL,
            interval_min INTEGER NOT NULL DEFAULT 15,
            js_render INTEGER NOT NULL DEFAULT 0,
            webhook_url TEXT,
            last_check TEXT,
            created_at TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 1
        );

        CREATE TABLE IF NOT EXISTS snapshots (
            id TEXT PRIMARY KEY,
            feed_id TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            content TEXT NOT NULL,
            checked_at TEXT NOT NULL,
            FOREIGN KEY (feed_id) REFERENCES feeds(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS items (
            id TEXT PRIMARY KEY,
            feed_id TEXT NOT NULL,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            url TEXT,
            diff_summary TEXT,
            detected_at TEXT NOT NULL,
            FOREIGN KEY (feed_id) REFERENCES feeds(id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_snapshots_feed ON snapshots(feed_id, checked_at DESC);
        CREATE INDEX IF NOT EXISTS idx_items_feed ON items(feed_id, detected_at DESC);
    """)
    conn.commit()
    conn.close()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# --- Feed CRUD ---

def create_feed(
    name: str,
    url: str,
    css_selector: str,
    interval_min: int = 15,
    js_render: bool = False,
    webhook_url: Optional[str] = None,
) -> dict:
    feed_id = uuid.uuid4().hex[:12]
    conn = get_db()
    conn.execute(
        """INSERT INTO feeds (id, name, url, css_selector, interval_min, js_render, webhook_url, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (feed_id, name, url, css_selector, max(interval_min, 5), int(js_render), webhook_url, now_iso()),
    )
    conn.commit()
    feed = get_feed(feed_id, conn)
    conn.close()
    return feed


def get_feed(feed_id: str, conn: Optional[sqlite3.Connection] = None) -> Optional[dict]:
    own_conn = conn is None
    if own_conn:
        conn = get_db()
    row = conn.execute("SELECT * FROM feeds WHERE id = ?", (feed_id,)).fetchone()
    if own_conn:
        conn.close()
    return dict(row) if row else None


def list_feeds(active_only: bool = True) -> list[dict]:
    conn = get_db()
    if active_only:
        rows = conn.execute("SELECT * FROM feeds WHERE active = 1 ORDER BY created_at DESC").fetchall()
    else:
        rows = conn.execute("SELECT * FROM feeds ORDER BY created_at DESC").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_feed_last_check(feed_id: str):
    conn = get_db()
    conn.execute("UPDATE feeds SET last_check = ? WHERE id = ?", (now_iso(), feed_id))
    conn.commit()
    conn.close()


def delete_feed(feed_id: str) -> bool:
    conn = get_db()
    cur = conn.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
    conn.commit()
    conn.close()
    return cur.rowcount > 0


def count_feeds() -> int:
    conn = get_db()
    count = conn.execute("SELECT COUNT(*) FROM feeds WHERE active = 1").fetchone()[0]
    conn.close()
    return count


# --- Snapshots ---

def save_snapshot(feed_id: str, content_hash: str, content: str) -> str:
    snap_id = uuid.uuid4().hex[:12]
    conn = get_db()
    conn.execute(
        "INSERT INTO snapshots (id, feed_id, content_hash, content, checked_at) VALUES (?, ?, ?, ?, ?)",
        (snap_id, feed_id, content_hash, content, now_iso()),
    )
    # Keep only last 100 snapshots per feed
    conn.execute("""
        DELETE FROM snapshots WHERE id IN (
            SELECT id FROM snapshots WHERE feed_id = ?
            ORDER BY checked_at DESC LIMIT -1 OFFSET 100
        )
    """, (feed_id,))
    conn.commit()
    conn.close()
    return snap_id


def get_latest_snapshot(feed_id: str) -> Optional[dict]:
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM snapshots WHERE feed_id = ? ORDER BY checked_at DESC LIMIT 1",
        (feed_id,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def get_last_two_snapshots(feed_id: str) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM snapshots WHERE feed_id = ? ORDER BY checked_at DESC LIMIT 2",
        (feed_id,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# --- Items ---

def add_item(feed_id: str, title: str, content: str, url: Optional[str] = None, diff_summary: Optional[str] = None) -> str:
    item_id = uuid.uuid4().hex[:12]
    conn = get_db()
    conn.execute(
        "INSERT INTO items (id, feed_id, title, content, url, diff_summary, detected_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (item_id, feed_id, title, content, url, diff_summary, now_iso()),
    )
    # Keep only last 200 items per feed
    conn.execute("""
        DELETE FROM items WHERE id IN (
            SELECT id FROM items WHERE feed_id = ?
            ORDER BY detected_at DESC LIMIT -1 OFFSET 200
        )
    """, (feed_id,))
    conn.commit()
    conn.close()
    return item_id


def get_items(feed_id: str, limit: int = 50) -> list[dict]:
    conn = get_db()
    rows = conn.execute(
        "SELECT * FROM items WHERE feed_id = ? ORDER BY detected_at DESC LIMIT ?",
        (feed_id, limit),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
