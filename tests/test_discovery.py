"""
Tests for discovery.py -- the auto-discovery pipeline. Uses a temp
SQLite file (via tmp_path) for db state and mocks the YouTubeClient
for anything that would otherwise need a real API key.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock

from src import db
from src.discovery import (
    extract_handles,
    discover_from_descriptions,
    discover_from_channel_sections,
    resolve_pending_handles,
    promote_resolved_candidates,
)
from src.youtube_client import VideoSnapshot, ChannelInfo
from datetime import datetime, timezone


@pytest.fixture
def test_db(tmp_path):
    test_db_path = tmp_path / "test_discovery.db"
    db.init_db(test_db_path)
    return test_db_path


def _snapshot(title, description, channel_id="UC_source"):
    return VideoSnapshot(
        video_id="vid_1", channel_id=channel_id, title=title,
        description=description, published_at=datetime.now(timezone.utc),
        view_count=1000, like_count=None, comment_count=None,
        fetched_at=datetime.now(timezone.utc),
    )


# ---------------------------------------------------------------------------
# Handle extraction (pure regex, no DB or network)
# ---------------------------------------------------------------------------

def test_extract_handles_finds_bare_mentions():
    text = "Big thanks to @CoolChannel for the collab!"
    assert extract_handles(text) == ["CoolChannel"]


def test_extract_handles_finds_url_mentions():
    text = "Check out https://youtube.com/@AnotherChannel for more"
    assert extract_handles(text) == ["AnotherChannel"]


def test_extract_handles_dedupes_within_text():
    text = "@SameChannel said it best. Also check @SameChannel again."
    assert extract_handles(text) == ["SameChannel"]


def test_extract_handles_returns_empty_for_no_matches():
    assert extract_handles("Just a normal description with no mentions.") == []


def test_extract_handles_handles_empty_string():
    assert extract_handles("") == []
    assert extract_handles(None) == []


# ---------------------------------------------------------------------------
# Discovery from descriptions (free, DB writes only)
# ---------------------------------------------------------------------------

def test_discover_from_descriptions_records_candidate(test_db):
    snapshots = [_snapshot("My video", "ft. @GuestChannel doing the thing")]
    discover_from_descriptions(snapshots, source_channel_id="UC_source", db_path=test_db)

    pending = db.get_pending_candidates(min_mentions=1, db_path=test_db)
    assert len(pending) == 1
    assert pending[0]["handle"] == "GuestChannel"
    assert pending[0]["mention_count"] == 1


def test_discover_from_descriptions_does_not_self_reference(test_db):
    # A channel mentioning its own handle shouldn't create a candidate
    snapshots = [_snapshot("My video", "Subscribe! @MySourceChannel")]
    discover_from_descriptions(snapshots, source_channel_id="MySourceChannel", db_path=test_db)

    pending = db.get_pending_candidates(min_mentions=1, db_path=test_db)
    assert len(pending) == 0


def test_mention_count_accumulates_across_different_sources(test_db):
    # Same handle mentioned by TWO different source channels should
    # accumulate to mention_count=2, not create two separate candidates
    discover_from_descriptions(
        [_snapshot("Video A", "Collab with @PopularGuest")],
        source_channel_id="UC_channel_one", db_path=test_db,
    )
    discover_from_descriptions(
        [_snapshot("Video B", "Also worked with @PopularGuest")],
        source_channel_id="UC_channel_two", db_path=test_db,
    )

    pending = db.get_pending_candidates(min_mentions=2, db_path=test_db)
    assert len(pending) == 1
    assert pending[0]["mention_count"] == 2


def test_single_source_mentioning_same_handle_many_times_does_not_inflate_count(test_db):
    # One channel saying the same handle in 5 different videos should
    # still only count as 1 piece of evidence, not 5 -- otherwise a
    # single creator who tags one collaborator in every video would
    # falsely look like strong cross-channel evidence.
    snapshots = [
        _snapshot("Video 1", "@RepeatedGuest"),
        _snapshot("Video 2", "@RepeatedGuest"),
        _snapshot("Video 3", "@RepeatedGuest"),
    ]
    discover_from_descriptions(snapshots, source_channel_id="UC_source", db_path=test_db)

    pending = db.get_pending_candidates(min_mentions=1, db_path=test_db)
    assert len(pending) == 1
    assert pending[0]["mention_count"] == 1


# ---------------------------------------------------------------------------
# Discovery from channel sections (mocked API call)
# ---------------------------------------------------------------------------

def test_discover_from_channel_sections_records_resolved_candidates(test_db):
    mock_client = MagicMock()
    mock_client.get_channel_sections.return_value = [["UC_featured_1", "UC_featured_2"]]

    count = discover_from_channel_sections(mock_client, "UC_source", db_path=test_db)

    assert count == 2
    # channel_section discoveries are already 'resolved' -- they came
    # with a real channel_id, no handle resolution step needed
    promotable = db.get_promotable_candidates(min_mentions=1, db_path=test_db)
    assert len(promotable) == 2


def test_discover_from_channel_sections_handles_no_shelves(test_db):
    mock_client = MagicMock()
    mock_client.get_channel_sections.return_value = []
    count = discover_from_channel_sections(mock_client, "UC_source", db_path=test_db)
    assert count == 0


# ---------------------------------------------------------------------------
# Handle resolution (mocked API)
# ---------------------------------------------------------------------------

def test_resolve_pending_handles_only_resolves_above_threshold(test_db):
    # One handle mentioned twice (should resolve), one mentioned once (should not)
    discover_from_descriptions(
        [_snapshot("A", "@WellEvidenced")], source_channel_id="UC_1", db_path=test_db)
    discover_from_descriptions(
        [_snapshot("B", "@WellEvidenced")], source_channel_id="UC_2", db_path=test_db)
    discover_from_descriptions(
        [_snapshot("C", "@OnlyOnce")], source_channel_id="UC_3", db_path=test_db)

    mock_client = MagicMock()
    mock_client.resolve_handle.return_value = "UC_resolved_id"

    resolved = resolve_pending_handles(mock_client, min_mentions=2, db_path=test_db)

    assert resolved == 1
    # Only the well-evidenced handle should have triggered an API call
    mock_client.resolve_handle.assert_called_once_with("WellEvidenced")


def test_resolve_pending_handles_marks_failed_resolution_as_rejected(test_db):
    discover_from_descriptions(
        [_snapshot("A", "@FakeHandle")], source_channel_id="UC_1", db_path=test_db)
    discover_from_descriptions(
        [_snapshot("B", "@FakeHandle")], source_channel_id="UC_2", db_path=test_db)

    mock_client = MagicMock()
    mock_client.resolve_handle.return_value = None  # not a real channel

    resolve_pending_handles(mock_client, min_mentions=2, db_path=test_db)

    pending = db.get_pending_candidates(min_mentions=2, db_path=test_db)
    assert len(pending) == 0  # no longer pending -- it's rejected now


def test_resolve_pending_handles_respects_quota_budget(test_db):
    for i in range(5):
        discover_from_descriptions(
            [_snapshot(f"A{i}", f"@Handle{i}")], source_channel_id="UC_1", db_path=test_db)
        discover_from_descriptions(
            [_snapshot(f"B{i}", f"@Handle{i}")], source_channel_id="UC_2", db_path=test_db)

    mock_client = MagicMock()
    # Each handle resolves to its OWN distinct channel_id, same as real
    # YouTube data would -- reusing one fake ID across handles would
    # collide with the uniqueness constraint we rely on in production.
    mock_client.resolve_handle.side_effect = lambda h: f"UC_resolved_{h}"

    resolved = resolve_pending_handles(mock_client, min_mentions=2, quota_budget=3, db_path=test_db)

    assert resolved == 3  # capped by budget, even though 5 were eligible
    assert mock_client.resolve_handle.call_count == 3


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------

def test_promote_resolved_candidates_moves_to_active_channels(test_db):
    mock_client = MagicMock()
    mock_client.get_channel_sections.return_value = [["UC_new_channel"]]
    mock_client.get_channel.return_value = ChannelInfo(
        channel_id="UC_new_channel", title="Discovered Channel",
        subscriber_count=5000, uploads_playlist_id="UU_x",
    )

    discover_from_channel_sections(mock_client, "UC_source", db_path=test_db)
    promoted = promote_resolved_candidates(mock_client, min_mentions=1, db_path=test_db)

    assert len(promoted) == 1
    assert "Discovered Channel" in promoted[0]

    active = db.get_active_channel_ids(db_path=test_db)
    assert "UC_new_channel" in active


def test_promote_does_not_promote_below_threshold(test_db):
    mock_client = MagicMock()
    mock_client.get_channel_sections.return_value = [["UC_weak_evidence"]]

    discover_from_channel_sections(mock_client, "UC_source", db_path=test_db)
    # require 2 mentions but this candidate only has 1
    promoted = promote_resolved_candidates(mock_client, min_mentions=2, db_path=test_db)

    assert promoted == []
    active = db.get_active_channel_ids(db_path=test_db)
    assert "UC_weak_evidence" not in active
