"""scheduler.py 的纯领域测试：不建库、不联网，全程毫秒级。

盯三件事：
1. 四级大白话评级真的映射到 fsrs，间隔被正确推进；
2. **确定性**——enable_fuzzing=False 意味着同样的历史永远算出同样的结果；
3. Card 的"销毁-重建"往返：排期表存的裸字段重建后继续复习，结果必须
   和不销毁直接复习**完全一致**（这是排期表设计的成立前提）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from backend.domain.scheduler import (
    CardState,
    UnknownRatingError,
    card_state_from_row,
    derive_stats,
    new_card_state,
    review,
)

NOW = datetime(2026, 11, 4, 12, 0, 0, tzinfo=timezone.utc)


def test_first_review_advances_due_and_sets_stability() -> None:
    fresh = new_card_state(due=NOW)
    assert fresh.last_review is None and fresh.stability is None

    result = review(fresh, "good", NOW)

    assert result.due > NOW
    assert result.stability is not None
    assert result.last_review == NOW
    # 首次 good 还在学习步里（fsrs 的当天短间隔），不能直接毕业
    assert result.state == "learning"


def test_graduates_after_learning_steps() -> None:
    state = new_card_state(due=NOW)
    for _ in range(3):
        state = review(state, "good", state.due)
    assert state.state == "review", "连续 good 应该走出学习步进入正式复习"
    assert state.due > NOW + timedelta(days=1)


def test_again_sends_review_state_back_to_relearning() -> None:
    state = new_card_state(due=NOW)
    for _ in range(3):
        state = review(state, "good", state.due)
    assert state.state == "review"

    lapsed = review(state, "again", NOW)
    assert lapsed.state == "relearning"


def test_rating_must_be_from_the_vocabulary() -> None:
    fresh = new_card_state(due=NOW)
    with pytest.raises(UnknownRatingError, match="again / hard / good / easy"):
        review(fresh, "记得", NOW)


def test_row_state_must_be_from_the_vocabulary() -> None:
    with pytest.raises(ValueError, match="排期状态"):
        card_state_from_row(
            state="exam_tomorrow", step=0, stability=None, difficulty=None,
            due=NOW, last_review=None,
        )


def test_review_is_deterministic() -> None:
    """enable_fuzzing=False 的硬承诺：同输入同输出。扰动了这条就必挂。"""
    fresh = new_card_state(due=NOW)
    first = review(fresh, "hard", NOW)
    second = review(fresh, "hard", NOW)
    assert first == second


def test_card_rebuild_roundtrip_preserves_scheduling() -> None:
    """排期表方案的成立前提：存出来的裸字段重建后复习，结果和不重建完全一致。"""
    state = new_card_state(due=NOW)
    state = review(state, "good", NOW)
    state = review(state, "easy", state.due)

    rebuilt = card_state_from_row(
        state=state.state,
        step=state.step,
        stability=state.stability,
        difficulty=state.difficulty,
        due=state.due,
        last_review=state.last_review,
    )
    assert review(rebuilt, "good", rebuilt.due) == review(state, "good", state.due)


def test_derive_stats_counts_reps_and_lapses() -> None:
    day = lambda n: NOW - timedelta(days=n)  # noqa: E731
    history = [
        (day(30), "good"),
        (day(20), "good"),
        (day(10), "again"),
    ]
    due = day(10) + timedelta(days=3)

    stats = derive_stats(history, current_due=due, now=NOW)

    assert stats.reps == 3
    assert stats.lapses == 1
    assert stats.elapsed_days == 10
    assert stats.scheduled_days == 3


def test_derive_stats_with_no_history_is_all_zero() -> None:
    stats = derive_stats([], current_due=NOW, now=NOW)
    assert (stats.reps, stats.lapses, stats.elapsed_days, stats.scheduled_days) == (0, 0, 0, 0)
