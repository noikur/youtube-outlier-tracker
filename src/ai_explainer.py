"""
AI Explanation Layer.

Takes a detected outlier video and the channel's recent content history,
and asks Claude to answer the three questions YouTube's own analytics
can't: WHY did it work, WHAT pattern does it fit, and WHAT should the
creator make next based on that?

TRANSCRIPT APPROACH
-------------------
YouTube's official captions API requires OAuth -- massive setup overhead
for what we need. Instead we use youtube-transcript-api, which fetches
auto-generated captions using just the video ID. It works on any video
where YouTube has generated auto-captions (the vast majority of
English-language content). If a video has no captions, we fall back to
analysing just the title and channel context, which is still useful.

WHAT WE SEND TO CLAUDE
-----------------------
- The outlier video's title
- Its transcript (first ~2,000 words -- the hook and first half of the
  video matter most; the outro rarely explains why something went viral)
- The channel's last 10 video titles as "what's normal for this channel"
  context, so Claude can see what's DIFFERENT about the outlier, not
  just what it contains in isolation
- The raw outlier score (multiplier + z-score) so Claude knows the
  magnitude of the outperformance it's explaining

WHAT WE GET BACK
-----------------
A structured breakdown with three sections:
  1. WHY -- specific reasons this video outperformed (topic, format,
     title structure, timing, emotional trigger, etc.)
  2. PATTERN -- the reusable insight abstracted from this specific video
     (e.g. "tribal comparison content performs above baseline on this
     channel" rather than just "this video did well")
  3. NEXT -- one concrete next-video idea that applies the same pattern
     to a fresh angle the channel hasn't done yet
"""

import os
import textwrap
from dataclasses import dataclass
from typing import List, Optional

import requests

try:
    from youtube_transcript_api import YouTubeTranscriptApi, NoTranscriptFound, TranscriptsDisabled
    TRANSCRIPT_AVAILABLE = True
except ImportError:
    TRANSCRIPT_AVAILABLE = False


@dataclass
class OutlierExplanation:
    video_id: str
    title: str
    multiplier: float
    z_score: float
    why: str
    pattern: str
    next_idea: str
    transcript_used: bool

    def display(self):
        width = 72
        print("\n" + "=" * width)
        print(f"  OUTLIER ANALYSIS")
        print(f"  \"{self.title}\"")
        print(f"  {self.multiplier}x views  |  z={self.z_score}")
        print("=" * width)

        print("\n WHY IT WORKED")
        print("-" * width)
        for line in textwrap.wrap(self.why, width - 2):
            print(f"  {line}")

        print("\n THE REUSABLE PATTERN")
        print("-" * width)
        for line in textwrap.wrap(self.pattern, width - 2):
            print(f"  {line}")

        print("\n NEXT VIDEO IDEA")
        print("-" * width)
        for line in textwrap.wrap(self.next_idea, width - 2):
            print(f"  {line}")

        if not self.transcript_used:
            print("\n  (Note: no transcript available -- analysis based on "
                  "title and channel context only)")
        print("=" * width + "\n")


def fetch_transcript(video_id: str, max_words: int = 2000) -> Optional[str]:
    """
    Fetches auto-generated captions for a video. Returns a plain-text
    string truncated to max_words, or None if no transcript is available.

    We cap at 2,000 words because the hook and first half of a video
    are what drive the click-through-and-retain behaviour that makes
    something go viral. The outro rarely matters for explaining
    outperformance, and shorter inputs keep the Claude API call cheaper
    and faster.
    """
    if not TRANSCRIPT_AVAILABLE:
        return None
    try:
        transcript = YouTubeTranscriptApi.get_transcript(video_id)
        text = " ".join(entry["text"] for entry in transcript)
        words = text.split()
        return " ".join(words[:max_words]) if len(words) > max_words else text
    except Exception:
        # NoTranscriptFound, TranscriptsDisabled, VideoUnavailable, etc.
        return None


def build_prompt(
    title: str,
    video_id: str,
    multiplier: float,
    z_score: float,
    transcript: Optional[str],
    recent_titles: List[str],
) -> str:
    channel_context = "\n".join(f"  - {t}" for t in recent_titles[:10])
    transcript_section = (
        f"TRANSCRIPT (first ~2,000 words):\n{transcript}"
        if transcript
        else "TRANSCRIPT: Not available -- base your analysis on the title and channel context."
    )

    return f"""You are an expert YouTube content analyst helping a small creator understand
why one of their videos dramatically outperformed their usual output.

OUTLIER VIDEO
Title: "{title}"
Performance: {multiplier}x their normal view count (z-score: {z_score})
Video ID: {video_id}

CHANNEL'S RECENT NORMAL CONTENT (what baseline looks like for them):
{channel_context}

{transcript_section}

Analyse why this specific video outperformed. Be concrete and specific --
reference actual words from the title or transcript, not generic advice.

Respond in exactly this format with these three labelled sections:

WHY IT WORKED:
[2-4 specific reasons this video outperformed. Reference the actual title
wording, specific topics, emotional triggers, or format choices. Explain
WHY those things work psychologically or algorithmically, not just what
they are.]

THE REUSABLE PATTERN:
[One paragraph. Abstract the insight away from this specific video into
a principle the creator can apply repeatedly. E.g. not "banter content
worked" but "tribal comparison formats (X era vs Y era, best ever XI)
consistently outperform this channel's baseline because they trigger
strong opinion-sharing behaviour in football audiences."]

NEXT VIDEO IDEA:
[One specific video concept that applies the same pattern to a fresh
angle this channel hasn't done yet. Give it an actual working title,
not a vague direction. Be specific enough that the creator could start
scripting it today.]"""


def explain_outlier(
    video_id: str,
    title: str,
    multiplier: float,
    z_score: float,
    recent_titles: List[str],
    api_key: Optional[str] = None,
) -> OutlierExplanation:
    """
    Main entry point. Fetches the transcript, builds the prompt, calls
    Claude, and parses the structured response back into an
    OutlierExplanation object.
    """
    anthropic_key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not anthropic_key:
        raise ValueError(
            "No Anthropic API key found. Set ANTHROPIC_API_KEY in your "
            ".env file. Get one at console.anthropic.com -- free credits "
            "are available for new accounts."
        )

    transcript = fetch_transcript(video_id)
    prompt = build_prompt(title, video_id, multiplier, z_score, transcript, recent_titles)

    response = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": anthropic_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": "claude-sonnet-4-6",
            "max_tokens": 1000,
            "messages": [{"role": "user", "content": prompt}],
        },
    )

    if not response.ok:
        raise RuntimeError(f"Claude API error [{response.status_code}]: {response.text[:300]}")

    raw = response.json()["content"][0]["text"]

    # Parse the three labelled sections out of Claude's response
    why = _extract_section(raw, "WHY IT WORKED:")
    pattern = _extract_section(raw, "THE REUSABLE PATTERN:")
    next_idea = _extract_section(raw, "NEXT VIDEO IDEA:")

    return OutlierExplanation(
        video_id=video_id,
        title=title,
        multiplier=multiplier,
        z_score=z_score,
        why=why,
        pattern=pattern,
        next_idea=next_idea,
        transcript_used=transcript is not None,
    )


def _extract_section(text: str, label: str) -> str:
    """
    Pulls the content between one section label and the next, stripping
    leading/trailing whitespace. Falls back to the full response if the
    label isn't found (Claude occasionally rewrites labels slightly).
    """
    if label not in text:
        return text.strip()
    start = text.index(label) + len(label)
    # Find where the next section starts (next line that ends with a colon
    # and is in all-caps), or take everything to the end.
    remaining = text[start:]
    next_section = None
    for other_label in ["WHY IT WORKED:", "THE REUSABLE PATTERN:", "NEXT VIDEO IDEA:"]:
        if other_label != label and other_label in remaining:
            pos = remaining.index(other_label)
            if next_section is None or pos < next_section:
                next_section = pos
    if next_section:
        remaining = remaining[:next_section]
    return remaining.strip()
