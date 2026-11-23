"""#178 평가셋 제약과 지표 의미를 검증한다. 네트워크·DB·LLM 호출은 없다."""

from __future__ import annotations

import asyncio
import json
import shutil
from collections import Counter
from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from evaluation.vendor_assist import make_dataset
from evaluation.vendor_assist.metrics import (
    FIELDS,
    load_cases,
    normalize_fields,
    price_for_usage,
    requires_final,
    summarize,
    valid_value,
    validate_case,
    validate_corpus,
)

DATA = Path(__file__).resolve().parents[1] / "vendor_assist"


@pytest.fixture(scope="module")
def corpus():
    return load_cases(DATA / "dev.jsonl"), load_cases(DATA / "heldout.jsonl")


def _case(corpus, *, form="quote", kind="simple"):
    return deepcopy(
        next(
            x
            for x in [*corpus[0], *corpus[1]]
            if x["form"] == form and x["kind"] == kind
        )
    )


def _prediction(case, changes=None):
    fields = {field: dict(value) for field, value in case["expected"].items()}
    fields.update(changes or {})
    return {
        "id": case["id"],
        "fields": fields,
        "latency_ms": 3.0,
        "usage": None,
        "error": None,
    }


def test_corpus_has_144_distinct_cases_2_to_1_per_form(corpus):
    dev, heldout = corpus
    assert validate_corpus(dev, heldout) == {
        "total": 144,
        "dev": 96,
        "heldout": 48,
        "per_form": {"period": 36, "quote": 36, "dates": 36, "extra": 36},
    }
    for form in FIELDS:
        assert sum(x["form"] == form for x in dev) == 24
        assert sum(x["form"] == form for x in heldout) == 12
    assert len({case["text"] for case in [*dev, *heldout]}) == 144


def test_all_kinds_and_critical_corrections_covered(corpus):
    cases = [*corpus[0], *corpus[1]]
    kinds = Counter(case["kind"] for case in cases)
    assert len(kinds) == 12
    assert set(kinds.values()) == {12}
    tags = {tag for case in cases for tag in case["tags"]}
    assert {
        "amount_correction",
        "duration_correction",
        "vat_negation",
        "double_negation",
        "multi_date_exclusion",
        "previous_correction",
        "no_action",
        "clear",
    } <= tags
    assert all(
        case["human_verified"] is False and case["reviewed_by"] is None
        for case in cases
    )
    assert all(set(case["expected"]) == set(FIELDS[case["form"]]) for case in cases)


def test_previous_candidate_has_complete_original_fields_and_evidence(corpus):
    previous = [x for x in [*corpus[0], *corpus[1]] if x["previous_candidate"]]
    assert len(previous) >= 24
    for case in previous:
        first = case["previous_candidate"]
        assert len(first["fields"]) == len(FIELDS[case["form"]])
        for field in first["fields"]:
            if field["action"] == "set":
                assert field["evidence"] in first["text"]


def test_generator_is_deterministic_and_files_match(corpus):
    generated = make_dataset.build()
    for split in ("dev", "heldout"):
        expected = "".join(
            json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n"
            for row in generated[split]
        )
        assert (DATA / f"{split}.jsonl").read_text(encoding="utf-8") == expected


def test_rejects_boolean_as_amount_and_invalid_dates(corpus):
    assert not valid_value("quote", "amount", True)
    assert not valid_value("quote", "days", 2.0)
    assert not valid_value("period", "start", "2026-02-31")
    assert not valid_value("dates", "days", ["2026-12-02", "2026-12-02"])
    case = _case(corpus)
    case["expected"]["amount"] = {"action": "clear", "value": 3_000_000}
    with pytest.raises(ValueError, match="value는 null"):
        validate_case(case)


def test_heldout_guard_uses_internal_metadata_even_if_renamed(corpus):
    dev, heldout = corpus
    assert requires_final(Path("renamed.jsonl"), heldout[:1], final=False)
    assert requires_final(Path("held-out.jsonl"), dev[:1], final=False)
    assert not requires_final(Path("renamed.jsonl"), heldout[:1], final=True)
    assert not requires_final(Path("dev.jsonl"), dev[:1], final=False)


def test_rule_missing_fields_are_keep_not_clear():
    pred = normalize_fields(
        "quote", [{"field": "amount", "action": "set", "value": 4_550_000}]
    )
    assert pred["amount"] == {"action": "set", "value": 4_550_000}
    assert pred["vat"] == {"action": "keep", "value": None}


def test_unvalidated_conflict_is_not_an_actionable_candidate():
    pred = normalize_fields(
        "quote",
        [
            {
                "field": "amount",
                "action": "uncertain",
                "value": None,
                "status": "conflict",
                "alternatives": [{"action": "set", "value": 4_550_000}],
            }
        ],
    )
    assert pred["amount"] == {"action": "uncertain", "value": None}
    invalid = normalize_fields(
        "quote",
        [{"field": "vat", "action": "set", "value": "included", "status": "invalid"}],
    )
    assert invalid["vat"]["action"] == "uncertain"
    with pytest.raises(ValueError, match="중복"):
        normalize_fields(
            "quote", [{"field": "amount", "action": "keep", "value": None}] * 2
        )


def test_wrong_set_is_both_false_candidate_and_omitted_correct_value(corpus):
    case = _case(corpus)
    report = summarize(
        [case],
        [_prediction(case, {"amount": {"action": "set", "value": 4_600_000}})],
        runner="test",
    )
    assert report["field_accuracy"] == 3 / 4
    assert report["false_candidates"] == 1
    assert report["false_candidate_rate"] == 1.0
    assert report["omissions"] == 1
    assert report["omission_rate"] == 1.0
    assert report["exact_cases"] == 0


def test_keep_on_unmentioned_field_is_not_false_candidate(corpus):
    case = _case(corpus, kind="no_action")
    report = summarize([case], [_prediction(case)], runner="test")
    assert report["exact_cases"] == 1
    assert report["false_candidates"] == 0
    assert report["false_candidate_rate"] is None  # 분모가 0이면 N/A
    assert report["omission_rate"] is None
    changed = summarize(
        [case],
        [_prediction(case, {"amount": {"action": "set", "value": 1_000_000}})],
        runner="test",
    )
    assert changed["false_candidates"] == 1
    assert changed["omissions"] == 0


def test_explicit_clear_matters_and_keep_is_omission(corpus):
    case = _case(corpus, kind="clear")
    field = next(
        f for f in FIELDS[case["form"]] if case["expected"][f]["action"] == "clear"
    )
    omitted = summarize(
        [case],
        [_prediction(case, {field: {"action": "keep", "value": None}})],
        runner="test",
    )
    asserted = summarize([case], [_prediction(case)], runner="test")
    assert omitted["omissions"] == 1
    assert omitted["false_candidates"] == 0
    assert asserted["exact_cases"] == 1


def test_conflict_and_errors_get_no_automatic_apply_credit(corpus):
    case = _case(corpus)
    field = "amount"
    uncertain = _prediction(
        case, {field: {"action": "uncertain", "value": None, "status": "conflict"}}
    )
    report = summarize([case], [uncertain], runner="test")
    assert report["false_candidates"] == 0
    assert report["omissions"] == 1
    errored = summarize(
        [case],
        [
            {
                "id": case["id"],
                "error": "TimeoutError",
                "timeout": True,
                "latency_ms": 40_000,
            }
        ],
        runner="test",
    )
    assert errored["error_count"] == 1
    assert errored["timeout_count"] == 1
    assert errored["field_correct"] == 0
    assert errored["omissions"] == 1


def test_provider_timeout_caught_by_service_is_still_counted_separately(corpus):
    """모델 실패를 규칙 후보로 복구하면 필드 점수는 유지하고 오류율에는 반영한다."""
    case = _case(corpus)
    fallback = _prediction(case)
    fallback.update(
        {
            "source": "rule",
            "adapter_invoked": True,
            "llm_error": "APITimeoutError",
            "llm_timeout": True,
        }
    )
    report = summarize([case], [fallback], runner="test")
    assert report["exact_cases"] == 1
    assert report["error_count"] == 0
    assert report["timeout_count"] == 0
    assert report["llm_adapter_invocations"] == 1
    assert report["llm_error_count"] == 1
    assert report["llm_timeout_count"] == 1
    assert report["llm_error_rate"] == 1
    assert report["source_counts"] == {"rule": 1}


def test_provider_wrapper_records_exception_before_fallback_handles_it():
    from evaluation.vendor_assist.run import _instrument_usage

    async def raises_timeout(*args, **kwargs):
        raise TimeoutError("공급자 요청 제한 시간을 넘었습니다")

    async def adapter(*args, **kwargs):
        return await measured("vendor_assist")

    fault, usage = {}, {}
    measured, measured_agent = _instrument_usage(raises_timeout, adapter, usage, fault)
    with pytest.raises(TimeoutError):
        asyncio.run(measured_agent())
    assert fault == {
        "adapter_invoked": True,
        "llm_error": "TimeoutError",
        "llm_timeout": True,
    }
    assert usage == {}


def test_rejects_unexpected_output_as_error_instead_of_crashing(corpus):
    case = _case(corpus)
    bad = _prediction(case, {"unexpected": {"action": "set", "value": 10}})
    report = summarize([case], [bad], runner="test")
    assert report["error_count"] == 1
    assert report["exact_cases"] == 0
    assert "InvalidCandidate" in report["rows"][0]["error"]


def test_tokens_cost_and_latency_never_invent_missing_numbers(corpus):
    usage = {"input_tokens": 1000, "cached_tokens": 200, "output_tokens": 400}
    prices = {"input": Decimal(1), "output": Decimal(5), "cached": Decimal("0.05")}
    assert price_for_usage(usage, prices) == pytest.approx(0.00281)
    assert price_for_usage(usage, {**prices, "cached": None}) is None
    assert price_for_usage({"input_tokens": 1000, "output_tokens": 400}, prices) is None
    assert price_for_usage(usage, None) is None
    case = _case(corpus)
    report = summarize([case], [_prediction(case)], runner="test")
    assert report["cost_usd"] is None
    assert report["usage"]["input_tokens"] is None
    assert report["latency_ms"] == {"count": 1, "mean": 3.0, "p50": 3.0, "p95": 3.0}


def test_live_unreviewed_heldout_is_blocked_before_any_inference(capsys):
    from evaluation.vendor_assist.run import main

    assert (
        main(
            [
                "--split",
                "heldout",
                "--mode",
                "hybrid",
                "--live",
                "--final",
                "--allow-unreviewed",
            ]
        )
        == 2
    )
    assert "미검수" in capsys.readouterr().err


def test_heldout_without_final_is_blocked_before_any_inference(capsys):
    from evaluation.vendor_assist.run import main

    assert main(["--split", "heldout", "--mode", "rule"]) == 2
    assert "--final" in capsys.readouterr().err


def test_default_hybrid_mode_is_offline_even_if_host_environment_is_live(
    monkeypatch, corpus, capsys
):
    from evaluation.vendor_assist import run

    monkeypatch.setenv("LLM_LIVE", "true")
    observed = []

    def fake_rule(cases, **kwargs):
        return [_prediction(case) for case in cases]

    async def fake_hybrid(cases, rules, *, live, timeout):
        observed.append(live)
        return rules

    monkeypatch.setattr(run, "rule_outputs", fake_rule)
    monkeypatch.setattr(run, "hybrid_outputs", fake_hybrid)
    assert (
        run.main(
            [
                "--split",
                "dev",
                "--form",
                "quote",
                "--mode",
                "hybrid",
                "--show-wrong",
                "0",
            ]
        )
        == 0
    )
    assert observed == [False]
    assert "ai_draft_unreviewed" in capsys.readouterr().out


def test_live_on_unreviewed_dev_needs_explicit_diagnostic_flag(monkeypatch, capsys):
    from evaluation.vendor_assist import run

    def must_not_infer(*args, **kwargs):
        raise AssertionError("실모델 평가 허가 전에 규칙·모델을 실행하면 안 됩니다")

    monkeypatch.setattr(run, "rule_outputs", must_not_infer)
    assert run.main(["--split", "dev", "--mode", "hybrid", "--live"]) == 2
    assert "--allow-unreviewed" in capsys.readouterr().err


@pytest.mark.skipif(shutil.which("node") is None, reason="Node 실행기가 없습니다")
def test_rule_bridge_invokes_existing_typescript_parser(corpus):
    from evaluation.vendor_assist.run import rule_outputs

    period = _case(corpus, form="period", kind="simple")
    quote = _case(corpus, form="quote", kind="simple")
    results = rule_outputs([period, quote])
    assert all(x["error"] is None and x["source"] == "rule" for x in results)
    assert normalize_fields("period", results[0]["fields"])["start"] == {
        "action": "set",
        "value": "2026-12-02",
    }
    assert normalize_fields("quote", results[1]["fields"])["amount"] == {
        "action": "set",
        "value": 4_550_000,
    }
