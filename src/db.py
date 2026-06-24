"""
SQLite persistence layer.

WHY SQLITE
----------
SQLite is a single file database built directly into Python -- no
server to install, no credentials to manage, no setup beyond this
module. The entire database lives in one file (tracker.db) in your
project folder. For a solo dev tool tracking dozens of channels,
it's the right choice. We'd only need to move to Postgres if this
scaled to hundreds of concurrent users.

SCHEMA DESIGN
-------------
Three tables:

  channels
  --------
  One row per tracked channel. Stores the channel's title and
  subscriber count as of the last time we checked. Lets us show
  channel names in reports instead of raw UC... IDs.

  video_snapshots
  ---------------
  The core table. Every time we run, we insert one row per video
  per channel with the current view count and a timestamp. This
  is what gives us a time series -- multiple rows for the same
  video_id, taken at different times, let us see how views are
  growing between runs.

  outlier_log
  -----------
  Every time a video crosses our outlier threshold, we log it
  once. This lets us later ask "when did this video first become
  an outlier?" and "how long did it stay one?" without having to
  recompute the outlier score across every snapshot each time.

  candidate_channels
  -------------------
  Channels we've *discovered* by scanning descriptions or featured-
  channel shelves of channels we already track, but haven't promoted
  to active tracking yet. We don't trust a single mention -- a channel
  needs to be referenced by multiple DIFFERENT tracked channels before
  we believe it's actually relevant to the niche, not just one creator's
  one-off shoutout. This table is where that evidence accumulates.

AUTO-DISCOVERY DESIGN
----------------------
The key cost-control idea: extracting @handle mentions from video
descriptions is completely FREE (we already have the description text
from videos.list, no extra API call). Resolving a handle into a real
channel_id costs 1 quota unit though, so we delay that step -- we only
spend quota resolving handles that have ALREADY been mentioned by 2+
different tracked channels. That means we're never spending API quota
chasing one-off shoutouts that probably aren't relevant to the niche,
only candidates with real cross-channel evidence behind them.

VELOCITY
--------
"Velocity" means views gained per hour between two consecutive
snapshots of the same video. If a video had 10,000 views at 9am
and 15,000 views at 12pm, its velocity is 5,000 / 3 = ~1,667
views/hour. We can then compare velocity between runs to see if
it's accelerating (growing faster than before) or decelerating
(the spike is fading). Accelerating velocity on a video that
hasn't yet crossed the outlier threshold is the "catch it early"
signal we're building toward.
"""

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

DB_PATH = Path(__file__).resolve().parent.parent / "tracker.db"


@contextmanager
def get_conn(db_path: Path = DB_PATH):
    """
    Context manager that opens a connection, yields it, commits on
    success, rolls back on error, and always closes. Using this
    everywhere means we never forget to commit or leave connections open.
    """
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row  # lets us access columns by name (row["title"])
    conn.execute("PRAGMA journal_mode=WAL")  # safer for concurrent reads
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: Path = DB_PATH):
    """
    Creates all three tables if they don't already exist. Safe to call
    on every run -- IF NOT EXISTS means it's a no-op if the db is
    already set up.
    """
    with get_conn(db_path) as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS channels (
                channel_id      TEXT PRIMARY KEY,
                title           TEXT,
                subscriber_count INTEGER,
                last_checked_at TEXT,  -- ISO 8601 timestamp
                status          TEXT DEFAULT 'active',   -- 'active' channels get polled
                discovery_source TEXT DEFAULT 'manual',  -- 'manual' or 'auto_discovered'
                added_at        TEXT
            );

            CREATE TABLE IF NOT EXISTS video_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id        TEXT NOT NULL,
                channel_id      TEXT NOT NULL,
                title           TEXT,
                view_count      INTEGER,
                like_count      INTEGER,
                comment_count   INTEGER,
                published_at    TEXT,  -- when the video was uploaded
                fetched_at      TEXT   -- when WE recorded this snapshot
            );

            -- Index on (video_id, fetched_at) so "get all snapshots for
            -- this video ordered by time" is fast even with thousands of rows
            CREATE INDEX IF NOT EXISTS idx_snapshots_video_time
                ON video_snapshots (video_id, fetched_at);

            -- Index on channel_id so "get all videos for this channel" is fast
            CREATE INDEX IF NOT EXISTS idx_snapshots_channel
                ON video_snapshots (channel_id, fetched_at);

            CREATE TABLE IF NOT EXISTS outlier_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                video_id        TEXT NOT NULL,
                channel_id      TEXT NOT NULL,
                title           TEXT,
                multiplier      REAL,
                z_score         REAL,
                view_count      INTEGER,
                first_seen_at   TEXT,  -- when we FIRST flagged this as an outlier
                last_seen_at    TEXT   -- most recent run where it was still an outlier
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_outlier_video
                ON outlier_log (video_id);

            CREATE TABLE IF NOT EXISTS candidate_channels (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                handle          TEXT,             -- raw "@SomeChannel", null once resolved via channel_section
                channel_id      TEXT,             -- filled in once resolved/known
                mention_count   INTEGER DEFAULT 1,
                source_channels TEXT,             -- comma-separated tracked channel_ids that mentioned this one
                discovery_method TEXT,            -- 'description_mention' or 'channel_section'
                status          TEXT DEFAULT 'pending',  -- 'pending' -> 'resolved' -> 'promoted' / 'rejected'
                first_seen_at   TEXT,
                last_seen_at    TEXT
            );

            -- A given handle (or, once resolved, a given channel_id)
            -- should only ever have ONE candidate row -- repeat mentions
            -- increment mention_count rather than creating duplicates.
            CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_handle
                ON candidate_channels (handle) WHERE handle IS NOT NULL;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_candidate_channel_id
                ON candidate_channels (channel_id) WHERE channel_id IS NOT NULL;
        """)


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------

def upsert_channel(channel_id: str, title: str, subscriber_count: int,
                   status: str = 'active', discovery_source: str = 'manual',
                   db_path: Path = DB_PATH):
    """Insert or update a channel's metadata. Existing channels keep their
    current status/discovery_source unless explicitly changed -- this call
    is mainly used to refresh title/subscriber_count on every poll."""
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(db_path) as conn:
        conn.execute("""
            INSERT INTO channels (channel_id, title, subscriber_count,
                                  last_checked_at, status, discovery_source, added_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_id) DO UPDATE SET
                title            = excluded.title,
                subscriber_count = excluded.subscriber_count,
                last_checked_at  = excluded.last_checked_at
        """, (channel_id, title, subscriber_count, now, status, discovery_source, now))


def get_active_channel_ids(db_path: Path = DB_PATH) -> List[str]:
    """Returns all channel_ids currently marked 'active' -- this is the
    real list of what gets polled, combining manually-seeded channels
    AND anything auto-discovery has promoted."""
    with get_conn(db_path) as conn:
        rows = conn.execute(
            "SELECT channel_id FROM channels WHERE status = 'active'"
        ).fetchall()
    return [row["channel_id"] for row in rows]


def save_snapshot(video_id: str, channel_id: str, title: str,
                  view_count: int, like_count: Optional[int],
                  comment_count: Optional[int], published_at: datetime,
                  db_path: Path = DB_PATH):
    """
    Save one view-count snapshot for one video. Called once per video
    per run. Over time, multiple rows pile up for the same video_id,
    each with a different fetched_at and view_count -- that's the
    time series we query for velocity.
    """
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(db_path) as conn:
        conn.execute("""
            INSERT INTO video_snapshots
                (video_id, channel_id, title, view_count, like_count,
                 comment_count, published_at, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (video_id, channel_id, title, view_count, like_count,
              comment_count, published_at.isoformat(), now))


def log_outlier(video_id: str, channel_id: str, title: str,
                multiplier: float, z_score: float, view_count: int,
                db_path: Path = DB_PATH):
    """
    Insert a new outlier record, or update last_seen_at if we've
    already logged this video before. This way we can track how long
    a video stays in outlier territory across multiple runs.
    """
    now = datetime.now(timezone.utc).isoformat()
    with get_conn(db_path) as conn:
        conn.execute("""
            INSERT INTO outlier_log
                (video_id, channel_id, title, multiplier, z_score,
                 view_count, first_seen_at, last_seen_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                multiplier   = excluded.multiplier,
                z_score      = excluded.z_score,
                view_count   = excluded.view_count,
                last_seen_at = excluded.last_seen_at
        """, (video_id, channel_id, title, multiplier, z_score,
              view_count, now, now))


# ---------------------------------------------------------------------------
# Read operations
# ---------------------------------------------------------------------------

@dataclass
class VelocityReading:
    video_id: str
    title: str
    channel_id: str
    current_views: int
    previous_views: int
    views_gained: int
    hours_elapsed: float
    views_per_hour: float
    is_accelerating: bool  # is it growing faster than the previous interval?
    current_multiplier: Optional[float] = None  # outlier score if available


def get_velocity(video_id: str, db_path: Path = DB_PATH) -> Optional[VelocityReading]:
    """
    Returns the current velocity for a video by comparing its two most
    recent snapshots. Returns None if there aren't at least two snapshots
    yet (can't compute a rate of change from a single data point).
    """
    with get_conn(db_path) as conn:
        rows = conn.execute("""
            SELECT video_id, channel_id, title, view_count, fetched_at
            FROM video_snapshots
            WHERE video_id = ?
            ORDER BY fetched_at DESC
            LIMIT 3
        """, (video_id,)).fetchall()

    if len(rows) < 2:
        return None

    latest = rows[0]
    previous = rows[1]

    t_latest = datetime.fromisoformat(latest["fetched_at"])
    t_previous = datetime.fromisoformat(previous["fetched_at"])
    hours = (t_latest - t_previous).total_seconds() / 3600
    if hours == 0:
        return None

    views_gained = latest["view_count"] - previous["view_count"]
    vph = views_gained / hours

    # Is it accelerating? Compare to the interval before that (if we have 3 rows)
    is_accelerating = False
    if len(rows) == 3:
        older = rows[2]
        t_older = datetime.fromisoformat(older["fetched_at"])
        hours_prev = (t_previous - t_older).total_seconds() / 3600
        if hours_prev > 0:
            prev_views_gained = previous["view_count"] - older["view_count"]
            prev_vph = prev_views_gained / hours_prev
            is_accelerating = vph > prev_vph

    return VelocityReading(
        video_id=video_id,
        title=latest["title"],
        channel_id=latest["channel_id"],
        current_views=latest["view_count"],
        previous_views=previous["view_count"],
        views_gained=views_gained,
        hours_elapsed=round(hours, 1),
        views_per_hour=round(vph, 0),
        is_accelerating=is_accelerating,
    )


def get_rising_videos(channel_id: str, min_velocity: float = 100,
                      db_path: Path = DB_PATH) -> List[VelocityReading]:
    """
    Returns all videos for a channel that have at least two snapshots
    AND are gaining views faster than min_velocity views/hour.
    These are candidates to watch even if they haven't crossed the
    outlier threshold yet -- they might be on their way.
    """
    with get_conn(db_path) as conn:
        # Get all distinct video IDs for this channel that have 2+ snapshots
        rows = conn.execute("""
            SELECT video_id
            FROM video_snapshots
            WHERE channel_id = ?
            GROUP BY video_id
            HAVING COUNT(*) >= 2
        """, (channel_id,)).fetchall()

    results = []
    for row in rows:
        v = get_velocity(row["video_id"], db_path)
        if v and v.views_per_hour >= min_velocity:
            results.append(v)

    return sorted(results, key=lambda v: v.views_per_hour, reverse=True)


def get_snapshot_history(video_id: str,
                         db_path: Path = DB_PATH) -> List[Tuple[str, int]]:
    """
    Returns all snapshots for a video as (timestamp, view_count) pairs,
    oldest first. Used for printing a simple growth curve.
    """
    with get_conn(db_path) as conn:
        rows = conn.execute("""
            SELECT fetched_at, view_count
            FROM video_snapshots
            WHERE video_id = ?
            ORDER BY fetched_at ASC
        """, (video_id,)).fetchall()
    return [(row["fetched_at"], row["view_count"]) for row in rows]


def get_all_outliers(db_path: Path = DB_PATH):
    """Returns everything in the outlier log, most recent first."""
    with get_conn(db_path) as conn:
        return conn.execute("""
            SELECT * FROM outlier_log
            ORDER BY last_seen_at DESC
        """).fetchall()


# ---------------------------------------------------------------------------
# Auto-discovery: candidate channels
# ---------------------------------------------------------------------------

def add_candidate(source_channel_id: str, discovery_method: str,
                  handle: Optional[str] = None, channel_id: Optional[str] = None,
                  db_path: Path = DB_PATH):
    """
    Record a sighting of a possible new channel. If we've seen this
    handle (or channel_id) before, increment mention_count and append
    the new source channel. If not, insert a fresh row.

    discovery_method is either 'description_mention' (free -- found by
    scanning text) or 'channel_section' (1 quota unit -- found via a
    channel's official featured-channels shelf, which already gives us
    a real channel_id with no resolution step needed).
    """
    if not handle and not channel_id:
        raise ValueError("Must provide either a handle or a channel_id")

    now = datetime.now(timezone.utc).isoformat()
    # channel_section discoveries already have a real channel_id, so they
    # start 'resolved' immediately and skip the handle-resolution step
    initial_status = 'resolved' if channel_id else 'pending'

    with get_conn(db_path) as conn:
        match_field = "channel_id" if channel_id else "handle"
        match_value = channel_id if channel_id else handle

        existing = conn.execute(
            f"SELECT id, mention_count, source_channels FROM candidate_channels "
            f"WHERE {match_field} = ?",
            (match_value,)
        ).fetchone()

        if existing:
            sources = set(existing["source_channels"].split(",")) if existing["source_channels"] else set()
            sources.add(source_channel_id)
            conn.execute("""
                UPDATE candidate_channels
                SET mention_count = ?, source_channels = ?, last_seen_at = ?
                WHERE id = ?
            """, (len(sources), ",".join(sorted(sources)), now, existing["id"]))
        else:
            conn.execute("""
                INSERT INTO candidate_channels
                    (handle, channel_id, mention_count, source_channels,
                     discovery_method, status, first_seen_at, last_seen_at)
                VALUES (?, ?, 1, ?, ?, ?, ?, ?)
            """, (handle, channel_id, source_channel_id, discovery_method,
                  initial_status, now, now))


def get_pending_candidates(min_mentions: int = 2, db_path: Path = DB_PATH):
    """
    Returns candidates that still need handle resolution (channel_id is
    NULL) but have already crossed the mention threshold -- i.e. worth
    spending a quota unit on. This is the cost-control gate: we never
    resolve a handle mentioned by only one source channel.
    """
    with get_conn(db_path) as conn:
        return conn.execute("""
            SELECT * FROM candidate_channels
            WHERE status = 'pending' AND mention_count >= ?
            ORDER BY mention_count DESC
        """, (min_mentions,)).fetchall()


def mark_candidate_resolved(handle: str, channel_id: Optional[str],
                            db_path: Path = DB_PATH):
    """
    Called after attempting to resolve a handle into a real channel_id.
    If resolution succeeded, channel_id is set and status becomes
    'resolved'. If the handle didn't correspond to a real channel,
    channel_id stays NULL and status becomes 'rejected' so we don't
    keep retrying it on every discovery run.
    """
    with get_conn(db_path) as conn:
        if channel_id:
            conn.execute("""
                UPDATE candidate_channels SET channel_id = ?, status = 'resolved'
                WHERE handle = ?
            """, (channel_id, handle))
        else:
            conn.execute("""
                UPDATE candidate_channels SET status = 'rejected'
                WHERE handle = ?
            """, (handle,))


def get_promotable_candidates(min_mentions: int = 2, db_path: Path = DB_PATH):
    """
    Returns resolved candidates (we have a real channel_id) that have
    been mentioned by enough different tracked channels to trust as
    genuinely relevant to the niche, and aren't already promoted.
    """
    with get_conn(db_path) as conn:
        return conn.execute("""
            SELECT * FROM candidate_channels
            WHERE status = 'resolved' AND mention_count >= ? AND channel_id IS NOT NULL
            ORDER BY mention_count DESC
        """, (min_mentions,)).fetchall()


def promote_candidate(channel_id: str, title: str = "", subscriber_count: int = 0,
                      db_path: Path = DB_PATH):
    """
    Moves a candidate into the active channels table (so it starts
    getting polled like any manually-added channel) and marks the
    candidate record as promoted so we don't try to promote it again.
    """
    upsert_channel(channel_id, title, subscriber_count,
                   status='active', discovery_source='auto_discovered',
                   db_path=db_path)
    with get_conn(db_path) as conn:
        conn.execute("""
            UPDATE candidate_channels SET status = 'promoted'
            WHERE channel_id = ?
        """, (channel_id,))


# ---------------------------------------------------------------------------
# API helpers (used by the local backend the Chrome extension calls)
# ---------------------------------------------------------------------------

def get_snapshot_history_for_scoring(channel_id: str, max_videos: int = 30,
                                      db_path: Path = DB_PATH) -> List[Tuple[str, int]]:
    """
    Returns (video_id, view_count) pairs for a channel's recent videos,
    using the MOST RECENT snapshot per video (not all historical snapshots).
    This is the baseline the API uses to score new videos it sees on
    your YouTube homepage.
    """
    with get_conn(db_path) as conn:
        rows = conn.execute("""
            SELECT video_id, view_count, MAX(fetched_at) as latest_fetch
            FROM video_snapshots
            WHERE channel_id = ?
            GROUP BY video_id
            ORDER BY latest_fetch DESC
            LIMIT ?
        """, (channel_id, max_videos)).fetchall()
    return [(row["video_id"], row["view_count"]) for row in rows]


def get_channel_title(channel_id: str, db_path: Path = DB_PATH) -> Optional[str]:
    """Returns the stored title for a channel, or None if not in DB yet."""
    with get_conn(db_path) as conn:
        row = conn.execute(
            "SELECT title FROM channels WHERE channel_id = ?", (channel_id,)
        ).fetchone()
    return row["title"] if row else None
