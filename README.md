# YouTube Outlier Tracker

A learning project: detects when a video on a tracked YouTube channel
dramatically outperforms that channel's own recent normal output (an
"outlier"), using only the free YouTube Data API.

## How it works

1. **`src/youtube_client.py`** — talks to YouTube's API, designed
   around the quota system (1-unit calls only, batched, never
   `search.list`).
2. **`src/outlier_engine.py`** — pure statistics, no network. Compares
   each video to a rolling baseline of that same channel's older
   videos (mean + standard deviation), flags anything that's both a
   big multiplier AND a big z-score above normal.
3. **`src/main.py`** — wires the two together: pull data, score it,
   print outliers.

The engine and the client are deliberately separate. The engine has
zero network dependency, so it's instantly testable; the client is
the only part that needs a real API key.

## Setup

```bash
cd youtube-outlier-tracker
python3 -m venv venv
source venv/bin/activate          # on Windows: venv\Scripts\activate
pip install -r requirements.txt
```

### Get a YouTube Data API key (takes ~5 minutes, free)

1. Go to [console.cloud.google.com](https://console.cloud.google.com)
   and create a new project (or use an existing one).
2. In the search bar, find **"YouTube Data API v3"** and click
   **Enable**.
3. Go to **APIs & Services → Credentials → Create Credentials → API key**.
4. Copy the key. (Optional but recommended: click "Restrict key" and
   limit it to the YouTube Data API v3, so a leaked key can't be used
   for other Google services.)
5. Copy `.env.example` to `.env` and paste your key in:
   ```bash
   cp .env.example .env
   # then edit .env and set YOUTUBE_API_KEY=your_actual_key
   ```

You get **10,000 free quota units per day**, no billing setup
required. This project is designed to use roughly 3 units per tracked
channel per run, so even tracking 50 channels costs ~150 units — you
have a lot of headroom.

### Add channels to track

Edit `src/config.py` and add real channel IDs (the `UC...` ones, not
`@handles`) to `TRACKED_CHANNEL_IDS`. Pick a niche you actually know,
so you can tell whether the results make sense.

### Run it

```bash
# Run the tests (no API key or network needed for these):
python3 -m pytest tests/ -v

# Run the synthetic-data demo (no API key needed):
python3 demo.py

# Run the real pipeline against YouTube (needs YOUTUBE_API_KEY set):
python3 -m src.main
```

Note the `-m src.main` — running `python3 src/main.py` directly won't
resolve the `src` package import correctly.

## What's next (not built yet)

- **Persistence**: right now every run recomputes from scratch. The
  next step is a small SQLite database that stores video snapshots
  over time, so we can track *when* a video started breaking out, not
  just whether it currently looks like an outlier.
- **Faster polling on a hot subset**: once we have persistence, we can
  poll a small "currently rising" subset of channels more frequently
  to catch early velocity instead of only flagging videos that have
  already fully matured into obvious outliers.
- **AI explanation layer**: feed a detected outlier's title/transcript
  to Claude and get a short "why this probably worked" breakdown.
- **A simple dashboard**: a web UI instead of printed terminal output.

## A note on the YouTube API Terms of Service

Google's terms restrict storing API data long-term beyond certain
windows and prohibit using multiple projects to multiply your quota.
Worth reading before you build anything you plan to put in front of
real users: https://developers.google.com/youtube/terms/api-services-terms-of-service
