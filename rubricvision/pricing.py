"""Cost estimates (before grading) and real costs (from Gemini's token counts)."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Config
from .media import Submission
from .rubric import Rubric

BASE_OUTPUT_TOKENS = 3000
OUTPUT_TOKENS_PER_ITEM = 220


def estimated_output_tokens(rubric: Rubric) -> int:
    """Answer plus thinking: grows with the number of rubric items."""
    return BASE_OUTPUT_TOKENS + OUTPUT_TOKENS_PER_ITEM * rubric.item_count


def input_tokens_per_second(config: Config, accuracy: str) -> int:
    model = config.model
    per_frame = model["video_tokens_per_frame"][accuracy]
    return int(per_frame) + int(model["audio_tokens_per_second"])  # 1 frame per second


def assumed_duration_s(sub: Submission, rubric: Rubric, config: Config) -> tuple[float, bool]:
    """(seconds, is_assumption). YouTube links have no known length before grading."""
    if sub.duration_s:
        return sub.duration_s, False
    if rubric.max_duration_s:
        return float(rubric.max_duration_s), True
    return float(config.get("youtube_assumed_minutes_without_rubric_limit")) * 60, True


@dataclass
class Estimate:
    usd: float
    assumed_count: int  # submissions whose length was assumed
    assumed_minutes: float


def estimate(subs: list[Submission], rubric: Rubric, config: Config, accuracy: str) -> Estimate:
    price_in, price_out = config.prices()
    per_second = input_tokens_per_second(config, accuracy)
    out_tokens = estimated_output_tokens(rubric)
    total = 0.0
    assumed = 0
    assumed_minutes = 0.0
    for sub in subs:
        seconds, is_assumed = assumed_duration_s(sub, rubric, config)
        if is_assumed:
            assumed += 1
            assumed_minutes = seconds / 60
        total += seconds * per_second / 1e6 * price_in + out_tokens / 1e6 * price_out
    return Estimate(usd=total, assumed_count=assumed, assumed_minutes=assumed_minutes)


def actual_cost(config: Config, prompt_tokens: int, output_tokens: int) -> float:
    price_in, price_out = config.prices()
    return prompt_tokens / 1e6 * price_in + output_tokens / 1e6 * price_out


def estimate_minutes_range(subs: list[Submission]) -> tuple[int, int]:
    """Rough total time: upload between 50 and 10 Mbps, plus analysis."""
    total_bytes = sum(s.size_bytes or 0 for s in subs if not s.is_youtube)
    fast = total_bytes * 8 / 50e6 / 60
    slow = total_bytes * 8 / 10e6 / 60
    return max(1, round(fast + 2)), max(2, round(slow + 4))


def format_usd(value: float) -> str:
    return f"US${value:,.2f}"
