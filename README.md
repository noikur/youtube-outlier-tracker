# YouTube Outlier Tracker

A full-stack tool that detects when a YouTube video dramatically
outperforms its channel's own historical baseline — and explains
*why* it worked using AI.

## Live Demo

**Dashboard:** https://youtube-outlier-tracker-production.up.railway.app/dashboard

The Chrome extension overlays real-time badges directly onto YouTube
thumbnails as you browse. Click any outlier on the dashboard to get
a Claude-powered breakdown of why it outperformed and what to make next.

## What it does

- Detects outlier videos using z-score and multiplier statistics
  relative to each channel's own recent baseline (not YouTube's
  generic trending algorithm)
- Scores any channel it encounters automatically — no manual setup
- Filters out noise (Topic channels, high-variance baselines,
  insufficient history)
- Explains outliers with AI: why it worked, the reusable pattern,
  and a specific next video idea
- Tracks velocity over time so rising videos are flagged before
  they fully break out

## Stack

- **Backend:** Python, FastAPI, deployed on Railway
- **Database:** PostgreSQL
- **Extension:** Chrome Extension (Manifest V3), content script,background service worker
- **AI:** Anthropic Claude API (explanation layer)
- **Data:** YouTube Data API v3 (quota-optimised: ~3 units per channel, never uses search.list)
- **Testing:** pytest, 39 tests, zero network dependency in CI

## Setup (local development)

```bash
git clone https://github.com/noikur/youtube-outlier-tracker
cd youtube-outlier-tracker
python -m venv venv
venv\Scripts\activate        # Windows
pip install -r requirements.txt
pip install youtube-transcript-api
cp .env.example .env
# Add YOUTUBE_API_KEY and ANTHROPIC_API_KEY to .env
python run_api.py
```
Load the `extension/` folder as an unpacked extension in Chrome
(`chrome://extensions` → Developer mode → Load unpacked).

## Key technical decisions

**Why z-score + multiplier, not just views?** 
A consistent channel with low variance produces huge z-scores from small bumps. A chaotic
channel needs a bigger multiplier to register as genuinely unusual. Both checks together filter 
out noise that either metric alone misses.

**Why playlist walking instead of search.list?** 
search.list costs 100 quota units per call. channels.list + playlistItems.list +
videos.list (batched 50 at a time) costs 3 units total per channel. Same data, 97% cheaper.

**Why Postgres instead of SQLite?** 
Railway's filesystem resets on every deployment. SQLite would wipe the database on every push.
Postgres runs as a separate service that persists independently.

## License
MIT
