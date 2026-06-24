"""
Run this occasionally (not every poll) to grow your tracked channel
list automatically. It scans your currently active channels for
mentions of other channels, builds up evidence across multiple runs,
and promotes anything that's been independently mentioned by 2+
different tracked channels into active tracking.

Usage:
    python -m src.discover

Unlike src/main.py, this DOES spend some extra quota (channelSections
calls, and handle resolution), so it's intentionally a separate command
you run when you want to grow your list -- e.g. once a day or once a
week -- rather than something that happens automatically on every poll.
"""

from dotenv import load_dotenv
load_dotenv()

from src import db
from src.config import TRACKED_CHANNEL_IDS, MAX_VIDEOS_PER_CHANNEL
from src.youtube_client import YouTubeClient, QuotaTracker, YouTubeAPIError
from src.discovery import (
    discover_from_descriptions,
    discover_from_channel_sections,
    resolve_pending_handles,
    promote_resolved_candidates,
)

MIN_MENTIONS_TO_RESOLVE = 2  # don't spend quota until 2+ channels mention it
MIN_MENTIONS_TO_PROMOTE = 2  # don't auto-track until 2+ channels mention it
HANDLE_RESOLUTION_BUDGET = 20  # max quota units to spend resolving handles per run


def run():
    db.init_db()

    # Seed the DB with anything from config.py that isn't there yet,
    # so manually-added channels and auto-discovered ones live in the
    # same place going forward.
    for channel_id in TRACKED_CHANNEL_IDS:
        db.upsert_channel(channel_id, title="", subscriber_count=0,
                          status='active', discovery_source='manual')

    active_ids = db.get_active_channel_ids()
    if not active_ids:
        print("No active channels yet. Add some to src/config.py and run "
              "src/main.py at least once first.")
        return

    quota = QuotaTracker()
    client = YouTubeClient(quota=quota)

    print(f"Scanning {len(active_ids)} tracked channel(s) for mentions of other channels...\n")

    section_finds = 0
    for channel_id in active_ids:
        try:
            # Free: scan descriptions of recent videos for @handles
            snapshots = client.get_channel_videos(channel_id, MAX_VIDEOS_PER_CHANNEL)
            discover_from_descriptions(snapshots, channel_id)

            # 1 quota unit: check for an official featured-channels shelf
            section_finds += discover_from_channel_sections(client, channel_id)
        except YouTubeAPIError as e:
            print(f"  [{channel_id}] skipped: {e}")
            continue

    print(f"Found {section_finds} channel(s) via featured-channel shelves.")
    print("Resolving handles that have been mentioned by 2+ different channels...\n")

    resolved = resolve_pending_handles(
        client, min_mentions=MIN_MENTIONS_TO_RESOLVE,
        quota_budget=HANDLE_RESOLUTION_BUDGET,
    )
    print(f"Resolved {resolved} new handle(s) into real channels.\n")

    print("Promoting candidates with enough evidence...\n")
    promoted = promote_resolved_candidates(client, min_mentions=MIN_MENTIONS_TO_PROMOTE)

    if promoted:
        print(f"Added {len(promoted)} new channel(s) to active tracking:")
        for p in promoted:
            print(f"  + {p}")
    else:
        print("No candidates had enough cross-channel evidence to promote yet.")
        print("Run this again after main.py has collected more video data --")
        print("evidence builds up over multiple runs.")

    print(f"\nQuota used this run: {quota.summary()}")


if __name__ == "__main__":
    run()
