"""
Unit tests for the outlier engine. These run instantly, with no network
and no API key -- which is exactly why we built the scoring logic as a
pure function of plain data, separate from the YouTube client. That
separation (data fetching vs. data analysis) is what makes this testable
at all; if scoring logic were buried inside API call code, you'd need a
live key and the internet just to check your math.
"""

import pytest
from src.outlier_engine import score_video, score_channel_videos


def test_clear_outlier_is_detected():
    result = score_video(
        candidate_views=500_000,
        candidate_title="Breakout video",
        candidate_id="vid_1",
        baseline_views=[10_000, 12_000, 11_000, 9_500, 10_500],
    )
    assert result.is_outlier
    assert result.multiplier > 40  # ~500k / ~10.6k average


def test_normal_video_is_not_flagged():
    result = score_video(
        candidate_views=11_500,
        candidate_title="Regular upload",
        candidate_id="vid_2",
        baseline_views=[10_000, 12_000, 11_000, 9_500, 10_500],
    )
    assert not result.is_outlier


def test_high_zscore_but_low_multiplier_is_not_an_outlier():
    # A very consistent channel (low variance) can produce a big z-score
    # from a small absolute bump. The multiplier gate exists precisely
    # to stop that from being misread as a breakout.
    result = score_video(
        candidate_views=13_000,
        candidate_title="Slightly above average",
        candidate_id="vid_3",
        baseline_views=[10_000, 10_100, 9_900, 10_050, 9_950],  # very tight baseline
    )
    assert result.z_score > 1.5
    assert result.multiplier < 2.0
    assert not result.is_outlier


def test_requires_minimum_baseline_size():
    with pytest.raises(ValueError):
        score_video(
            candidate_views=100_000,
            candidate_title="Too new to score",
            candidate_id="vid_4",
            baseline_views=[10_000, 11_000],  # only 2 -- not enough
        )


def test_score_channel_videos_never_uses_future_data():
    # videos are newest -> oldest, matching YouTube's playlist order.
    # The outlier (index 0) should be scored against OLDER videos only
    # (index 1+), never against itself.
    videos = [
        {"id": "newest_outlier", "title": "Big hit", "view_count": 300_000},
        {"id": "v2", "title": "Normal", "view_count": 10_000},
        {"id": "v3", "title": "Normal", "view_count": 11_000},
        {"id": "v4", "title": "Normal", "view_count": 9_500},
    ]
    results = score_channel_videos(videos, baseline_window=3)
    top = results[0]
    assert top.video_id == "newest_outlier"
    assert top.is_outlier
    # baseline_mean should reflect the 3 older videos (~10,166), not
    # include the 300,000-view video itself.
    assert top.baseline_mean < 15_000


def test_score_channel_videos_skips_videos_without_enough_history():
    # The oldest videos in a short list won't have enough older videos
    # left to build a baseline from -- they should be silently skipped,
    # not crash.
    videos = [
        {"id": "v1", "title": "A", "view_count": 50_000},
        {"id": "v2", "title": "B", "view_count": 10_000},
        {"id": "v3", "title": "C", "view_count": 9_000},
    ]
    results = score_channel_videos(videos, baseline_window=5)
    assert results == []  # nobody has 3+ older videos to compare against
