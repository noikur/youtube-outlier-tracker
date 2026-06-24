"""
Tests for YouTubeClient -- using mocked HTTP responses, never the real
YouTube API. This is the standard pattern for testing any code that
calls a third-party API: you don't want your test suite to depend on
network access, a valid API key, or YouTube's servers being up. Instead
we fake the response YouTube WOULD send, and check that our code parses
it correctly.

When you're ready to test against the real API, set YOUTUBE_API_KEY
in your environment and run src/main.py instead -- that's the "does
this actually work against real YouTube" check, separate from "is the
parsing logic correct," which is what these tests verify.
"""

from unittest.mock import patch, MagicMock
from src.youtube_client import YouTubeClient, QuotaTracker


def _mock_response(json_data, status=200):
    resp = MagicMock()
    resp.ok = status < 400
    resp.status_code = status
    resp.json.return_value = json_data
    resp.text = str(json_data)
    return resp


@patch("requests.Session.get")
def test_get_channel_parses_response_and_costs_one_unit(mock_get):
    mock_get.return_value = _mock_response({
        "items": [{
            "snippet": {"title": "Test Channel"},
            "statistics": {"subscriberCount": "125000"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU_test_playlist"}},
        }]
    })

    quota = QuotaTracker()
    client = YouTubeClient(api_key="fake_key_for_test", quota=quota)
    channel = client.get_channel("UC_test_channel")

    assert channel.title == "Test Channel"
    assert channel.subscriber_count == 125_000
    assert channel.uploads_playlist_id == "UU_test_playlist"
    assert quota.used == 1  # channels.list = 1 unit


@patch("requests.Session.get")
def test_get_video_stats_batches_correctly(mock_get):
    mock_get.return_value = _mock_response({
        "items": [
            {
                "id": "vid_a",
                "snippet": {
                    "title": "Video A",
                    "channelId": "UC_test_channel",
                    "publishedAt": "2026-01-15T12:00:00Z",
                },
                "statistics": {"viewCount": "50000", "likeCount": "2000", "commentCount": "150"},
            },
            {
                "id": "vid_b",
                "snippet": {
                    "title": "Video B",
                    "channelId": "UC_test_channel",
                    "publishedAt": "2026-01-10T12:00:00Z",
                },
                "statistics": {"viewCount": "12000"},  # no likeCount/commentCount -- some videos hide these
            },
        ]
    })

    quota = QuotaTracker()
    client = YouTubeClient(api_key="fake_key_for_test", quota=quota)
    snapshots = client.get_video_stats(["vid_a", "vid_b"])

    assert len(snapshots) == 2
    assert snapshots[0].view_count == 50_000
    assert snapshots[0].like_count == 2_000
    assert snapshots[1].like_count is None  # missing field handled gracefully, not a crash
    # One call for TWO video ids -- this is the batching behavior that
    # makes videos.list cheap. If this cost 2 units instead of 1,
    # something regressed.
    assert quota.used == 1


@patch("requests.Session.get")
def test_quota_tracker_accumulates_across_multiple_calls(mock_get):
    mock_get.return_value = _mock_response({
        "items": [{
            "snippet": {"title": "Channel"},
            "statistics": {"subscriberCount": "1000"},
            "contentDetails": {"relatedPlaylists": {"uploads": "UU_x"}},
        }]
    })
    quota = QuotaTracker()
    client = YouTubeClient(api_key="fake_key_for_test", quota=quota)
    client.get_channel("UC_1")
    client.get_channel("UC_2")
    client.get_channel("UC_3")

    assert quota.used == 3
    assert "3 / 10,000" in quota.summary()


def test_missing_api_key_raises_clear_error(monkeypatch):
    monkeypatch.delenv("YOUTUBE_API_KEY", raising=False)
    try:
        YouTubeClient(api_key=None)
        assert False, "should have raised ValueError"
    except ValueError as e:
        assert "YOUTUBE_API_KEY" in str(e)
