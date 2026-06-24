"""
Auto-discovery: finds new channels worth tracking, without ever calling
the expensive search.list endpoint.

TWO DISCOVERY METHODS
-----------------------
1. Description mentions (FREE) -- creators constantly reference other
   channels in video descriptions: collab partners, podcast guests,
   "channel I mentioned: @handle". We already have this text from
   videos.list (no extra API call), so scanning it costs nothing.

2. Channel sections (1 quota unit per tracked channel) -- some channels
   set up an official "Featured channels" shelf on their homepage. When
   they have, channelSections.list hands us real channel_ids directly,
   no resolution step needed.

THE EVIDENCE-BASED PROMOTION RULE
-----------------------------------
A single mention isn't enough signal -- one creator might shout out a
totally unrelated channel once. We wait until a candidate has been
mentioned by at least N *different* tracked channels (controlled by
min_mentions) before we even spend a quota unit resolving its handle,
and again before we promote it to active tracking. This is the same
"don't trust noise, trust converging evidence" idea behind the outlier
engine's multiplier + z-score double-check.
"""

import re
from typing import List

from src import db
from src.youtube_client import YouTubeClient, VideoSnapshot

# Matches "@SomeHandle" or "youtube.com/@SomeHandle", case-sensitive
# handle characters only (letters, digits, underscore, period, hyphen).
# This intentionally catches some false positives (e.g. non-YouTube
# @mentions) -- that's fine, because resolve_handle() will simply fail
# to find a real channel for those, and they get marked 'rejected'
# rather than causing any real problem.
HANDLE_PATTERN = re.compile(r'(?:youtube\.com/)?@([A-Za-z0-9_.-]{3,30})')


def extract_handles(text: str) -> List[str]:
    """Returns a deduplicated list of @handles found in a block of text."""
    if not text:
        return []
    found = HANDLE_PATTERN.findall(text)
    return sorted(set(found))


def discover_from_descriptions(snapshots: List[VideoSnapshot], source_channel_id: str,
                               db_path=None):
    """
    Scans every video's title + description for @handle mentions and
    records each as a candidate sighting. Completely free -- no API
    calls, since we already have this text from the videos.list call
    that fetched these snapshots in the first place.
    """
    kwargs = {"db_path": db_path} if db_path else {}
    seen_in_this_channel = set()  # don't double-count the same handle
                                    # appearing in 50 videos from ONE channel
    for snap in snapshots:
        text = f"{snap.title} {snap.description}"
        for handle in extract_handles(text):
            if handle.lower() == source_channel_id.lower():
                continue  # a channel mentioning itself isn't a discovery
            if handle in seen_in_this_channel:
                continue
            seen_in_this_channel.add(handle)
            db.add_candidate(
                source_channel_id=source_channel_id,
                discovery_method='description_mention',
                handle=handle,
                **kwargs,
            )


def discover_from_channel_sections(client: YouTubeClient, channel_id: str,
                                   db_path=None) -> int:
    """
    Pulls this channel's featured-channels shelves (if any) and records
    each listed channel as a candidate. Costs 1 quota unit. Returns how
    many channels were found (0 is common -- not every channel sets
    this up).
    """
    kwargs = {"db_path": db_path} if db_path else {}
    shelves = client.get_channel_sections(channel_id)
    count = 0
    for shelf in shelves:
        for found_channel_id in shelf:
            if found_channel_id == channel_id:
                continue
            db.add_candidate(
                source_channel_id=channel_id,
                discovery_method='channel_section',
                channel_id=found_channel_id,
                **kwargs,
            )
            count += 1
    return count


def resolve_pending_handles(client: YouTubeClient, min_mentions: int = 2,
                            quota_budget: int = 20, db_path=None) -> int:
    """
    Spends up to quota_budget quota units resolving handles that have
    crossed the min_mentions threshold (i.e. real cross-channel
    evidence). Returns how many resolved successfully into real
    channel_ids. Handles that don't resolve to a real channel are
    marked 'rejected' so we don't keep retrying them every run.
    """
    kwargs = {"db_path": db_path} if db_path else {}
    candidates = db.get_pending_candidates(min_mentions=min_mentions, **kwargs)

    resolved_count = 0
    spent = 0
    for candidate in candidates:
        if spent >= quota_budget:
            break
        channel_id = client.resolve_handle(candidate["handle"])
        spent += 1
        db.mark_candidate_resolved(candidate["handle"], channel_id, **kwargs)
        if channel_id:
            resolved_count += 1
    return resolved_count


def promote_resolved_candidates(client: YouTubeClient, min_mentions: int = 2,
                                db_path=None) -> List[str]:
    """
    Promotes any resolved candidate with enough mentions into active
    tracking. Fetches the channel's title/subscriber count so the
    channels table has real data from the start. Returns the list of
    newly-promoted channel_ids (titles included via a quick lookup).
    """
    kwargs = {"db_path": db_path} if db_path else {}
    promotable = db.get_promotable_candidates(min_mentions=min_mentions, **kwargs)

    promoted = []
    for candidate in promotable:
        channel_id = candidate["channel_id"]
        try:
            info = client.get_channel(channel_id)
            title, subs = info.title, info.subscriber_count
        except Exception:
            title, subs = "", 0
        db.promote_candidate(channel_id, title=title, subscriber_count=subs, **kwargs)
        promoted.append(f"{title or channel_id} (mentioned by {candidate['mention_count']} channels)")
    return promoted
