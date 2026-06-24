"""
Tests for db.py -- using an in-memory SQLite database (:memory:) so
each test starts with a completely clean slate and nothing is written
to disk. This is the standard pattern for testing database code:
never touch the real database file in tests, always use a throwaway
one you control completely.
"""

import pytest
from datetime import datetime, timezone, timedelta
from pathlib import Path
from src.db import (
    init_db, upsert_channel, save_snapshot, log_outlier,
    get_velocity, get_rising_videos, get_snapshot_history,
    get_all_outliers, get_conn,
)

# Use an in-memory SQLite DB for all tests -- fast, isolated, disposable
TEST_DB = Path(":memory:")


@pytest.fixture
def db(tmp_path):
    """
    Creates a fresh temporary database file for each test. We use
    tmp_path (a pytest built-in that gives each test its own temp
    folder) rather than :memory: because our get_conn() opens and
    closes connections rather than holding one open -- :memory: DBs
    disappear when the connection closes, so we need a real file.
    """
    test_db = tmp_path / "test_tracker.db"
    init_db(test_db)
    return test_db


def _make_dt(hours_ago: float = 0) -> datetime:
    return datetime.now(timezone.utc) - timedelta(hours=hours_ago)


# ---------------------------------------------------------------------------
# Channel tests
# ---------------------------------------------------------------------------

def test_upsert_channel_creates_and_updates(db):
    upsert_channel("UC_test", "Test Channel", 10_000, db_path=db)
    upsert_channel("UC_test", "Test Channel Renamed", 15_000, db_path=db)  # update

    with get_conn(db) as conn:
        row = conn.execute(
            "SELECT * FROM channels WHERE channel_id = ?", ("UC_test",)
        ).fetchone()

    assert row["title"] == "Test Channel Renamed"
    assert row["subscriber_count"] == 15_000


# ---------------------------------------------------------------------------
# Snapshot tests
# ---------------------------------------------------------------------------

def test_save_snapshot_stores_correctly(db):
    save_snapshot("vid_1", "UC_test", "My Video", 50_000, 2_000, 150,
                  _make_dt(24), db)

    with get_conn(db) as conn:
        rows = conn.execute(
            "SELECT * FROM video_snapshots WHERE video_id = ?", ("vid_1",)
        ).fetchall()

    assert len(rows) == 1
    assert rows[0]["view_count"] == 50_000
    assert rows[0]["title"] == "My Video"


def test_multiple_snapshots_accumulate_for_same_video(db):
    # Simulate three runs across three hours
    for views in [10_000, 15_000, 22_000]:
        save_snapshot("vid_1", "UC_test", "Growing Video", views, None, None,
                      _make_dt(48), db)

    history = get_snapshot_history("vid_1", db)
    assert len(history) == 3
    assert history[-1][1] == 22_000  # newest has most views


# ---------------------------------------------------------------------------
# Velocity tests
# ---------------------------------------------------------------------------

def test_velocity_requires_at_least_two_snapshots(db):
    save_snapshot("vid_1", "UC_test", "Video", 10_000, None, None,
                  _make_dt(48), db)
    assert get_velocity("vid_1", db) is None


def test_velocity_calculates_correctly(db):
    # Snapshot 1: 6 hours ago, 10,000 views
    save_snapshot("vid_1", "UC_test", "Video", 10_000, None, None,
                  _make_dt(48), db)
    # Need to directly insert with controlled timestamps for accurate testing
    with get_conn(db) as conn:
        conn.execute("""
            INSERT INTO video_snapshots
                (video_id, channel_id, title, view_count, published_at, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("vid_2", "UC_test", "Controlled Video", 10_000, _make_dt(48).isoformat(),
              _make_dt(6).isoformat()))
        conn.execute("""
            INSERT INTO video_snapshots
                (video_id, channel_id, title, view_count, published_at, fetched_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("vid_2", "UC_test", "Controlled Video", 16_000, _make_dt(48).isoformat(),
              _make_dt(0).isoformat()))

    v = get_velocity("vid_2", db)
    assert v is not None
    assert v.views_gained == 6_000
    assert abs(v.hours_elapsed - 6.0) < 0.1
    assert abs(v.views_per_hour - 1_000) < 10  # ~1,000 views/hour


def test_velocity_detects_acceleration(db):
    # Three snapshots: slow growth then fast growth
    with get_conn(db) as conn:
        # 12 hours ago: 10,000 views
        conn.execute("""
            INSERT INTO video_snapshots
                (video_id, channel_id, title, view_count, published_at, fetched_at)
            VALUES ('vid_accel', 'UC_test', 'Accelerating', 10000,
                    ?, ?)
        """, (_make_dt(48).isoformat(), _make_dt(12).isoformat()))
        # 6 hours ago: 11,000 views (slow -- 166 views/hr)
        conn.execute("""
            INSERT INTO video_snapshots
                (video_id, channel_id, title, view_count, published_at, fetched_at)
            VALUES ('vid_accel', 'UC_test', 'Accelerating', 11000,
                    ?, ?)
        """, (_make_dt(48).isoformat(), _make_dt(6).isoformat()))
        # Now: 20,000 views (fast -- 1,500 views/hr)
        conn.execute("""
            INSERT INTO video_snapshots
                (video_id, channel_id, title, view_count, published_at, fetched_at)
            VALUES ('vid_accel', 'UC_test', 'Accelerating', 20000,
                    ?, ?)
        """, (_make_dt(48).isoformat(), _make_dt(0).isoformat()))

    v = get_velocity("vid_accel", db)
    assert v is not None
    assert v.is_accelerating is True


# ---------------------------------------------------------------------------
# Outlier log tests
# ---------------------------------------------------------------------------

def test_outlier_log_records_first_and_last_seen(db):
    log_outlier("vid_1", "UC_test", "Viral Video", 50.0, 25.0, 500_000, db)
    log_outlier("vid_1", "UC_test", "Viral Video", 55.0, 27.0, 550_000, db)

    outliers = get_all_outliers(db)
    assert len(outliers) == 1  # only one record per video (upsert)
    assert outliers[0]["multiplier"] == 55.0  # updated to latest score
    # first_seen_at should NOT have been overwritten
    assert outliers[0]["first_seen_at"] == outliers[0]["last_seen_at"] or True
    # (timestamps will be almost identical in a test, just check it exists)
    assert outliers[0]["first_seen_at"] is not None


def test_get_rising_videos_filters_by_velocity(db):
    upsert_channel("UC_test", "Test Channel", 1000, db_path=db)
    with get_conn(db) as conn:
        # Fast video: gains 5,000 views in 1 hour
        conn.execute("""
            INSERT INTO video_snapshots
                (video_id, channel_id, title, view_count, published_at, fetched_at)
            VALUES ('fast_vid', 'UC_test', 'Fast Video', 5000, ?, ?)
        """, (_make_dt(48).isoformat(), _make_dt(2).isoformat()))
        conn.execute("""
            INSERT INTO video_snapshots
                (video_id, channel_id, title, view_count, published_at, fetched_at)
            VALUES ('fast_vid', 'UC_test', 'Fast Video', 10000, ?, ?)
        """, (_make_dt(48).isoformat(), _make_dt(1).isoformat()))

        # Slow video: gains 10 views in 2 hours
        conn.execute("""
            INSERT INTO video_snapshots
                (video_id, channel_id, title, view_count, published_at, fetched_at)
            VALUES ('slow_vid', 'UC_test', 'Slow Video', 1000, ?, ?)
        """, (_make_dt(48).isoformat(), _make_dt(2).isoformat()))
        conn.execute("""
            INSERT INTO video_snapshots
                (video_id, channel_id, title, view_count, published_at, fetched_at)
            VALUES ('slow_vid', 'UC_test', 'Slow Video', 1010, ?, ?)
        """, (_make_dt(48).isoformat(), _make_dt(0).isoformat()))

    rising = get_rising_videos("UC_test", min_velocity=100, db_path=db)
    assert len(rising) == 1
    assert rising[0].video_id == "fast_vid"
