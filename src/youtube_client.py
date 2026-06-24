"""
YouTube Data API v3 client, designed around the quota system from day one.

QUOTA COSTS (per Google's quota calculator, current as of 2026)
-----------------------------------------------------------------
  channels.list        -> 1 unit
  playlistItems.list   -> 1 unit
  videos.list           -> 1 unit  (covers up to 50 video IDs per call!)
  search.list           -> 100 units

Default quota: 10,000 units/day per project. That's effectively a
non-issue if you stick to the 1-unit endpoints, but only ~100 calls/day
if you lean on search.list.

THE STRATEGY
------------
We never call search.list in the normal polling path. Instead:
  1. channels.list once per channel -> gives us that channel's
     "uploads" playlist ID (1 unit).
  2. playlistItems.list to walk that playlist and get recent video
     IDs (1 unit per page of up to 50).
  3. videos.list with up to 50 video IDs AT ONCE to get view counts
     (1 unit total -- NOT 1 unit per video). Batching here is the
     single biggest quota lever you have: fetching 50 videos one at a
     time costs 50 units; fetching them together costs 1.

Tracking 20 channels with this approach costs roughly: 20 (channels)
+ 20 (playlist pages) + 20 (batched video stats) = ~60 units per full
poll. You could poll that same list over 150 times a day and still be
under the free quota.
"""

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional

import requests

YOUTUBE_API_BASE = "https://www.googleapis.com/youtube/v3"


@dataclass
class ChannelInfo:
    channel_id: str
    title: str
    subscriber_count: int
    uploads_playlist_id: str


@dataclass
class VideoSnapshot:
    video_id: str
    channel_id: str
    title: str
    description: str
    published_at: datetime
    view_count: int
    like_count: Optional[int]
    comment_count: Optional[int]
    fetched_at: datetime


class QuotaTracker:
    """
    A small in-memory counter so you can SEE your usage while developing.
    This does not enforce Google's limit -- Google does that server-side
    and will return a 403 quotaExceeded error if you go over. This just
    helps you build intuition before that ever happens.
    """

    COSTS = {
        "channels": 1,
        "playlistItems": 1,
        "videos": 1,
        "search": 100,
        "channelSections": 1,
    }

    def __init__(self):
        self.used = 0
        self.calls: List[tuple] = []

    def record(self, endpoint: str):
        cost = self.COSTS.get(endpoint, 1)
        self.used += cost
        self.calls.append((endpoint, cost))

    def summary(self) -> str:
        return f"{self.used} / 10,000 daily units used, across {len(self.calls)} calls"


class YouTubeAPIError(Exception):
    pass


class YouTubeClient:
    def __init__(self, api_key: Optional[str] = None, quota: Optional[QuotaTracker] = None):
        self.api_key = api_key or os.environ.get("YOUTUBE_API_KEY")
        if not self.api_key:
            raise ValueError(
                "No YouTube API key found. Set YOUTUBE_API_KEY in your "
                "environment or .env file -- see README.md for how to get one."
            )
        self.quota = quota or QuotaTracker()
        self.session = requests.Session()

    def _get(self, endpoint: str, params: dict) -> dict:
        params = {**params, "key": self.api_key}
        resp = self.session.get(f"{YOUTUBE_API_BASE}/{endpoint}", params=params)
        self.quota.record(endpoint)
        if not resp.ok:
            raise YouTubeAPIError(f"{endpoint} failed [{resp.status_code}]: {resp.text[:300]}")
        return resp.json()

    def get_channel(self, channel_id: str) -> ChannelInfo:
        """1 quota unit. Resolves a channel's metadata + its 'uploads'
        playlist ID, which is the key to listing its videos cheaply
        without ever touching search.list."""
        data = self._get(
            "channels",
            {"part": "snippet,statistics,contentDetails", "id": channel_id},
        )
        if not data.get("items"):
            raise YouTubeAPIError(f"No channel found for id={channel_id}")
        item = data["items"][0]
        return ChannelInfo(
            channel_id=channel_id,
            title=item["snippet"]["title"],
            subscriber_count=int(item["statistics"].get("subscriberCount", 0)),
            uploads_playlist_id=item["contentDetails"]["relatedPlaylists"]["uploads"],
        )

    def get_recent_video_ids(self, uploads_playlist_id: str, max_results: int = 50) -> List[str]:
        """1 quota unit per page of up to 50 results. This is the cheap
        substitute for search.list -- same videos, 1/100th the cost,
        because we're walking a known playlist instead of searching."""
        data = self._get(
            "playlistItems",
            {
                "part": "contentDetails",
                "playlistId": uploads_playlist_id,
                "maxResults": min(max_results, 50),
            },
        )
        return [item["contentDetails"]["videoId"] for item in data.get("items", [])]

    def get_video_stats(self, video_ids: List[str]) -> List[VideoSnapshot]:
        """1 quota unit for up to 50 video IDs AT ONCE. Always batch this --
        this is the call where it's easiest to accidentally waste quota by
        looping one-id-at-a-time instead of joining them with commas."""
        if not video_ids:
            return []
        data = self._get(
            "videos",
            {"part": "snippet,statistics", "id": ",".join(video_ids[:50])},
        )
        now = datetime.now(timezone.utc)
        snapshots = []
        for item in data.get("items", []):
            stats = item["statistics"]
            snapshots.append(
                VideoSnapshot(
                    video_id=item["id"],
                    channel_id=item["snippet"]["channelId"],
                    title=item["snippet"]["title"],
                    description=item["snippet"].get("description", ""),
                    published_at=datetime.fromisoformat(
                        item["snippet"]["publishedAt"].replace("Z", "+00:00")
                    ),
                    view_count=int(stats.get("viewCount", 0)),
                    like_count=int(stats["likeCount"]) if "likeCount" in stats else None,
                    comment_count=int(stats["commentCount"]) if "commentCount" in stats else None,
                    fetched_at=now,
                )
            )
        return snapshots

    def get_channel_videos(self, channel_id: str, max_videos: int = 50) -> List[VideoSnapshot]:
        """Convenience method that chains the three calls above:
        channel -> playlist -> video IDs -> video stats.
        Total cost for one channel: ~3 units (1 + 1 + 1), regardless of
        whether max_videos is 10 or 50, since videos.list is batched."""
        channel = self.get_channel(channel_id)
        video_ids = self.get_recent_video_ids(channel.uploads_playlist_id, max_videos)
        return self.get_video_stats(video_ids)

    def resolve_handle(self, handle: str) -> Optional[str]:
        """1 quota unit. Turns a raw @handle (e.g. 'SomeChannel', with or
        without the leading @) into a real channel_id, using the
        forHandle parameter. Returns None if the handle doesn't
        correspond to a real channel -- this happens often, since text
        scraped from descriptions catches false positives (usernames,
        social media handles that aren't YouTube channels, etc). A
        failed resolution just means we discard that candidate, not
        an error."""
        clean_handle = handle.lstrip("@")
        try:
            data = self._get("channels", {"part": "id", "forHandle": f"@{clean_handle}"})
        except YouTubeAPIError:
            return None
        items = data.get("items", [])
        return items[0]["id"] if items else None

    def get_channel_sections(self, channel_id: str) -> List[List[str]]:
        """1 quota unit. Returns the lists of channel IDs from any
        'featured channels' shelves this channel has set up on their
        homepage. Not every channel sets this up, so an empty list back
        is common and not an error -- it just means this channel hasn't
        curated a featured-channels shelf."""
        data = self._get(
            "channelSections",
            {"part": "contentDetails", "channelId": channel_id},
        )
        shelves = []
        for item in data.get("items", []):
            channels = item.get("contentDetails", {}).get("channels")
            if channels:
                shelves.append(channels)
        return shelves
