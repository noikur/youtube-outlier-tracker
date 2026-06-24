"""
Proves the outlier engine works, using fake (but realistic) data --
no API key, no network, no quota. This is the same shape of data
the real YouTube client will produce later: a list of videos, newest
first, each with an id/title/view_count.

Run it: python3 demo.py
"""

from src.outlier_engine import score_channel_videos

# A small football channel that normally gets 11K-16K views, except for
# one video that clearly broke out.
synthetic_videos = [
    {"id": "vid_newest", "title": "The TRUTH About England's 0-0 Draw With Ghana", "view_count": 482_000},
    {"id": "vid_2", "title": "Our EARLY 1-20 Premier League Predictions 25/26! ", "view_count": 14_200},
    {"id": "vid_3", "title": "Q&A With My Subscribers", "view_count": 11_800},
    {"id": "vid_4", "title": "MY PREMIER LEAGUE 2022/23 PREDICTIONS", "view_count": 15_900},
    {"id": "vid_5", "title": "The END Of Erik Ten Hag?", "view_count": 13_100},
    {"id": "vid_6", "title": "IS THIS MAN UTD'S MOST EMBARRASSING DEFEAT?? ", "view_count": 12_400},
    {"id": "vid_7", "title": "THE FIRST EVER ALL ENGLISH UCL FINAL ", "view_count": 10_900},
]

print(f"{'':12} {'TITLE':45} {'MULTIPLIER':10} {'Z-SCORE':8}")
print("-" * 80)
for result in score_channel_videos(synthetic_videos, baseline_window=5):
    flag = "OUTLIER" if result.is_outlier else "normal"
    print(f"{flag:12} {result.title[:43]:45} {result.multiplier}x{'':6} {result.z_score}")

print()
print("Baseline for the outlier video was calculated from the 5 videos")
print("right after it (older), which is exactly what you'd want: judge")
print("a video against what was 'normal' for that channel at the time,")
print("not against numbers that didn't exist yet.")
