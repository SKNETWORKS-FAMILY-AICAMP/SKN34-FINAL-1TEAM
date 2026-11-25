"""R3 보완: 실제 대입값의 경계와 확정 날짜 이동을 검증한다."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.vendor.assist_date_edit import (
    date_edit_reference_prefix,
    date_replacement_requested,
    validated_date_edit,
)
from app.vendor.assist_intent_safety import assignment_directive_in_value
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

CTX = AssistContext(today=date(2026, 11, 24), base_month=date(2026, 12, 1), crew_max=6)
EXTRA = {"title": "보강", "reason": "현장 추가", "amount": 1000000, "vat": "included"}
DAYS = ["2026-12-03", "2026-12-20"]
EXCLUDED = ["2026-12-09", "2026-12-15", "2026-12-21"]


def _set(field, value, evidence, *, model=False):
    return FieldIntent(
        field=field,
        action="set",
        value=value,
        evidence=evidence,
        intent_kind="explicit_change" if model else None,
        value_source="utterance" if model else None,
    )


def _keep(field, *, model=False):
    return FieldIntent(
        field=field,
        action="keep",
        value=None,
        evidence="",
        intent_kind="no_mention" if model else None,
    )


def _run(monkeypatch, request, context, model, *, use_model=True):
    calls = []

    async def stub(data, _context):
        calls.append(data)
        return ModelInterpretation(fields=model)

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    result = asyncio.run(interpret(request, context, use_model=use_model))
    assert len(calls) == int(use_model)
    return {field.field: field for field in result.fields}


# 동결 qa179-r3-live50-results.json의 E07/E10 T1 값과 근거를 그대로 사용한다.
@pytest.mark.parametrize("case", ["E07", "E10"])
@pytest.mark.parametrize("use_model", [True, False])
def test_archived_extra_assignment_boundary(monkeypatch, case, use_model):
    if case == "E07":
        text = "항목명은 벽면 보수, 사유는 석고보드 균열로 초안 부탁"
        field, clean, bad = "reason", "석고보드 균열", "석고보드 균열로 초안 부탁"
        rules = [
            _set("title", "벽면 보수", text),
            _set(field, bad, text),
            _keep("amount"),
            _keep("vat"),
        ]
        model = [
            _set("title", "벽면 보수", "항목명은 벽면 보수", model=True),
            _set(field, clean, "사유는 석고보드 균열", model=True),
            _keep("amount", model=True),
            _keep("vat", model=True),
        ]
    else:
        text = "추가 작업명 천장 고정, 금액 84만원으로 먼저 제안합니다"
        field, clean, bad = "title", "천장 고정", "추가 작업명 천장 고정"
        rules = [
            _set(field, bad, text),
            _keep("reason"),
            _set("amount", 840000, text),
            _keep("vat"),
        ]
        model = [
            _set(field, clean, "추가 작업명 천장 고정", model=True),
            _keep("reason", model=True),
            _set("amount", 840000, "금액 84만원", model=True),
            _keep("vat", model=True),
        ]
    request = AssistRequest(
        request_id=case,
        resource_id=UUID(int=179),
        form="extra",
        text=text,
        current=EXTRA,
        previous_candidate=None,
        rule_fields=rules,
    )
    result = _run(monkeypatch, request, CTX, model, use_model=use_model)[field]
    assert not result.alternatives
    if use_model:
        assert (result.status, result.action, result.value) == ("valid", "set", clean)
    else:
        assert result.action != "set" or result.value != bad


@pytest.mark.parametrize(
    "field,text,bad,clean",
    [
        (
            "reason",
            "사유는 판재 파손으로 초안 작성 부탁드립니다",
            "판재 파손으로 초안 작성 부탁드립니다",
            "판재 파손",
        ),
        ("reason", "사유는 나사 변형으로 부탁해요", "나사 변형으로 부탁해요", "나사 변형"),
        ("reason", "사유는 나사 변형으로 초안 부탁", "나사 변형으로 초안", "나사 변형"),
        ("title", "추가 작업명 천장 고정", "작업명 천장 고정", "천장 고정"),
        ("title", "공사명: 문틀 고정", "공사명: 문틀 고정", "문틀 고정"),
        ("reason", "금액 40만원, 추가 사유 고정 불량", "추가 사유 고정 불량", "고정 불량"),
        (
            "title",
            "금액은 40만원으로 제안하고 작업명은 천장 고정",
            "작업명은 천장 고정",
            "천장 고정",
        ),
    ],
)
def test_real_assignment_boundary(field, text, bad, clean):
    assert assignment_directive_in_value(field, bad, text)
    assert not assignment_directive_in_value(field, clean, text)


@pytest.mark.parametrize(
    "field,text,value",
    [
        ("title", "항목명은 작업명 표기 오류", "작업명 표기 오류"),
        ("reason", "사유: 사유 확인 지연", "사유 확인 지연"),
        ("title", "항목명은 '추가 작업명 천장 고정'입니다", "추가 작업명 천장 고정"),
        ("reason", '사유는 "석고보드 균열로 초안 부탁"', "석고보드 균열로 초안 부탁"),
        ("title", "항목명은 설계 초안 작성", "설계 초안 작성"),
        ("reason", "사유는 고객 요청으로 공정 변경", "고객 요청으로 공정 변경"),
        ("reason", "사유는 기존 도면으로 확정된 공법과 불일치", "기존 도면으로 확정된 공법"),
        ("reason", "사유는 현장 보고로 초안 부탁서 누락", "현장 보고로 초안 부탁서 누락"),
    ],
)
def test_real_nouns_and_quoted_names_preserved(field, text, value):
    assert not assignment_directive_in_value(field, value, text)


@pytest.mark.parametrize(
    "field,text,prior,expected",
    [
        (
            "days",
            "앞서 제안한 공사일 20일만 23일로 이동",
            DAYS,
            ["2026-12-03", "2026-12-23"],
        ),
        (
            "excluded",
            "아까 제안한 제외일 중 15일 대신 17일로 이동",
            EXCLUDED,
            ["2026-12-09", "2026-12-17", "2026-12-21"],
        ),
        (
            "excluded",
            "이전 제안한 제외 날짜에서 16일을 18일로 교체 부탁",
            ["2026-12-10", "2026-12-16"],
            ["2026-12-10", "2026-12-18"],
        ),
        (
            "days",
            "앞에서 제안한 작업 날짜 21일 대신 25일로 바꿔요",
            ["2026-12-07", "2026-12-21"],
            ["2026-12-07", "2026-12-25"],
        ),
        (
            "days",
            "3일은 건드리지 말고, 20일만 23일로 이동",
            DAYS,
            ["2026-12-03", "2026-12-23"],
        ),
        (
            "days",
            "20일만 23일로 이동하고 인원은 그대로 두세요.",
            DAYS,
            ["2026-12-03", "2026-12-23"],
        ),
    ],
)
def test_definite_date_moves(field, text, prior, expected):
    before = list(prior)
    context = CTX.model_copy(update={"required_days": len(prior)})
    assert date_replacement_requested(field, text)
    assert date_edit_reference_prefix(field, text) is not None
    assert validated_date_edit(field, text, prior, context, require_affirmative=True) == expected
    assert prior == before


@pytest.mark.parametrize(
    "field,text,prior",
    [
        ("days", "20일만 23일로 이동할까요?", DAYS),
        ("days", "20일만 23일로 이동할지 확인해 주세요", DAYS),
        ("days", "20일만 23일로 이동 가능", DAYS),
        ("days", "20일만 23일로 이동하지 마세요", DAYS),
        ("days", "20일만 23일로 안 이동합니다", DAYS),
        ("days", "20일만 23일로 이동 예정", DAYS),
        ("days", "20일만 23일로 이동 안 합니다", DAYS),
        ("days", "20일만 23일로 이동 취소", DAYS),
        ("days", "20일만 23일로 이동, 3일도 4일로 이동", DAYS),
        ("days", "20일은 건드리지 말고, 20일만 23일로 이동", DAYS),
        ("days", "20일만 23일로 이동, 4일은 그대로", DAYS),
        ("days", "20일만 3일로 이동", DAYS),
        ("days", "19일만 23일로 이동", DAYS),
        ("days", "20일만 32일로 이동", DAYS),
        ("days", "20일만 1월 23일로 이동", DAYS),
        ("days", "20일만 23일로 이동", ["2026-12-20", "2027-01-20"]),
        ("days", "제외일 20일만 23일로 이동", DAYS),
        ("days", "시작일 20일만 23일로 이동", DAYS),
        ("excluded", "작업일 15일 대신 17일로 이동", EXCLUDED),
        ("excluded", "15일 대신 17일로 이동", EXCLUDED),
        ("excluded", "제외일 15일 대신 17일로 이동하면 될까요?", EXCLUDED),
        ("excluded", "제외일 15일 대신 17일로 교체 부탁할까요?", EXCLUDED),
        ("excluded", "가능하면 제외일 15일 대신 17일로 교체 부탁", EXCLUDED),
        ("excluded", "제외일 15일 대신 17일로 교체 부탁은 취소", EXCLUDED),
    ],
)
def test_incomplete_unsafe_or_wrong_scope_date_moves_are_rejected(field, text, prior):
    context = CTX.model_copy(update={"required_days": len(prior)})
    assert validated_date_edit(field, text, prior, context, require_affirmative=True) is None


def test_nominal_move_intent_is_not_a_bare_date_enumeration():
    assert date_replacement_requested("days", "작업일 20일만 이동")
    assert date_replacement_requested("days", "작업일 20일을 23일로 이동할지 확인")
    assert not date_replacement_requested("days", "작업일 20일과 23일 현장 이동 동선 확인")
    assert validated_date_edit("days", "20일만 23일로 이동할지 확인", DAYS, CTX) is None


def test_archived_d09_t2_restores_whole_verified_list(monkeypatch):
    # 동결 요청의 이전 후보, 규칙의 두 날짜 열거, 모델의 모호 응답을 재생한다.
    current = {"days": ["2026-12-09", "2026-12-16"], "crew": 2}
    text = "앞서 제안한 공사일 20일만 23일로 이동"
    prior_id = "5d85a846-e919-4c69-bdca-41e5e9f92614"
    previous = PreviousCandidate(
        request_id=prior_id,
        text="12월 3일, 20일 작업하고 인원은 4명으로 제안해주세요",
        fields=[
            _set("days", DAYS, "12월 3일, 20일 작업"),
            _set("crew", 4, "인원은 4명으로"),
        ],
        baselines=current,
        sources={"days": "llm", "crew": "llm"},
        origins={"days": prior_id, "crew": prior_id},
    )
    request = AssistRequest(
        request_id="23f2797f-3afa-4db1-a21c-3d57887a13aa",
        form="dates",
        resource_id=UUID(int=179),
        text=text,
        current=current,
        previous_candidate=previous,
        rule_fields=[_set("days", ["2026-12-20", "2026-12-23"], text), _keep("crew")],
    )
    model = [
        FieldIntent(
            field="days", action="uncertain", value=None, evidence=text, intent_kind="ambiguous"
        ),
        _keep("crew", model=True),
    ]
    result = _run(monkeypatch, request, CTX.model_copy(update={"required_days": 2}), model)
    candidate = result["days"]
    assert (candidate.status, candidate.action, candidate.value) == (
        "valid",
        "set",
        ["2026-12-03", "2026-12-23"],
    )
    assert candidate.value_source == "derived"
    assert candidate.correction.rejected_value == DAYS
    assert not candidate.alternatives
