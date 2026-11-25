"""실제 철회 대상과 행위 객체의 원문 증명. 이력·상태 검증은 호출자가 한다."""

import re
from dataclasses import dataclass
from itertools import pairwise
from typing import Literal

from app.vendor.assist_intent import (
    _EXECUTION_NOUN,
    CONDITIONAL_LINK,
    DATE_CHANGE_ACTION,
    HISTORY_PAST_MODIFIER,
    HISTORY_TIME,
    WITHDRAW_IDIOM,
    WITHDRAW_IDIOM_REQUEST,
    Reference,
    field_mentions,
)
from app.vendor.assist_intent_safety import _ASSIGNMENT_STEM
from app.vendor.assist_schemas import FormName

# 대입의 실행 동사와 같은 어휘 계열에서 명사·관형형 행위 객체를 조립한다.
# 일반 명사(완료 보고, 내역 조회)는 행위 객체로 승격하지 않는다.
_OPERATION_NOUN = (
    rf"(?:{_EXECUTION_NOUN}|{_ASSIGNMENT_STEM}|증액|감액|이동|(?:재\s*)?배치|삭제|제거)"
)
_REQUEST_NOUN = r"(?:요청|제안|변경안|정정안|후보|선택|내용|건|거|것|안)"


def _operation_object(noun: str) -> str:
    # 신청은 실제 변경 행위에 결합된 요청 객체일 때만 인정한다.
    # 단순 신청/신청서나 업무 계약의 철회로 의미를 확장하지 않는다.
    adnominal = (
        rf"(?:(?:{noun})\s*하|{DATE_CHANGE_ACTION}|올리|내리|넣)"
        r"(?:는|려던|려고\s*했던|기로\s*했던|던|려는)"
    )
    return (
        rf"(?:(?:{noun}\s*|{adnominal}\s*)(?:{_REQUEST_NOUN}|신청)"
        rf"|{_REQUEST_NOUN}|{noun})(?:은|는|을|를|이|가|도|만)?"
    )


_OBJECT = _operation_object(_OPERATION_NOUN)
# 인원 증감은 명시된 인원 필드의 행위에만 결합한다.
_CREW_OBJECT = _operation_object(rf"(?:{_OPERATION_NOUN}|증원|감원)")
_DISCOURSE = r"(?:(?:아|아니|잠깐)\s*)?"
_PREVIOUS_LEAD = rf"(?:(?:{HISTORY_TIME})\s*(?:{HISTORY_PAST_MODIFIER})?|기존|앞선|지난|전)"
_CURRENT_LEAD = r"(?:현재|지금)(?:\s*(?:폼|화면|입력란)(?:에|에서)?)?(?:\s*(?:입력한|입력된|등록한|등록된|있는|적힌))?"
_REFERENCE_LEAD = rf"(?:{_PREVIOUS_LEAD}|{_CURRENT_LEAD}|그|이|전체|모든)"
_OWNER_PREFIX = re.compile(rf"{_DISCOURSE}(?P<reference>{_REFERENCE_LEAD})?\s*")
_GROUP_LINK = re.compile(r"\s*(?:과|와|및|이랑|랑|하고|,|·|/)\s*")
_OWNER_PARTICLE = re.compile(r"\s*(?:[은는이가을를만도](?=$|\s))?\s*")
_QUANTIFIER = r"(?:전부\s*|모두\s*|다\s*)?"
_PREDICATE = (
    r"(?:(?:취소|철회)(?:\s*(?:해\s*(?:주세요|줘요|주십시오)|"
    r"합니다|할게요|하겠습니다|해요|하고요?|하며))?"
    rf"|{WITHDRAW_IDIOM_REQUEST})"
)


def _withdrawal_request(obj: str) -> re.Pattern[str]:
    return re.compile(
        rf"{_DISCOURSE}(?P<reference>{_REFERENCE_LEAD})?\s*"
        rf"(?P<operation>{obj})?\s*{_QUANTIFIER}(?P<predicate>{_PREDICATE})"
    )


WITHDRAWAL_REQUEST = _withdrawal_request(_OBJECT)
_CREW_REQUEST = _withdrawal_request(_CREW_OBJECT)
_OPERATION_LINK = re.compile(rf"\s*(?P<operation>{_OBJECT}){_GROUP_LINK.pattern}")
_CREW_OPERATION_LINK = re.compile(rf"\s*(?P<operation>{_CREW_OBJECT}){_GROUP_LINK.pattern}")
# The original unowned candidate-wide command vocabulary is deliberately
# narrower than field-bound operation objects. A newly recognized operation
# noun without an owner cannot cancel every outstanding form field.
_GENERIC_OBJECT = (
    rf"(?:(?:(?:변경|수정|정정)\s*|(?:바꾸|고치|변경하|수정하|정정하)\s*는\s*)?"
    rf"{_REQUEST_NOUN}|변경|수정|정정)(?:은|는|을|를|이|가|도|만)?"
)
_GENERIC_REQUEST = re.compile(
    rf"{_DISCOURSE}(?:(?:그|이|방금|아까|앞서|이전|지난|기존|전|전체|모든)\s*)?"
    rf"(?:{_GENERIC_OBJECT}\s*)?{_QUANTIFIER}{_PREDICATE}"
)
_CANCEL_MARK = re.compile(rf"(?:취소|철회|{WITHDRAW_IDIOM}|원래대로\s*돌)")
_VALUE_OPERATION = re.compile(
    rf"(?P<value>.+?)\s*(?:으로|로|이라고|라고)\s*"
    rf"(?P<operation>{_OBJECT})\s*{_QUANTIFIER}(?P<predicate>{_PREDICATE})"
)
_VALUE_CANDIDATE = re.compile(
    r"(?P<value>.+?)\s*"
    r"(?P<operation>(?:후보(?:안)?|제안(?:안)?|선택|변경안|정정안)(?:은|는|을|를|이|가|도|만)?)"
    rf"\s*{_QUANTIFIER}(?P<predicate>{_PREDICATE})"
)
_VALUE_RISK = re.compile(
    r"미정|보류|고민|검토\s*중|혹시|아마|또는|혹은|아니면|"
    r"하지\s*(?:않|마|말)|(?<![가-힣])(?:안|못)\s*(?:하|해|할)|[?？]"
)
_PARTIAL_VALUE = re.compile(r"^(?:중|가운데)\s|\s(?:중|가운데)\s")
_QUOTED = re.compile(r""""[^"]*(?:"|$)|'[^']*(?:'|$)|“[^”]*(?:”|$)|‘[^’]*(?:’|$)""")


@dataclass(frozen=True)
class WithdrawalOperation:
    """한 필드 또는 공동 필드에 귀속된 실제 행위 객체의 원문 범위."""

    fields: frozenset[str]
    start: int
    end: int


@dataclass(frozen=True)
class WithdrawalObject:
    """절 내부 좌표의 어휘 증명. definite는 이력까지 승인한 뜻이 아니다."""

    fields: frozenset[str]
    start: int
    end: int
    operation_start: int | None
    operation_end: int | None
    predicate_start: int
    reference: Reference
    requires_value_proof: bool
    value_span: tuple[int, int] | None
    selected_modes: frozenset[str]
    definite: bool
    reason: str | None
    value_kind: Literal["operation", "candidate"] | None = None
    operation_units: tuple[WithdrawalOperation, ...] = ()


def complete_withdrawal_body(body: str, *, owned: bool = True) -> bool:
    """대상명을 제외한 전체 요청이 완결된 철회 객체와 술어인지 확인한다."""
    grammar = WITHDRAWAL_REQUEST if owned else _GENERIC_REQUEST
    return bool(grammar.fullmatch(body.strip()))


def _reference(prefix: str | None, body: str | None) -> Reference:
    refs = (prefix or "", body or "")
    if any(re.fullmatch(_CURRENT_LEAD, ref) for ref in refs):
        return "current"
    if any(ref != "기존" and re.fullmatch(_PREVIOUS_LEAD, ref) for ref in refs):
        return "previous"
    return "pending"


def withdrawal_object(
    form: FormName, clause: str, *, cancel_at: int | None = None
) -> WithdrawalObject | None:
    """공통 절의 실제 명명 대상·공동 객체·철회 술어만 증명한다.

    다른 절의 조건이나 동일 대상의 경쟁 요청, 이전 후보의 값과
    출처 확인은 검증기가 책임진다. 값이 명시됐으면 반드시 그 값의
    이력 증명을 요구하고 목록 일부 철회는 전체 취소로 인정하지 않는다.
    """
    masked = _QUOTED.sub(lambda match: " " * len(match.group()), clause)
    cancellation = _CANCEL_MARK.search(masked)
    if cancellation is None or (cancel_at is not None and cancel_at != cancellation.start()):
        return None
    # 바깥 문장부호만 제거한다. 원문과 동일한 오프셋을 유지한다.
    start = len(clause) - len(clause.lstrip())
    end = len(clause.rstrip().rstrip(",;.!。 ").rstrip())
    mentions = tuple(m for m in field_mentions(form, masked[:end]) if m.end <= cancellation.start())
    fields = frozenset(name for m in mentions for name in m.fields)
    selected_modes = frozenset(m.selected_mode for m in mentions if m.selected_mode)
    offset = start
    prefix = None
    owned = True
    units: list[WithdrawalOperation] = []
    if mentions:
        prefix_match = _OWNER_PREFIX.fullmatch(masked[start : mentions[0].start])
        owned = bool(prefix_match)
        shared = True
        for left, right in pairwise(mentions):
            gap = clause[left.end : right.start]
            if _GROUP_LINK.fullmatch(gap):
                continue
            shared = False
            # 각 대상에 붙은 행위 객체만 접속사 앞까지 읽는다.
            # 임의의 명사, 값, 보고, 문의, 다른 참조를 건너뛰지 않는다.
            grammar = _CREW_OPERATION_LINK if left.fields == {"crew"} else _OPERATION_LINK
            link = grammar.fullmatch(gap)
            if link is None:
                owned = False
                break
            a, b = link.span("operation")
            units.append(WithdrawalOperation(left.fields, left.end + a, left.end + b))
        if not shared:
            # 기존의 단순 공동 주어와 각각의 완결 행위 객체를 구분한다.
            # 중간 행위 생략, 중복 대상, 선택값 혼합은 새 증명이 아니다.
            owned = (
                owned
                and len(units) == len(mentions) - 1
                and sum(len(m.fields) for m in mentions) == len(fields)
                and not selected_modes
            )
        if prefix_match:
            prefix = prefix_match["reference"]
        offset = mentions[-1].end
        offset += _OWNER_PARTICLE.match(clause[offset:end]).end()
    # 인용값은 필드 귀속 검사에만 가리고, 실제 객체 문법에서는
    # 값 자체를 보존한다. 인용값을 지운 뒤 후보 철회로 승격하지 않는다.
    body = clause[offset:end]
    last_fields = mentions[-1].fields if units else fields
    grammar = _CREW_REQUEST if last_fields == {"crew"} else WITHDRAWAL_REQUEST
    request = grammar.fullmatch(body)
    value_span = None
    value_kind = "candidate" if selected_modes else None
    body_ref = None
    if request:
        body_ref = request["reference"]
    elif len(fields) == 1 and owned:
        request = _VALUE_OPERATION.fullmatch(body)
        if request:
            value_kind = "operation"
        elif _reference(prefix, None) == "previous":
            request = _VALUE_CANDIDATE.fullmatch(body)
            if request:
                value_kind = "candidate"
        if request:
            a, b = request.span("value")
            value_span = (offset + a, offset + a + len(body[a:b].rstrip()))
    operation_start = operation_end = None
    predicate_start = cancellation.start()
    reason = None
    if request:
        if request["operation"] is not None:
            a, b = request.span("operation")
            operation_start, operation_end = offset + a, offset + b
            if fields:
                units.append(WithdrawalOperation(last_fields, operation_start, operation_end))
        elif selected_modes and mentions:
            operation_start, operation_end = mentions[-1].start, mentions[-1].end
        elif units:
            # 마지막 주어에도 명시된 행위가 있어야 공동 철회로 결합한다.
            owned = False
        predicate_start = offset + request.start("predicate")
        if predicate_start != cancellation.start():
            reason = "predicate"
    else:
        reason = "object"
    if not owned:
        reason = "ownership"
    elif CONDITIONAL_LINK.search(masked) or _VALUE_RISK.search(masked):
        reason = "risk"
    elif value_span is not None and _PARTIAL_VALUE.search(clause[slice(*value_span)]):
        reason = "partial_reference"
    elif selected_modes and (fields != {"vat"} or len(selected_modes) != 1):
        reason = "value_reference"
    return WithdrawalObject(
        fields=fields,
        start=start,
        end=end,
        operation_start=operation_start,
        operation_end=operation_end,
        predicate_start=predicate_start,
        reference=_reference(prefix, body_ref),
        requires_value_proof=value_span is not None or bool(selected_modes),
        value_span=value_span,
        selected_modes=selected_modes,
        definite=reason is None,
        reason=reason,
        value_kind=value_kind,
        operation_units=tuple(units),
    )
