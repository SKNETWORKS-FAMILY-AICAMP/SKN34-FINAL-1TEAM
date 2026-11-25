"""R2/R3 잔여: 대입값 경계와 날짜 교체의 확정 근거."""

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
    CorrectionRelation,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

CTX = AssistContext(today=date(2026, 11, 24), base_month=date(2026, 12, 1), crew_max=6)
DATE_CTX = CTX.model_copy(update={"required_days": 2})
EXTRA = {"title": "보강", "reason": "현장 추가", "amount": 1000000, "vat": "included"}
CURRENT_DATES = {"days": ["2026-12-09", "2026-12-16"], "crew": 2}
PRIOR_DAYS = ["2026-12-07", "2026-12-24"]


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
    trace = {}
    result = asyncio.run(interpret(request, context, use_model=use_model, trace=trace))
    assert len(calls) == int(use_model)
    return {field.field: field for field in result.fields}, trace


@pytest.mark.parametrize(
    "field,label,value,suffix",
    [
        ("title", "항목명은", "바닥 이음부 보완", "으로 지정해주세요"),
        ("title", "제목은", "방음재 접합부 보강", "으로 조정해주세요"),
        ("reason", "사유는", "고정 불량", "으로 다시 표기해 주세요"),
        ("title", "작업명은", "문틀 보수", "로 명시 부탁드립니다"),
        ("reason", "이유는", "고정 나사 손상", "으로 기재해줘"),
        ("title", "항목명은", "천장 보강", "으로 정하겠습니다"),
        ("title", "제목은", "마루 보수", "로 잡아 주세요"),
        ("reason", "사유는", "도장면 파손", "으로 후보 등록 부탁"),
        ("title", "공사명은", "벽면 보강", "으로 교체해요"),
        ("title", "작업명은", "도어 보완", "으로 지정해 주실 수 있나요?"),
        ("reason", "사유는", "배관 고정 불량", "으로 최종 작성해 주세요"),
    ],
)
def test_assignment_verbs_with_explicit_request_endings(field, label, value, suffix):
    bad = (value + suffix).rstrip("?")
    text = f"{label} {value}{suffix}"
    assert assignment_directive_in_value(field, bad, text)
    assert not assignment_directive_in_value(field, value, text)


@pytest.mark.parametrize(
    "field,text,value",
    [
        ("title", "항목명은 창호 높이 조정", "창호 높이 조정"),
        ("reason", "사유는 기준 높이로 조정", "기준 높이로 조정"),
        ("reason", "사유는 설계 도면으로 지정된 위치의 오차", "설계 도면으로 지정된 위치의 오차"),
        ("reason", "사유는 고객 요청으로 지정 품목 변경", "고객 요청으로 지정 품목 변경"),
        ("title", "제목은 가구 배치 조정 작업", "가구 배치 조정 작업"),
        ("title", "항목명은 '바닥 보완으로 지정해주세요'입니다", "바닥 보완으로 지정해주세요"),
        ("reason", '사유는 "문틀 보강으로 조정해주세요"', "문틀 보강으로 조정해주세요"),
        ("reason", "사유는 초안 표기 누락", "초안 표기 누락"),
        ("title", "제목은 작업명 지정 오류", "작업명 지정 오류"),
        ("reason", "사유는 기존 도면으로 지정된 자재", "기존 도면으로 지정"),
        (
            "reason",
            "사유는 문틀 높이로 조정해주세요라는 요청 전달 누락",
            "문틀 높이로 조정해주세요",
        ),
    ],
)
def test_real_noun_phrases_and_literal_names_remain_eligible(field, text, value):
    assert not assignment_directive_in_value(field, value, text)


@pytest.mark.parametrize("case", ["E08T4", "E12T5"])
@pytest.mark.parametrize("use_model", [True, False])
def test_archived_assignment_suffix_never_selectable(monkeypatch, case, use_model):
    # qa179-r31-live50-results.json의 요청·이전 후보·모델 응답 값과 근거를 재생한다.
    if case == "E08T4":
        text = "항목명은 바닥 이음부 보완으로 지정해주세요"
        value = "바닥 이음부 보완"
        bad = "바닥 이음부 보완으로 지정해주세요"
        prior = PreviousCandidate(
            request_id="1810081d-224a-4ea1-971c-43a75495c89c",
            text=(
                "추가 사유는 바닥 접합부 단차, 추가금은 46만원으로 제안\n"
                "사유는 마감재 두께 차이로 수정합니다\n"
                "추가금은 현재 폼에 입력된 값대로 두겠습니다"
            ),
            fields=[_set("reason", "마감재 두께 차이", "사유는 마감재 두께 차이로 수정합니다")],
            sources={"reason": "llm"},
            origins={"reason": "7c8a8ad1-75e7-4b18-84f6-3cd40cab593c"},
            baselines={"reason": "현장 추가"},
        )
        request_id = "674d7bbe-9b01-476f-9f9b-6d5e0af7fafa"
    else:
        text = "제목은 방음재 접합부 보강으로 조정해주세요"
        value = "방음재 접합부 보강"
        bad = "방음재 접합부 보강으로 조정해주세요"
        prior = PreviousCandidate(
            request_id="e88fdb9d-753e-441c-b049-8d7aaffaf6a9",
            text=(
                "추가 작업명 방음재 보강, 추가 금액 61만원으로 후보 부탁드립니다\n"
                "추가 사유는 접착면 박리로 기재해주세요\n"
                "세금 별도 방식으로 표시 부탁합니다\n"
                "추가금은 64만원으로 변경하겠습니다"
            ),
            fields=[
                _set("title", "방음재 보강", "추가 작업명 방음재 보강"),
                _set("reason", "접착면 박리", "추가 사유는 접착면 박리로 기재해주세요"),
                _set("amount", 640000, "추가금은 64만원으로 변경"),
                _set("vat", "excluded", "세금 별도 방식으로 표시"),
            ],
            sources={field: "llm" for field in EXTRA},
            origins={
                "title": "69312e02-7c6f-4e37-8d8a-7b1e0ee413e5",
                "reason": "369c2d78-99fb-4bc5-a994-9d2645c5f57d",
                "amount": "e88fdb9d-753e-441c-b049-8d7aaffaf6a9",
                "vat": "452d8d32-75d1-4bf8-bf3b-870a4ce6cc71",
            },
            baselines=EXTRA,
        )
        request_id = "ee1398e3-ebb9-4781-8cd8-f5af14a8b08d"
    others = ("reason", "amount", "vat")
    request = AssistRequest(
        request_id=request_id,
        resource_id=UUID("00000000-0000-4000-8000-000000000179"),
        form="extra",
        text=text,
        current=EXTRA,
        previous_candidate=prior,
        rule_fields=[_set("title", bad, text), *(_keep(field) for field in others)],
    )
    model = [_set("title", value, text, model=True), *(_keep(f, model=True) for f in others)]
    fields, trace = _run(monkeypatch, request, CTX, model, use_model=use_model)
    candidate = fields["title"]
    assert not candidate.alternatives, trace
    if use_model:
        assert (candidate.status, candidate.action, candidate.value) == ("valid", "set", value)
    else:
        assert candidate.action != "set" or candidate.value != bad


@pytest.mark.parametrize(
    "text",
    [
        "앞에서 제안한 작업일 7일 대신 9일로 바꾸고 싶어요. 변경 부탁합니다",
        "작업일 7일만 9일로 옮기고 싶습니다. 그렇게 변경해 주세요.",
        "공사일 7일을 9일로 수정하고 싶어요\n수정 부탁드립니다",
        "작업일 7일 대신 9일로 미루고 싶어요. 변경 부탁합니다!",
        "24일은 건드리지 말고, 작업일 7일만 9일로 변경하고 싶어요. 변경 부탁",
    ],
)
def test_adjacent_definite_request_confirms_desire(text):
    assert date_replacement_requested("days", text)
    assert validated_date_edit("days", text, PRIOR_DAYS, DATE_CTX, require_affirmative=True) == [
        "2026-12-09",
        "2026-12-24",
    ]


@pytest.mark.parametrize(
    "text",
    [
        "작업일 7일 대신 9일로 바꾸고 싶어요",
        "작업일 7일 대신 9일로 바꾸고 싶어요. 변경 가능할까요?",
        "작업일 7일 대신 9일로 바꾸고 싶어요. 변경 부탁할까요?",
        "작업일 7일 대신 9일로 바꾸고 싶어요. 변경 부탁합니다만 아직 미정입니다",
        "작업일 7일 대신 9일로 바꾸고 싶어요. 인원 변경 부탁합니다",
        "작업일 7일 대신 9일로 바꾸고 싶어요. 변경 부탁합니다. 다시 검토해 주세요",
        "작업일 7일 대신 9일로 바꾸고 싶어요. 11일로 변경 부탁합니다",
        "작업일 7일 대신 9일로 바꾸고 싶어요. 변경 부탁합니다. 24일도 옮겨주세요",
        "작업일 7일 대신 9일로 바꾸고 싶어요. 변경 부탁합니다. 취소합니다",
        "가능하면 작업일 7일 대신 9일로 바꾸고 싶어요. 변경 부탁합니다",
        "작업일 7일 대신 9일로 당기고 싶어요. 변경 부탁합니다",
        "작업일 7일 대신 9일로 바꾸고 싶어요. 변경은 하지 마세요",
        "작업일 7일은 건드리지 말고, 7일 대신 9일로 바꾸고 싶어요. 변경 부탁합니다",
        "작업일 7일 대신 24일로 바꾸고 싶어요. 변경 부탁합니다",
        "작업일 8일 대신 9일로 바꾸고 싶어요. 변경 부탁합니다",
        "작업일 7일 대신 32일로 바꾸고 싶어요. 변경 부탁합니다",
        "제외일 7일 대신 9일로 바꾸고 싶어요. 변경 부탁합니다",
        '작업일 "7일 대신 9일로 바꾸고 싶어요. 변경 부탁합니다"라고 말했어요',
    ],
)
def test_desire_cannot_bypass_direction_scope_or_certainty(text):
    assert validated_date_edit("days", text, PRIOR_DAYS, DATE_CTX, require_affirmative=True) is None


def test_desire_alone_not_confirmed_even_when_affirmative_not_required():
    text = "작업일 7일 대신 9일로 바꾸고 싶어요"
    assert validated_date_edit("days", text, PRIOR_DAYS, DATE_CTX) is None


def test_archived_d11_t3_correction_proof(monkeypatch):
    text = "앞에서 제안한 작업일 7일 대신 9일로 바꾸고 싶어요. 변경 부탁합니다"
    prior = PreviousCandidate(
        request_id="ad54c33d-f01f-4380-882d-5e763a55593a",
        text=("작업 일정 12월 7일, 24일로 후보를 잡겠습니다\n투입 인원 여섯 명으로 정해주세요"),
        fields=[
            _set("days", PRIOR_DAYS, "12월 7일, 24일로 후보를 잡겠습니다"),
            _set("crew", 6, "투입 인원 여섯 명으로 정해주세요"),
        ],
        sources={"days": "llm", "crew": "llm"},
        origins={
            "days": "d23afc65-8cfe-4719-8d17-7439a29b6f83",
            "crew": "ad54c33d-f01f-4380-882d-5e763a55593a",
        },
        baselines=CURRENT_DATES,
    )
    request = AssistRequest(
        request_id="2ed8a34b-b1e7-41e5-9fea-2c8dbaae513f",
        resource_id=UUID("00000000-0000-4000-8000-000000000179"),
        form="dates",
        text=text,
        current=CURRENT_DATES,
        previous_candidate=prior,
        rule_fields=[_keep("days"), _keep("crew")],
    )
    model = [
        FieldIntent(
            field="days",
            action="set",
            value=["2026-12-09", "2026-12-24"],
            evidence="앞에서 제안한 작업일 7일 대신 9일로 바꾸고 싶어요",
            intent_kind="explicit_change",
            value_source="derived",
            correction=CorrectionRelation(
                kind="replace",
                rejected_value=["2026-12-07"],
                rejected_evidence="7일 대신",
                accepted_evidence="9일로 바꾸고 싶어요",
            ),
        ),
        _keep("crew", model=True),
    ]
    fields, trace = _run(monkeypatch, request, DATE_CTX, model)
    candidate = fields["days"]
    assert (candidate.status, candidate.action, candidate.value) == (
        "valid",
        "set",
        ["2026-12-09", "2026-12-24"],
    ), trace
    assert candidate.value_source == "derived"
    assert candidate.correction.rejected_value == PRIOR_DAYS
    assert not candidate.alternatives


def test_archived_d12_t13_edit_requires_verified_reset_list():
    # T12 전체 새 목록이 검증된 경우만 기존 T1~T10 목록을 대신할 수 있다.
    text = "직전 작업일 중 8일을 10일로 이동합니다"
    latest = ["2026-12-08", "2026-12-26"]
    assert date_edit_reference_prefix("days", text) == "직전 작업일 중 "
    assert date_replacement_requested("days", text)
    assert validated_date_edit("days", text, latest, DATE_CTX, require_affirmative=True) == [
        "2026-12-10",
        "2026-12-26",
    ]
    assert (
        validated_date_edit(
            "days", text, ["2026-12-06", "2026-12-24"], DATE_CTX, require_affirmative=True
        )
        is None
    )
    assert (
        validated_date_edit("days", text, CURRENT_DATES["days"], DATE_CTX, require_affirmative=True)
        is None
    )
    assert validated_date_edit("days", text, None, DATE_CTX, require_affirmative=True) is None
