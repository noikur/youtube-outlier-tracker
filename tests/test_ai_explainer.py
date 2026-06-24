"""
Tests for ai_explainer.py -- mocking both the transcript fetch and the
Claude API call so these run instantly with no network or API keys.
"""

from unittest.mock import patch, MagicMock
from src.ai_explainer import explain_outlier, _extract_section, build_prompt


FAKE_CLAUDE_RESPONSE = """WHY IT WORKED:
The title uses a superlative-question format ("BEST... OF ALL TIME?!") combined
with a strong tribal signal (the Italian flag emoji) that immediately activates
the audience's desire to defend or debate. The "Banter Era" framing taps into
a specific cultural moment in football fandom that feels current and shareable.
The laughing emoji signals the tone is entertaining rather than analytical,
lowering the barrier to click for casual fans who wouldn't engage with a
stats-heavy breakdown.

THE REUSABLE PATTERN:
Tribal comparison content -- framing a video as a definitive verdict on a
contested fan debate -- consistently outperforms this channel's baseline
because it triggers strong opinion-sharing behaviour. When a title implies
"here's the answer to something you already have feelings about," football
audiences click to confirm, challenge, or share their disagreement. The emoji
combination (laughing + flag) signals entertainment value while the ALL CAPS
superlative signals stakes.

NEXT VIDEO IDEA:
"The WORST Banter Era Signing OF ALL TIME?! 😬🏴󠁧󠁢󠁥󠁮󠁧󠁿" -- flip the format from
best to worst, apply it to a different footballing nation's Banter Era to
broaden the debate beyond Italy while keeping the same title structure that
just proved it works. The inversion (worst instead of best) adds fresh
controversy while the proven template carries the click-through."""


@patch("src.ai_explainer.fetch_transcript", return_value="This is the transcript text")
@patch("requests.post")
def test_explain_outlier_parses_three_sections(mock_post, mock_transcript):
    mock_post.return_value = MagicMock(
        ok=True,
        json=lambda: {"content": [{"text": FAKE_CLAUDE_RESPONSE}]},
    )

    result = explain_outlier(
        video_id="test_vid",
        title="The BEST Banter Era Team OF ALL TIME?!",
        multiplier=106.71,
        z_score=172.36,
        recent_titles=["Regular video 1", "Regular video 2"],
        api_key="fake_key",
    )

    assert result.why != ""
    assert result.pattern != ""
    assert result.next_idea != ""
    assert result.transcript_used is True
    assert result.multiplier == 106.71
    assert "tribal" in result.pattern.lower()  # Claude mentioned this pattern


@patch("src.ai_explainer.fetch_transcript", return_value=None)
@patch("requests.post")
def test_explain_outlier_handles_missing_transcript(mock_post, mock_transcript):
    mock_post.return_value = MagicMock(
        ok=True,
        json=lambda: {"content": [{"text": FAKE_CLAUDE_RESPONSE}]},
    )

    result = explain_outlier(
        video_id="test_vid",
        title="Some video",
        multiplier=5.0,
        z_score=3.2,
        recent_titles=["Video A", "Video B"],
        api_key="fake_key",
    )
    # Should still return a result -- just falls back to title-only analysis
    assert result.transcript_used is False
    assert result.why != ""


def test_extract_section_handles_all_three_labels():
    why = _extract_section(FAKE_CLAUDE_RESPONSE, "WHY IT WORKED:")
    pattern = _extract_section(FAKE_CLAUDE_RESPONSE, "THE REUSABLE PATTERN:")
    next_idea = _extract_section(FAKE_CLAUDE_RESPONSE, "NEXT VIDEO IDEA:")

    assert "superlative" in why
    assert "Tribal comparison" in pattern
    assert "WORST" in next_idea
    # Sections should not bleed into each other
    assert "THE REUSABLE PATTERN" not in why
    assert "NEXT VIDEO IDEA" not in pattern


def test_build_prompt_includes_key_context():
    prompt = build_prompt(
        title="Test Video Title",
        video_id="abc123",
        multiplier=10.5,
        z_score=8.3,
        transcript="This is the transcript",
        recent_titles=["Normal video 1", "Normal video 2", "Normal video 3"],
    )
    assert "Test Video Title" in prompt
    assert "10.5x" in prompt
    assert "Normal video 1" in prompt
    assert "This is the transcript" in prompt


def test_missing_api_key_raises_clear_error(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    try:
        explain_outlier("vid", "title", 5.0, 3.0, [], api_key=None)
        assert False, "should have raised ValueError"
    except ValueError as e:
        assert "ANTHROPIC_API_KEY" in str(e)
