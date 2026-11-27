"""검증된 이전 제안의 필드별 재참조와 취소·수동 입력 방어."""

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

CONTEXT = AssistContext(today=date(2026, 11, 23), base_month=date(2026, 12, 1))
RESOURCE = UUID("00000000-0000-4000-8000-000000000179")


def _run(
    monkeypatch,
    form,
    current,
    name,
    value,
    old_text,
    old_evidence,
    text,
    evidence,
    *,
    ambiguous=False,
    stale=False,
    missing=False,
    origin="origin",
    old_request="origin",
    other_fields=(),
):
    previous = PreviousCandidate(
        request_id=old_request,
        text=old_text,
        fields=[FieldIntent(field=name, action="set", value=value, evidence=old_evidence)],
        baselines={} if missing else {name: None if stale else current[name]},
        sources={name: "llm"},
        origins={name: origin},
    )
    item = FieldIntent(
        field=name,
        action="uncertain" if ambiguous else "keep",
        value=None,
        evidence=evidence,
        intent_kind="ambiguous" if ambiguous else "retain_previous",
        value_source=None if ambiguous else "previous",
    )
    data = AssistRequest(
        request_id="new",
        resource_id=RESOURCE,
        form=form,
        current=current,
        text=text,
        previous_candidate=previous,
        rule_fields=[],
    )

    async def stub(_safe_request, _context):
        return ModelInterpretation(fields=[item, *other_fields])

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    result = asyncio.run(interpret(data, CONTEXT, trace=trace))
    assert result.source == "llm"
    assert data.current == current
    return next(part for part in result.fields if part.field == name), trace


@pytest.mark.parametrize(
    "form,current,name,value,old_text,old_evidence,text,evidence",
    [
        (
            "period",
            {"start": "2026-12-03", "end": "2026-12-22", "excluded": []},
            "end",
            "2026-12-25",
            "종료일은 12월 25일로 잡아 주세요.",
            "12월 25일",
            "착공일 변경은 취소합니다. 준공일만 앞서 알려드린 날짜로 해 주세요.",
            "준공일만 앞서 알려드린 날짜로",
        ),
        (
            "period",
            {"start": "2026-12-03", "end": "2026-12-22", "excluded": []},
            "end",
            "2026-12-25",
            "종료일은 12월 25일로 잡아 주세요.",
            "12월 25일",
            "쉬는 날은 아직 미정입니다. 마감은 전에 정한 날짜대로 잡아 주세요.",
            "마감은 전에 정한 날짜대로",
        ),
        (
            "period",
            {"start": "2026-12-03", "end": "2026-12-22", "excluded": []},
            "end",
            "2026-12-25",
            "종료일은 12월 25일로 잡아 주세요.",
            "12월 25일",
            "끝나는 날은 앞서 정한 일정대로 가고, 첫 작업일은 12월 8일로 잡아주시겠어요?",
            "끝나는 날은 앞서 정한 일정대로 가고",
        ),
        (
            "period",
            {"start": "2026-12-03", "end": "2026-12-22", "excluded": ["2026-12-13"]},
            "end",
            "2026-12-25",
            "종료일은 12월 25일로 잡아 주세요.",
            "12월 25일",
            "제외일은 제가 직접 바꿨으니 놔두시고 마지막 작업일은 앞서 알려드린 그대로 부탁드립니다.",
            "마지막 작업일은 앞서 알려드린 그대로",
        ),
        (
            "quote",
            {"amount": 3_200_000, "vat": "excluded", "days": 3, "valid": None},
            "amount",
            5_800_000,
            "견적 금액은 580만 원으로 입력해 주세요.",
            "580만 원",
            "공사 일수만 변경합니다. 금액은 앞서 전달한 걸 그대로 쓰면 됩니다.",
            "금액은 앞서 전달한 걸 그대로",
        ),
        (
            "quote",
            {"amount": 3_200_000, "vat": "excluded", "days": 3, "valid": "2026-12-20"},
            "valid",
            "2026-12-28",
            "유효기간은 12월 28일까지로 해 주세요.",
            "12월 28일",
            "금액은 직접 고쳤습니다. 만료일은 아까 요청한 날짜로 해주세요.",
            "만료일은 아까 요청한 날짜로",
        ),
        (
            "dates",
            {"days": ["2026-12-07", "2026-12-09"], "crew": 2},
            "days",
            ["2026-12-08", "2026-12-10"],
            "12월 8일하고 10일에 작업하겠습니다.",
            "12월 8일하고 10일",
            "작업 일정은 앞서 정한 걸로 하고, 인원은 아직 미정입니다.",
            "작업 일정은 앞서 정한 걸로 하고",
        ),
        (
            "dates",
            {"days": ["2026-12-07", "2026-12-09"], "crew": 2},
            "crew",
            3,
            "인원은 3명으로 잡아 주세요.",
            "3명",
            "날짜는 화면에서 직접 변경했습니다. 투입 인원은 전에 알려드린 인원으로 가고요.",
            "투입 인원은 전에 알려드린 인원으로",
        ),
        (
            "extra",
            {"title": "보강", "reason": "기존 사유", "amount": 120_000, "vat": "excluded"},
            "vat",
            "exempt",
            "세금은 면세로 설정해 주세요.",
            "면세",
            "세금 방식은 앞에서 정한 대로 두고, 사유 변경은 취소합니다.",
            "세금 방식은 앞에서 정한 대로 두고",
        ),
    ],
)
def test_explicit_prior_reference_preserves_verified_field(
    monkeypatch, form, current, name, value, old_text, old_evidence, text, evidence
):
    candidate, trace = _run(
        monkeypatch, form, current, name, value, old_text, old_evidence, text, evidence
    )
    assert trace["previous_verified"]["fields"][0]["field"] == name
    assert (candidate.status, candidate.action, candidate.value) == ("valid", "set", value)
    assert (candidate.source, candidate.origin_request_id, candidate.value_source) == (
        "llm",
        "origin",
        "previous",
    )


@pytest.mark.parametrize(
    "text,evidence",
    [
        (
            "인원은 앞서 알려드린 인원으로 가고요?",
            "인원은 앞서 알려드린 인원으로",
        ),
        (
            "가능하면 인원은 앞서 알려드린 인원으로 가고요.",
            "인원은 앞서 알려드린 인원으로",
        ),
        (
            "인원은 앞서 알려드린 인원으로 가고 싶지만 검토 중입니다.",
            "인원은 앞서 알려드린 인원으로",
        ),
        (
            "인원은 앞서 알려드린 인원으로 가고요. 인원 변경은 취소합니다.",
            "인원은 앞서 알려드린 인원으로",
        ),
        (
            "인원은 앞서 알려드린 인원으로 가고요. 그 요청은 철회합니다.",
            "인원은 앞서 알려드린 인원으로",
        ),
        (
            "인원은 앞서 알려드린 인원으로 가고요. 4명으로 바꾸고 날짜는 12월 11일로 잡아 주세요.",
            "인원은 앞서 알려드린 인원으로",
        ),
        (
            "인원은 앞서 알려드린 그대로 두지 마세요.",
            "인원은 앞서 알려드린 그대로",
        ),
        (
            "인원은 앞서 알려드린 그대로. 인원 변경은 보류합니다.",
            "인원은 앞서 알려드린 그대로",
        ),
        (
            "인원은 앞서 알려드린 그대로 유지하면 연락 주세요.",
            "인원은 앞서 알려드린 그대로",
        ),
        (
            "인원은 앞서 알려드린 그대로 부탁합니다?",
            "인원은 앞서 알려드린 그대로",
        ),
        (
            "인원은 앞서 알려드린 그대로 부탁합니다. 그 요청은 취소합니다.",
            "인원은 앞서 알려드린 그대로",
        ),
        (
            "날짜는 앞서 알려드린 일정대로 해주세요.",
            "날짜는 앞서 알려드린 일정대로",
        ),
    ],
)
@pytest.mark.parametrize("ambiguous", [False, True])
def test_prior_reference_never_revives_canceled_or_uncertain_crew(
    monkeypatch, text, evidence, ambiguous
):
    candidate, trace = _run(
        monkeypatch,
        "dates",
        {"days": ["2026-12-07", "2026-12-09"], "crew": 2},
        "crew",
        3,
        "인원은 3명으로 잡아 주세요.",
        "3명",
        text,
        evidence,
        ambiguous=ambiguous,
    )
    assert trace["previous_verified"] is not None
    assert not (candidate.status == "valid" and candidate.action == "set"), candidate


@pytest.mark.parametrize("stale,missing", [(True, False), (False, True)])
def test_prior_reference_requires_same_field_baseline(monkeypatch, stale, missing):
    text = "세금 방식은 앞서 알려드린 대로 두세요."
    candidate, trace = _run(
        monkeypatch,
        "extra",
        {"title": "보강", "reason": "기존 사유", "amount": 120_000, "vat": "excluded"},
        "vat",
        "exempt",
        "부가세는 면세로 해주세요.",
        "면세",
        text,
        text,
        stale=stale,
        missing=missing,
    )
    assert trace["previous_rejected"]["vat"] == "stale_context"
    assert candidate.status != "valid" or candidate.action != "set"


@pytest.mark.parametrize("first_origin", [True, False])
def test_ambiguous_model_first_reference_requires_actual_first_turn_evidence(
    monkeypatch, first_origin
):
    first = "12월 8일하고 10일 작업으로 잡아주세요."
    second = "날짜는 그대로 유지하고 인원은 4명으로 변경할게요."
    original = "12월 8일하고 10일"
    if not first_origin:
        second = "작업일을 12월 11일하고 15일로 변경해 주세요."
        original = "12월 11일하고 15일"
    value = ["2026-12-08", "2026-12-10"] if first_origin else ["2026-12-11", "2026-12-15"]
    text = "인원은 화면에서 직접 고쳤습니다. 작업일은 처음에 요청한 일정 그대로 가죠."
    candidate, trace = _run(
        monkeypatch,
        "dates",
        {"days": ["2026-12-07", "2026-12-09"], "crew": 2},
        "days",
        value,
        first + "\n" + second,
        original,
        text,
        "작업일은 처음에 요청한 일정 그대로 가죠",
        ambiguous=True,
        origin="origin" if first_origin else "middle",
        old_request="middle",
    )
    assert trace["previous_verified"] is not None
    if first_origin:
        assert (
            candidate.status,
            candidate.value,
            candidate.origin_request_id,
            candidate.source,
        ) == ("valid", value, "origin", "llm")
    else:
        assert not (candidate.status == "valid" and candidate.action == "set"), candidate
