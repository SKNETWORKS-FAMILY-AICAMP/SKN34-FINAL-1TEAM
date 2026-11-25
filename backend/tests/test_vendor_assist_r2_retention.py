"""R2: 실제 유지 대상, 참조 명사, 독립 절 및 취소 범위."""

import asyncio
from datetime import date
from uuid import UUID

import pytest

from app.agent import vendor_assist
from app.vendor.assist_intent import retention_scopes
from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret
from app.vendor.assist_validation import normalized, review_fields

CTX = AssistContext(today=date(2026, 11, 23), base_month=date(2026, 12, 1))
RESOURCE = UUID("00000000-0000-4000-8000-000000000179")
BASES = {
    "quote": {"amount": 3000000, "vat": "included", "days": 2, "valid": "2026-12-18"},
    "period": {"start": "2026-12-03", "end": "2026-12-24", "excluded": []},
    "dates": {"days": ["2026-12-07", "2026-12-09"], "crew": 2},
    "extra": {"title": "점검", "reason": "기존 사유", "amount": 120000, "vat": "included"},
}
PROPOSALS = {
    "quote": {
        "amount": (5000000, "견적 금액 500만원"),
        "vat": ("excluded", "부가세 별도"),
        "days": (6, "공사 일수 6일"),
        "valid": ("2026-12-28", "유효기간 12월 28일"),
    },
    "period": {
        "start": ("2026-12-05", "시작일 12월 5일"),
        "end": ("2026-12-28", "종료일 12월 28일"),
        "excluded": (["2026-12-12"], "제외일 12월 12일"),
    },
    "dates": {
        "days": (["2026-12-08", "2026-12-10"], "작업 날짜 12월 8일과 10일"),
        "crew": (3, "인원 3명"),
    },
    "extra": {
        "title": ("문틀 보강", "항목명은 문틀 보강"),
        "reason": ("고정 불량", "사유는 고정 불량"),
        "amount": (530000, "추가 금액 53만원"),
        "vat": ("excluded", "부가세 별도"),
    },
}


def prior(form, names=None):
    selected = {
        name: value for name, value in PROPOSALS[form].items() if names is None or name in names
    }
    return PreviousCandidate(
        request_id="old",
        text="\n".join(evidence for _, evidence in selected.values()),
        fields=[
            FieldIntent(field=name, action="set", value=value, evidence=evidence)
            for name, (value, evidence) in selected.items()
        ],
        origins={name: "origin-" + name for name in selected},
        sources={name: "llm" for name in selected},
        baselines={name: BASES[form][name] for name in selected},
    )


def request(form, text, previous, *, current=None, rules=()):
    return AssistRequest(
        request_id="new",
        resource_id=RESOURCE,
        form=form,
        text=text,
        current=BASES[form] if current is None else current,
        previous_candidate=previous,
        rule_fields=list(rules),
    )


def retain(name, evidence, kind="retain_previous"):
    return FieldIntent(
        field=name,
        action="uncertain" if kind == "ambiguous" else "keep",
        value=None,
        evidence=evidence,
        intent_kind=kind,
        value_source={"retain_previous": "previous", "retain_current": "current"}.get(kind),
    )


def assert_prior(candidate, form, name):
    assert (
        candidate.status,
        candidate.action,
        candidate.value,
        candidate.origin_request_id,
        candidate.source,
    ) == ("valid", "set", PROPOSALS[form][name][0], "origin-" + name, "llm"), candidate


@pytest.mark.parametrize(
    "form,fields,text,evidence,kind",
    [
        (
            "quote",
            ("valid",),
            "공사 일수만 5일로 변경하고 유효기간은 앞서 정한 날짜 그대로",
            "유효기간은 앞서 정한 날짜 그대로",
            "retain_previous",
        ),
        (
            "quote",
            ("amount", "days"),
            "부가세는 별도로 결정하고 나머지는 그대로 유지해 주세요",
            "나머지는 그대로",
            "retain_current",
        ),
        (
            "quote",
            ("days",),
            "금액은 현재 입력한 값대로 두겠습니다. 기간은 아까 정한 그대로 유지",
            "기간은 아까 정한 그대로",
            "retain_previous",
        ),
        (
            "quote",
            ("days",),
            "공사 일수는 앞서 수정한 값대로 유지하고 부가세 면세로 확정",
            "공사 일수는 앞서 수정한 값대로",
            "ambiguous",
        ),
        (
            "quote",
            ("amount", "vat"),
            "견적과 세금은 앞서 정한 대로 유지하고 공사는 5일로 확정",
            "견적과 세금은 앞서 정한 대로 유지",
            "retain_previous",
        ),
        (
            "quote",
            ("amount",),
            "공사 기간을 5일로 바꾸고 견적 금액은 그대로 유지해 주세요",
            "견적 금액은 그대로",
            "retain_current",
        ),
        (
            "quote",
            ("amount",),
            "작업 기간은 5일로 결정하고 금액은 앞서 제안한 값대로",
            "금액은 앞서 제안한 값대로",
            "ambiguous",
        ),
        (
            "period",
            ("end",),
            "시작일만 12월 6일로 바꾸고 종료일은 앞서 정한 대로 유지",
            "종료일은 앞서 정한 대로",
            "retain_previous",
        ),
        (
            "period",
            ("start", "end", "excluded"),
            "시작일과 종료일, 제외일은 앞서 정한 값으로 유지해 주세요",
            "시작일과 종료일, 제외일은 앞서 정한 값으로",
            "retain_previous",
        ),
        (
            "period",
            ("start",),
            "시작일은 앞서 정한 날짜대로 유지하고 제외일 12월 15일로 확정",
            "시작일은 앞서 정한 날짜대로",
            "retain_previous",
        ),
        (
            "period",
            ("start", "end", "excluded"),
            "실측 가능 기간과 제외일은 이전 제안 그대로 유지",
            "실측 가능 기간과 제외일은 이전 제안 그대로",
            "retain_previous",
        ),
        (
            "period",
            ("start", "end", "excluded"),
            "앞서 정한 기간과 제외 날짜 모두 그대로 유지해 주세요",
            "앞서 정한 기간과 제외 날짜 모두 그대로",
            "retain_previous",
        ),
        (
            "dates",
            ("days",),
            "작업자는 네 명으로 수정하고 날짜는 아까 정한 값으로 유지",
            "날짜는 아까 정한 값으로",
            "retain_previous",
        ),
        (
            "dates",
            ("days", "crew"),
            "작업일하고 작업자 수는 앞서 정한 값 그대로 유지할게요",
            "작업일하고 작업자 수는 앞서 정한 값 그대로",
            "ambiguous",
        ),
        (
            "dates",
            ("crew",),
            "작업일은 12월 8일, 12일로 변경하고 인원은 앞서 제안한 그대로 유지",
            "인원은 앞서 제안한 그대로",
            "retain_previous",
        ),
        (
            "extra",
            ("title", "reason"),
            "추가금만 60만원으로 바꾸고 항목명과 사유는 앞서 정한 대로 유지",
            "항목명과 사유는 앞서 정한 대로",
            "retain_previous",
        ),
        (
            "extra",
            ("title", "reason", "amount", "vat"),
            "항목명과 사유, 금액, 부가세를 앞서 정한 그대로 유지해 주세요",
            "항목명과 사유, 금액, 부가세를 앞서 정한 그대로",
            "ambiguous",
        ),
        (
            "extra",
            ("title",),
            "추가 비용은 49만 또는 55만 중 미정입니다. 작업명은 앞서 정한 그대로 유지",
            "작업명은 앞서 정한 그대로",
            "retain_previous",
        ),
        (
            "extra",
            ("title", "amount"),
            "부가세는 면세로 선택하고 항목명과 금액은 앞서 정한 대로 유지",
            "항목명과 금액은 앞서 정한 대로",
            "retain_previous",
        ),
        (
            "extra",
            ("title", "amount"),
            "부가세는 별도로 정정하고 나머지는 앞서 정한 그대로 유지",
            "나머지는 앞서 정한 그대로",
            "ambiguous",
        ),
    ],
)
def test_scoped_retention_preserves_verified_candidate(form, fields, text, evidence, kind):
    previous = prior(form)
    data = request(form, text, previous)
    checked, _ = review_fields(
        [retain(name, evidence, kind) for name in fields], "llm", data, CTX, previous=previous
    )
    for name in fields:
        assert_prior(checked[name], form, name)


@pytest.mark.parametrize(
    "form,field,text,evidence",
    [
        (
            "quote",
            "days",
            "유효기간은 앞서 정한 날짜 그대로 유지",
            "유효기간은 앞서 정한 날짜 그대로",
        ),
        (
            "quote",
            "valid",
            "유효기간은 앞서 정한 일수 그대로 유지",
            "유효기간은 앞서 정한 일수 그대로",
        ),
        (
            "quote",
            "amount",
            "가능하면 금액과 부가세는 앞서 정한 대로 유지",
            "금액과 부가세는 앞서 정한 대로",
        ),
        (
            "quote",
            "amount",
            "금액은 앞서 정한 대로 유지. 금액은 현재 값 그대로 유지",
            "금액은 앞서 정한 대로",
        ),
        (
            "quote",
            "amount",
            "금액은 앞서 정한 대로 유지. 금액은 600만원으로 바꿀게요",
            "금액은 앞서 정한 대로",
        ),
        (
            "period",
            "end",
            "시작일과 종료일은 앞서 정한 대로 유지할까요?",
            "시작일과 종료일은 앞서 정한 대로",
        ),
        (
            "dates",
            "crew",
            "인원은 앞서 정한 인원으로 가고요. 4명으로 바꾸고 날짜는 12월 11일로 잡아 주세요.",
            "인원은 앞서 정한 인원으로",
        ),
        (
            "extra",
            "title",
            "항목명은 앞서 정한 그대로 유지. 그 요청은 취소합니다.",
            "항목명은 앞서 정한 그대로",
        ),
    ],
)
def test_scope_does_not_weaken_competing_reference_or_risk(form, field, text, evidence):
    previous = prior(form)
    data = request(form, text, previous)
    checked, _ = review_fields([retain(field, evidence)], "llm", data, CTX, previous=previous)
    assert not (checked[field].status == "valid" and checked[field].action == "set")


def test_scope_record_uses_actual_subject_not_referent():
    raw = normalized("유효기간은 앞서 정한 날짜 그대로")
    (scope,) = retention_scopes("quote", raw)
    assert scope.fields == {"valid"}
    assert scope.reference == "previous"
    assert raw[scope.start : scope.end] == raw


def run(monkeypatch, data, intents):
    async def stub(*args):
        return ModelInterpretation(fields=intents)

    monkeypatch.setattr(vendor_assist, "interpret", stub)
    trace = {}
    result = asyncio.run(interpret(data, CTX, trace=trace))
    assert result.source == "llm"
    return {field.field: field for field in result.fields}, trace


def test_compound_cancel_only_releases_its_field(monkeypatch):
    old = prior("extra", {"title", "amount"})
    text = "항목명 변경 요청은 취소하겠습니다. 추가 금액은 앞서 정한 그대로 유지해 주세요"
    fields, _ = run(
        monkeypatch,
        request("extra", text, old),
        [
            retain("title", "항목명 변경 요청은 취소하겠습니다", "ambiguous"),
            retain("amount", "추가 금액은 앞서 정한 그대로"),
        ],
    )
    assert (
        fields["title"].status,
        fields["title"].action,
        fields["title"].intent_kind,
        fields["title"].value_source,
    ) == ("valid", "keep", "retain_current", "current")
    assert_prior(fields["amount"], "extra", "amount")


def test_invalid_rule_title_in_retention_scope_is_not_conflict_alternative(monkeypatch):
    old = prior("extra", {"title", "reason", "amount", "vat"})
    text = "항목명과 사유, 금액, 부가세를 앞서 정한 그대로 유지해주세요"
    bogus = FieldIntent(
        field="title", action="set", value="항목명과 사유", evidence="항목명과 사유"
    )
    fields, trace = run(
        monkeypatch,
        request("extra", text, old, rules=[bogus]),
        [retain("title", text)],
    )
    assert_prior(fields["title"], "extra", "title")
    assert not fields["title"].alternatives
    assert trace["rule_reviewed"][0]["status"] == "invalid"


def test_plain_pending_policy_does_not_restore_stale_value(monkeypatch):
    old = prior("quote", {"amount"})
    text = "금액은 그대로 유지해 주세요"
    fields, trace = run(
        monkeypatch,
        request("quote", text, old, current={**BASES["quote"], "amount": 4200000}),
        [retain("amount", text, "retain_current")],
    )
    assert trace["previous_rejected"]["amount"] == "stale_context"
    assert fields["amount"].status != "valid"
