"""
Outlier scoring engine.

THE CORE IDEA
-------------
A video is an "outlier" if it dramatically beats that SAME channel's
recent normal output. We're not comparing across channels of different
sizes (a 10M-sub channel and a 1K-sub channel have nothing in common
numerically) -- we're comparing a channel to itself, over time.

THE METHOD
----------
1. Take a channel's last N videos, excluding the one we're scoring.
2. Compute the mean and standard deviation of their view counts.
   This is the channel's "baseline" -- what normal looks like for them.
3. Score the candidate as a z-score: how many standard deviations
   above that baseline it sits.
4. Also compute a simple multiplier (views / mean), since z-scores are
   less intuitive than "this got 3.2x a normal video."

WHY BOTH NUMBERS MATTER
------------------------
Two videos can have the same multiplier but very different z-scores,
if one channel is unusually consistent and the other is unusually
volatile. A channel that swings wildly between 5K and 500K views needs
a BIGGER multiplier to register as a genuine outlier than a channel
that's normally rock-steady at 50K -- because for the volatile channel,
big swings are just... normal. The z-score captures that; the
multiplier alone doesn't.
"""

from dataclasses import dataclass
from statistics import mean, stdev
from typing import List, Dict


@dataclass
class OutlierResult:
    video_id: str
    title: str
    view_count: int
    baseline_mean: float
    baseline_stdev: float
    multiplier: float
    z_score: float

    @property
    def is_outlier(self) -> bool:
        # Starting thresholds -- tune these once you've calibrated
        # against real videos in your chosen niche. A multiplier of 2x
        # sounds generous, but for very small/new channels with noisy
        # baselines, you'll want the z-score check to do most of the
        # filtering so you don't flag normal variance as "viral."
        return self.multiplier >= 2.0 and self.z_score >= 1.5


def score_video(
    candidate_views: int,
    candidate_title: str,
    candidate_id: str,
    baseline_views: List[int],
) -> OutlierResult:
    if len(baseline_views) < 3:
        raise ValueError("Need at least 3 baseline videos for a meaningful score")

    baseline_mean = mean(baseline_views)
    # stdev() throws if every value is identical; guard against that
    # (it happens on brand-new channels with very few uploads).
    baseline_stdev = stdev(baseline_views) if len(set(baseline_views)) > 1 else 1.0

    multiplier = candidate_views / baseline_mean if baseline_mean else 0.0
    z = (candidate_views - baseline_mean) / baseline_stdev if baseline_stdev else 0.0

    return OutlierResult(
        video_id=candidate_id,
        title=candidate_title,
        view_count=candidate_views,
        baseline_mean=round(baseline_mean, 1),
        baseline_stdev=round(baseline_stdev, 1),
        multiplier=round(multiplier, 2),
        z_score=round(z, 2),
    )


def score_channel_videos(
    videos: List[Dict],
    baseline_window: int = 20,
) -> List[OutlierResult]:
    """
    videos: list of dicts with keys 'id', 'title', 'view_count', ordered
            NEWEST -> OLDEST (this is the order YouTube's playlist API
            returns them in, so the real client won't need to re-sort).

    For each video, its baseline is the next `baseline_window` OLDER
    videos -- never newer ones. This matters: if you let a video's
    baseline include videos published AFTER it, you're using
    information that didn't exist yet when that video came out. That's
    a subtle but real bug once this runs on an actual rolling time
    series instead of a fixed snapshot.
    """
    results = []
    for i, video in enumerate(videos):
        baseline_slice = videos[i + 1 : i + 1 + baseline_window]
        if len(baseline_slice) < 3:
            continue  # not enough history yet to judge this one fairly
        baseline_views = [v["view_count"] for v in baseline_slice]
        results.append(
            score_video(
                candidate_views=video["view_count"],
                candidate_title=video["title"],
                candidate_id=video["id"],
                baseline_views=baseline_views,
            )
        )
    return sorted(results, key=lambda r: r.multiplier, reverse=True)
