"""LLM call wrapper and unit-conversion helpers shared by the coach package.

Persona text now lives in coach/personas.py — this module is kept narrow.
"""

import logging

import anthropic

from running_coach_ai.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Unit conversion helpers — used by planner, adapter, conversation, feedback
# ---------------------------------------------------------------------------

_KM_PER_MI = 1.60934
_MI_PER_KM = 1.0 / _KM_PER_MI


def km_to_mi(km: float) -> float:
    return km * _MI_PER_KM


def mi_to_km(mi: float) -> float:
    return mi * _KM_PER_MI


def format_miles(km: float) -> str:
    """Format a km value as a rounded miles string (nearest 0.5 mi under 8, nearest 1 above)."""
    miles = km * _MI_PER_KM
    if miles < 8:
        rounded = round(miles * 2) / 2  # nearest 0.5
    else:
        rounded = round(miles)          # nearest whole mile
    return f"{rounded:.1f} mi" if rounded != int(rounded) else f"{int(rounded)} mi"


def format_pace_mi(min_per_km: float) -> str:
    """Convert min/km pace to a formatted min/mi string."""
    min_per_mi = min_per_km * _KM_PER_MI
    mins = int(min_per_mi)
    secs = int(round((min_per_mi - mins) * 60))
    if secs == 60:
        mins += 1
        secs = 0
    return f"{mins}:{secs:02d}/mi"


def round_to_5(minutes: float) -> int:
    """Round a duration to the nearest 5 minutes."""
    return max(5, round(minutes / 5) * 5)


def call_claude(system_prompt: str, messages: list[dict], max_tokens: int = 4096) -> str:
    """Call Claude API and return the raw response text.

    Args:
        system_prompt: Full assembled system prompt including persona + context.
        messages: List of {"role": "user"|"assistant", "content": "..."} dicts.
        max_tokens: Maximum tokens in the response (default 4096).

    Returns:
        The assistant's response text.
    """
    client = anthropic.Anthropic(api_key=settings.ANTHROPIC_API_KEY)

    try:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=max_tokens,
            system=system_prompt,
            messages=messages,
            timeout=30.0,
        )
        text = response.content[0].text
        if response.stop_reason == "max_tokens":
            logger.warning("Claude response truncated (hit %d token limit)", max_tokens)
        return text
    except anthropic.APITimeoutError as e:
        logger.error("Claude API timeout after 30s: %s", e)
        raise TimeoutError("Coach response timed out — please try again.") from e
    except anthropic.APIError as e:
        logger.error("Claude API error: %s", e)
        raise
