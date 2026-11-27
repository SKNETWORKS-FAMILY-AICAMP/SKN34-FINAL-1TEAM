"""편집 동작으로 생성한 기대 상태와 실제 적용 오류의 집계를 검사한다."""

import asyncio
import json
from collections import Counter
from pathlib import Path

from evaluation.vendor_assist.scenarios.oracle import advance, compare
from evaluation.vendor_assist.scenarios.run import evaluate, load_scenarios


def test_wrong_target_is_both_unwanted_change_and_missed_requested_change():
    current = {"amount": 100, "days": 2, "valid": None, "vat": "included"}
    a, target = advance(current, current, [("set", "amount", 300), ("set", "days", 4)])
    assert compare("quote", a, target, {"amount": 200, "vat": "excluded"}) == {
        "wrong": ["amount", "vat"],
        "missed": ["amount", "days"],
        "correct": [],
    }
    assert compare("quote", a, target, {"amount": 300, "days": 4})["wrong"] == []


def test_unapplied_edit_survives_only_until_manual_reset_or_hold():
    current = {"days": ["2026-12-08", "2026-12-09"], "crew": 2}
    form, target = advance(
        current,
        current,
        [("replace", "days", "2026-12-09", "2026-12-11"), ("set", "crew", 3)],
    )
    assert form == current
    form, target = advance(form, target, [("manual", "crew", 4), ("previous", "days")])
    assert compare("dates", form, target, {"days": ["2026-12-11", "2026-12-08"]}) == {
        "wrong": [],
        "missed": [],
        "correct": ["days"],
    }
    _, target = advance(form, target, [("hold", "days")])
    assert target == form


def test_offline_scenarios_prevent_new_wrong_or_missed_fields():
    cases = load_scenarios()
    rows = asyncio.run(evaluate(cases))
    baseline = json.loads(
        (
            Path(__file__).parents[1] / "vendor_assist/scenarios/rule_baseline.json"
        ).read_text(encoding="utf-8")
    )
    turns = Counter(row["case"] for row in rows)
    for case, count in baseline["cases"].items():
        assert turns[case] >= count, f"기존 시나리오 턴 누락 {case}"
    observed = set()
    for row in rows:
        for stage in ("preview_score", "apply_score"):
            score = row[stage]
            if score is not None:
                for category in ("wrong", "missed"):
                    for field in score[category]:
                        observed.add(
                            f"{row['case']}/{row['turn']}/{stage}/{category}/{field}"
                        )
    extra = sorted(observed - set(baseline["observed"]))
    assert not extra, f"새로 발생한 잘못된 변경·누락 {extra}"
