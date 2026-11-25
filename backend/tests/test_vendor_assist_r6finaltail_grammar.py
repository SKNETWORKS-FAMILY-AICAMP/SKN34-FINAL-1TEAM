"""완결 제안 요청과 선언형 유지의 독립 서비스 회귀. 실제 외부 호출 없음."""

import asyncio
import os
import sys
from datetime import date
from pathlib import Path
from uuid import UUID

import pytest

from app.agent import llm, vendor_assist
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

RESOURCE = UUID("00000000-0000-4000-8000-000000000179")
TODAY = date(2026, 11, 25)
MONEY = 643000
MONEY_TEXT = "비용은 64만 3천 원으로 확정합니다."
TAX_TEXT = "부가세는 별도로 처리합니다."
BASES = {
    "extra": {"title": "보강", "reason": "현장 추가", "amount": 1000000, "vat": "included"},
    "quote": {"amount": 4550000, "days": 3, "vat": "included", "valid": "2026-12-17"},
    "period": {"start": "2026-12-02", "end": "2026-12-24", "excluded": []},
    "dates": {"days": ["2026-12-09", "2026-12-16"], "crew": 2},
}
# 명확한 대입은 실제 명사 구간과 완결 요청 구간을 분리한다.
# field, label, literal, particle + predicate
GRAMMAR = [
    ("title", "제목은", "창호 주변 마감", "으로 등록할 후보를 부탁드립니다"),
    ("reason", "추가 사유는", "걸레받이 틈새 발생", "으로 작성할 초안 부탁합니다"),
    ("title", "작업명은", "현관 바닥 정리", "로 새로 반영할 후보 부탁해요"),
    ("reason", "사유는", "문턱 높이 차이", "로 기재할 후보를 만들어 주세요"),
    ("title", "항목명은", "천장 모서리 보강", "으로 우선 입력할 초안 부탁드립니다"),
    ("reason", "이유는", "배관 주변 흔들림", "으로 수정할 후보를 제안해주세요"),
    ("title", "항목명은", "몰딩 연결부 보수", "로 후보 등록을 부탁합니다"),
    ("reason", "추가 사유는", "벽체 안쪽 습기", "로 제안할 후보를 보여 주세요"),
]
LITERALS = [
    ("title", '제목은 "최종 등록할 후보 검토"로 정합니다.', "최종 등록할 후보 검토"),
    ("title", "항목명은 등록할 후보 안내판으로 확정합니다.", "등록할 후보 안내판"),
    ("reason", "사유는 기재할 초안 검토 누락으로 기록합니다.", "기재할 초안 검토 누락"),
    (
        "reason",
        "사유는 ‘누수 확인으로 등록할 후보 부탁합니다’로 적어주세요.",
        "누수 확인으로 등록할 후보 부탁합니다",
    ),
    (
        "title",
        "제목은 '현장 기록, 비용은 23만원!'으로 등록할 후보 부탁합니다.",
        "현장 기록, 비용은 23만원!",
    ),
    (
        "reason",
        '사유는 "부가세는 포함. 후보 확인 누락"으로 작성할 초안 부탁드립니다.',
        "부가세는 포함. 후보 확인 누락",
    ),
]
NEGATIVES = [
    ("title", "제목은 창호 마감으로 등록할 후보 부탁할까요?", "창호 마감"),
    ("reason", "가능하면 사유는 누수로 작성할 초안 부탁합니다.", "누수"),
    ("title", "항목명은 배관 보수로 등록할 후보는 부탁하지 마세요.", "배관 보수"),
    ("reason", "사유는 결로 발생으로 등록할 후보 부탁합니다. 사유 변경은 취소합니다.", "결로 발생"),
    ("title", "제목은 천장 정리로 등록할 후보", "천장 정리로 등록할 후보"),
    ("reason", "사유는 바닥 긁힘으로 다시 반영할 초안 부탁", "바닥 긁힘으로 다시 반영할 초안"),
    ("title", "제목은 '벽면 마감으로 등록할 후보 부탁합니다.", "벽면 마감"),
    ("reason", "사유는 자재 파손으로 등록할 후보가 아직 미정입니다.", "자재 파손"),
    ("title", "제목은 마감 교체 또는 창틀 보수로 등록할 후보를 검토 중입니다.", "창틀 보수"),
]
# 실제 첫 후보와 최근 후보를 별개 턴에서 만들고 원래 폼과 출처를 보존한다.
HISTORY = {
    "quote": (
        (
            "days",
            7,
            "공사 기간은",
            "공사 기간은 7일로 제안합니다.",
            9,
            "공사 기간은 9일로 변경합니다.",
        ),
        (
            "amount",
            4850000,
            "견적 금액은",
            "견적 금액은 485만원으로 제안합니다.",
            5170000,
            "견적 금액은 517만원으로 변경합니다.",
        ),
    ),
    "period": (
        (
            "start",
            "2026-12-06",
            "시작일은",
            "시작일은 12월 6일로 제안합니다.",
            "2026-12-08",
            "시작일은 12월 8일로 변경합니다.",
        ),
        (
            "end",
            "2026-12-22",
            "종료일은",
            "종료일은 12월 22일로 제안합니다.",
            "2026-12-23",
            "종료일은 12월 23일로 변경합니다.",
        ),
    ),
    "dates": (
        (
            "days",
            ["2026-12-10", "2026-12-22"],
            "시공 날짜는",
            "시공 날짜는 12월 10일과 22일로 제안합니다.",
            ["2026-12-11", "2026-12-23"],
            "시공 날짜는 12월 11일과 23일로 변경합니다.",
        ),
        ("crew", 3, "인원은", "인원은 3명으로 제안합니다.", 4, "인원은 4명으로 변경합니다."),
    ),
    "extra": (
        (
            "reason",
            "배관 고정 불량",
            "추가 사유는",
            "추가 사유는 배관 고정 불량으로 정합니다.",
            "벽면 손상",
            "추가 사유는 벽면 손상으로 변경합니다.",
        ),
        (
            "amount",
            273000,
            "비용은",
            "비용은 27만 3천 원으로 제안합니다.",
            369000,
            "비용은 36만 9천 원으로 변경합니다.",
        ),
    ),
}


def field(name, action="keep", value=None, evidence="", *, intent=None, source=None):
    return FieldIntent(
        field=name,
        action=action,
        value=value,
        evidence=evidence,
        intent_kind=intent,
        value_source=source,
    )


def set_value(name, value, evidence):
    return field(name, "set", value, evidence, intent="explicit_change", source="utterance")


def keep(name, evidence, reference="previous"):
    return field(name, evidence=evidence, intent="retain_" + reference, source=reference)


def uncertain(name, evidence):
    return field(name, "uncertain", evidence=evidence, intent="ambiguous")


def context(form):
    return AssistContext(
        today=TODAY,
        base_month=date(2026, 12, 1),
        crew_max=6,
        required_days=2 if form == "dates" else None,
    )


def verify_imports():
    root = os.environ.get("R6FINALTAIL_TEST_ROOT")
    if root:
        expected = Path(root).resolve() / "backend"
        loaded = [m for n, m in sys.modules.items() if n == "app" or n.startswith("app.")]
        assert all(
            Path(m.__file__).resolve().is_relative_to(expected)
            for m in loaded
            if getattr(m, "__file__", None)
        ), "mixed source imports"
    assert not settings.llm_live


async def service(mp, request, model, use_model):
    verify_imports()
    calls = []

    async def forbidden(*args, **kwargs):
        pytest.fail("provider call forbidden")

    async def stub(*args, **kwargs):
        calls.append(args)
        return ModelInterpretation(
            fields=[
                next((f for f in model if f.field == name), field(name, intent="no_mention"))
                for name in FORM_FIELDS[request.form]
            ]
        )

    mp.setattr(llm, "structured", forbidden)
    mp.setattr(vendor_assist, "interpret", stub)
    trace = {}
    before = request.model_dump(mode="json")
    response = await interpret(request, context(request.form), use_model=use_model, trace=trace)
    assert response.source == ("llm" if use_model else "rule")
    assert response.request_id == request.request_id and len(calls) == int(use_model)
    assert before == request.model_dump(mode="json")
    verify_imports()
    return response, trace


def run(
    mp, form, text, rules=(), model=(), *, use_model=True, previous=None, current=None, rid="turn"
):
    request = AssistRequest(
        form=form,
        resource_id=RESOURCE,
        request_id=rid,
        text=text,
        current=BASES[form] if current is None else current,
        rule_fields=list(rules),
        previous_candidate=previous,
    )
    response, trace = asyncio.run(service(mp, request, model, use_model))
    return {f.field: f for f in response.fields}, trace


def exact(f, value):
    assert (f.status, f.action, f.value) == ("valid", "set", value), f
    assert not f.alternatives


def no_offer(f):
    assert f.action not in ("set", "clear") and not f.alternatives, f


def carry(f, previous):
    before = next(item for item in previous.fields if item.field == f.field)
    exact(f, before.value)
    assert f.intent_kind == "retain_previous" and f.value_source == "previous", f
    assert f.origin_request_id == previous.origins[f.field], f
    assert f.source == previous.sources[f.field] and f.evidence == before.evidence, f


def prior_for(form, rid, text, fields):
    selected = [f for f in fields.values() if f.status == "valid" and f.action == "set"]
    return PreviousCandidate(
        request_id=rid,
        text=text,
        fields=[field(f.field, "set", f.value, f.evidence) for f in selected],
        origins={f.field: f.origin_request_id for f in selected},
        sources={f.field: f.source for f in selected},
        baselines={f.field: BASES[form][f.field] for f in selected},
    )


def without(previous, name):
    return previous.model_copy(
        update={
            "fields": [f for f in previous.fields if f.field != name],
            **{
                k: {f: v for f, v in getattr(previous, k).items() if f != name}
                for k in ("origins", "sources", "baselines")
            },
        }
    )


def extras():
    return [set_value("amount", MONEY, MONEY_TEXT), set_value("vat", "excluded", TAX_TEXT)]


def add_scalars(text):
    return (
        text.rstrip(".") + ("" if text.endswith("?") else ".") + " " + MONEY_TEXT + " " + TAX_TEXT
    )


def grammar_clause(case):
    name, label, value, suffix = case
    return name, value, f"{label} {value}{suffix}", suffix


@pytest.mark.parametrize("case", GRAMMAR)
@pytest.mark.parametrize("mode", ["rule", "stub", "bad-rule", "bad-model", "both-bad"])
def test_structured_request_value_boundary(monkeypatch, case, mode):
    name, value, clause, suffix = grammar_clause(case)
    good = set_value(name, value, clause)
    contaminated = set_value(name, value + suffix, clause)
    # 근거를 잘라 전달해도 정확한 원문 대입 경계를 검증한다.
    clipped_value = value + suffix.split(" 후보", 1)[0].split(" 초안", 1)[0]
    if clipped_value == contaminated.value:
        clipped_value = value + suffix[: suffix.find(" ") + 4]
    clipped = set_value(name, clipped_value, f"{case[1]} {clipped_value}")
    rules = [good] if mode in ("rule", "stub", "bad-model") else [contaminated]
    model = [good] if mode in ("stub", "bad-rule") else [clipped]
    fields, _ = run(
        monkeypatch,
        "extra",
        add_scalars(clause),
        rules + extras(),
        model + extras(),
        use_model=mode != "rule",
    )
    exact(fields["amount"], MONEY)
    exact(fields["vat"], "excluded")
    if mode == "both-bad":
        no_offer(fields[name])
    else:
        exact(fields[name], value)


@pytest.mark.parametrize("case", GRAMMAR)
@pytest.mark.parametrize("kind", ["full-directive", "partial-value", "label", "directive-fragment"])
@pytest.mark.parametrize("use_model", [False, True])
def test_forged_fragment_never_offered(monkeypatch, case, kind, use_model):
    name, value, clause, suffix = grammar_clause(case)
    wrong = {
        "full-directive": value + suffix,
        "partial-value": value.rsplit(" ", 1)[0],
        "label": case[1] + " " + value,
        "directive-fragment": value + suffix[: suffix.find(" ") + 4],
    }[kind]
    forged = set_value(name, wrong, f"{case[1]} {wrong}" if kind != "label" else wrong)
    fields, _ = run(
        monkeypatch,
        "extra",
        add_scalars(clause),
        [forged, *extras()],
        [forged, *extras()],
        use_model=use_model,
    )
    no_offer(fields[name])
    exact(fields["amount"], MONEY)
    exact(fields["vat"], "excluded")


@pytest.mark.parametrize("name,text,value", LITERALS)
@pytest.mark.parametrize("use_model", [False, True])
def test_literal_nominal_and_quoted_request_content(monkeypatch, name, text, value, use_model):
    good = set_value(name, value, text)
    fields, _ = run(
        monkeypatch,
        "extra",
        add_scalars(text),
        [good, *extras()],
        [good, *extras()],
        use_model=use_model,
    )
    exact(fields[name], value)
    exact(fields["amount"], MONEY)
    exact(fields["vat"], "excluded")
    # 인용 내부 스칼라/다른 필드 문구로 별도 값을 만들지 않는다.
    no_offer(fields["reason" if name == "title" else "title"])


@pytest.mark.parametrize("name,text,value", NEGATIVES)
@pytest.mark.parametrize("use_model", [False, True])
def test_incomplete_or_nonaffirmative_proposal_request(monkeypatch, name, text, value, use_model):
    wrong = set_value(name, value, text)
    fields, _ = run(
        monkeypatch,
        "extra",
        MONEY_TEXT + " " + TAX_TEXT + " " + text,
        [wrong, *extras()],
        [wrong, *extras()],
        use_model=use_model,
    )
    no_offer(fields[name])
    exact(fields["amount"], MONEY)
    exact(fields["vat"], "excluded")


def seed_history(mp, form, use_model, *, replace_first=False):
    first, recent = HISTORY[form]
    seed = first[3] + " " + recent[3]
    initial = [set_value(first[0], first[1], first[3]), set_value(recent[0], recent[1], recent[3])]
    fields, _ = run(mp, form, seed, initial, initial, use_model=use_model, rid="first")
    for f in initial:
        exact(fields[f.field], f.value)
    previous = prior_for(form, "first", seed, fields)
    second = recent[5]
    changed = [set_value(recent[0], recent[4], recent[5])]
    if replace_first:
        second += " " + first[5]
        changed.append(set_value(first[0], first[4], first[5]))
    fields, _ = run(
        mp,
        form,
        second,
        changed,
        changed,
        use_model=use_model,
        previous=previous,
        rid="recent",
    )
    exact(fields[recent[0]], recent[4])
    if replace_first:
        exact(fields[first[0]], first[4])
    else:
        carry(fields[first[0]], previous)
    return prior_for(form, "recent", seed + "\n" + second, fields)


@pytest.mark.parametrize("form", list(HISTORY))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("ending", ["그대로입니다", "그대로예요", "그대로 유지합니다"])
def test_declarative_first_and_recent_reference(monkeypatch, form, use_model, ending):
    first, recent = HISTORY[form]
    previous = seed_history(monkeypatch, form, use_model)
    a = first[2] + " 처음 후보 " + ending
    b = recent[2] + " 방금 정한 후보 " + ending
    text = a + ". " + b + "."
    fields, _ = run(
        monkeypatch,
        form,
        text,
        model=[keep(first[0], a), keep(recent[0], b)],
        use_model=use_model,
        previous=previous,
    )
    carry(fields[first[0]], previous)
    carry(fields[recent[0]], previous)
    assert fields[first[0]].origin_request_id == "first"
    assert fields[recent[0]].origin_request_id == "recent"


@pytest.mark.parametrize("form", list(HISTORY))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("ending", ["그대로입니다", "그대로 적용해주세요"])
def test_shared_finite_reference_predicate(monkeypatch, form, use_model, ending):
    first, recent = HISTORY[form]
    previous = seed_history(monkeypatch, form, use_model)
    a = first[2] + " 처음 후보"
    b = recent[2] + " 방금 정한 후보 " + ending
    text = a + ", " + b + "."
    fields, _ = run(
        monkeypatch,
        form,
        text,
        model=[keep(first[0], a), keep(recent[0], b)],
        use_model=use_model,
        previous=previous,
    )
    carry(fields[first[0]], previous)
    carry(fields[recent[0]], previous)


@pytest.mark.parametrize("form", list(HISTORY))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("replace_first", [False, True])
def test_first_reference_alone_requires_actual_first_origin(
    monkeypatch, form, use_model, replace_first
):
    first, recent = HISTORY[form]
    previous = seed_history(monkeypatch, form, use_model, replace_first=replace_first)
    text = first[2] + " 최초 후보 그대로입니다."
    fields, _ = run(
        monkeypatch,
        form,
        text,
        model=[keep(first[0], text)],
        use_model=use_model,
        previous=previous,
    )
    if replace_first:
        no_offer(fields[first[0]])
    else:
        carry(fields[first[0]], previous)
        assert fields[first[0]].origin_request_id == "first"
    carry(fields[recent[0]], previous)


@pytest.mark.parametrize("form", list(HISTORY))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize("ending", ["그대로인가요?", "그대로가 아닙니다.", ""])
def test_unfinished_or_nonaffirmative_shared_reference(monkeypatch, form, use_model, ending):
    first, recent = HISTORY[form]
    previous = seed_history(monkeypatch, form, use_model)
    a = first[2] + " 처음 후보"
    b = recent[2] + " 방금 정한 후보 " + ending
    fields, _ = run(
        monkeypatch,
        form,
        a + ", " + b,
        model=[keep(first[0], a), keep(recent[0], b.strip())],
        use_model=use_model,
        previous=previous,
    )
    no_offer(fields[first[0]])
    no_offer(fields[recent[0]])


@pytest.mark.parametrize("form", list(HISTORY))
@pytest.mark.parametrize("use_model", [False, True])
@pytest.mark.parametrize(
    "variant",
    [
        "current",
        "condition",
        "negative",
        "question",
        "bare",
        "cancel",
        "missing",
        "stale",
        "wrong-first",
    ],
)
def test_retention_identity_guards(monkeypatch, form, use_model, variant):
    first, recent = HISTORY[form]
    previous = seed_history(monkeypatch, form, use_model, replace_first=variant == "wrong-first")
    current = dict(BASES[form])
    a = first[2] + " 처음 후보 그대로입니다"
    b = recent[2] + " 방금 정한 후보 그대로 유지합니다"
    if variant == "current":
        a = first[2] + " 현재 폼 입력값 그대로입니다"
    elif variant == "condition":
        a = "가능하면 " + a
    elif variant == "negative":
        a = first[2] + " 처음 후보 그대로가 아닙니다"
    elif variant == "question":
        a = first[2] + " 처음 후보 그대로인가요?"
    elif variant == "bare":
        a = first[2] + " 처음 후보"
    elif variant == "cancel":
        a += ". " + first[2][:-1] + " 변경은 취소합니다"
    elif variant == "missing":
        previous = without(previous, first[0])
    elif variant == "stale":
        current[first[0]] = first[4]
    text = a + ("" if a.endswith("?") else ".") + " " + b + "."
    # 모델이 위험한 참조를 유지로 오인해도 실제 절 및 이전 상태를 확인한다.
    model = [
        keep(first[0], a, "current" if variant == "current" else "previous"),
        keep(recent[0], b),
    ]
    fields, _ = run(
        monkeypatch,
        form,
        text,
        model=model,
        previous=previous,
        current=current,
        use_model=use_model,
    )
    no_offer(fields[first[0]])
    carry(fields[recent[0]], previous)


@pytest.mark.parametrize("form", list(HISTORY))
@pytest.mark.parametrize("use_model", [False, True])
def test_canceled_history_cannot_be_revived_by_declarative_identity(monkeypatch, form, use_model):
    first, recent = HISTORY[form]
    previous = seed_history(monkeypatch, form, use_model)
    cancel = first[2][:-1] + " 변경은 취소합니다."
    fields, _ = run(
        monkeypatch,
        form,
        cancel,
        model=[keep(first[0], cancel, "current")],
        previous=previous,
        use_model=use_model,
        rid="cancel",
    )
    no_offer(fields[first[0]])
    carry(fields[recent[0]], previous)
    # 취소 직전 후보와 원본 출처를 그대로 위조해도 누적 이력의 철회가 우선한다.
    forged = previous.model_copy(update={"text": previous.text + "\n" + cancel})
    text = first[2] + " 처음 후보 그대로입니다. " + recent[2] + " 방금 정한 후보 그대로 유지합니다."
    fields, _ = run(
        monkeypatch,
        form,
        text,
        model=[keep(first[0], text), keep(recent[0], text)],
        previous=forged,
        use_model=use_model,
    )
    no_offer(fields[first[0]])
    carry(fields[recent[0]], forged)
