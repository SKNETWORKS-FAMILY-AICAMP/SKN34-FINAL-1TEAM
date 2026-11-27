"""R9 독립 철회 상태 대조. 실제 서비스, 오프라인 생산자만 사용한다."""

import asyncio
import os
import sys
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.config import settings
from app.vendor.assist_schemas import (
    FORM_FIELDS,
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

# 현 폼과 미적용 후보를 분리한다. 모든 seed 후보는 실제 서비스에서 검증한다.
FORMS = {
    "quote": {
        "current": {"amount": 3_200_000, "vat": "included", "days": 3, "valid": "2026-12-22"},
        "seed": (
            ("amount", 4_100_000, "견적 금액은 410만원으로 제안합니다."),
            ("vat", "excluded", "부가세는 별도로 지정합니다."),
            ("days", 5, "공사 일수는 5일로 지정합니다."),
        ),
    },
    "period": {
        "current": {"start": "2026-12-03", "end": "2026-12-25", "excluded": ["2026-12-16"]},
        "seed": (
            ("start", "2026-12-07", "시작일은 12월 7일로 제안합니다."),
            ("end", "2026-12-28", "종료일은 12월 28일로 지정합니다."),
            ("excluded", ["2026-12-11", "2026-12-18"], "제외일은 12월 11일과 18일로 정합니다."),
        ),
    },
    "dates": {
        "current": {"days": ["2026-12-08", "2026-12-15"], "crew": 2},
        "seed": (
            ("days", ["2026-12-10", "2026-12-17"], "작업일은 12월 10일과 17일로 제안합니다."),
            ("crew", 4, "투입 인원은 4명으로 지정합니다."),
        ),
    },
    "extra": {
        "current": {
            "title": "보수",
            "reason": "실측 후 추가",
            "amount": 120_000,
            "vat": "included",
        },
        "seed": (
            ("title", "문틀 보강", "항목명은 문틀 보강으로 입력합니다."),
            ("reason", "마감 손상", "사유는 마감 손상으로 기재합니다."),
            ("amount", 340_000, "추가 금액은 34만원으로 제안합니다."),
        ),
    },
}


def _set(field, value, evidence):
    return FieldIntent(
        field=field,
        action="set",
        value=value,
        evidence=evidence,
        intent_kind="explicit_change",
        value_source="utterance",
    )


def _keep(field, evidence, source="current"):
    return FieldIntent(
        field=field,
        action="keep",
        value=None,
        evidence=evidence,
        intent_kind="retain_" + source,
        value_source=source,
    )


def _hold(field, evidence):
    return FieldIntent(
        field=field, action="uncertain", value=None, evidence=evidence, intent_kind="ambiguous"
    )


@pytest.fixture(autouse=True)
def _isolated_app():
    root = os.environ.get("R9WITHDRAW_ROOT")
    if root:
        backend = Path(root).resolve() / "backend"
        assert all(
            Path(module.__file__).resolve().is_relative_to(backend)
            for name, module in tuple(sys.modules.items())
            if (name == "app" or name.startswith("app.")) and getattr(module, "__file__", None)
        )


def _run(monkeypatch, form, text, candidates, producer, *, prior=None, current=None, rid="follow"):
    assert not settings.llm_live
    current = dict(FORMS[form]["current"] if current is None else current)
    filled = [
        next(
            (item for item in candidates if item.field == field),
            FieldIntent(
                field=field, action="keep", value=None, evidence="", intent_kind="no_mention"
            ),
        )
        for field in FORM_FIELDS[form]
    ]
    req = AssistRequest(
        request_id=rid,
        resource_id=UUID(int=179),
        form=form,
        text=text,
        current=current,
        previous_candidate=prior,
        rule_fields=filled if producer in ("rule", "both") else [],
    )
    ctx = AssistContext(
        today=date(2026, 11, 25),
        base_month=date(2026, 12, 1),
        days_max=120,
        required_days=2 if form == "dates" else None,
        crew_max=6,
        vat_modes=["included", "excluded"]
        if form == "extra"
        else ["included", "excluded", "exempt"],
    )
    before = req.model_dump(mode="json")
    calls = []

    async def stub(data, context):
        calls.append(data)
        return ModelInterpretation(fields=filled)

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    res = asyncio.run(interpret(req, ctx, use_model=producer != "rule", trace=trace))
    assert len(calls) == int(producer != "rule")
    assert req.model_dump(mode="json") == before
    assert res.request_id == rid
    return {item.field: item for item in res.fields}, trace


def _history(text, fields, current, rid, prior=None):
    eligible = [item for item in fields.values() if item.action == "set" and item.status == "valid"]
    if not eligible:
        return None
    return PreviousCandidate(
        request_id=rid,
        text=text if prior is None else prior.text + "\n" + text,
        fields=[
            FieldIntent(
                field=item.field, action=item.action, value=item.value, evidence=item.evidence
            )
            for item in eligible
        ],
        baselines={item.field: current[item.field] for item in eligible},
        sources={item.field: item.source for item in eligible},
        origins={item.field: item.origin_request_id for item in eligible},
    )


def _seed(monkeypatch, form, producer):
    seed = FORMS[form]["seed"]
    text = " ".join(part[2] for part in seed)
    fields, trace = _run(
        monkeypatch, form, text, [_set(*part) for part in seed], producer, rid="seed"
    )
    for field, value, _ in seed:
        assert (fields[field].action, fields[field].status, fields[field].value) == (
            "set",
            "valid",
            value,
        ), trace
    return _history(text, fields, FORMS[form]["current"], "seed")


def _release(item, text):
    assert (item.action, item.status, item.intent_kind, item.value_source) == (
        "keep",
        "valid",
        "retain_current",
        "current",
    ), item
    assert item.value is None and not item.alternatives
    assert item.origin_request_id == "follow"
    assert item.evidence.strip() and item.evidence.strip() in text


def _unsafe(item):
    assert item.action not in ("set", "clear") and not item.alternatives, item
    assert not (
        item.status == "valid" and item.action == "keep" and item.intent_kind == "retain_current"
    ), item


def _carried(item, prior):
    old = next(part for part in prior.fields if part.field == item.field)
    assert (item.status, item.action, item.value, item.intent_kind, item.value_source) == (
        "valid",
        "set",
        old.value,
        "retain_previous",
        "previous",
    ), item
    assert item.origin_request_id == prior.origins[item.field]
    assert item.source == prior.sources[item.field] and item.evidence == old.evidence


# 철회 대상의 실제 변경행위 명사/관형형과 동일 의미의 완결 요청을 대조한다.
POSITIVE = [
    ("period", ("start",), "시작일 조정은 철회합니다."),
    ("period", ("end",), "종료일 연장 요청을 취소해 주세요."),
    ("period", ("excluded",), "제외일 목록 수정안은 철회합니다."),
    ("period", ("start", "end"), "시작일과 종료일 조정 요청은 취소합니다."),
    ("quote", ("amount",), "견적 금액 조정안을 철회합니다."),
    ("quote", ("days",), "공사 일수 단축 요청은 취소합니다."),
    ("quote", ("vat",), "부가세 설정 요청은 철회해 주세요."),
    ("quote", ("amount", "vat"), "견적 금액과 부가세 변경 요청은 취소합니다."),
    ("dates", ("days",), "작업일 재배치 요청을 철회합니다."),
    ("dates", ("crew",), "인원 조정 요청은 취소합니다."),
    ("dates", ("days", "crew"), "작업일과 투입 인원 변경안을 철회합니다."),
    ("extra", ("title",), "항목명 기재 요청은 철회합니다."),
    ("extra", ("reason",), "사유 수정안은 취소합니다."),
    ("extra", ("amount",), "추가 금액 증액 요청은 철회합니다."),
    ("extra", ("title", "reason"), "항목명과 사유 정정 요청을 취소합니다."),
]


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize("form,fields,text", POSITIVE)
def test_r9_explicit_withdrawal(monkeypatch, producer, form, fields, text):
    prior = _seed(monkeypatch, form, producer)
    candidates = [_keep(field, text.rstrip(".")) for field in fields]
    response, trace = _run(monkeypatch, form, text, candidates, producer, prior=prior)
    for field in fields:
        _release(response[field], text)
    # 실제 review 단계도 동일 의미 증명을 사용해야 한다.
    for layer in ("rule_reviewed", "model_reviewed"):
        if layer == "model_reviewed" and producer == "rule":
            continue
        if layer == "rule_reviewed" and producer == "model":
            continue
        for item in trace[layer]:
            if item["field"] in fields:
                assert (item["status"], item["action"], item["intent_kind"]) == (
                    "valid",
                    "keep",
                    "retain_current",
                ), item
    new_prior = _history(text, response, FORMS[form]["current"], "follow", prior)
    # 철회한 필드를 다음 턴 무언급에서 다시 살려서는 안 된다.
    next_fields, _ = _run(
        monkeypatch, form, "다른 칸은 확인 중입니다.", [], producer, prior=new_prior, rid="next"
    )
    for field in fields:
        assert next_fields[field].action not in ("set", "clear")


# 불완전한 철회, 조건, 경쟁, 실제 업무 취소, 과거 보고에는 현재값 확정 권한이 없다.
NEGATIVE = [
    ("period", "start", "시작일 조정은 철회할까요?"),
    ("period", "end", "종료일 변경은 취소하지 마세요."),
    ("period", "start", "가능하면 시작일 변경 요청을 철회합니다."),
    ("period", "start", "종료일을 늘릴 수 있으면 시작일 변경은 취소합니다."),
    ("period", "start", "시작일 변경은 취소합니다. 시작일은 12월 8일로 변경할지 고민 중입니다."),
    ("period", "start", "지난번 시작일 변경 요청은 이미 철회했습니다."),
    ("period", "start", "실측 자체를 취소합니다."),
    ("quote", "amount", "견적 금액 변경은 취소할지 아직 모르겠습니다."),
    ("quote", "vat", "부가세 조건 변경을 철회하라는 뜻은 아닙니다."),
    ("quote", "amount", "고객에게 이미 보낸 견적서를 취소합니다."),
    ("dates", "crew", "작업을 취소합니다."),
    ("dates", "days", "작업일 변경 요청은 철회하지 않겠습니다."),
    ("extra", "amount", "추가 공사 자체를 취소합니다."),
    ("extra", "reason", "사유 변경을 취소할 수도 있습니다."),
    ("extra", "title", "항목명 수정 요청을 어제 취소했어요."),
    ("quote", "amount", "견적 금액 조정 요청 철회 가능 여부를 알려 주세요."),
    ("period", "start", "시작일 변경은 취소한다고 고객에게 전달해 주세요."),
    ("dates", "crew", "인원 변경을 취소합니다. 그런데 그건 취소하지 마세요."),
]


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize("form,field,text", NEGATIVE)
def test_r9_unsafe_withdrawal_never_releases(monkeypatch, producer, form, field, text):
    prior = _seed(monkeypatch, form, producer)
    response, _ = _run(
        monkeypatch, form, text, [_keep(field, text.rstrip("."))], producer, prior=prior
    )
    _unsafe(response[field])


# 같은 철회와 독립 유지/신규 변경/미정의 조합. 유지 시 이전 출처가 보존되어야 한다.
MIXED = [
    (
        "period",
        "start",
        "시작일 조정 요청은 철회합니다",
        "end",
        "2026-12-30",
        "실측 마감은 12월 30일로 지정합니다",
        "excluded",
        "제외일은 앞서 제안한 그대로 유지해 주세요",
    ),
    (
        "quote",
        "amount",
        "견적 금액 조정은 철회합니다",
        "days",
        6,
        "공사 일수는 6일로 확정합니다",
        "vat",
        "부가세는 앞서 제안한 그대로 유지해 주세요",
    ),
    (
        "extra",
        "title",
        "항목명 기재 요청은 철회합니다",
        "amount",
        380_000,
        "추가 금액은 38만원으로 확정합니다",
        "reason",
        "사유는 앞서 제안한 그대로 유지해 주세요",
    ),
]


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize("form,drop,withdraw,change,value,assignment,keep,retention", MIXED)
def test_r9_independent_sibling_state(
    monkeypatch, producer, form, drop, withdraw, change, value, assignment, keep, retention
):
    prior = _seed(monkeypatch, form, producer)
    text = f"{withdraw}. {assignment}. {retention}."
    candidates = [
        _keep(drop, withdraw),
        _set(change, value, assignment),
        _keep(keep, retention, "previous"),
    ]
    response, _ = _run(monkeypatch, form, text, candidates, producer, prior=prior)
    _release(response[drop], text)
    assert (response[change].action, response[change].status, response[change].value) == (
        "set",
        "valid",
        value,
    )
    _carried(response[keep], prior)


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
def test_r9_dates_withdraw_and_independent_hold(monkeypatch, producer):
    prior = _seed(monkeypatch, "dates", producer)
    withdraw = "투입 인원 조정 요청은 철회합니다"
    inquiry = "작업일은 12월 9일로 변경할지 아직 미정입니다"
    text = withdraw + ". " + inquiry + "."
    response, _ = _run(
        monkeypatch,
        "dates",
        text,
        [_keep("crew", withdraw), _hold("days", inquiry)],
        producer,
        prior=prior,
    )
    _release(response["crew"], text)
    _unsafe(response["days"])


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize("prior_mode", ["matched", "missing", "stale", "wrong_value"])
def test_r9_named_vat_selection_proof(monkeypatch, producer, prior_mode):
    prior = _seed(monkeypatch, "quote", producer)
    current = dict(FORMS["quote"]["current"])
    if prior_mode == "missing":
        prior = None
    elif prior_mode == "stale":
        current["vat"] = "exempt"
    elif prior_mode == "wrong_value":
        prior = prior.model_copy(
            update={
                "fields": [
                    item.model_copy(update={"value": "exempt"}) if item.field == "vat" else item
                    for item in prior.fields
                ]
            }
        )
    text = "부가세 별도 선택은 철회합니다."
    response, trace = _run(
        monkeypatch,
        "quote",
        text,
        [_keep("vat", text.rstrip("."))],
        producer,
        prior=prior,
        current=current,
    )
    if prior_mode == "matched":
        _release(response["vat"], text)
    else:
        _unsafe(response["vat"])
        for layer in ("rule_reviewed", "model_reviewed"):
            for item in trace.get(layer) or []:
                if item["field"] == "vat":
                    assert item["intent_kind"] != "retain_current" or item["status"] != "valid"


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
def test_r9_wrong_field_and_forged_evidence(monkeypatch, producer):
    prior = _seed(monkeypatch, "period", producer)
    text = "시작일 변경 요청은 취소합니다."
    response, trace = _run(
        monkeypatch,
        "period",
        text,
        [_keep("end", "종료일 변경 요청은 취소합니다")],
        producer,
        prior=prior,
    )
    _release(response["start"], text)
    # 독립 철회 근거로 다른 필드의 hold를 풀 수 없다.
    _unsafe(response["end"])
    for layer in ("rule_reviewed", "model_reviewed"):
        for item in trace.get(layer) or []:
            if item["field"] == "end":
                assert item["status"] != "valid" or item["intent_kind"] != "retain_current"


# 명시적으로 이전 후보 자체를 지목할 때만 값·이력 증명을 요구한다.
# 이미 설명한 변경행위 자체의 철회와 구분한다(기존 stage2 계약).
VALUE_WITHDRAWAL = [
    (
        "quote",
        "amount",
        "앞서 제안한 견적 금액 410만원 후보를 철회합니다.",
        "앞서 제안한 견적 금액 420만원 후보를 철회합니다.",
        3_700_000,
    ),
    (
        "dates",
        "crew",
        "앞서 제안한 인원 4명 후보를 취소합니다.",
        "앞서 제안한 인원 5명 후보를 취소합니다.",
        3,
    ),
    (
        "period",
        "start",
        "앞서 제안한 시작일 12월 7일 후보를 철회합니다.",
        "앞서 제안한 시작일 12월 9일 후보를 철회합니다.",
        "2026-12-04",
    ),
    (
        "extra",
        "title",
        "앞서 제안한 항목명 문틀 보강 후보를 취소합니다.",
        "앞서 제안한 항목명 벽면 보수 후보를 취소합니다.",
        "문짝 수리",
    ),
]


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize("prior_mode", ["matched", "mismatch", "missing", "stale"])
@pytest.mark.parametrize("form,field,matched,mismatch,manual", VALUE_WITHDRAWAL)
def test_r9_explicit_value_withdrawal_proof(
    monkeypatch, producer, prior_mode, form, field, matched, mismatch, manual
):
    prior = _seed(monkeypatch, form, producer)
    current = dict(FORMS[form]["current"])
    text = mismatch if prior_mode == "mismatch" else matched
    if prior_mode == "missing":
        prior = None
    elif prior_mode == "stale":
        current[field] = manual
    response, trace = _run(
        monkeypatch,
        form,
        text,
        [_keep(field, text.rstrip("."))],
        producer,
        prior=prior,
        current=current,
    )
    if prior_mode == "matched":
        _release(response[field], text)
    else:
        _unsafe(response[field])
        for layer in ("rule_reviewed", "model_reviewed"):
            for item in trace.get(layer) or []:
                if item["field"] == field:
                    assert item["status"] != "valid" or item["intent_kind"] != "retain_current"


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "form,field,text",
    [
        ("period", "start", "시작일은 변경 요청 철회 여부를 확인합니다."),
        ("dates", "crew", "인원 변경은 취소가 아닙니다."),
        ("extra", "title", "항목명과 무관한 작업 요청을 취소합니다."),
        ("quote", "amount", "견적 금액 변경안을 취소했다는 보고서를 확인해 주세요."),
    ],
)
def test_r9_malformed_operation_object_never_releases(monkeypatch, producer, form, field, text):
    prior = _seed(monkeypatch, form, producer)
    response, _ = _run(
        monkeypatch, form, text, [_keep(field, text.rstrip("."))], producer, prior=prior
    )
    _unsafe(response[field])


@pytest.mark.parametrize("producer", ["rule", "model", "both"])
@pytest.mark.parametrize(
    "form,field,text",
    [
        ("quote", "amount", "견적 금액 410만원으로 변경하려던 요청은 철회합니다."),
        ("dates", "crew", "인원 4명으로 변경하려던 요청은 취소합니다."),
    ],
)
def test_r9_described_operation_without_prior_remains_current_keep(
    monkeypatch, producer, form, field, text
):
    response, _ = _run(monkeypatch, form, text, [_keep(field, text.rstrip("."))], producer)
    _release(response[field], text)
