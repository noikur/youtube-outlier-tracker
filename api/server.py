"""
Local API server -- the bridge between the Chrome extension and the
Python backend we've already built. Run it with: python run_api.py

The extension sends YouTube video IDs it sees on your homepage or any
other YouTube page. This server:
  1. Fetches view counts for those videos (one batched API call)
  2. Checks whether we have enough baseline data for their channels
  3. If not, fetches the channel's recent history (3 units per new channel)
  4. Runs the same outlier engine we built earlier
  5. Returns a score for each video that the extension overlays as a badge

QUOTA MATH FOR THE EXTENSION USE CASE
---------------------------------------
Your homepage shows ~20 videos. Best case (all channels known):
  1 unit (videos.list to get view counts)

Worst case (all 20 videos from 20 brand-new channels):
  1 unit (initial videos.list)
+ 20 * 3 units (channels.list + playlistItems.list + videos.list per channel)
= 61 units per full homepage load

After that first load, every channel is cached in the DB. Subsequent
visits cost 1 unit total, regardless of how many channels appear.
10,000 unit daily budget makes this extremely comfortable.
"""

import os
from datetime import datetime, timezone
from typing import List, Optional

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from src.youtube_client import YouTubeClient, QuotaTracker, YouTubeAPIError
from src.outlier_engine import score_video
from src import db

app = FastAPI(title="YouTube Outlier Tracker API", version="1.0")

# Allow the Chrome extension (which runs in a browser context) to call
# this local server. Without this, the browser blocks cross-origin requests
# even to localhost.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET", "DELETE", "OPTIONS"],
    allow_headers=["*"],
    allow_credentials=False,
)

from fastapi import Request
from fastapi.responses import Response

@app.options("/{rest_of_path:path}")
async def preflight_handler(rest_of_path: str, request: Request):
    return Response(
        status_code=200,
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
            "Access-Control-Allow-Headers": "*",
        }
    )

# Videos with fewer than this many total views are brand-new and can't
# be scored meaningfully yet (a 200-view video will look like a massive
# outlier on any channel just because it hasn't had time to accumulate
# views -- that's noise, not signal).
MIN_VIEWS_TO_SCORE = 500


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ScoreRequest(BaseModel):
    video_ids: List[str]


class VideoScore(BaseModel):
    video_id: str
    video_title: str
    channel_id: str
    channel_title: str
    is_outlier: bool
    multiplier: Optional[float] = None
    z_score: Optional[float] = None
    badge_text: str = ""
    status: str  # 'scored' | 'insufficient_data' | 'too_new' | 'error'


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/api/health")
def health():
    """The extension polls this on startup to check the server is running."""
    db.init_db()
    outliers = db.get_all_outliers()
    return {
        "status": "ok",
        "db": str(db.DB_PATH),
        "tracked_channels": len(db.get_active_channel_ids()),
        "outliers_logged": len(outliers),
    }


@app.post("/api/score")
def score_videos_endpoint(request: ScoreRequest):
    """
    Main endpoint. Receives a list of video IDs visible on the current
    YouTube page and returns outlier scores for each one.
    """
    if not request.video_ids:
        return {"results": {}, "quota_used": 0}

    db.init_db()
    quota = QuotaTracker()
    client = YouTubeClient(quota=quota)
    results = {}

    # ------------------------------------------------------------------
    # Step 1: Fetch current stats for all requested videos in ONE call.
    # This gives us view_count and channel_id for each video.
    # ------------------------------------------------------------------
    try:
        snapshots = client.get_video_stats(request.video_ids[:50])
    except YouTubeAPIError as e:
        return {"results": {}, "error": str(e), "quota_used": quota.used}

    # Save to DB so polling runs and the extension share the same data
    for snap in snapshots:
        db.save_snapshot(
            video_id=snap.video_id,
            channel_id=snap.channel_id,
            title=snap.title,
            view_count=snap.view_count,
            like_count=snap.like_count,
            comment_count=snap.comment_count,
            published_at=snap.published_at,
        )

    # Group videos by channel
    channel_groups: dict = {}
    for snap in snapshots:
        channel_groups.setdefault(snap.channel_id, []).append(snap)

    # ------------------------------------------------------------------
    # Step 2: For each channel, ensure we have enough baseline data.
    # If we've already seen this channel before (tracked or previously
    # visited), we use the DB. If not, we fetch it now.
    # ------------------------------------------------------------------
    channel_titles = {}

    for channel_id, channel_snaps in channel_groups.items():
        history = db.get_snapshot_history_for_scoring(channel_id)

        if len(history) < 15:
            # Not enough baseline data -- fetch this channel's recent videos
            try:
                channel_info = client.get_channel(channel_id)
                channel_titles[channel_id] = channel_info.title
                db.upsert_channel(channel_id, channel_info.title,
                                  channel_info.subscriber_count)

                video_ids = client.get_recent_video_ids(
                    channel_info.uploads_playlist_id, max_results=30)
                more_snaps = client.get_video_stats(video_ids)

                for snap in more_snaps:
                    db.save_snapshot(
                        video_id=snap.video_id,
                        channel_id=snap.channel_id,
                        title=snap.title,
                        view_count=snap.view_count,
                        like_count=snap.like_count,
                        comment_count=snap.comment_count,
                        published_at=snap.published_at,
                    )
                history = db.get_snapshot_history_for_scoring(channel_id)
            except YouTubeAPIError:
                pass
        else:
            channel_titles[channel_id] = (
                db.get_channel_title(channel_id) or channel_id
            )

        # --------------------------------------------------------------
        # Step 3: Score each video from this channel 
        # --------------------------------------------------------------
        channel_title = channel_titles.get(channel_id, channel_id)
        baseline_views = [v for _, v in history]

        for snap in channel_snaps:
            if snap.view_count < MIN_VIEWS_TO_SCORE:
                results[snap.video_id] = VideoScore(
                    video_id=snap.video_id,
                    video_title=snap.title,
                    channel_id=channel_id,
                    channel_title=channel_title,
                    is_outlier=False,
                    badge_text="",
                    status="too_new",
                ).model_dump()
                continue

            # Exclude this video's own view count from its baseline so
            # a video isn't partly scored against itself
            this_baseline = [v for vid_id, v in history
                             if vid_id != snap.video_id][:20]
            
            if len(this_baseline) < 10:
                results[snap.video_id] = VideoScore(
                    video_id=snap.video_id,
                    video_title=snap.title,
                    channel_id=channel_id,
                    channel_title=channel_title,
                    is_outlier=False,
                    badge_text="",
                    status="insufficient_data",
                ).model_dump()
                continue

            baseline_mean = sum(this_baseline) / len(this_baseline)
            if baseline_mean < 1000:
                results[snap.video_id] = VideoScore(
                    video_id=snap.video_id,
                    video_title=snap.title,
                    channel_id=channel_id,
                    channel_title=channel_title,
                    is_outlier=False,
                    badge_text="",
                    status="insufficient_data",
                ).model_dump()
                continue
            try:
                scored = score_video(
                    candidate_views=snap.view_count,
                    candidate_title=snap.title,
                    candidate_id=snap.video_id,
                    baseline_views=this_baseline,
                )

                # Badge text: emoji + multiplier for outliers only
                if scored.is_outlier:
                    if scored.multiplier >= 10:
                        badge_text = f"🔥 {scored.multiplier}x"
                    elif scored.multiplier >= 3:
                        badge_text = f"⚡ {scored.multiplier}x"
                    else:
                        badge_text = f"↑ {scored.multiplier}x"
                    db.log_outlier(snap.video_id, channel_id, snap.title,
                                   scored.multiplier, scored.z_score,
                                   snap.view_count)
                else:
                    badge_text = ""

                results[snap.video_id] = VideoScore(
                    video_id=snap.video_id,
                    video_title=snap.title,
                    channel_id=channel_id,
                    channel_title=channel_title,
                    is_outlier=scored.is_outlier,
                    multiplier=scored.multiplier,
                    z_score=scored.z_score,
                    badge_text=badge_text,
                    status="scored",
                ).model_dump()

            except ValueError:
                results[snap.video_id] = VideoScore(
                    video_id=snap.video_id,
                    video_title=snap.title,
                    channel_id=channel_id,
                    channel_title=channel_title,
                    is_outlier=False,
                    badge_text="",
                    status="insufficient_data",
                ).model_dump()

    return {"results": results, "quota_used": quota.used}

from fastapi.responses import HTMLResponse

@app.get("/api/stats")
def get_stats():
    """Returns summary stats for the dashboard."""
    db.init_db()
    outliers = db.get_all_outliers()
    active = db.get_active_channel_ids()
    return {
        "channels_tracked": len(active),
        "outliers_logged": len(outliers),
        "top_multiplier": max((o["multiplier"] for o in outliers), default=0),
        "outliers": [
            {
                "video_id": o["video_id"],
                "title": o["title"],
                "channel_id": o["channel_id"],
                "channel_title": db.get_channel_title(o["channel_id"]) or o["channel_id"],
                "multiplier": o["multiplier"],
                "z_score": o["z_score"],
                "view_count": o["view_count"],
                "first_seen_at": o["first_seen_at"],
                "last_seen_at": o["last_seen_at"],
            }
            for o in outliers[:50]
        ]
    }

@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    """Serves the dashboard webpage."""
    return HTMLResponse(content=open("dashboard.html").read())

@app.delete("/api/outliers/clean")
def clean_outliers():
    """Remove outliers from channels with insufficient baseline quality."""
    with db.get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("""
                DELETE FROM outlier_log
                WHERE video_id IN (
                    SELECT o.video_id
                    FROM outlier_log o
                    JOIN (
                        SELECT channel_id,
                               AVG(view_count) as avg_views,
                               COUNT(DISTINCT video_id) as video_count
                        FROM video_snapshots
                        GROUP BY channel_id
                    ) stats ON o.channel_id = stats.channel_id
                    WHERE stats.avg_views < 1000
                    OR stats.video_count < 10
                )
            """)
            deleted = cur.rowcount
    return {"deleted": deleted}
