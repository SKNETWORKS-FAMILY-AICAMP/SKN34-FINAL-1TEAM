"""Explicit value-span QA, independent of the rule extractor and model provider."""

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
    CorrectionRelation,
    FieldIntent,
    ModelInterpretation,
    PreviousCandidate,
)
from app.vendor.assist_service import interpret

RESOURCE = UUID("00000000-0000-4000-8000-000000000179")
TODAY = date(2026, 11, 25)
CURRENT = {"title": "보강", "reason": "현장 추가", "amount": 1000000, "vat": "included"}
AMOUNT = 682000
AMOUNT_CLAUSE = "금액은 68만 2천 원으로 확정합니다."
VAT_CLAUSE = "부가세는 별도로 처리합니다."

# Explicit subject, lexical value, remaining predicate. All gold is authored here;
# no unseen evaluation text is used. The other text field is independently bound.
SPANS = [
    ("title", "추가 작업은", "창틀 모서리 마감", "", "추가 사유는 줄눈 손상으로 적어주세요."),
    ("title", "추가공사는", "배관 덮개 설치", "", "이유는 고정 불량으로 기재합니다."),
    ("title", "추가 시공은", "문턱 하부 보수", "로 확정합니다", "사유는 수평 오차로 정합니다."),
    ("title", "추가 작업 제목은", "창가 몰딩 교체", "", "추가 사유는 접착제 부족으로 입력합니다."),
    (
        "title",
        "추가 공사 항목명은",
        "벽체 틈새 충전",
        "으로 등록할 후보 부탁드립니다",
        "이유는 바람 유입으로 적어주세요.",
    ),
    ("title", "추가 작업 이름은", "천장 이음매 정리", "", "사유는 연결부 단차로 등록합니다."),
    ("title", "항목 이름은", "선반 받침 설치", "", "추가 사유는 나사 누락으로 정합니다."),
    ("title", "작업명:", "현관 바닥 보수", "", "사유는 표면 손상으로 기록합니다."),
    ("title", "추가 작업을", "창호 테두리 마감", "으로 정합니다", "사유는 틈새 발생으로 정합니다."),
    ("reason", "추가 작업 사유는", "몰딩 끝부분 파손", "", "제목은 모서리 보강으로 기재합니다."),
    (
        "reason",
        "추가 공사 이유는",
        "바닥 높이 차이",
        "로 기록합니다",
        "항목명은 문턱 교체로 정합니다.",
    ),
    ("reason", "추가 사유는", "석고 접합 불량", "", "추가 작업은 이음부 보수로 기재합니다."),
    ("reason", "사유:", "배관 접촉 소음", "", "제목은 고정부 교체로 정합니다."),
]
OTHER_VALUES = [
    "줄눈 손상",
    "고정 불량",
    "수평 오차",
    "접착제 부족",
    "바람 유입",
    "연결부 단차",
    "나사 누락",
    "표면 손상",
    "틈새 발생",
    "모서리 보강",
    "문턱 교체",
    "이음부 보수",
    "고정부 교체",
]
CASES = [(*item, other) for item, other in zip(SPANS, OTHER_VALUES, strict=True)]

LITERALS = [
    ("title", '추가 작업은 "추가 작업은 안내 문구 교체"로 정합니다.', "추가 작업은 안내 문구 교체"),
    ("title", "추가 공사는 ‘추가 공사는 도색 안내’로 기재합니다.", "추가 공사는 도색 안내"),
    (
        "title",
        '추가 작업 제목은 "금액은 19만원, 부가세는 포함!"으로 등록합니다.',
        "금액은 19만원, 부가세는 포함!",
    ),
    (
        "reason",
        "추가 작업 사유는 '사유는 누수. 제목은 기록판'으로 적어주세요.",
        "사유는 누수. 제목은 기록판",
    ),
    ("title", "제목은 작업대 상판 보강으로 정합니다.", "작업대 상판 보강"),
    ("title", "항목명은 추가 작업 구역 정리로 확정합니다.", "추가 작업 구역 정리"),
    ("title", "추가 작업은 작업명 안내판 교체로 정합니다.", "작업명 안내판 교체"),
    ("title", "추가 작업은 공사명 표찰 설치로 기재합니다.", "공사명 표찰 설치"),
    ("reason", "사유는 추가 공사 이력 누락으로 기록합니다.", "추가 공사 이력 누락"),
    ("title", "추가 작업은 추가 작업 안전 표지 설치로 정합니다.", "추가 작업 안전 표지 설치"),
    ("title", "작업대 상판 보강", "작업대 상판 보강"),
    ("title", "추가 작업 구역 보호재 설치", "추가 작업 구역 보호재 설치"),
    (
        "title",
        '추가 작업은 "추가 작업은 점검 안내."로 등록할 후보 부탁합니다.',
        "추가 작업은 점검 안내.",
    ),
    ("reason", "사유는 ‘추가 작업은 확인 완료’로 정합니다.", "추가 작업은 확인 완료"),
]
UNSAFE = [
    ("title", "가능하면 추가 작업은 배관 덮개 교체로 제안합니다.", "배관 덮개 교체"),
    ("title", "추가 공사는 문턱 정리로 해도 될까요?", "문턱 정리"),
    ("title", "추가 작업은 걸레받이 교체로 등록하지 마세요.", "걸레받이 교체"),
    (
        "title",
        "추가 시공은 바닥 수평 보정으로 정합니다. 추가 시공 변경은 취소합니다.",
        "바닥 수평 보정",
    ),
    ("title", "추가 작업은", "추가 작업은"),
    ("title", "추가 작업은 ‘배관 받침 교체로 정합니다.", "배관 받침 교체"),
    ("title", "추가 작업은 조명 받침 보강으로 새로 정", "조명 받침 보강"),
    ("title", "추가 공사는 문틀 수리로 등록할 후보", "문틀 수리"),
    ("title", "추가 작업은 이전에 정한 후보 그대로입니다.", "이전에 정한 후보 그대로입니다"),
    ("title", "추가 작업은 바닥 도색 또는 몰딩 교체 중 고민입니다.", "몰딩 교체"),
    ("reason", "추가 작업 사유는 단차 발생으로 기록할까요?", "단차 발생"),
    ("reason", "추가 사유는 파손으로 정합니다. 사유 변경은 철회합니다.", "파손"),
    ("reason", "추가 공사 이유는 앞서 제안한 사유 그대로입니다.", "앞서 제안한 사유 그대로입니다"),
    ("reason", "추가 작업 사유는 결로로 적지 마세요.", "결로"),
]
FOREIGN = [
    (
        "추가 작업 금액은 68만 2천 원으로 확정합니다.",
        "추가 작업 금액은 68만 2천 원",
        "amount",
        AMOUNT,
    ),
    (
        "추가 공사 비용은 68만 2천 원으로 확정합니다.",
        "추가 공사 비용은 68만 2천 원",
        "amount",
        AMOUNT,
    ),
    ("추가 작업 사유는 바닥 눌림으로 기록합니다.", "바닥 눌림", "reason", "바닥 눌림"),
    (
        "추가 공사 이유는 접착 누락으로 정합니다.",
        "추가 공사 이유는 접착 누락",
        "reason",
        "접착 누락",
    ),
    ("부가세는 별도로 정합니다.", "부가세는 별도", "vat", "excluded"),
]


def field(
    name, action="keep", value=None, evidence="", *, intent=None, source=None, correction=None
):
    return FieldIntent(
        field=name,
        action=action,
        value=value,
        evidence=evidence,
        intent_kind=intent,
        value_source=source,
        correction=correction,
    )


def setting(name, value, evidence, correction=None):
    return field(
        name,
        "set",
        value,
        evidence,
        intent="explicit_change",
        source="utterance",
        correction=correction,
    )


def retain(name, evidence, ref="previous"):
    return field(name, evidence=evidence, intent="retain_" + ref, source=ref)


def verify_imports():
    root = os.environ.get("R6INTEGRATED_TEST_ROOT")
    if root:
        expected = Path(root).resolve() / "backend"
        modules = [m for n, m in sys.modules.items() if n == "app" or n.startswith("app.")]
        assert all(
            Path(m.__file__).resolve().is_relative_to(expected)
            for m in modules
            if getattr(m, "__file__", None)
        ), "mixed product source imports"
    assert not settings.llm_live


async def service(mp, request, model, use_model):
    verify_imports()
    calls = []

    async def forbidden(*args, **kwargs):
        pytest.fail("provider access forbidden")

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
    before = request.model_dump(mode="json")
    trace = {}
    response = await interpret(
        request,
        AssistContext(today=TODAY, base_month=date(2026, 12, 1), crew_max=6),
        use_model=use_model,
        trace=trace,
    )
    assert response.source == ("llm" if use_model else "rule")
    assert len(calls) == int(use_model)
    assert response.request_id == request.request_id
    assert before == request.model_dump(mode="json")
    verify_imports()
    return response, trace


def run(
    mp,
    text,
    rules=(),
    model=(),
    *,
    use_model=True,
    prior=None,
    current=None,
    rid="turn",
    form="extra",
):
    request = AssistRequest(
        request_id=rid,
        form=form,
        resource_id=RESOURCE,
        text=text,
        rule_fields=list(rules),
        previous_candidate=prior,
        current=CURRENT if current is None else current,
    )
    response, trace = asyncio.run(service(mp, request, model, use_model))
    return {f.field: f for f in response.fields}, trace


def exact(f, value):
    assert (f.status, f.action, f.value) == ("valid", "set", value), f
    assert not f.alternatives


def no_offer(f):
    assert f.action not in ("set", "clear") and not f.alternatives, f


def only_values(f, allowed):
    assert f.action != "clear"
    assert all(a.action == "set" and a.value in allowed for a in f.alternatives), f
    if f.action == "set":
        assert f.status == "valid" and f.value in allowed, f


def carried(f, prior):
    old = next(x for x in prior.fields if x.field == f.field)
    exact(f, old.value)
    assert (f.intent_kind, f.value_source) == ("retain_previous", "previous"), f
    assert f.origin_request_id == prior.origins[f.field]
    assert f.source == prior.sources[f.field] and f.evidence == old.evidence


def prior_for(text, rid, fields, current):
    selected = [f for f in fields.values() if f.status == "valid" and f.action == "set"]
    return PreviousCandidate(
        request_id=rid,
        text=text,
        fields=[field(f.field, "set", f.value, f.evidence) for f in selected],
        origins={f.field: f.origin_request_id for f in selected},
        sources={f.field: f.source for f in selected},
        baselines={f.field: current[f.field] for f in selected},
    )


def scalar_fields():
    return [setting("amount", AMOUNT, AMOUNT_CLAUSE), setting("vat", "excluded", VAT_CLAUSE)]


def with_scalars(text):
    return (
        text.rstrip(".")
        + ("" if text.endswith("?") else ".")
        + " "
        + AMOUNT_CLAUSE
        + " "
        + VAT_CLAUSE
    )


def material(case):
    name, label, val, ending, other_clause, other_val = case
    other_name = "reason" if name == "title" else "title"
    clause = f"{label} {val}{ending}"
    text = with_scalars(clause + (". " if ending else ", ") + other_clause)
    return text, setting(name, val, clause), setting(other_name, other_val, other_clause)


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("mode", ["rule", "stub", "bad-rule", "bad-model", "both-bad", "stub-only"])
def test_exact_nominal_span_producer_parity(monkeypatch, case, mode):
    name, label, val, _, _, _ = case
    text, good, other = material(case)
    polluted = setting(name, label + " " + val, label + " " + val)
    clipped = setting(name, val.rsplit(" ", 1)[0], val.rsplit(" ", 1)[0])
    rules = {
        "rule": [good],
        "stub": [good],
        "bad-rule": [polluted],
        "bad-model": [good],
        "both-bad": [polluted],
        "stub-only": [],
    }[mode]
    model = [clipped] if mode in ("bad-model", "both-bad") else [good]
    fields, _ = run(
        monkeypatch,
        text,
        [*rules, other, *scalar_fields()],
        [*model, other, *scalar_fields()],
        use_model=mode != "rule",
    )
    exact(fields[other.field], other.value)
    exact(fields["amount"], AMOUNT)
    exact(fields["vat"], "excluded")
    if mode == "both-bad":
        no_offer(fields[name])
    else:
        exact(fields[name], val)


@pytest.mark.parametrize("case", CASES)
@pytest.mark.parametrize("kind", ["label", "head-clip", "tail-clip", "other-field"])
@pytest.mark.parametrize("use_model", [False, True])
def test_forged_exact_value_proof(monkeypatch, case, kind, use_model):
    name, label, val, _, _, other_val = case
    text, _, other = material(case)
    wrong = {
        "label": label + " " + val,
        "head-clip": val.split(" ", 1)[1],
        "tail-clip": val.rsplit(" ", 1)[0],
        "other-field": other_val,
    }[kind]
    forged = setting(name, wrong, wrong)
    fields, _ = run(
        monkeypatch,
        text,
        [forged, other, *scalar_fields()],
        [forged, other, *scalar_fields()],
        use_model=use_model,
    )
    no_offer(fields[name])
    exact(fields[other.field], other.value)
    exact(fields["amount"], AMOUNT)
    exact(fields["vat"], "excluded")


@pytest.mark.parametrize("name,text,value", LITERALS)
@pytest.mark.parametrize("use_model", [False, True])
def test_real_nominal_and_quoted_literals(monkeypatch, name, text, value, use_model):
    good = setting(name, value, text)
    fields, _ = run(
        monkeypatch,
        with_scalars(text),
        [good, *scalar_fields()],
        [good, *scalar_fields()],
        use_model=use_model,
    )
    exact(fields[name], value)
    exact(fields["amount"], AMOUNT)
    exact(fields["vat"], "excluded")
    no_offer(fields["reason" if name == "title" else "title"])


@pytest.mark.parametrize("name,text,value", LITERALS[:4] + LITERALS[12:])
@pytest.mark.parametrize("use_model", [False, True])
def test_quoted_labels_exact_boundary(monkeypatch, name, text, value, use_model):
    bad = setting(name, value.rsplit(" ", 1)[0], text)
    fields, _ = run(monkeypatch, text, [bad], [bad], use_model=use_model)
    no_offer(fields[name])


@pytest.mark.parametrize("name,text,wrong", UNSAFE)
@pytest.mark.parametrize("use_model", [False, True])
def test_unsafe_incomplete_or_retracted_value(monkeypatch, name, text, wrong, use_model):
    forged = setting(name, wrong, text)
    fields, _ = run(
        monkeypatch,
        AMOUNT_CLAUSE + " " + VAT_CLAUSE + " " + text,
        [forged, *scalar_fields()],
        [forged, *scalar_fields()],
        use_model=use_model,
    )
    no_offer(fields[name])
    exact(fields["amount"], AMOUNT)
    exact(fields["vat"], "excluded")


@pytest.mark.parametrize("text,wrong,other,value", FOREIGN)
@pytest.mark.parametrize("use_model", [False, True])
def test_other_field_does_not_become_title(monkeypatch, text, wrong, other, value, use_model):
    bad = setting("title", wrong, wrong)
    good = setting(other, value, text)
    fields, _ = run(monkeypatch, text, [bad, good], [bad, good], use_model=use_model)
    no_offer(fields["title"])
    exact(fields[other], value)


@pytest.mark.parametrize(
    "name,label,old,new",
    [
        ("title", "추가 작업은", "창틀 보수", "창틀 교체"),
        ("reason", "추가 작업 사유는", "기존 접착 불량", "지지대 흔들림"),
    ],
)
def test_correction_and_competing_values(monkeypatch, name, label, old, new):
    text = f"{label} {old} 말고 {new}로 변경합니다."
    a, b = setting(name, old, old), setting(name, new, new)
    fields, _ = run(monkeypatch, with_scalars(text), [a, *scalar_fields()], [b, *scalar_fields()])
    only_values(fields[name], {old, new})
    assert not (fields[name].status == "valid" and fields[name].value == old)
    corrected = setting(
        name,
        new,
        new,
        CorrectionRelation(
            kind="replace",
            rejected_value=old,
            rejected_evidence=old,
            accepted_evidence=new,
        ),
    )
    fields, _ = run(
        monkeypatch,
        with_scalars(text),
        [a, *scalar_fields()],
        [corrected, *scalar_fields()],
    )
    exact(fields[name], new)
    exact(fields["amount"], AMOUNT)
    exact(fields["vat"], "excluded")
    uncertain = f"{label} {old} 또는 {new} 중 아직 고민입니다."
    fields, _ = run(monkeypatch, uncertain, [a], [b])
    no_offer(fields[name])


@pytest.mark.parametrize(
    "name,old,new",
    [
        ("title", "현관 몰딩 보강", "현관 몰딩 교체"),
        ("reason", "벽지 접착 불량", "벽체 표면 손상"),
    ],
)
def test_true_grounded_conflict_is_not_model_priority(monkeypatch, name, old, new):
    # 기존 자유 명사/정정 문맥에서 독립 근거가 있는 충돌은 보존한다.
    text = f"{old} 말고 {new}"
    fields, _ = run(monkeypatch, text, [setting(name, old, old)], [setting(name, new, new)])
    f = fields[name]
    assert (f.status, f.action) == ("conflict", "uncertain"), f
    assert {a.value for a in f.alternatives} == {old, new}, f
    assert {a.source for a in f.alternatives} == {"rule", "llm"}, f


@pytest.mark.parametrize("case", [CASES[0], CASES[3], CASES[9], CASES[11]])
@pytest.mark.parametrize(
    "state", ["verified", "label", "head-clip", "stale", "missing", "canceled", "wrong-evidence"]
)
@pytest.mark.parametrize("use_model", [False, True])
def test_prior_revalidation_full_value_span(monkeypatch, case, state, use_model):
    name, label, val, _, _, _ = case
    text, good, other = material(case)
    initial, _ = run(
        monkeypatch,
        text,
        [good, other, *scalar_fields()],
        [good, other, *scalar_fields()],
        use_model=use_model,
        rid="seed",
    )
    exact(initial[name], val)
    exact(initial["amount"], AMOUNT)
    prior = prior_for(text, "seed", initial, CURRENT)
    current = CURRENT.copy()
    if state in ("label", "head-clip", "wrong-evidence"):
        corrupted = {
            "label": field(name, "set", label + " " + val, good.evidence),
            "head-clip": field(name, "set", val.rsplit(" ", 1)[0], good.evidence),
            "wrong-evidence": field(name, "set", val, other.evidence),
        }[state]
        prior = prior.model_copy(
            update={"fields": [corrupted if f.field == name else f for f in prior.fields]}
        )
    elif state == "stale":
        current[name] = "직접 변경한 값"
    elif state == "missing":
        prior = prior.model_copy(
            update={
                "fields": [f for f in prior.fields if f.field != name],
                **{
                    k: {f: v for f, v in getattr(prior, k).items() if f != name}
                    for k in ("origins", "sources", "baselines")
                },
            }
        )
    elif state == "canceled":
        cancellation = ("제목" if name == "title" else "사유") + " 변경은 취소합니다."
        prior = prior.model_copy(update={"text": text + "\n" + cancellation})
    # Same compound label must retain only a verified prior value, never become new value.
    reference = label + " 앞서 제안한 값으로 유지해 주세요."
    other_reference = "금액은 앞서 제안한 값으로 유지해 주세요."
    fields, _ = run(
        monkeypatch,
        reference + " " + other_reference,
        model=[retain(name, reference), retain("amount", other_reference)],
        use_model=use_model,
        prior=prior,
        current=current,
    )
    carried(fields["amount"], prior)
    if state == "verified":
        carried(fields[name], prior)
    else:
        no_offer(fields[name])


@pytest.mark.parametrize("label", ["사유 칸에", "추가 작업 이유는"])
@pytest.mark.parametrize(
    "suffix", ["써 놓은 건 전부 지워서 빈칸으로 두세요", "비워서 빈칸으로 두세요"]
)
def test_master_clear_instruction_is_not_nominal_value(monkeypatch, label, suffix):
    text = label + " " + suffix + ". " + AMOUNT_CLAUSE + " " + VAT_CLAUSE
    forged = setting("reason", suffix, text)
    cleared = field(
        "reason", "clear", None, label + " " + suffix, intent="explicit_change", source="utterance"
    )
    fields, _ = run(monkeypatch, text, [forged, *scalar_fields()], [cleared, *scalar_fields()])
    assert fields["reason"].status == "valid" and fields["reason"].action == "clear"
    assert not fields["reason"].alternatives
    exact(fields["amount"], AMOUNT)
    fields, _ = run(monkeypatch, text, [forged, *scalar_fields()], [forged, *scalar_fields()])
    no_offer(fields["reason"])
    exact(fields["amount"], AMOUNT)


@pytest.mark.parametrize("name,label", [("title", "추가 작업은"), ("reason", "추가 작업 사유는")])
@pytest.mark.parametrize("literal", ["삭제 안내 이력", "지워서 빈칸으로 두세요"])
def test_master_clear_words_in_quoted_literal_are_values(monkeypatch, name, label, literal):
    text = f'{label} "{literal}"로 정합니다.'
    item = setting(name, literal, text)
    fields, _ = run(monkeypatch, text, [item], [item])
    exact(fields[name], literal)
