"""#179의 추가 지표·공통 서비스 실행·단계 trace를 합성 사례로 검증한다."""

from __future__ import annotations

import asyncio
import json
import sys
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from evaluation.vendor_assist import run
from evaluation.vendor_assist.metrics import (
    FIELDS,
    is_known_heldout_path,
    requires_final_before_read,
    summarize,
)


def _intent(action="keep", value=None):
    return {"action": action, "value": value}


def _case(*, current=5_000_000, gold=None, case_id="SYN-Q-1"):
    expected = {field: _intent() for field in FIELDS["quote"]}
    if gold is not None:
        expected["amount"] = deepcopy(gold)
    return {
        "id": case_id,
        "form": "quote",
        "kind": "simple",
        "tags": ["simple"],
        "text": "합성 견적 요청",
        "split": "dev",
        "human_verified": False,
        "current": {"amount": current, "vat": None, "days": None, "valid": None},
        "previous_candidate": None,
        "expected": expected,
    }


def _outcome(case, *, amount=None):
    fields = deepcopy(case["expected"])
    if amount is not None:
        fields["amount"] = amount
    return {"id": case["id"], "fields": fields, "error": None, "latency_ms": 1.0}


def _score(case, outcome):
    return summarize([case], [outcome], runner="synthetic")


def test_actual_change_tp_and_existing_metrics_preserved():
    case = _case(gold=_intent("set", 6_000_000))
    score = _score(case, _outcome(case))
    assert (
        score["change_true_positive"],
        score["change_false_positive"],
        score["change_false_negative"],
    ) == (1, 0, 0)
    assert score["change_precision"] == score["change_recall"] == 1
    assert score["field_accuracy"] == 1
    assert score["false_candidates"] == score["omissions"] == 0
    assert score["baseline_declared_fields"] == 4


def test_wrong_new_value_is_both_change_fp_and_fn():
    case = _case(gold=_intent("set", 6_000_000))
    score = _score(case, _outcome(case, amount=_intent("set", 7_000_000)))
    assert (
        score["change_true_positive"],
        score["change_false_positive"],
        score["change_false_negative"],
    ) == (0, 1, 1)
    assert score["change_precision"] == score["change_recall"] == 0
    assert score["false_candidates"] == score["omissions"] == 1


def test_reconfirming_current_value_is_noop_and_real_change_is_still_missing():
    case = _case(gold=_intent("set", 6_000_000))
    score = _score(case, _outcome(case, amount=_intent("set", 5_000_000)))
    assert score["change_true_positive"] == 0
    assert score["change_false_positive"] == 0
    assert score["change_false_negative"] == 1
    assert score["change_precision"] is None
    assert score["change_recall"] == 0
    assert score["spurious_noop_reconfirmations"] == 1


def test_same_value_reconfirmation_never_counts_as_actual_change():
    case = _case(gold=_intent("set", 5_000_000))
    score = _score(case, _outcome(case))
    assert score["gold_noop_reconfirmations"] == 1
    assert score["predicted_noop_reconfirmations"] == 1
    assert score["matched_noop_reconfirmations"] == 1
    assert score["change_true_positive"] == 0
    assert score["change_precision"] is None and score["change_recall"] is None
    case["expected"]["amount"] = _intent()
    score = _score(case, _outcome(case, amount=_intent("set", 5_000_000)))
    assert score["spurious_noop_reconfirmations"] == 1
    assert score["change_false_positive"] == 0
    assert score["false_candidates"] == 1  # 기존 후보 오류율은 그대로 유지


def test_explicit_clear_and_existing_empty_value_have_different_change_classification():
    populated = _case(gold=_intent("clear"))
    changed = _score(populated, _outcome(populated))
    assert changed["change_true_positive"] == 1
    already_empty = _case(current=None, gold=_intent("clear"))
    unchanged = _score(already_empty, _outcome(already_empty))
    assert unchanged["change_true_positive"] == 0
    assert unchanged["matched_noop_reconfirmations"] == 1


def test_date_arrays_compare_as_sets_without_inventing_previous_state():
    case = _case()
    case["form"] = "dates"
    case["current"] = {"days": [], "crew": 2}
    case["expected"] = {
        "days": _intent("set", ["2026-12-02", "2026-12-03"]),
        "crew": _intent("set", 2),
    }
    outcome = _outcome(case)
    outcome["fields"]["days"] = _intent("set", ["2026-12-03", "2026-12-02"])
    score = _score(case, outcome)
    assert score["change_true_positive"] == 1
    assert score["matched_noop_reconfirmations"] == 1  # 인원 2명 재확인
    assert score["exact_cases"] == 1


def test_missing_current_fields_are_unscored_not_assumed_null():
    case = _case(gold=_intent("set", 6_000_000))
    del case["current"]["amount"]
    score = _score(case, _outcome(case))
    assert score["field_accuracy"] == 1
    assert score["baseline_missing_fields"] == 1
    assert score["actions_unscored_missing_baseline"] == 1
    assert score["change_true_positive"] == 0
    assert score["change_precision"] is None and score["change_recall"] is None
    assert score["rows"][0]["baseline_missing_fields"] == ["amount"]


def test_missing_previous_capture_baselines_are_reported_without_inference():
    case = _case(gold=_intent("set", 6_000_000))
    case["previous_candidate"] = {
        "request_id": "prior",
        "text": "그전 견적",
        "fields": [],
    }
    score = _score(case, _outcome(case))
    assert score["baseline_declared_fields"] == 4
    assert score["previous_baselines_missing_cases"] == 1
    assert score["previous_baselines_present_cases"] == 0
    case["previous_candidate"]["baselines"] = {}
    assert _score(case, _outcome(case))["previous_baselines_missing_cases"] == 1
    case["previous_candidate"]["baselines"] = {"amount": 5_000_000}
    score = _score(case, _outcome(case))
    assert score["previous_baselines_missing_cases"] == 0
    assert score["previous_baselines_present_cases"] == 1
    assert score["change_true_positive"] == 1


def test_conflict_alternative_is_not_successful_change():
    case = _case(gold=_intent("set", 6_000_000))
    fields = [
        {"field": field, **value, "status": "valid"}
        for field, value in case["expected"].items()
    ]
    fields[0].update(
        action="uncertain",
        value=None,
        status="conflict",
        alternatives=[
            {"action": "set", "value": 6_000_000},
            {"action": "set", "value": 7_000_000},
        ],
    )
    score = _score(case, {"id": case["id"], "fields": fields})
    assert score["change_true_positive"] == 0
    assert score["change_false_negative"] == 1
    assert score["change_false_positive"] == 0
    assert score["conflict_fields"] == 1
    assert score["predicted_status_counts"]["conflict"] == 1
    assert score["conflict_option_mismatches"] == 1


@pytest.mark.parametrize(
    ("status", "expected_key"),
    [
        ("uncertain", "correct_uncertain_abstentions"),
        ("conflict", "uncertain_as_conflict"),
        ("invalid", "uncertain_as_invalid"),
    ],
)
def test_true_uncertainty_stays_separate_from_conflict_or_invalid(status, expected_key):
    case = _case(gold=_intent("uncertain"))
    fields = [
        {"field": field, **value, "status": "valid"}
        for field, value in case["expected"].items()
    ]
    fields[0].update(action="uncertain", value=None, status=status)
    if status == "conflict":
        fields[0]["alternatives"] = [
            {"action": "set", "value": 6_000_000},
            {"action": "set", "value": 7_000_000},
        ]
    score = _score(case, {"id": case["id"], "fields": fields})
    assert score["gold_uncertain_fields"] == 1
    assert score[expected_key] == 1
    if status != "uncertain":
        assert score["correct_uncertain_abstentions"] == 0
    assert score["change_true_positive"] == 0


def test_valid_proposal_on_uncertain_gold_is_unsafe_even_when_value_is_grounded():
    case = _case(gold=_intent("uncertain"))
    score = _score(case, _outcome(case, amount=_intent("set", 6_000_000)))
    assert score["uncertain_unsafe_actions"] == 1
    assert score["change_false_positive"] == 1
    assert score["correct_uncertain_abstentions"] == 0


def test_known_heldout_is_detected_before_reading_any_data(tmp_path):
    assert is_known_heldout_path(Path("held-out.jsonl"))
    assert is_known_heldout_path(Path("held_out.jsonl"))
    assert requires_final_before_read(
        Path("renamed.jsonl"), split="heldout", final=False
    )
    assert requires_final_before_read(Path("heldout.jsonl"), split="dev", final=False)
    assert not requires_final_before_read(
        tmp_path / "synthetic-dev.jsonl", split="dev", final=False
    )


def test_rule_service_path_calls_interpret_with_use_model_false_and_local_trace(
    monkeypatch,
):
    seen = []

    async def interpret(request, context, *, use_model=True, trace=None):
        seen.append((request, context, use_model, trace is not None))
        if trace is not None:
            trace.update(
                {
                    "rule_raw": [],
                    "previous_verified": None,
                    "rule_reviewed": [],
                    "merged": [],
                    "final": [],
                }
            )
        fields = [
            SimpleNamespace(
                model_dump=lambda mode: {
                    "field": "amount",
                    "action": "set",
                    "value": 6_000_000,
                    "status": "valid",
                }
            )
        ]
        return SimpleNamespace(fields=fields, source="rule", warnings=[])

    fake_vendor = ModuleType("app.vendor")
    fake_vendor.assist_service = SimpleNamespace(interpret=interpret)
    fake_app = ModuleType("app")
    fake_app.vendor = fake_vendor
    monkeypatch.setitem(sys.modules, "app", fake_app)
    monkeypatch.setitem(sys.modules, "app.vendor", fake_vendor)
    monkeypatch.setattr(run, "_request", lambda case, rule: ("request", "context"))
    case = _case(gold=_intent("set", 6_000_000))
    results = asyncio.run(
        run.hybrid_outputs(
            [case],
            [{"fields": []}],
            live=False,
            timeout=2,
            use_model=False,
            capture_trace=True,
        )
    )
    assert seen == [("request", "context", False, True)]
    assert results[0]["source"] == "rule"
    assert set(results[0]["trace"]) >= {
        "rule_raw",
        "previous_verified",
        "rule_reviewed",
        "merged",
        "final",
    }
    assert not results[0].get(
        "adapter_invoked", False
    )  # 규칙 모드는 모델 SDK에 진입하지 않는다.


def test_server_without_fixed_use_model_trace_signature_fails_before_scoring(
    monkeypatch,
):
    async def old_interpret(request, context, *, use_model=True):
        raise AssertionError("호환되지 않는 서버 구현은 실행하면 안 됩니다")

    fake_vendor = ModuleType("app.vendor")
    fake_vendor.assist_service = SimpleNamespace(interpret=old_interpret)
    fake_app = ModuleType("app")
    fake_app.vendor = fake_vendor
    monkeypatch.setitem(sys.modules, "app", fake_app)
    monkeypatch.setitem(sys.modules, "app.vendor", fake_vendor)
    with pytest.raises(RuntimeError, match="계약 통합"):
        asyncio.run(
            run.hybrid_outputs(
                [_case()], [{"fields": []}], live=False, timeout=1, use_model=False
            )
        )


def test_rule_and_raw_rule_cli_paths_are_distinct_and_trace_is_opt_in(
    monkeypatch, tmp_path, capsys
):
    case = _case(gold=_intent("set", 6_000_000))
    called = []
    trace_file = tmp_path / "stages.jsonl"

    monkeypatch.setattr(run, "load_cases", lambda path: [case])
    monkeypatch.setattr(
        run, "rule_outputs", lambda cases, parser_path=None: [_outcome(case)]
    )

    async def service(cases, rules, *, live, timeout, use_model, capture_trace):
        called.append((use_model, capture_trace))
        return [
            {
                **_outcome(case),
                "source": "rule",
                "trace": {"rule_raw": [], "merged": []},
            }
        ]

    monkeypatch.setattr(run, "hybrid_outputs", service)
    assert run.main(["--mode", "raw_rule", "--split", "dev", "--show-wrong", "0"]) == 0
    assert called == []
    assert not trace_file.exists()
    assert (
        run.main(
            [
                "--mode",
                "rule",
                "--split",
                "dev",
                "--trace-jsonl",
                str(trace_file),
                "--show-wrong",
                "0",
            ]
        )
        == 0
    )
    assert called == [(False, True)]
    row = json.loads(trace_file.read_text(encoding="utf-8").strip())
    assert row == {
        "id": case["id"],
        "mode": "rule",
        "trace": {"rule_raw": [], "merged": []},
        "error": None,
    }
    assert "실제 변경" in capsys.readouterr().out


def test_trace_refuses_heldout_before_loader_is_invoked(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        run, "load_cases", lambda path: pytest.fail("heldout 파일 접근")
    )
    assert (
        run.main(
            [
                "--mode",
                "raw_rule",
                "--split",
                "heldout",
                "--final",
                "--trace-jsonl",
                str(tmp_path / "cannot-write.jsonl"),
            ]
        )
        == 2
    )
    assert not (tmp_path / "cannot-write.jsonl").exists()
    assert "dev" in capsys.readouterr().err


def test_trace_encoder_serializes_date_decimal_and_pydantic_style_values():
    class LocalModel:
        def model_dump(self, *, mode):
            assert mode == "json"
            return {"action": "set", "value": 6_000_000}

    serialized = json.dumps(
        {
            "day": date(2026, 12, 8),
            "amount": Decimal("0.05"),
            "candidate": LocalModel(),
        },
        default=run._trace_json_default,
    )
    assert json.loads(serialized) == {
        "day": "2026-12-08",
        "amount": "0.05",
        "candidate": {"action": "set", "value": 6_000_000},
    }
    with pytest.raises(TypeError, match="trace"):
        run._trace_json_default(object())
