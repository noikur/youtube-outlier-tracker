"""
Project configuration: which channels to track, and basic settings.

For now this is just a Python list -- intentionally simple. Once we add
the database layer (next step), this will move to being something you
manage through a small CLI or the dashboard instead of editing code.
But starting with a plain list keeps this stage focused on "does the
data pipeline work" without also building channel-management UI yet.

HOW TO FIND A CHANNEL ID
-------------------------
Channel IDs look like "UCxxxxxxxxxxxxxxxxxxxxxx" (24 chars, starts with
UC). The easiest way to get one: go to the channel's YouTube page,
view page source (or use a tool like https://commentpicker.com/
youtube-channel-id.php), and look for "channelId". A channel's @handle
(like @mkbhd) is NOT the same as its channel ID -- you need the actual
UC... id for the API calls in this project.
"""

import os

# Replace these with channel IDs in YOUR chosen niche. Pick a niche you
# actually know well enough to judge whether the outlier results make
# sense -- that judgment is what makes this useful rather than just a
# number-generator.
TRACKED_CHANNEL_IDS = [
     "UCSZAEqHbu6E5WP3_IwyDXAA",  # replace with real channel IDs
]

YOUTUBE_API_KEY = os.environ.get("YOUTUBE_API_KEY")

# How many recent videos to pull per channel per run.
MAX_VIDEOS_PER_CHANNEL = 50

# Outlier engine settings
BASELINE_WINDOW = 15  # how many older videos to use as "normal" for comparison

# Get your Anthropic API key at console.anthropic.com
# New accounts get free credits -- enough to run hundreds of analyses.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
