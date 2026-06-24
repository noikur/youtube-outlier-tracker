"""
Main pipeline -- now with persistence.

Every run does three things:
  1. Pull fresh video data from YouTube and save snapshots to the DB
  2. Score for outliers (same as before) and log any found
  3. Check velocity -- flag videos that are gaining views fast across
     multiple runs, even if they haven't hit outlier threshold yet

The "rising videos" section is the new early-detection piece. After
you've run this tool a few times across a few hours, it will start
showing you videos that are accelerating BEFORE they've crossed the
outlier line -- that's the "catch the wave early" angle.
"""

from dotenv import load_dotenv
load_dotenv()

from src.config import TRACKED_CHANNEL_IDS, MAX_VIDEOS_PER_CHANNEL, BASELINE_WINDOW
from src.youtube_client import YouTubeClient, QuotaTracker, YouTubeAPIError
from src.outlier_engine import score_channel_videos
from src.ai_explainer import explain_outlier
from src import db


def run(explain: bool = True):
    # Make sure the database and tables exist before we try to write to them
    db.init_db()

    # Seed any channels from config.py into the DB as 'active' (no-op if
    # already there). This means the DB -- not the config file -- is the
    # real source of truth for what gets polled, which matters once
    # auto-discovery (src/discover.py) starts promoting new channels into
    # active tracking. You'll see those new channels picked up here
    # automatically on the next run, with no config.py edits needed.
    for channel_id in TRACKED_CHANNEL_IDS:
        db.upsert_channel(channel_id, title="", subscriber_count=0,
                          status='active', discovery_source='manual')

    active_ids = db.get_active_channel_ids()
    if not active_ids:
        print("No channels configured yet. Add channel IDs to src/config.py")
        return

    quota = QuotaTracker()
    client = YouTubeClient(quota=quota)

    for channel_id in active_ids:

        # ----------------------------------------------------------------
        # 1. FETCH from YouTube
        # ----------------------------------------------------------------
        try:
            snapshots = client.get_channel_videos(channel_id, MAX_VIDEOS_PER_CHANNEL)
            channel_info = client.get_channel(channel_id)
        except YouTubeAPIError as e:
            print(f"[{channel_id}] skipped: {e}")
            continue

        if not snapshots:
            print(f"[{channel_id}] no videos found")
            continue

        # ----------------------------------------------------------------
        # 2. SAVE to database
        # ----------------------------------------------------------------
        db.upsert_channel(
            channel_id=channel_id,
            title=channel_info.title,
            subscriber_count=channel_info.subscriber_count,
        )

        for s in snapshots:
            db.save_snapshot(
                video_id=s.video_id,
                channel_id=channel_id,
                title=s.title,
                view_count=s.view_count,
                like_count=s.like_count,
                comment_count=s.comment_count,
                published_at=s.published_at,
            )

        # ----------------------------------------------------------------
        # 3. SCORE for outliers
        # ----------------------------------------------------------------
        sorted_snapshots = sorted(snapshots, key=lambda s: s.published_at, reverse=True)
        videos_for_scoring = [
            {"id": s.video_id, "title": s.title, "view_count": s.view_count}
            for s in sorted_snapshots
        ]
        recent_titles = [s.title for s in sorted_snapshots]

        results = score_channel_videos(videos_for_scoring, baseline_window=BASELINE_WINDOW)
        outliers = [r for r in results if r.is_outlier]

        print(f"\n{'='*60}")
        print(f"  {channel_info.title}")
        print(f"  {len(snapshots)} videos checked  |  {len(outliers)} outlier(s) found")
        print(f"{'='*60}")

        # Log outliers to DB so we track when they were first detected
        for o in outliers:
            db.log_outlier(
                video_id=o.video_id,
                channel_id=channel_id,
                title=o.title,
                multiplier=o.multiplier,
                z_score=o.z_score,
                view_count=o.view_count,
            )
            print(f"\n  OUTLIER  {o.multiplier}x  (z={o.z_score})")
            print(f"  \"{o.title}\"")

        # ----------------------------------------------------------------
        # 4. VELOCITY -- rising videos not yet at outlier threshold
        # ----------------------------------------------------------------
        rising = db.get_rising_videos(channel_id, min_velocity=50)
        # Filter out already-confirmed outliers so we don't double-report
        outlier_ids = {o.video_id for o in outliers}
        rising = [v for v in rising if v.video_id not in outlier_ids]

        if rising:
            print(f"\n  RISING (not yet outliers, but gaining fast):")
            for v in rising[:5]:  # top 5 by velocity
                accel = " ACCELERATING" if v.is_accelerating else ""
                print(f"  {int(v.views_per_hour):,} views/hr{accel}  \"{v.title}\"")

        # ----------------------------------------------------------------
        # 5. AI EXPLANATION for outliers
        # ----------------------------------------------------------------
        if outliers and explain:
            print(f"\n  Running AI analysis...")
            for o in outliers:
                try:
                    explanation = explain_outlier(
                        video_id=o.video_id,
                        title=o.title,
                        multiplier=o.multiplier,
                        z_score=o.z_score,
                        recent_titles=[t for t in recent_titles if t != o.title],
                    )
                    explanation.display()
                except Exception as e:
                    print(f"\n  [AI analysis failed for '{o.title}']: {e}")

    print(f"\nYouTube API quota used: {quota.summary()}")
    print(f"Database: {db.DB_PATH}")


if __name__ == "__main__":
    run()
