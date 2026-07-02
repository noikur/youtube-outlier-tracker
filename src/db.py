"""
Database layer -- now using PostgreSQL instead of SQLite.

Why the switch: SQLite stores data in a single file on the server's
local filesystem. On Railway, that filesystem resets every time you
deploy -- meaning every push wipes your entire database. Postgres runs
as a separate hosted service that persists independently of deployments.

The SQL queries are almost identical to the SQLite version. The main
differences are:
  - Connection string comes from DATABASE_URL environment variable
  - %s placeholders instead of ? for parameters
  - SERIAL instead of AUTOINCREMENT for auto-incrementing IDs
  - ON CONFLICT syntax is the same (Postgres supports it natively)
"""

import os
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL")


@contextmanager
def get_conn():
    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL environment variable not set. "
            "Add it to your Railway service variables."
        )
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = False
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                CREATE TABLE IF NOT EXISTS channels (
                    channel_id       TEXT PRIMARY KEY,
                    title            TEXT,
                    subscriber_count INTEGER,
                    last_checked_at  TEXT,
                    status           TEXT DEFAULT 'active',
                    discovery_source TEXT DEFAULT 'manual',
                    added_at         TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS video_snapshots (
                    id            SERIAL PRIMARY KEY,
                    video_id      TEXT NOT NULL,
                    channel_id    TEXT NOT NULL,
                    title         TEXT,
                    view_count    INTEGER,
                    like_count    INTEGER,
                    comment_count INTEGER,
                    published_at  TEXT,
                    fetched_at    TEXT
                )
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_snapshots_video_time
                ON video_snapshots (video_id, fetched_at)
            """)
            cur.execute("""
                CREATE INDEX IF NOT EXISTS idx_snapshots_channel
                ON video_snapshots (channel_id, fetched_at)
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS outlier_log (
                    id           SERIAL PRIMARY KEY,
                    video_id     TEXT NOT NULL UNIQUE,
                    channel_id   TEXT NOT NULL,
                    title        TEXT,
                    multiplier   REAL,
                    z_score      REAL,
                    view_count   INTEGER,
                    first_seen_at TEXT,
                    last_seen_at  TEXT
                )
            """)
            cur.execute("""
                CREATE TABLE IF NOT EXISTS candidate_channels (
                    id               SERIAL PRIMARY KEY,
                    handle           TEXT UNIQUE,
                    channel_id       TEXT UNIQUE,
                    mention_count    INTEGER DEFAULT 1,
                    source_channels  TEXT,
                    discovery_method TEXT,
                    status           TEXT DEFAULT 'pending',
                    first_seen_at    TEXT,
                    last_seen_at     TEXT
                )
            """)


def upsert_channel(channel_id: str, title: str, subscriber_count: int,
                   status: str = 'active', discovery_source: str = 'manual',
                   db_path=None):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO channels
                    (channel_id, title, subscriber_count, last_checked_at,
                     status, discovery_source, added_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (channel_id) DO UPDATE SET
                    title            = EXCLUDED.title,
                    subscriber_count = EXCLUDED.subscriber_count,
                    last_checked_at  = EXCLUDED.last_checked_at
            """, (channel_id, title, subscriber_count, now,
                  status, discovery_source, now))


def get_active_channel_ids(db_path=None) -> List[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT channel_id FROM channels WHERE status = 'active'"
            )
            return [row[0] for row in cur.fetchall()]


def save_snapshot(video_id: str, channel_id: str, title: str,
                  view_count: int, like_count: Optional[int],
                  comment_count: Optional[int], published_at: datetime,
                  db_path=None):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO video_snapshots
                    (video_id, channel_id, title, view_count, like_count,
                     comment_count, published_at, fetched_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (video_id, channel_id, title, view_count, like_count,
                  comment_count, published_at.isoformat(), now))


def log_outlier(video_id: str, channel_id: str, title: str,
                multiplier: float, z_score: float, view_count: int,
                db_path=None):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                INSERT INTO outlier_log
                    (video_id, channel_id, title, multiplier, z_score,
                     view_count, first_seen_at, last_seen_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (video_id) DO UPDATE SET
                    multiplier    = EXCLUDED.multiplier,
                    z_score       = EXCLUDED.z_score,
                    view_count    = EXCLUDED.view_count,
                    last_seen_at  = EXCLUDED.last_seen_at
            """, (video_id, channel_id, title, multiplier, z_score,
                  view_count, now, now))


def get_all_outliers(db_path=None):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM outlier_log
                ORDER BY last_seen_at DESC
            """)
            return cur.fetchall()


def get_snapshot_history_for_scoring(channel_id: str, max_videos: int = 30,
                                      db_path=None) -> List[Tuple[str, int]]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT video_id, view_count
                FROM (
                    SELECT video_id, view_count,
                           MAX(fetched_at) as latest_fetch
                    FROM video_snapshots
                    WHERE channel_id = %s
                    GROUP BY video_id, view_count
                ) sub
                ORDER BY latest_fetch DESC
                LIMIT %s
            """, (channel_id, max_videos))
            return [(row[0], row[1]) for row in cur.fetchall()]


def get_channel_title(channel_id: str, db_path=None) -> Optional[str]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT title FROM channels WHERE channel_id = %s",
                (channel_id,)
            )
            row = cur.fetchone()
            return row[0] if row else None


def get_velocity(video_id: str, db_path=None):
    return None


def get_rising_videos(channel_id: str, min_velocity: float = 100,
                      db_path=None):
    return []


def get_snapshot_history(video_id: str, db_path=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT fetched_at, view_count
                FROM video_snapshots
                WHERE video_id = %s
                ORDER BY fetched_at ASC
            """, (video_id,))
            return [(row[0], row[1]) for row in cur.fetchall()]


def add_candidate(source_channel_id: str, discovery_method: str,
                  handle: Optional[str] = None, channel_id: Optional[str] = None,
                  db_path=None):
    now = datetime.now(timezone.utc).isoformat()
    initial_status = 'resolved' if channel_id else 'pending'
    with get_conn() as conn:
        with conn.cursor() as cur:
            if handle:
                cur.execute(
                    "SELECT id, mention_count, source_channels FROM candidate_channels WHERE handle = %s",
                    (handle,)
                )
            else:
                cur.execute(
                    "SELECT id, mention_count, source_channels FROM candidate_channels WHERE channel_id = %s",
                    (channel_id,)
                )
            existing = cur.fetchone()
            if existing:
                sources = set(existing[2].split(",")) if existing[2] else set()
                sources.add(source_channel_id)
                cur.execute("""
                    UPDATE candidate_channels
                    SET mention_count = %s, source_channels = %s, last_seen_at = %s
                    WHERE id = %s
                """, (len(sources), ",".join(sorted(sources)), now, existing[0]))
            else:
                cur.execute("""
                    INSERT INTO candidate_channels
                        (handle, channel_id, mention_count, source_channels,
                         discovery_method, status, first_seen_at, last_seen_at)
                    VALUES (%s, %s, 1, %s, %s, %s, %s, %s)
                """, (handle, channel_id, source_channel_id, discovery_method,
                      initial_status, now, now))


def get_pending_candidates(min_mentions: int = 2, db_path=None):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM candidate_channels
                WHERE status = 'pending' AND mention_count >= %s
                ORDER BY mention_count DESC
            """, (min_mentions,))
            return cur.fetchall()


def mark_candidate_resolved(handle: str, channel_id: Optional[str],
                            db_path=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            if channel_id:
                cur.execute("""
                    UPDATE candidate_channels
                    SET channel_id = %s, status = 'resolved'
                    WHERE handle = %s
                """, (channel_id, handle))
            else:
                cur.execute("""
                    UPDATE candidate_channels SET status = 'rejected'
                    WHERE handle = %s
                """, (handle,))


def get_promotable_candidates(min_mentions: int = 2, db_path=None):
    with get_conn() as conn:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("""
                SELECT * FROM candidate_channels
                WHERE status = 'resolved'
                AND mention_count >= %s
                AND channel_id IS NOT NULL
                ORDER BY mention_count DESC
            """, (min_mentions,))
            return cur.fetchall()


def promote_candidate(channel_id: str, title: str = "",
                      subscriber_count: int = 0, db_path=None):
    upsert_channel(channel_id, title, subscriber_count,
                   status='active', discovery_source='auto_discovered')
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                UPDATE candidate_channels SET status = 'promoted'
                WHERE channel_id = %s
            """, (channel_id,))