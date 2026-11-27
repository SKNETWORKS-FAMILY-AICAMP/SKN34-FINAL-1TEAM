"""이전 후보의 유지·취소와 현재 입력을 구분하는 서비스 회귀."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

CTX = AssistContext(today=date(2026, 11, 23), base_month=date(2026, 12, 1))
RESOURCE = UUID("00000000-0000-4000-8000-000000000179")
CURRENT = {"days": ["2026-12-07", "2026-12-09"], "crew": 2}
PROPOSED = ["2026-12-08", "2026-12-10"]
ORIGINAL = "작업일은 12월 8일하고 10일, 인원은 3명으로 잡아 주세요."


def prior(*, history=ORIGINAL):
    return PreviousCandidate(
        request_id="previous",
        text=history,
        fields=[
            FieldIntent(field="days", action="set", value=PROPOSED, evidence="12월 8일하고 10일"),
            FieldIntent(field="crew", action="set", value=3, evidence="3명"),
        ],
        sources={"days": "rule", "crew": "llm"},
        origins={"days": "original-days", "crew": "original-crew"},
        baselines=CURRENT,
    )


def keep(field, evidence, intent="retain_previous"):
    return FieldIntent(
        field=field,
        action="keep",
        value=None,
        evidence=evidence,
        intent_kind=intent,
        value_source="previous" if intent == "retain_previous" else "current",
    )


def run(monkeypatch, text, intents=(), *, mode="model", old=None, current=None, rules=()):
    data = AssistRequest(
        request_id="new",
        form="dates",
        resource_id=RESOURCE,
        text=text,
        current=CURRENT if current is None else current,
        previous_candidate=prior() if old is None else old,
        rule_fields=list(rules),
    )
    calls = []

    async def stub(*args):
        calls.append(1)
        if mode == "fallback":
            raise RuntimeError("offline test")
        return ModelInterpretation(fields=list(intents))

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    result = asyncio.run(interpret(data, CTX, use_model=mode != "rules", trace=trace))
    assert len(calls) == int(mode != "rules")
    assert result.source == ("llm" if mode == "model" else "rule")
    return {part.field: part for part in result.fields}, trace


def assert_held(field):
    assert field.action == "uncertain" and field.status != "valid" and field.value is None


def assert_kept(field):
    assert (field.action, field.status, field.value, field.intent_kind, field.value_source) == (
        "keep",
        "valid",
        None,
        "retain_current",
        "current",
    )
    assert field.evidence and field.origin_request_id == "new"


def assert_prior(field, value, origin, source):
    assert (field.action, field.status, field.value, field.origin_request_id, field.source) == (
        "set",
        "valid",
        value,
        origin,
        source,
    )


@pytest.mark.parametrize(
    "text",
    [
        "인원은 이전 제안 그대로 유지해 주세요. 인원 변경은 취소합니다.",
        "인원은 이전 제안 그대로 유지해 주세요. 인원은 4명으로 바꿀게요.",
        "인원은 이전 제안 그대로 유지해 주세요. 인원은 4명.",
        "인원은 이전 제안 그대로 유지하고 인원은 4명으로 바꿀게요.",
        "인원은 이전 제안 그대로 유지해 주세요. 4명으로 할게요.",
        "가능하면 인원은 이전 제안 그대로 유지해 주세요.",
        "가능하면, 인원은 이전 제안 그대로 유지해 주세요.",
        "인원은 이전 제안 그대로 유지해 주세요. 그 요청은 철회합니다.",
        "인원은 이전 제안 그대로 유지해 주세요. 검토 후에 결정하겠습니다.",
    ],
)
def test_clipped_prior_evidence_cannot_override_competing_instruction(monkeypatch, text):
    fields, trace = run(monkeypatch, text, [keep("crew", "인원은 이전 제안 그대로")])
    assert trace["previous_verified"]["origins"]["crew"] == "original-crew"
    assert_held(fields["crew"])


@pytest.mark.parametrize("mode", ["model", "rules", "fallback"])
def test_generic_cancellation_discards_all_pending_fields(monkeypatch, mode):
    fields, _ = run(monkeypatch, "그 요청은 취소할게요.", mode=mode)
    for name in ("days", "crew"):
        assert_kept(fields[name])


@pytest.mark.parametrize("mode", ["model", "rules", "fallback"])
def test_question_about_cancellation_holds_pending_fields(monkeypatch, mode):
    fields, _ = run(monkeypatch, "그 요청은 취소할까요?", mode=mode)
    for name in ("days", "crew"):
        assert_held(fields[name])


@pytest.mark.parametrize(
    "text", ["지금 값 그대로 유지해 주세요.", "가능하면 그대로 유지해 주세요."]
)
@pytest.mark.parametrize("mode", ["model", "rules", "fallback"])
def test_unscoped_current_or_conditional_control_never_restores_pending(monkeypatch, text, mode):
    fields, _ = run(monkeypatch, text, mode=mode)
    for name in ("days", "crew"):
        assert_held(fields[name])


@pytest.mark.parametrize("mode", ["model", "rules", "fallback"])
def test_field_cancellation_preserves_independent_pending_value(monkeypatch, mode):
    fields, trace = run(monkeypatch, "인원 변경은 취소할게요.", mode=mode)
    assert_kept(fields["crew"])
    assert_prior(fields["days"], PROPOSED, "original-days", "rule")
    assert trace["previous_verified"]["baselines"] == CURRENT


@pytest.mark.parametrize(
    "text,evidence",
    [
        ("인원은 지금 값 그대로 유지해 주세요.", "그대로 유지해 주세요"),
        ("인원은 현재 인원 그대로 유지해 주세요.", "인원 그대로 유지해 주세요"),
        ("현재 인원은 같아요.", "인원은 같아요"),
    ],
)
@pytest.mark.parametrize("model_intent", ["retain_previous", "retain_current"])
def test_explicit_current_reference_cannot_reintroduce_previous_value(
    monkeypatch, text, evidence, model_intent
):
    fields, _ = run(monkeypatch, text, [keep("crew", evidence, model_intent)])
    assert_kept(fields["crew"])
    assert_prior(fields["days"], PROPOSED, "original-days", "rule")


def test_unrelated_uncertain_field_does_not_block_explicit_retention(monkeypatch):
    text = "작업일은 아직 미정입니다. 인원은 이전 제안 그대로 유지해 주세요."
    fields, trace = run(
        monkeypatch,
        text,
        [keep("crew", "인원은 이전 제안 그대로 유지해 주세요")],
    )
    assert_prior(fields["crew"], 3, "original-crew", "llm")
    assert_held(fields["days"])
    assert trace["previous_verified"]["baselines"]["crew"] == CURRENT["crew"]


def test_same_field_manual_baseline_change_still_fails_closed(monkeypatch):
    text = "인원은 이전 제안 그대로 유지해 주세요."
    fields, trace = run(monkeypatch, text, [keep("crew", text)], current={**CURRENT, "crew": 4})
    assert trace["previous_rejected"]["crew"] == "stale_context"
    assert_held(fields["crew"])
    assert_prior(fields["days"], PROPOSED, "original-days", "rule")


@pytest.mark.parametrize(
    "history",
    [
        ORIGINAL + "\n인원 변경은 취소할게요.",
        ORIGINAL + "\n인원은 지금 값 그대로 유지해 주세요.",
    ],
)
def test_canceled_history_cannot_be_carried_on_unrelated_next_turn(monkeypatch, history):
    fields, trace = run(monkeypatch, "네, 확인했습니다.", old=prior(history=history))
    assert trace["previous_rejected"]["crew"] == "ambiguous_intent"
    assert_held(fields["crew"])
    assert_prior(fields["days"], PROPOSED, "original-days", "rule")


def test_generic_cancellation_in_history_cannot_revive_old_candidates(monkeypatch):
    fields, trace = run(
        monkeypatch,
        "네, 확인했습니다.",
        old=prior(history=ORIGINAL + "\n그 요청은 취소할게요."),
    )
    assert trace["previous_verified"] is None
    assert trace["previous_rejected"] == {"days": "ambiguous_intent", "crew": "ambiguous_intent"}
    assert_held(fields["days"])
    assert_held(fields["crew"])


@pytest.mark.parametrize("mode", ["model", "rules", "fallback"])
@pytest.mark.parametrize(
    "text",
    [
        "날짜 변경안은 아직 고민 중입니다. 인원 세 명 제안은 그대로 적용해 주세요.",
        "인원 3명 제안은 그대로 둘게요.",
    ],
)
def test_verified_explicit_retention_when_model_omits_field(monkeypatch, mode, text):
    fields, _ = run(monkeypatch, text, mode=mode)
    assert_prior(fields["crew"], 3, "original-crew", "llm")


@pytest.mark.parametrize(
    "text",
    [
        "인원 4명 제안은 그대로 둘게요.",
        "가능하면 인원 3명 제안은 그대로 둘게요.",
        "인원 3명 제안은 그대로 둘게요. 인원은 4명으로 변경해 주세요.",
        "현재 인원은 그대로 유지해 주세요.",
    ],
)
def test_implicit_retention_never_restores_wrong_or_conditional_prior(monkeypatch, text):
    fields, _ = run(monkeypatch, text, mode="rules")
    assert fields["crew"].action != "set"
