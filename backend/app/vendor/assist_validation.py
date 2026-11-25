"""업체 도우미 후보의 원문·타입·업무 한도를 결정적으로 검증한다."""

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from itertools import pairwise
from types import MappingProxyType
from typing import Literal

from app.calc.schedule import JOB_MAX_DAYS
from app.quote.schemas import AMOUNT_MAX
from app.schedule.models import EXTRA_AMOUNT_MAX
from app.vendor.assist_boundary import affirmative_end_deadline, explicit_prior_end_reference
from app.vendor.assist_date_edit import (
    date_edit_confirmation_start,
    date_edit_reference_prefix,
    date_protection_clause,
    date_replacement_requested,
    validated_date_edit,
)
from app.vendor.assist_intent import (
    _REFERENT,
    CONDITIONAL_LINK,
    EXCLUDED_EDIT_REFERENCE,
    EXTRA_LABEL_MARKER,
    EXTRA_REASON_LABEL,
    EXTRA_TITLE_LABEL,
    HISTORY_PAST_MODIFIER,
    HISTORY_TIME,
    PLACEMENT_KEEP_DIRECTIVE,
    RECENT_HISTORY_TIME,
    WITHDRAW_IDIOM,
    IntentClause,
    RetentionScope,
    compatible_reference,
    date_list_prefix,
    explicit_group_retention_scopes,
    field_mentions,
    first_history_reference,
    intent_clauses,
    polite_execution_risk_text,
    retention_scopes,
)
from app.vendor.assist_intent_safety import (
    affirmative_assignment_suffix,
    assignment_directive_in_value,
    date_set_is_inquiry,
    date_value_clauses,
    explicit_text_assignment,
    mask_bound_text_literals,
    mask_explicit_text_value,
    mask_owned_text_values,
    text_set_is_inquiry,
    text_value_boundary_ok,
    text_value_is_clear_instruction,
    text_value_scope_ok,
)
from app.vendor.assist_normalize import (
    _SCALAR_CONDITION,
    ScalarRiskScope,
    complete_range_evidence,
    crew_mentions,
    crew_replacement,
    crew_tokens,
    date_list_count_mismatch,
    date_list_declared_counts,
    date_mentions,
    date_tokens,
    duration_quantity_risk,
    duration_tokens,
    scalar_clause_risk,
)
from app.vendor.assist_period_constraints import (
    period_state,
    period_violations,
    resolve_period_candidates,
)
from app.vendor.assist_schemas import (
    FORM_FIELDS,
    Alternative,
    AssistContext,
    AssistRequest,
    CandidateField,
    CorrectionRelation,
    FieldIntent,
    FormName,
    IntentKind,
    PreviousCandidate,
    ReasonCode,
    Source,
    Value,
    check_value,
)
from app.vendor.assist_vat_semantics import (
    VAT_NEGATIVE_INCLUSION,
    scoped_vat_modes,
    scoped_vat_tokens,
    standalone_vat_modes,
    vat_tokens,
    vat_value_matches,
)
from app.vendor.assist_vat_semantics import (
    vat_modes as _vat_modes,
)
from app.vendor.assist_withdrawal import (
    complete_withdrawal_body,
    withdrawal_object,
)
from app.vendor.assist_written_money import written_money_tokens

_MONEY_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)"
_MONEY_SMALL = rf"{_MONEY_NUMBER}\s*[천백십]"
_MONEY_SMALL_SEQUENCE = rf"{_MONEY_SMALL}(?:\s*{_MONEY_SMALL})*"
_MONEY_COEFFICIENT = rf"(?:{_MONEY_SMALL_SEQUENCE}(?:\s*{_MONEY_NUMBER})?|{_MONEY_NUMBER})"
_MONEY_MAJOR_SEQUENCE = (
    rf"(?:{_MONEY_COEFFICIENT}\s*억(?:\s*{_MONEY_COEFFICIENT}\s*만)?"
    rf"|{_MONEY_COEFFICIENT}\s*만)"
)
_MONEY_REMAINDER = (
    rf"(?:{_MONEY_SMALL_SEQUENCE}(?:\s*{_MONEY_NUMBER}(?=\s*원))?"
    rf"|{_MONEY_NUMBER}(?=\s*원))"
)
MONEY = re.compile(
    rf"(?<![\d십백천만억])(?<!\d[,.])"
    rf"(?:(?P<major>{_MONEY_MAJOR_SEQUENCE})(?:\s*(?P<remainder>{_MONEY_REMAINDER}))?"
    rf"|(?P<small>{_MONEY_SMALL_SEQUENCE})(?:\s*(?P<tail>{_MONEY_NUMBER})(?=\s*원))?"
    rf"|(?P<plain>{_MONEY_NUMBER}))(?P<won>\s*원)?"
)
_MONEY_COMPONENT = re.compile(rf"({_MONEY_NUMBER})(?:\s*([천백십]))?")
_MONEY_MAJOR_COMPONENT = re.compile(rf"({_MONEY_COEFFICIENT})\s*([억만])")
_MONEY_SMALL_UNIT = {"천": 1_000, "백": 100, "십": 10, None: 1}
_MONEY_MAJOR_UNIT = {"억": 100_000_000, "만": 10_000}
CLEAR = re.compile(r"(?:지워|지우|삭제|비워|비우|없애|제거|초기화)")
CLEAR_NEGATED = re.compile(
    r"(?:지우지|지워지지|삭제하지|비우지|없애지|제거하지|초기화하지|삭제 금지|삭제 말)"
)
_CLEAR_DENIAL = re.compile(
    r"(?:지워서는\s*안|삭제는\s*하지|삭제를\s*하지|삭제\s*안|"
    r"지우는\s*건\s*안|지우기를\s*하지|지우\s*안|하지\s*않)"
)


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def normalized_scope(text: str) -> str:
    """Normalize tokens without erasing independent line boundaries."""
    return "\n".join(line for part in text.splitlines() if (line := normalized(part)))


def evidence_matches(text: str, evidence: str) -> bool:
    return bool(evidence.strip()) and normalized(evidence) in normalized(text)


def _money_coefficient(text: str) -> tuple[Decimal, int] | None:
    """'1천500'처럼 큰 단위 앞에 오는 숫자는 먼저 하나로 읽는다."""
    total = Decimal(0)
    first_scale = 0
    previous_scale = 10_000
    for part in _MONEY_COMPONENT.finditer(text):
        scale = _MONEY_SMALL_UNIT[part[2]]
        if scale >= previous_scale:
            return None
        try:
            total += Decimal(part[1].replace(",", "")) * scale
        except InvalidOperation:
            return None
        first_scale = first_scale or scale
        previous_scale = scale
    return total, first_scale


def _amount_tokens(evidence: str) -> list[tuple[int, int, int]]:
    """완전한 금액만 원문 위치와 함께 반환하고 합성 금액의 조각은 내보내지 않는다."""
    written = written_money_tokens(evidence)
    if written is not None:
        return written
    groups: list[tuple[int, int, int]] = []
    for item in MONEY.finditer(evidence):
        major = item["major"]
        remainder = item["remainder"]
        small = item["small"]
        won = item["won"]
        bare = not (major or small or won)
        # 날짜 조각·공사 일수·인원 숫자를 금액으로 재활용하지 않는다.
        before = evidence[item.start() - 1 : item.start()] if item.start() else ""
        after = evidence[item.end() : item.end() + 4]
        if (
            re.match(r"\d|[,.]\d", after)
            or before in ("-", "−", "/")
            or (
                bare
                and (
                    after.startswith(("-", "/", "."))
                    or after.lstrip().startswith(
                        (
                            "일",
                            "명",
                            "인",
                            "월",
                            "년",
                            "개",
                            "주",
                            "시",
                            "분",
                            "%",
                            "㎡",
                            "평",
                            "대",
                        )
                    )
                )
            )
        ):
            continue
        if bare:
            # '견적 5000'처럼 숫자와 바로 연결된 금액 문구만 허용한다.
            nearby = evidence[max(item.start() - 28, 0) : item.start()]
            nearby = re.split(r"[,;.!?\n]", nearby)[-1]
            if not re.search(
                r"(?:견적|금액|가격|비용|공사비|추가\s*금액|합계|총액|단가|청구)"
                r"(?:은|는|이|가|을|를|도|만|으로|:)?\s*$",
                nearby,
            ):
                continue
        if major:
            n = Decimal(0)
            for part in _MONEY_MAJOR_COMPONENT.finditer(major):
                parsed = _money_coefficient(part[1])
                if parsed is None:
                    break
                n += parsed[0] * _MONEY_MAJOR_UNIT[part[2]]
            else:
                if remainder:
                    parsed = _money_coefficient(remainder)
                    if parsed is None:
                        continue
                    n += parsed[0]
                parsed = (n, 0)
        else:
            coefficient = small or item["plain"]
            if item["tail"]:
                coefficient += " " + item["tail"]
            parsed = _money_coefficient(coefficient)
        if parsed is None:
            continue
        n = parsed[0]
        if n != n.to_integral_value():
            continue
        amount = int(n)
        # '원'은 큰/작은 단위 뒤에서 생략할 수 있지만 숫자 단독 근거는 허용하지 않는다.
        if remainder:
            end = item.end("remainder") if remainder[-1] in "천백십" else item.end()
        elif major:
            end = item.end("major")
        elif small and not item["tail"]:
            end = item.end("small")
        else:
            end = item.end()
        groups.append((item.start(), end, amount))
    return groups


def _amount_mentions(evidence: str) -> set[int]:
    return {value for _, _, value in _amount_tokens(evidence)}


def _scalar_grounded(
    field: str, value: Value, evidence: str, text: str | None, *, extra: bool = False
) -> bool:
    if type(value) is not int:
        return False
    needle = normalized(evidence)
    tokens = _amount_tokens if field == "amount" else crew_tokens
    # '두 사람/세 분'은 잘린 근거에서는 수량만 읽되, 원문에서 실제
    # 작업 인원에 연결된 완전한 토큰인지 아래 위치 대조로 입증한다.
    extracted = (
        crew_tokens(needle, allow_people=text is not None) if field == "crew" else tokens(needle)
    )
    mentions = [(start, end) for start, end, number in extracted if number == value]
    if not mentions:
        return False
    if text is None:
        return True
    raw = normalized(text)
    # 같은 숫자가 다른 곳에 있어도 잘린 근거를 통과시키지 않는다.
    full_text = mask_owned_text_values(raw) if extra and field == "amount" else raw
    full = {(start, end) for start, end, number in tokens(full_text) if number == value}
    return any(
        (match.start() + start, match.start() + end) in full
        for match in re.finditer(re.escape(needle), raw)
        for start, end in mentions
    )


def _quote_duration_tokens(raw: str) -> list[tuple[int, int, int]]:
    """일수의 완전한 수량 토큰을 실제 견적 필드에 연결한다.

    날짜 정규화가 달력 날짜를 제외하더라도, '효력은 닷새'처럼
    유효기간에 연결된 수량은 공사 일수가 아니다. 원문 오프셋을 유지한다.
    """
    parts = intent_clauses("quote", raw)
    result = []
    for start, end, value in duration_tokens(raw):
        part = next((p for p in parts if p.start <= start < p.end), None)
        if part is not None:
            preceding = [mention for mention in part.mentions if mention.end <= start]
            if preceding and preceding[-1].fields == {"valid"}:
                continue
            if "valid" in part.fields and "days" not in part.fields:
                continue
        result.append((start, end, value))
    return result


def _duration_grounded(value: Value, evidence: str, text: str | None) -> bool:
    if type(value) is not int:
        return False
    needle = normalized(evidence)
    spans = [(a, b) for a, b, n in _quote_duration_tokens(needle) if n == value]
    if not spans:
        return False
    if text is None:
        return True
    raw = normalized(text)
    full = {(a, b) for a, b, n in _quote_duration_tokens(raw) if n == value}
    return any(
        (match.start() + a, match.start() + b) in full
        for match in re.finditer(re.escape(needle), raw)
        for a, b in spans
    )


def _date_assignment_scope_ok(
    form: FormName, field: str, value: Value, evidence: str, text: str, context: AssistContext
) -> bool:
    """명시된 다른 필드의 날짜를 값 근거로 바꾸지 않는다.

    실제 범위와 목록 파생은 기존 정규화 및 정정 검증에서 담당한다.
    이전 후보의 경우 근거가 남은 실제 발화만 검사한다.
    """
    if field not in ("start", "end", "valid"):
        return True
    if not isinstance(value, str):
        return False
    day = date.fromisoformat(value)
    matched_target = False
    matched_other = False
    for turn in text.splitlines():
        if not evidence_matches(turn, evidence):
            continue
        raw = normalized(turn)
        # 기간의 범위 뒤 쉼표 및 제외일 열거는 공통 절 분석에서 이미
        # 독립 대상으로 증명된다. 문장 전체의 마지막 제외 술어를
        # 앞의 범위에 전파하지 않는다.
        scoped = (
            tuple(
                (
                    raw[part.clause.start : part.clause.end],
                    frozenset({"unowned_boundary"}) if part.boundary_competition else part.fields,
                )
                for part in _resolved_intent_clauses(form, raw, context)
            )
            if form == "period"
            else date_value_clauses(form, raw)
        )
        for clause, targets in scoped:
            days = {d for _, _, d in date_tokens(clause, context, allow_bare=field != "valid")}
            if day not in days:
                continue
            if not targets or field in targets:
                matched_target = True
            else:
                matched_other = True
        if form == "period" and field in ("start", "end") and not matched_target:
            # 값 없는 유지 절 뒤에 독립적인 단일 경계 날짜가 이어지는
            # 경우에는 별도 날짜 절의 실제 접속 범위와 경계 표지를 확인한다.
            marker = "부터" if field == "start" else "까지"
            for clause, targets in date_value_clauses(form, raw):
                tokens = date_tokens(clause, context)
                if (
                    targets == {"start", "end"}
                    and len(tokens) == 2
                    and _RULE_DATE_RANGE.search(clause)
                    and day == tokens[0 if field == "start" else 1][2]
                    and evidence_matches(clause, evidence)
                    and not field_mentions("period", clause)
                ):
                    # 범위가 종료된 뒤 연결되는 별도 제외일은 경계 근거가 아니다.
                    matched_target = True
                if (
                    targets == {"start", "end"}
                    and len(tokens) == 1
                    and tokens[0][2] == day
                    and clause[tokens[0][1] :].lstrip().startswith(marker)
                    and evidence_matches(clause, evidence)
                    and not field_mentions("period", clause)
                ):
                    matched_target = True
    return matched_target or not matched_other


def _single_date_competing_values(
    form: FormName, field: str, text: str, context: AssistContext
) -> bool:
    """명시한 단일 날짜 필드에 서로 다른 값이 남아 있으면 임의 선택 금지.

    기간 전체 범위는 두 경계의 값이므로 단일 필드의 경쟁값이 아니다.
    검증된 정정 또는 유효한 충돌 대안은 병합 단계에서 별도로 처리한다.
    """
    raw = normalized_scope(text)
    values: set[date] = set()
    for part in _resolved_intent_clauses(form, raw, context):
        if part.fields != {field} and field not in part.boundary_competition:
            continue
        clause = raw[part.clause.start : part.clause.end]
        dates = {day for _, _, day in date_tokens(clause, context, allow_bare=field != "valid")}
        values.update(dates)
    return len(values) > 1


def _grounded(
    form: FormName,
    field: str,
    value: Value,
    evidence: str,
    context: AssistContext,
    *,
    text: str | None = None,
) -> bool:
    if field in ("amount", "crew"):
        return _scalar_grounded(field, value, evidence, text, extra=form == "extra")
    if form == "quote" and field == "days":
        return _duration_grounded(value, evidence, text)
    if field == "vat":
        if type(value) is not str:
            return False
        needle = normalized_scope(evidence)
        spans = [(token.start, token.end) for token in vat_tokens(needle) if token.mode == value]
        if not spans:
            return False
        if text is None:
            return True
        raw = normalized_scope(text)
        masked = mask_owned_text_values(raw) if form == "extra" else raw
        full = {(token.start, token.end) for token in vat_tokens(masked) if token.mode == value}
        return any(
            (match.start() + start, match.start() + end) in full
            for match in re.finditer(re.escape(needle), raw)
            for start, end in spans
        )
    if (
        text is not None
        and field in ("start", "end", "days", "excluded", "valid")
        and not complete_range_evidence(normalized(evidence), normalized(text))
    ):
        return False
    if field in ("start", "end", "valid"):
        if type(value) is not str:
            return False
        date_value = date.fromisoformat(value)
        tokens = date_tokens(evidence, context, allow_bare=field != "valid")
        if date_value in {item[2] for item in tokens}:
            return True
        if field not in ("start", "end"):
            return False
        return _boundary_trim_supported(field, date_value, evidence, context, tokens)
    if field in ("excluded", "days"):
        if type(value) is not list or field == "excluded" and not value:
            # '추출되지 않음'을 이전 제외 날짜 삭제로 해석하지 않는다.
            return False
        if text is not None:
            for turn in text.splitlines():
                if evidence_matches(turn, evidence):
                    raw = normalized(turn)
                    # Explicit, contiguous owned calendar lists are complete
                    # values, not a pool of individually selectable tokens.
                    # The relation-specific path still handles partial edits;
                    # ranges and independent requests retain their own proof.
                    if (form, field) in (
                        ("period", "excluded"),
                        ("dates", "days"),
                    ) and not date_replacement_requested(field, raw):
                        for part in _resolved_intent_clauses(form, raw, context):
                            if part.fields != {field} or part.unbound or part.clause.retentions:
                                continue
                            clause = raw[part.clause.start : part.clause.end]
                            tokens = date_tokens(clause, context)
                            if len(tokens) < 2 or _RULE_DATE_RANGE.search(clause):
                                continue
                            # Adjacent date tokens must be connected only by
                            # actual enumeration separators, never by a second
                            # predicate, a competing field or a range.
                            first, last = tokens[0][0], tokens[-1][1]
                            if date_list_prefix(clause[first:last]) and set(
                                map(date.fromisoformat, value)
                            ) != {day for _, _, day in tokens}:
                                return False
                    count_values = set()
                    for clause, targets in date_value_clauses(form, raw):
                        if field not in targets:
                            continue
                        if date_list_count_mismatch(clause, context):
                            return False
                        count_values.update(n for _, _, n in date_list_declared_counts(clause))
                    if count_values and count_values != {len(value)}:
                        return False
        return set(map(date.fromisoformat, value)) <= date_mentions(evidence, context, expand=True)
    if field in ("title", "reason"):
        return (
            type(value) is str
            and bool(value.strip())
            and normalized(value) in normalized(evidence)
            and text_value_boundary_ok(field, value, text if text is not None else evidence)
            and not _assignment_directive_in_value(
                field, value, text if text is not None else evidence
            )
        )
    return False


_EXCLUDE_DAY = re.compile(
    r"(?:안\s*돼|안\s*되|안\s*됨|못\s*(?:가|받)|불가능|제외|빼|뺄(?:지|까|\s*날)|어려)"
)
# 이전에 쉬거나 빼기로 한 날짜는 '제외일'이라는 단어가 없어도 같은 필드를 가리킨다.
# 날짜/날과 연결된 표현만 인정해 공사 시작·종료일의 일반 언급과 구분한다.
_EXCLUDED_DAY_REFERENCE = re.compile(
    r"(?:빼(?:둔|놓은|놨던|기로\s*한|는)|뺀|뺐던|"
    r"제외(?:한|했던|된|해\s*둔)?|안\s*되는|"
    r"쉬(?:는|기로\s*한|기로\s*했던)|"
    r"쉰다고\s*(?:한|했던)|못\s*간다고\s*(?:한|했던))"
    r"\s*(?:날짜|날|일자|일)"
    rf"|{EXCLUDED_EDIT_REFERENCE}"
)
_EXCLUDED_RELEASE_ALL = re.compile(
    r"(?:다|전부|모두|몽땅)\s*(?:풀|해제)|"
    r"(?:전체|모든)\s*(?:제외|빼|쉬)[^,;.!?？\n]{0,24}(?:풀|해제)"
)
_EXCLUDED_RELEASE_DIRECT = re.compile(
    r"(?:\s*[은는을를도])?\s*(?:이제\s*)?"
    r"(?:다|전부|모두|몽땅)\s*"
    r"(?:풀어|해제\s*해)\s*(?:줘(?:요)?|주(?:세요|셔요)|요)(?=\s|$|[.,;!])"
)
_EXCLUDED_RELEASE_DENIAL = re.compile(
    r"풀(?:어\s*주)?(?:지|지는|면)\s*(?:말|마|않|안|못)|"
    r"풀(?:어\s*주)?는\s*건\s*아니|"
    r"풀(?:어\s*주)?는\s*것은\s*아니|"
    r"(?:풀어|해제\s*해)\s*(?:줘(?:요)?|주(?:세요|셔요)|요)\s*(?:말고|취소|보류|아니)|"
    r"해제\s*(?:하(?:지|지는|면)\s*(?:말|마|않|안|못)|하는\s*(?:건|것은)\s*아니)"
)


def _boundary_trim_supported(
    field: str,
    value: date,
    evidence: str,
    context: AssistContext,
    tokens: list[tuple[int, int, date]],
) -> bool:
    """범위 끝날을 제외해 옮긴 날짜는 그 끝날의 명시적 제외 지시가 있어야 한다."""
    for left, right in pairwise(tokens):
        start, end = left[2], right[2]
        if not start < end:
            continue
        # 날짜 정규화 모듈이 검증한 실제 범위만 사용한다.
        span = evidence[left[0] : right[1]]
        if value not in date_mentions(span, context, expand=True):
            continue
        boundary = start if field == "start" else end
        expected = boundary + timedelta(days=1 if field == "start" else -1)
        if value != expected:
            continue
        for occurrence in tokens:
            if occurrence in (left, right) or occurrence[2] != boundary:
                continue
            nearby = evidence[occurrence[1] : occurrence[1] + 28]
            if _EXCLUDE_DAY.search(nearby):
                return True
    return False


def _deletion_requested(evidence: str) -> bool:
    return bool(CLEAR.search(evidence)) and not (
        CLEAR_NEGATED.search(evidence) or _CLEAR_DENIAL.search(evidence)
    )


_CLEAR_TARGETS: dict[str, tuple[str, ...]] = {
    "start": ("시작", "첫날"),
    "end": ("종료", "끝", "마지막"),
    "excluded": ("제외", "빼는", "안 되는 날", "불가능"),
    "amount": ("금액", "견적", "비용", "가격", "공사비", "총액"),
    "vat": ("부가세", "면세"),
    "days": ("기간", "일수", "공사", "날짜", "날"),
    "crew": ("인원", "작업자", "사람", "명"),
    "title": ("항목", "제목", "이름"),
    "reason": ("이유", "사유"),
    "valid": ("유효기간", "유효기한", "유효일", "만료", "효력"),
}
_CURRENT_VALUE_REFERENCE = (
    r"(?:현재|지금)\s?(?:(?:(?:폼|화면|입력란)에\s?)?(?:있는|입력된|입력한|적힌|적은)\s?)?"
    r"값(?:으로|대로)"
)
_CURRENT_VALUE_KEEP = (
    rf"{_CURRENT_VALUE_REFERENCE}\s?"
    rf"(?:{PLACEMENT_KEEP_DIRECTIVE})(?=$|[ ,;.!?？])"
)
_RETAIN = re.compile(
    r"(?:그대로|유지|바꾸지|변경하지|수정하지|건드리지|"
    r"전과\s*같|이전과\s*같|예전과\s*같|"
    r"같아요|같습니다|같아|같은\s*상태|동일|"
    r"살려|이어서\s*사용|폼대로|"
    r"(?:아까|앞서|이전|방금|지난)[^,;.!?？\n]{0,24}대로|"
    r"(?:제안|변경|정리|정한|앞서|아까|방금|이전|지난)"
    r"[^,;.!?？\n]{0,28}(?:안으로|걸로|내용으로)\s*(?:두|둘)|"
    rf"{_CURRENT_VALUE_KEEP})"
)
_RETAIN_NEGATED = re.compile(
    r"(?:그대로|유지).{0,16}(?:말고|두지\s*말|않|아니|아닙|아님|취소|빼고)"
    r"|(?:유지하지|그대로\s*두지)"
    r"|(?<![가-힣])(?:(?:놔|놓아|남겨|내버려)\s*)?두지\s*(?:마|말|않|못)"
    r"|(?<![가-힣])안\s*(?:(?:놔|놓아|남겨|내버려)\s*)?(?:두|둘|둬)"
    r"|(?:같지\s*않|같은\s*건\s*아니|같은\s*게\s*아니|동일하지\s*않)"
    r"|(?:살려|이어서\s*사용|(?:안으로|걸로|내용으로)\s*(?:두|둘))"
    r"[^,;.!?？\n]{0,14}(?:지\s*마|지\s*말|지\s*않|안\s*해|취소)"
)
_UNCERTAIN_CHANGE = re.compile(r"(?:미정|모르|애매|정도|였나|일까|혹시|또는|아니면|사이|[?？])")
_DIRECT_REPLACE = re.compile(r"(?:말고|아니고|아니라|대신|→|->)")
_CHANGE_TARGET = re.compile(
    r"^(?:으로|로)?\s*(?:바꿔|바꾸|변경|수정|고쳐|고치|올려|내려|정정|확정)"
)
_CORRECTION_AT_OLD_END = re.compile(r"(?:말고|아니고|아니라|대신|→|->)\s*$")
_CORRECTION_AT_NEW_START = re.compile(r"^\s*(?:말고|아니고|아니라|대신|→|->)\s*")
_CORRECTION_FROM = re.compile(r"에서\s*$")
_CORRECTION_TO = re.compile(
    r"(?:으로|로)\s*(?:수정|변경|바꿔|바꾸|바꿉|고쳐|고치|정정|확정|올려|내려)"
)
_CORRECTION_CLAUSE_BREAK = re.compile(r"[,;.!?？\n]")
_CORRECTION_UNRELATED = re.compile(r"(?:인원|작업자|기간|일수|시작|종료|항목|사유|이유)")
_HISTORICAL_REFERENCE = re.compile(r"(?:아까|앞서|지난번|이전에|예전에|방금|조금\s*전)")
_HISTORICAL_REPORT_LINK = re.compile(
    r"(?:이라고|라고|이라|라|으로|로)?\s*"
    r"(?:했|말했|말씀드렸|적었|썼|입력했|전달했)"
    r"(?:는데|다가|지만|고)\s*$"
    r"|(?:이라던|라던)\s*(?:추가\s*금액|추가금|견적|금액|인원|작업자|비용|기간)?"
    r"(?:은|는|을|를)?\s*$"
)
_HISTORICAL_NEW_ACTION = re.compile(
    r"(?:으로|로)\s*(?:고칠|고치|고쳐|바꾸|바꿔|바꿀|변경|수정|정정|확정|올리|내리)"
)
_HISTORICAL_DENIED_NEW = re.compile(
    r"(?:고치|고쳐|고칠|바꾸|바꿔|바꿀|수정|변경|정정|확정)"
    r"(?:지\s*(?:말|마|않|못)|지는\s*않|기\s*싫|지\s*안)"
    r"|(?:수정|변경|정정)\s*(?:은|는|을|를)?\s*(?:하지\s*마|하지\s*않|안\s*하)"
)
_RULE_QUESTION_OR_UNDECIDED = re.compile(
    r"(?:[?？]|거죠|까요|미정|또는|아니면|혹은|모르|애매|사이|정도|쯤|고민|생각\s*중)"
)
_INDIRECT_INQUIRY = (
    r"수\s?있(?:는지|을지|나요)|가능(?:한지|할지|한가요|하나요)|"
    r"(?:해도|넣어도|되어도|돼도)\s?되(?:는지|나요)|될지|"
    r"맞는지|괜찮(?:은지|을지|나요)|여부"
)
_RULE_NEGATED_ACTION = re.compile(
    r"(?:취소|철회|하지\s*마|하지\s*말|포함하지|보내지|제출하지|입력하지|"
    r"건드리지|수정하지|변경하지|아니|아닙|아님|말고|빼고|안\s*맞|"
    r"원하지|거부|잘못|틀렸)"
)
_RULE_HISTORICAL = re.compile(r"(?:지난|이전|기존|예전|과거|원래)")
_RULE_NEGATED_EXCLUSION = re.compile(
    r"(?:못\s*가|안\s*돼|안\s*되|불가능|제외|빼)[^,;.!?？\n]{0,22}"
    r"(?:아니|아닙|않|말|없는\s*건\s*아니)"
)
_RULE_START_ONLY = re.compile(r"(?:시작일|첫날)\s*만")
_RULE_END_ONLY = re.compile(r"(?:종료일|끝나는\s*날|마지막\s*날|끝날)\s*만")
_RULE_DATE_RANGE = re.compile(r"(?:부터.{1,32}까지|~|∼|〜|－|–|—)")
_RULE_TITLE_ASSIGN = re.compile(rf"^\s*(?:{EXTRA_TITLE_LABEL}){EXTRA_LABEL_MARKER}\s*")
_RULE_REASON_ASSIGN = re.compile(rf"^\s*(?:{EXTRA_REASON_LABEL}){EXTRA_LABEL_MARKER}\s*")
_RULE_ASSIGN_VERBS = ("적어", "입력", "수정", "변경", "바꿔", "바꾸", "고쳐", "고치")


def _intent(item: FieldIntent) -> IntentKind:
    if item.intent_kind is not None:
        return item.intent_kind
    return {
        "keep": "no_mention",
        "set": "explicit_change",
        "clear": "explicit_change",
        "uncertain": "ambiguous",
    }[item.action]


def _mentions_field(form: FormName, field: str, text: str, context: AssistContext) -> bool:
    """규칙 미추출 뒤 이전 값을 되살리지 않기 위한 최소 '언급됨' 검사."""
    scope = _field_scope(form, field, text, context)
    if scope != "none":
        return scope in ("target", "unbound")
    return _legacy_mentions_field(form, field, text, context)


def _legacy_mentions_field(form: FormName, field: str, text: str, context: AssistContext) -> bool:
    if form == "period" and field == "excluded":
        # 기간 끝날이 언급됐다는 이유만으로 기존 제외일을 버리지 않는다.
        return bool(_EXCLUDE_DAY.search(text) or _EXCLUDED_DAY_REFERENCE.search(text))
    if form == "period" and field in ("start", "end"):
        if _other_boundary_only(form, field, text):
            return False
        # '제외할 날 9일 대신 11일'에 나온 날짜는 start/end 수정 지시가 아니다.
        if (
            any(word in text for word in ("제외", "빼는 날", "빼는 날짜", "안 되는 날"))
            and not _RULE_DATE_RANGE.search(text)
            and not any(word in text for word in ("시작", "첫날", "종료", "끝날", "마지막", "기간"))
        ):
            return False
    if form == "quote" and field == "days":
        return _quote_duration_mentioned(text, context)
    if any(word in text for word in _CLEAR_TARGETS[field]):
        return True
    if field == "amount":
        return bool(_amount_mentions(text))
    if field == "crew":
        return bool(crew_mentions(text)) or "명" in text or "인원" in text
    if field in ("start", "end", "valid", "excluded") or form == "dates" and field == "days":
        return bool(date_tokens(text, context))
    return False


def _target_word(form: FormName, field: str, text: str) -> bool:
    if form == "quote" and field in ("days", "valid"):
        mentions = field_mentions(form, text)
        if mentions:
            return any(field in mention.fields for mention in mentions)
    if form == "quote" and field == "days":
        text = re.sub(r"유효\s*기간", "", text)
    if form == "period" and field == "excluded" and _EXCLUDED_DAY_REFERENCE.search(text):
        return True
    return any(word in text for word in _CLEAR_TARGETS[field])


def _explicit_all_exclusions_release(evidence: str, text: str) -> bool:
    """기존 제외일 전체가 '풀어 주세요'의 직접 목적어인 확정 요청만 인정한다."""
    if not _EXCLUDED_RELEASE_ALL.search(evidence):
        return False
    raw, needle = normalized(text), normalized(evidence)
    if not needle or raw.count(needle) != 1:
        return False
    at = raw.find(needle)
    left = max((part.end() for part in re.finditer(r"[,;.!?？\n]", raw[:at])), default=0)
    end = re.search(r"[,;.!?？\n]", raw[at:])
    if end and end.group() in ("?", "？"):
        return False
    right = at + end.start() if end else len(raw)
    clause = raw[left:right]
    if (
        _RULE_QUESTION_OR_UNDECIDED.search(clause)
        or _EXCLUDED_RELEASE_DENIAL.search(clause)
        or _RETAIN_NEGATED.search(clause)
        or re.search(r"(?:그거|그\s*요청|제외일\s*해제)(?:는|를|은)?\s*(?:취소|철회)", raw[at:])
    ):
        return False
    for mention in _EXCLUDED_DAY_REFERENCE.finditer(clause):
        directive = _EXCLUDED_RELEASE_DIRECT.match(clause, mention.end())
        # '풀어줘야 하면'처럼 요청형 어미 뒤에 조건이 붙는 문장을 배제한다.
        if directive and (directive.end() == len(clause) or not clause[directive.end()].isalpha()):
            return True
    return False


def _targeted_clear(form: FormName, field: str, evidence: str, text: str) -> bool:
    released_exclusions = (
        form == "period"
        and field == "excluded"
        and _explicit_all_exclusions_release(evidence, text)
    )
    if not (released_exclusions or _deletion_requested(evidence)):
        return False
    # 근거가 '삭제해 주세요'처럼 동사만 있어도, 바로 앞의 목적어로 필드를 확인한다.
    at = normalized(text).find(normalized(evidence))
    before = normalized(text)[max(at - 35, 0) : at] if at >= 0 else ""
    after = normalized(text)[at + len(normalized(evidence)) : at + len(normalized(evidence)) + 35]
    scope = re.split(r"[,;.!?\n]", before)[-1] + normalized(evidence) + after
    if (
        _CLEAR_DENIAL.search(scope)
        or CLEAR_NEGATED.search(scope)
        or (released_exclusions and _EXCLUDED_RELEASE_DENIAL.search(scope))
    ):
        return False
    if not released_exclusions and any(word in evidence for word in ("전부", "모두", "전체")):
        return True
    return _target_word(form, field, scope)


def _targeted_retention(
    form: FormName,
    field: str,
    evidence: str,
    text: str,
    context: AssistContext,
    *,
    retaining_previous: bool = True,
    require_current_reference: bool = False,
    retained_value: Value = None,
    independent_clauses: frozenset[str] = frozenset(),
) -> bool:
    scoped = _scoped_retention(
        form,
        field,
        evidence,
        text,
        context,
        retaining_previous=retaining_previous,
        require_current_reference=require_current_reference,
        independent_clauses=independent_clauses,
    )
    if scoped is not None and not (retaining_previous and scoped[0].first):
        if (
            retaining_previous
            and (field == "vat" or (form, field) == ("quote", "days"))
            and retained_value is not None
        ):
            raw = normalized(text)
            scope = scoped[0]
            if _retention_has_different_value(
                form, field, retained_value, raw[scope.start : scope.end], context
            ):
                return False
        return scoped[1]
    if not evidence_matches(text, evidence):
        return False
    raw = normalized(text)
    needle = normalized(evidence)
    # 같은 근거가 긍정/부정의 두 절에 반복되면 어떤 절인지 확정할 수 없다.
    if raw.count(needle) != 1:
        return False
    at = raw.find(needle)
    if not retaining_previous:
        # 모델이 현재값 참조까지만 근거로 잘라 반환한 경우, 같은 절에
        # 바로 이어지는 명시적 긍정 유지 동사만 원문에서 확인한다.
        reference = re.search(rf"{_CURRENT_VALUE_REFERENCE}$", needle)
        if reference:
            complete = re.compile(_CURRENT_VALUE_KEEP).match(raw, at + reference.start())
            if complete and complete.end() > at + len(needle):
                needle = raw[at : complete.end()]
    generic_scope = any(
        word in needle for word in ("나머지", "다른", "그 외", "전체", "전부", "모두")
    )
    clause_break = re.compile(r"[,;.!?？\n]")
    for retained in _RETAIN.finditer(needle):
        verb_at = at + retained.start()
        left = max((part.end() for part in clause_break.finditer(raw, 0, verb_at)), default=0)
        right_break = clause_break.search(raw, verb_at)
        # 물음표는 유지 자체가 질문이라는 근거라 검사 범위에 남긴다.
        if right_break is None:
            right = len(raw)
        else:
            right = right_break.end() if right_break.group() in ("?", "？") else right_break.start()
        # '아까 제안한 공사 기간만 그대로'처럼 유지 표현 안에 대상이 들어가기도 한다.
        prefix = raw[left : at + retained.end()]

        # 보통 '금액과 부가세는 앞서 정한 안으로 두고'처럼 여러 대상이
        # 한 조사로 묶인다. 가장 가까운 명사구의 연결된 대상만 인정한다.
        targets: list[tuple[int, int, str]] = []
        for name in FORM_FIELDS[form]:
            words = _CLEAR_TARGETS[name]
            if name == "vat":
                words = (*words, "세금 조건", "세금조건", "세금 방식")
            if name == "valid":
                words = (*words, "유효 기간")
            if (form, name) == ("dates", "days"):
                words = (*words, "작업일")
            if form == "period" and name in ("start", "end"):
                words = (*words, "기간", "시작일" if name == "start" else "종료일")
            for word in words:
                for match in re.finditer(re.escape(word), prefix):
                    targets.append((match.start(), match.end(), name))
        if form == "period":
            targets.extend(
                (match.start(), match.end(), "excluded")
                for match in _EXCLUDED_DAY_REFERENCE.finditer(prefix)
            )
        targets.extend(
            (match.start(), match.end(), match.lastgroup)
            for match in _prior_field_matches(form, prefix)
        )

        scope_start = left
        bound = False
        if targets:
            nearest_end = max(end for _, end, _ in targets)
            # 유효기간의 '기간'처럼 긴 필드명의 일부만 일치한 후보는 제외한다.
            targets = [
                part
                for part in targets
                if not any(start < part[0] and part[1] == end for start, end, _ in targets)
            ]
            nearest = [part for part in targets if part[1] == nearest_end]
            names = {name for _, _, name in nearest}
            first = min(start for start, _, _ in nearest)
            # '금액과 부가세'는 양쪽 필드지만 '금액은 바꾸고 부가세는'
            # 유지할 대상이 부가세뿐이다.
            for start, end, name in sorted(targets, reverse=True):
                if end > first:
                    continue
                if re.fullmatch(r"\s*(?:과|와|랑|및|하고|·|/)\s*", prefix[end:first]):
                    names.add(name)
                    first = start
            bound = field in names
            scope_start = left + first

        # '나머지는 그대로'는 다른 필드만 갱신한 상황에서 사용한다.
        if generic_scope and not _target_word(form, field, needle):
            without_evidence = raw.replace(needle, "", 1)
            if not (
                _other_boundary_only(form, field, without_evidence)
                or not _mentions_field(form, field, without_evidence, context)
            ):
                continue
            generic_at = max(
                (
                    prefix.rfind(word)
                    for word in ("나머지", "다른", "그 외", "전체", "전부", "모두")
                ),
                default=-1,
            )
            scope_start = left + max(generic_at, 0)
            bound = True

        if not bound:
            continue
        # 다른 필드의 새 절에 있는 불확실성·부정은 유지 지시를 무효화하지 않는다.
        # 반대로 해당 필드의 '모르겠어요/두지 마세요'는 같은 국소 범위에서 거부한다.
        for name in FORM_FIELDS[form]:
            if name == field:
                continue
            for word in _CLEAR_TARGETS[name]:
                next_target = raw.find(word, verb_at + len(retained.group()), right)
                if next_target >= 0:
                    right = min(right, next_target)
        for target in _prior_field_matches(form, raw):
            if (
                target.lastgroup != field
                and verb_at + len(retained.group()) <= target.start() < right
                and re.match(r"\s*[은는이가을를만도]", raw[target.end() :])
            ):
                right = target.start()
        scope = raw[scope_start:right]
        if require_current_reference and not (
            _current_retention_reference(form, field, scope)
            or re.search(r"(?:현재|지금)\s*$", raw[:scope_start])
        ):
            continue
        if (
            not retaining_previous
            and (re.search(_PRIOR_REFERENCE, scope) or _PRIOR_PROPOSAL_REFERENCE.search(scope))
            and not _current_retention_reference(form, field, scope)
        ):
            # 명시적인 이전 제안 유지 지시를 현재값 유지로 바꾸지 않는다.
            continue
        if _RETAIN_NEGATED.search(scope) or _RULE_QUESTION_OR_UNDECIDED.search(scope):
            continue
        # 유지 지시 뒤에 같은 절의 취소가 나오면 현재값 유지도 확정할 수 없다.
        if _PENDING_CANCEL.search(raw[at + retained.end() : right]):
            continue
        if _retention_scope_conflict(
            form,
            field,
            raw,
            scope_start,
            right,
            context,
            retaining_previous=retaining_previous,
            independent_clauses=independent_clauses,
        ):
            continue
        if (
            retaining_previous
            and retained_value is not None
            and (field in ("amount", "crew", "vat") or (form, field) == ("quote", "days"))
            and _retention_has_different_value(form, field, retained_value, scope, context)
        ):
            continue
        return True
    return False


def _retention_has_different_value(
    form: FormName, field: str, value: Value, evidence: str, context: AssistContext
) -> bool:
    """새 발화에서 명시한 숫자·날짜가 옛 제안과 다르면 옛 값을 되살리지 않는다."""
    mentioned: set[Value] | set[date]
    if field == "crew":
        mentioned = crew_mentions(evidence)
    elif field == "amount":
        mentioned = _amount_mentions(evidence)
    elif (form, field) == ("quote", "days"):
        mentioned = {n for _, _, n in _quote_duration_tokens(normalized(evidence))}
    elif field == "vat":
        mentioned = _vat_modes(normalized(evidence))
    elif field in ("start", "end", "valid"):
        mentioned = {day for _, _, day in date_tokens(evidence, context)}
        value = date.fromisoformat(value) if isinstance(value, str) else value
    else:
        return False
    return bool(mentioned) and mentioned != {value}


# 앞서 실제로 말한 값을 다시 확인하는 표현은 동사만 잘라낸 모델 근거가
# 아니라 원문의 같은 절에서 대상, 값, 유지 지시를 함께 확인한다.
_REAFFIRM_REFERENCE = re.compile(rf"{RECENT_HISTORY_TIME}\s*{HISTORY_PAST_MODIFIER}\s*")
_REAFFIRM_DIRECTIVE = (
    r"(?:가(?:고요?|주세요|주십시오|요|겠습니다)|"
    rf"{PLACEMENT_KEEP_DIRECTIVE}|"
    r"(?:반영|유지|사용|적용)\s*(?:해\s*(?:주세요|줘요|주십시오)|할게요|하고요?|하겠습니다)?|"
    r"해\s*(?:주세요|줘요|주십시오)|할게요|하겠습니다)"
)
_REAFFIRM_KEEP = re.compile(
    r"그대로(?:\s*" + _REAFFIRM_DIRECTIVE + r")?"
    r"|(?:걸로|대로|(?:조건|방식|안|내용)(?:으로|로))\s*(?:계속\s*)?" + _REAFFIRM_DIRECTIVE
)
_REAFFIRM_BREAK = re.compile(r"(?<!\d)\.|\.(?!\d)|[;!?？]")
_REAFFIRM_UNDECIDED = re.compile(
    r"(?:나중에|추후|다음에|예정|검토|생각\s*중|고민|보류|취소|철회|"
    r"않|아니|말고|지\s*마|지\s*말|거라면|할지|할까|하면|할\s*경우|[?？])"
)
_REAFFIRM_TARGETS = {
    "amount": r"추가\s*(?:금액|비용|금|비)|견적\s*(?:총액|금액)|금액|가격|비용|총액|견적",
    "vat": r"부가세|세금(?:\s*(?:조건|방식))?",
    "days": r"(?:공사|작업)\s*(?:날짜|일수|기간|일정)|날짜|일수|기간|일정|작업일|공사일",
    "crew": r"인원|작업자",
    "title": r"공사명|작업명|항목명|제목|항목|이름",
    "reason": r"사유|이유",
    "valid": r"유효\s*기간|만료일",
}


def _reaffirm_target(form: FormName, text: str, *, prefix: bool) -> tuple[str, re.Match] | None:
    """앞 참조 바로 전의 목적어 또는 참조 뒤의 명시적인 대상만 받는다."""
    for field in FORM_FIELDS[form]:
        words = _REAFFIRM_TARGETS.get(field)
        if not words:
            continue
        target = rf"(?:{words})(?:\s*[은는이가을를만도])?\s*"
        match = re.search(target + r"$" if prefix else r"^" + target, text)
        if match:
            return field, match
    return None


def _without_tokens(text: str, tokens: list[tuple[int, int, object]]) -> str:
    for start, end, _ in reversed(tokens):
        text = text[:start] + text[end:]
    return text


def _reaffirmed_value(
    form: FormName, field: str, value: Value, body: str, context: AssistContext
) -> bool:
    """반복 값은 부분 일치가 아니라 정규화한 전체 값과 일치해야 한다."""
    if field == "amount":
        tokens = _amount_tokens(body)
        remainder = re.sub(r"\s+", "", _without_tokens(body, tokens))
        return (
            len(tokens) == 1
            and {part[2] for part in tokens} == {value}
            and bool(re.fullmatch(r"원?[은는이가을를도만]?", remainder))
        )
    if (form, field) == ("dates", "days"):
        dates = date_tokens(body, context)
        remainder = re.sub(r"\s+", "", _without_tokens(body, dates))
        remainder = re.sub(r"그리고|하고|이랑|부터|까지|랑|과|와|및", "", remainder)
        return (
            isinstance(value, list)
            and bool(dates)
            and {day.isoformat() for day in date_mentions(body, context, expand=True)} == set(value)
            and all(char in ",，、·~∼〜–—－-은는이가을를도만" for char in remainder)
        )
    if field == "vat":
        return isinstance(value, str) and vat_value_matches(value, body)
    if field == "title":
        return isinstance(value, str) and normalized(value) == body
    return False


def _reaffirmation_conflicts(
    form: FormName, field: str, outside: str, context: AssistContext, value: Value
) -> bool:
    # 다른 칸의 직접 입력·정정은 유지 대상에 전파하지 않는다.
    if re.search(
        r"(?:그\s*(?:요청|제안|거)|방금\s*(?:요청|말)|유지\s*요청)"
        r"(?:\s*[은는을를])?\s*(?:취소|철회|보류)",
        outside,
    ):
        return True
    words = _REAFFIRM_TARGETS.get(field)
    if not words:
        return False
    for clause in re.split(r"(?<!\d),|,(?!\d)|(?<!\d)\.|\.(?!\d)|[;!?？]", outside):
        # 목적어를 생략한 취소·재정정도 바로 앞 유지 지시를 철회할 수 있다.
        clause = clause.strip()
        if re.match(
            r"^(?:아니\s*|아\s*|잠깐\s*)?(?:그\s*(?:건|거|것)(?:\s*[은는을를])?\s*)?"
            r"(?:취소|철회|보류|안\s*할|아직\s*미정)",
            clause,
        ):
            return True
        other_target = any(
            re.search(rf"(?:{target})", clause)
            for name, target in _REAFFIRM_TARGETS.items()
            if name in FORM_FIELDS[form] and name != field
        )
        change = re.search(
            r"(?:바꿔|바꾸|고쳐|고치|고쳤|수정|변경|정정|입력|덮어|대신|말고|아니)", clause
        )
        if not other_target and change:
            if field == "amount" and (amounts := _amount_mentions(clause)) and amounts != {value}:
                return True
            if (form, field) == ("dates", "days") and isinstance(value, list):
                dates = {day.isoformat() for day in date_mentions(clause, context, expand=True)}
                if dates and dates != set(value):
                    return True
            if field == "vat" and _retention_has_different_value(
                form, field, value, clause, context
            ):
                return True
        if re.search(rf"(?:{words})\s*[은는이가을를만도]?", clause) and (
            _deletion_requested(clause)
            or _RULE_QUESTION_OR_UNDECIDED.search(clause)
            or _REAFFIRM_UNDECIDED.search(clause)
            or re.search(r"(?:바꿔|바꾸|고쳐|고치|고쳤|수정|변경|정정|입력|덮어)", clause)
            or _retention_has_different_value(form, field, value, clause, context)
        ):
            return True
    return False


def _verified_reaffirmation(
    form: FormName, field: str, value: Value, evidence: str, text: str, context: AssistContext
) -> bool | None:
    """명시적인 이전 값 재확인만 복구한다. 다른 유지 표현은 기존 검증에 맡긴다."""
    if field not in ("amount", "vat", "title") and (form, field) != ("dates", "days"):
        return None
    raw, needle = normalized(text), normalized(evidence)
    if not needle or raw.count(needle) != 1:
        return False
    at = raw.index(needle)
    refs = [
        match
        for match in _REAFFIRM_REFERENCE.finditer(raw)
        if at <= match.start() < at + len(needle)
    ]
    if not refs:
        return None
    if len(refs) != 1:
        return False
    ref = refs[0]
    stop = _REAFFIRM_BREAK.search(raw, ref.end())
    right = stop.start() if stop else len(raw)
    after_ref = raw[ref.end() : right]
    keep = _REAFFIRM_KEEP.search(after_ref)
    if keep is None:
        return None
    end = ref.end() + keep.end()
    tail = raw[end:right].lstrip()
    if tail and not tail.startswith(","):
        next_target = _reaffirm_target(form, tail, prefix=False)
        if next_target is None or next_target[0] == field:
            return False
    elif not tail and stop and stop.group() in ("?", "？"):
        return False
    prefix = raw[: ref.start()]
    target = _reaffirm_target(form, prefix, prefix=True)
    scope_start = target[1].start() if target else ref.start()
    body = after_ref[: keep.start()].strip()
    if target and target[0] != field:
        # '금액과 부가세는 앞서 정리한 안으로 두고'의 공동 대상은 기존 검증을 쓴다.
        if not body and _targeted_retention(form, field, evidence, text, context):
            return None
        return False
    body_target = _reaffirm_target(form, body, prefix=False)
    if body_target:
        if body_target[0] != field:
            return False
        body = body[body_target[1].end() :].strip()
    scope = raw[scope_start:end]
    if (
        _RETAIN_NEGATED.search(scope)
        or _RULE_QUESTION_OR_UNDECIDED.search(scope)
        or _REAFFIRM_UNDECIDED.search(scope)
        or re.search(
            r"(?:혹시|아마|나중에|추후|다음에|가능하면|조건부로)\s*$",
            raw[:scope_start],
        )
        or re.search(r"(?:현재|지금)\s*(?:폼|화면|입력)|폼\s*값\s*대로", scope)
    ):
        return False
    # 모델이 대상 명사를 잘랐어도 유일한 완전한 금액 또는 날짜 목록은
    # 같은 필드의 근거가 된다. 값 없는 유지에는 명시적인 대상이 필요하다.
    if body:
        if not _reaffirmed_value(form, field, value, body, context):
            return False
        if field in ("vat", "title") and not (target or body_target):
            return False
    elif not (target or body_target):
        return None
    outside = raw[:scope_start] + " ; " + raw[end:]
    return not (
        _retention_scope_conflict(form, field, raw, scope_start, end, context)
        or _reaffirmation_conflicts(form, field, outside, context, value)
    )


_PRIOR_FIELD_WORDS = {
    "start": r"착공일?|시작\s*(?:날짜|일자|일)|첫\s*(?:(?:공사|작업)\s*일|날짜|날)",
    "end": r"준공일?|마감\s*(?:날짜|일자|일)?|종료\s*(?:날짜|일자|일)|끝나는\s*날|마지막\s*(?:(?:공사|작업)\s*일|날짜|날)|끝날",
    "excluded": r"제외\s*(?:날짜|일자|일)|쉬는\s*날|빼는\s*날|작업\s*안\s*하는\s*날",
    "amount": r"견적\s*(?:금액|총액)|추가\s*(?:금액|비용|금)|금액|가격|비용|총액|공사비",
    "vat": r"부가세(?:\s*(?:조건|방식|구분))?|세금(?:\s*(?:조건|방식|구분))?",
    "days": r"(?:공사|작업)\s*(?:날짜|일자|일수|기간|일정|일)|날짜|일정|일수|기간|공기",
    "crew": r"(?:투입|작업)\s*인원|인원|인력|작업자",
    "title": EXTRA_TITLE_LABEL,
    "reason": EXTRA_REASON_LABEL,
    "valid": r"(?:견적(?:서)?\s*)?(?:유효\s*(?:기간|기한|일|날짜|일자)|만료\s*(?:일|날짜|일자)|효력(?:\s*(?:기간|기한|일))?)",
}
_PRIOR_REFERENCE = rf"(?P<when>{HISTORY_TIME})\s*{HISTORY_PAST_MODIFIER}"
_PRIOR_PROPOSAL_REFERENCE = re.compile(
    r"(?:이전|앞서|아까|방금|직전|지난) ?(?:제안|요청|변경안|정정안)"
)
_PRIOR_REFERENT = {
    "start": r"날짜|일자|일정",
    "end": r"날짜|일자|일정",
    "excluded": r"날짜|일자|목록",
    "amount": r"금액|가격|비용|총액",
    "vat": r"조건|방식|구분",
    "days": r"날짜|일정|기간|일수|공기|목록",
    "crew": r"인원|인력",
    "title": r"항목|제목",
    "reason": r"사유|이유",
    "valid": r"날짜|일자|기간",
}
_PRIOR_DIRECTIVE = re.compile(
    r"(?:\s*(?:계속\s*)?(?:"
    r"(?:해|잡아|맞춰|반영해|적용해|유지해|사용해|써)\s*(?:주세요|줘요|주십시오)"
    r"|부탁(?:합니다|드립니다|해요|드려요)"
    rf"|가(?:고요?|죠|겠습니다|주세요|요)|{PLACEMENT_KEEP_DIRECTIVE}"
    r"|(?:유지|사용|반영|적용)\s*(?:하고요?|할게요|하겠습니다|합니다|해요)"
    r"|하고요?|할게요|하겠습니다|쓰면\s*됩니다))"
)
_PRIOR_CANCEL_OR_CONDITION = re.compile(
    rf"(?:취소|철회|보류|{WITHDRAW_IDIOM}|미정|검토|고민|나중에|추후|혹시|아마|"
    rf"{CONDITIONAL_LINK.pattern}|"
    r"가능하면|경우|조건부|하면|라면|으면|다면|할지|할까|않|아니|아닙|아님|말고|지\s*마|지\s*말)"
)
_RETENTION_CONDITION = re.compile(
    r"(?:가능하면|가능할\s*때|조건부|경우|(?:이|라|다|으)면|할\s*때|하면|"
    r"나중에|추후|혹시|아마|미정|보류|검토|고민|예정|잠정|할지|할까|"
    r"(?:결정|확정)(?:하지 ?않| ?못 ?했| ?전)|"
    rf"{CONDITIONAL_LINK.pattern}|{_INDIRECT_INQUIRY}|[?？])"
)
_PENDING_CANCEL = re.compile(rf"(?:취소|철회|{WITHDRAW_IDIOM}|원래대로\s*돌)")
_PENDING_CHANGE = re.compile(
    r"(?:바꿔|바꾸|바꿀|고쳐|고치|고칠|고쳤|수정|변경|정정|덮어|입력|삭제|지우|제거)"
)
_CURRENT_RETENTION = re.compile(
    r"(?:현재|지금)\s*(?:대로|값|폼|화면|입력|기존|직접|적은|적힌|있는)"
    r"|(?:기존|원래)\s*(?:폼|화면|입력)"
    r"|(?:폼|화면|입력란)(?:에|에서)?\s*(?:있던|있는|값|직접)"
)
_PENDING_CLAUSE = re.compile(r"(?<!\d),|,(?!\d)|(?<!\d)\.|\.(?!\d)|[;!。\n]")
_SCALAR_CANCEL_CHANGE = re.compile(
    r"(?:으로|로)\s?"
    r"(?:(?:변경|수정|정정|확정|입력|설정|조정|증액|감액)하|"
    r"올리|내리|바꾸|고치|넣)(?:는|려던|려고 했던|기로 했던)\s?"
    r"(?:요청|제안|변경안|정정안|건|것|거)(?:은|는|을|를|이|가|만)?"
)


def _scalar_cancellation_field(form: FormName, clause: str, cancel_at: int) -> str | None:
    """수치를 포함한 변경 요청 자체를 명시적으로 취소했는지 확인한다.

    취소된 수치는 적용할 값이 아니다. 문장 전체의 대상과 취소 목적어를
    검증하며, 숫자만으로 다른 필드의 보류를 해제하지 않는다.
    """
    if not complete_withdrawal_body(clause[cancel_at:]):
        return None
    description = clause[:cancel_at].strip()
    matches = []
    for field in ("amount", "crew"):
        if field not in FORM_FIELDS[form]:
            continue
        tokens = _scalar_tokens(field, description)
        if len(tokens) not in (1, 2):
            continue
        start = tokens[0][0]
        target = _prior_field_word(form, field)
        # 견적만 명시한 금액 요청도 해당 폼에서만 허용한다.
        if form == "quote" and field == "amount":
            target = rf"(?:{target}|견적)"
        if not re.fullmatch(
            rf"(?:(?:아까|앞서|방금|이전에|지난번에)\s?)?"
            rf"(?:(?:{target})(?:은|는|이|가|을|를|만)?\s?)?",
            description[:start],
        ):
            continue
        ends = [a + len(_scalar_span_evidence(field, description, a, b)) for a, b, _ in tokens]
        if len(tokens) == 2 and not re.fullmatch(
            r"\s?(?:을|를|에서|말고|대신)\s?", description[ends[0] : tokens[1][0]]
        ):
            continue
        if _SCALAR_CANCEL_CHANGE.fullmatch(description[ends[-1] :].strip()):
            matches.append(field)
    return matches[0] if len(matches) == 1 else None


def _prior_field_word(form: FormName, field: str) -> str:
    if (form, field) == ("quote", "days"):
        return r"(?:공사|작업|시공)\s*(?:일수|기간|소요일(?:수)?)|기간|일수|공기|소요일(?:수)?"
    return _PRIOR_FIELD_WORDS[field]


def _prior_field_pattern(form: FormName) -> str:
    # 긴 필드명 전체를 먼저 읽어 '유효기간'의 '기간'을 별도 대상으로 보지 않는다.
    return "|".join(
        f"(?P<{name}>{_prior_field_word(form, name)})"
        for name in sorted(FORM_FIELDS[form], key=lambda name: name != "valid")
    )


def _prior_field_matches(form: FormName, text: str) -> list[re.Match]:
    pattern = _prior_field_pattern(form)
    return list(
        re.finditer(rf"(?<![가-힣])(?:{pattern})(?=$|[\s,;.!?？은는이가을를만도과와])", text)
    )


def _current_retention_reference(form: FormName, field: str, text: str) -> bool:
    return bool(
        _CURRENT_RETENTION.search(text)
        or re.search(
            rf"(?:현재|지금)\s*(?:{_prior_field_word(form, field)})(?=$|[\s은는이가을를만도])",
            text,
        )
    )


def _retention_clause_fields(form: FormName, clause: str) -> tuple[set[str], int]:
    """완전한 유지 절이면 참조 명사가 아니라 실제 대상 집합을 사용한다."""
    parts = intent_clauses(form, clause)
    names = {name for part in parts for name in part.fields}
    first = min(
        (target.start for part in parts for target in (*part.mentions, *part.retentions)),
        default=len(clause),
    )
    if not names:
        legacy = _prior_field_matches(form, clause)
        names.update(target.lastgroup for target in legacy)
        first = min((target.start() for target in legacy), default=first)
    return names, first


@dataclass(frozen=True)
class _ResolvedIntentClause:
    clause: IntentClause
    fields: frozenset[str]
    unbound: bool
    # 대상 없는 후속 날짜는 이전 경계의 경쟁 가능성일 뿐 새 경계의 근거가 아니다.
    boundary_competition: frozenset[str] = frozenset()


def _resolved_intent_clauses(
    form: FormName, raw: str, context: AssistContext
) -> tuple[_ResolvedIntentClause, ...]:
    """라벨과 타입이 확인된 값만 대상에 귀속한다. 불명확한 변경은 숨기지 않는다."""
    result = []
    for part in intent_clauses(form, raw):
        text = raw[part.start : part.end].strip()
        fields = set(part.fields)
        boundary_competition = frozenset()
        if not fields and not part.retentions:
            fields.update(target.lastgroup for target in _prior_field_matches(form, text))
        if form == "period" and _EXCLUDED_DAY_REFERENCE.search(text):
            fields.add("excluded")
        if "amount" in FORM_FIELDS[form]:
            amounts = _amount_tokens(text)
            # 면세의 0원은 독립 견적 금액이 아니다.
            if amounts and not (
                fields == {"vat"} and {value for _, _, value in amounts} == {0} and _vat_modes(text)
            ):
                fields.add("amount")
        if form == "dates" and crew_tokens(text):
            fields.add("crew")
        tokens = date_tokens(text, context)
        if form == "quote":
            # 날짜 토큰과 일수 토큰은 겹칠 수 있다. 명시 유효기간 날짜 또는
            # 월/년/슬래시가 포함된 날짜만 일수 검사에서 제외한다.
            date_spans = [
                (a, b)
                for a, b, _ in tokens
                if "valid" in fields or re.search(r"월|년|[/.-]|오늘|내일|모레|요일", text[a:b])
            ]
            if date_spans:
                fields.add("valid")
            if _quote_duration_tokens(text):
                fields.add("days")
        elif form == "dates" and tokens:
            fields.add("days")
        elif form == "period" and tokens:
            if not fields.intersection(("start", "end")) and _EXCLUDE_DAY.search(text):
                fields.add("excluded")
            if (
                not fields
                and result
                and result[-1].clause.boundary == "comma"
                and result[-1].fields == {"excluded"}
                and not re.search(r"시작|종료|마감|기간", text)
                and re.fullmatch(
                    r"[\d\s월년일/.,-]+(?:\s*(?:한|두|세|네|다섯|여섯|일곱|여덟|아홉|열|\d+)\s*(?:개|날짜|일자))?(?:도|은|는|을|를|으로|로)?\s*(?:(?:새로|다시)\s*)?(?:(?:제안|확정|변경|추가|입력|등록|설정|지정|반영|빼|제외|넣|정해|정하).*)?",
                    text,
                )
            ):
                fields.add("excluded")
            if not fields.intersection(("start", "end", "excluded")) or (
                fields.intersection(("start", "end")) and _RULE_DATE_RANGE.search(text)
            ):
                if not fields and not _RULE_DATE_RANGE.search(text) and len(tokens) == 1:
                    marker = re.match(r"\s*(부터|까지)", text[tokens[0][1] :])
                    if marker:
                        fields.add("start" if marker[1] == "부터" else "end")
                    elif result and result[-1].fields in ({"start"}, {"end"}):
                        last = result[-1].clause
                        if date_tokens(raw[last.start : last.end], context):
                            boundary_competition = result[-1].fields
                if not fields or _RULE_DATE_RANGE.search(text):
                    fields.update(("start", "end"))
        # 세금 방식만 적은 절은 다른 라벨이 없을 때 세금 요청으로 읽는다.
        if "vat" in FORM_FIELDS[form] and not fields and standalone_vat_modes(text):
            fields.add("vat")
        # 바로 앞의 명시적 문의에 이어지는 단순 미정 보고는 그 문의의 대상이다.
        # 목적어 없는 새 변경·취소 또는 일반 조건은 여기에 귀속하지 않는다.
        if (
            not fields
            and result
            and result[-1].clause.boundary == "question"
            and len(result[-1].fields) == 1
            and not result[-1].unbound
            and re.fullmatch(
                r"(?:아직\s*)?(?:미정(?:입니다|이에요|이네요)?|모르겠(?:습니다|어요)|고민\s*중(?:입니다|이에요)?)[.!。]?",
                text,
            )
        ):
            fields.update(result[-1].fields)
        # 구체적인 날짜 교체에 연결된 마지막 확인 문장만 같은 대상으로
        # 분류한다. 목적어 없는 독립 변경·취소는 계속 unbound로 남긴다.
        edit_field = {"dates": "days", "period": "excluded"}.get(form)
        if (
            edit_field is not None
            and not fields
            and len(result) == 1
            and result[-1].fields == {edit_field}
            and not result[-1].unbound
            and result[-1].clause.boundary == "sentence"
            and part.boundary in ("sentence", "end")
            and (confirmation := date_edit_confirmation_start(edit_field, raw)) is not None
            and part.start <= confirmation < part.end
        ):
            fields.add(edit_field)
        risk = (
            _RETENTION_CONDITION.search(text)
            or _RULE_QUESTION_OR_UNDECIDED.search(text)
            or _PENDING_CANCEL.search(text)
            or _RETAIN_NEGATED.search(text)
            or _PENDING_CHANGE.search(text)
            or _RETAIN.search(text)
        )
        # 미완결 조건은 뒤 절에도 걸릴 수 있다. 질문으로 끝난 독립 절은 제외한다.
        condition = (
            part.boundary in ("comma", "connective")
            and _SCALAR_CONDITION.search(text)
            and not _RULE_QUESTION_OR_UNDECIDED.search(text)
        )
        unbound = bool(condition or (not fields and risk and not part.retentions))
        result.append(_ResolvedIntentClause(part, frozenset(fields), unbound, boundary_competition))
    return tuple(result)


def _scalar_risk_scopes(
    clauses: tuple[_ResolvedIntentClause, ...],
) -> tuple[ScalarRiskScope, ...]:
    """이미 증명한 절 경계와 필드 귀속을 값 검증으로 전달한다."""
    return tuple(
        ScalarRiskScope(part.clause.start, part.clause.end, part.fields, part.unbound)
        for part in clauses
    )


def _conditional_assignment_fields(
    form: FormName, text: str, context: AssistContext
) -> frozenset[str]:
    """공통 절에서 확인된 조건부 요청 대상만 상태 보류로 전달한다."""
    raw = normalized_scope(text)
    if form == "extra":
        raw = mask_bound_text_literals(raw)
    dependent = False
    fields: set[str] = set()
    for part in _resolved_intent_clauses(form, raw, context):
        clause = part.clause
        dependent = dependent or bool(CONDITIONAL_LINK.search(raw[clause.start : clause.end]))
        if dependent:
            fields.update(part.fields)
        if clause.boundary in ("sentence", "question"):
            dependent = False
    return frozenset(fields)


def _unresolved_owned_fields(
    data: AssistRequest, context: AssistContext, previous: PreviousCandidate | None
) -> frozenset[str]:
    """Preserve unresolved field intent even when both producers abstain."""
    raw = normalized_scope(data.text)
    if data.form == "extra":
        raw = mask_bound_text_literals(raw)
    fields: set[str] = set()
    for resolved in _resolved_intent_clauses(data.form, raw, context):
        part = resolved.clause
        owners = {field for mention in part.mentions for field in mention.fields}
        if len(owners) != 1 or resolved.fields != owners:
            continue
        field = next(iter(owners))
        clause = raw[part.start : part.end]
        risk = _RULE_QUESTION_OR_UNDECIDED.search(polite_execution_risk_text(clause))
        if risk and (
            _PENDING_CHANGE.search(clause)
            or _amount_tokens(clause)
            and field == "amount"
            or crew_tokens(clause)
            and field == "crew"
            or re.fullmatch(r"(?:미정|모르|애매|고민|생각\s*중)", risk.group())
        ):
            fields.add(field)
    verified = {item.field for item in previous.fields} if previous else set()
    for scope in retention_scopes(data.form, raw):
        if scope.compatible and scope.reference == "previous" and not scope.remaining:
            fields.update(scope.fields - verified)
    return frozenset(fields)


def _field_scope(
    form: FormName, field: str, text: str, context: AssistContext
) -> Literal["target", "other", "unbound", "none"]:
    """target: 해당 칸, other: 독립 다른 칸, unbound: 미확정/전역, none: 요청 미식별."""
    clauses = _resolved_intent_clauses(form, normalized_scope(text), context)
    if any(clause.unbound for clause in clauses):
        return "unbound"
    if any(field in clause.fields for clause in clauses):
        return "target"
    if any(clause.fields for clause in clauses):
        return "other"
    return "none"


def _has_new_field_risk(data: AssistRequest, context: AssistContext, field: str) -> bool:
    """A new hold requires evidence for this field, not a parser side effect.

    Explicit field-owned questions/undecided operations and value spans remain
    holds. General capability questions with no boundary values, and
    independently owned sibling assignments, are not edits to every field.
    """
    raw = normalized_scope(data.text)
    if data.form == "extra":
        raw = mask_owned_text_values(raw)
    for part in _resolved_intent_clauses(data.form, raw, context):
        clause = part.clause
        body = raw[clause.start : clause.end]
        if field not in part.fields:
            continue
        owned = any(mention.fields == {field} for mention in clause.mentions)
        if owned or any(field in scope.fields for scope in clause.retentions):
            return True
        if data.form == "period" and field in ("start", "end"):
            if date_tokens(body, context) or _PENDING_CANCEL.search(body):
                return True
            if re.search(r"미정|모르|고민|보류|할지|정할까|정해야", body):
                return True
            continue
        if part.fields == {field}:
            return True
    # Unbound strong controls cannot be turned into a new operation. If a
    # previous candidate exists, the transition below keeps the original
    # uncertainty; absent history, only independently owned risk creates it.
    return False


def _quote_duration_mentioned(text: str, context: AssistContext) -> bool:
    """효력 날짜의 '일'과 실제 공사 일수의 언급을 분리한다."""
    raw = normalized(text)
    for part in intent_clauses("quote", raw):
        clause = raw[part.start : part.end].strip()
        names = part.fields
        if "days" in names:
            return True
        remaining = re.sub(r"유효\s*기간", "", clause)
        if "valid" in names:
            # 명시된 유효기간에 결합된 날짜 한 개만 일수 검사에서 제거한다.
            # 별도 숫자·기간 언급을 모두 날짜로 지우지 않는다.
            mentions = field_mentions("quote", clause)
            valid = next((mention for mention in mentions if "valid" in mention.fields), None)
            dates = date_tokens(clause, context)
            if valid is not None and len(dates) == 1 and dates[0][0] >= valid.end:
                a, b, _ = dates[0]
                remaining = re.sub(r"유효\s*기간|만료일", "", clause[:a] + clause[b:])
        if _quote_duration_tokens(remaining) or any(
            word in remaining for word in ("공사", "기간", "일수", "작업 날짜")
        ):
            return True
    return False


def _scoped_retention(
    form: FormName,
    field: str,
    evidence: str,
    text: str,
    context: AssistContext,
    *,
    retaining_previous: bool,
    require_current_reference: bool = False,
    independent_clauses: frozenset[str] = frozenset(),
) -> tuple[RetentionScope, bool] | None:
    """공통 대상·참조 증명. 미지원 문장은 기존 값 검증에 맡긴다."""
    raw, needle = normalized(text), normalized(evidence)
    if not needle or raw.count(needle) != 1:
        return None
    at = raw.index(needle)
    scopes = retention_scopes(form, raw)
    # 모델의 근거가 그룹의 대상에서 끝났더라도 원문 속 유일한 완전한
    # 유지 범위를 확인한다. 다른 대상만 잘라 낸 근거는 확장하지 않는다.
    named = {name for mention in field_mentions(form, needle) for name in mention.fields}
    matching = [
        scope
        for scope in scopes
        if at < scope.end
        and (
            at + len(needle) > scope.predicate_start
            or scope.start <= at
            and field in scope.fields
            and field in named
        )
    ]
    if not matching:
        return None
    eligible = []
    for scope in matching:
        if field in scope.fields:
            eligible.append(scope)
        elif scope.remaining:
            outside = raw[: scope.start] + " ; " + raw[scope.end :]
            names, _ = _retention_clause_fields(form, outside)
            # '나머지'는 실제로 별개 요청이 있을 때만 그 밖의 필드에 결합한다.
            if field not in names and (names or independent_clauses):
                eligible.append(scope)
    if len(eligible) != 1:
        return matching[0], False
    scope = eligible[0]
    if (
        not scope.compatible
        or retaining_previous
        and scope.reference == "current"
        or not retaining_previous
        and scope.reference == "previous"
        or require_current_reference
        and scope.reference != "current"
        or any(
            other != scope
            and field in other.fields
            and (other.reference == "current") != (scope.reference == "current")
            for other in scopes
        )
    ):
        return scope, False
    span = raw[scope.start : scope.end]
    if _RETAIN_NEGATED.search(span) or _RULE_QUESTION_OR_UNDECIDED.search(span):
        return scope, False
    if scope.shared:
        independent_clauses = independent_clauses | frozenset(
            raw[other.start : other.end].strip()
            for other in scopes
            if other.shared and other != scope and field not in other.fields
        )
    return scope, not _retention_scope_conflict(
        form,
        field,
        raw,
        scope.start,
        scope.end,
        context,
        retaining_previous=retaining_previous,
        independent_clauses=independent_clauses,
    )


def _retention_scope_conflict(
    form: FormName,
    field: str,
    raw: str,
    start: int,
    end: int,
    context: AssistContext,
    *,
    retaining_previous: bool = True,
    independent_clauses: frozenset[str] = frozenset(),
) -> bool:
    """유지 절의 조건과 같은 필드의 별도 정정·취소를 검사한다."""
    scope = raw[start:end]
    if _RETENTION_CONDITION.search(scope):
        return True
    if retaining_previous and (
        _PENDING_CANCEL.search(scope)
        or _current_retention_reference(form, field, scope)
        or re.search(r"(?:현재|지금)\s*$", raw[:start])
    ):
        return True

    for before, outside in ((True, raw[:start]), (False, raw[end:])):
        preceding_names: set[str] = set()
        for clause in _PENDING_CLAUSE.split(outside):
            clause = clause.strip()
            if not clause:
                continue
            if clause in independent_clauses:
                continue
            names, first_target = _retention_clause_fields(form, clause)
            if form == "period" and re.search(r"기간\s*[은는이가을를만도]", clause):
                names.update(("start", "end"))
            if form == "period" and _EXCLUDED_DAY_REFERENCE.search(clause):
                names.add("excluded")
            if names:
                preceding_names = names
            elif (
                preceding_names
                and field not in preceding_names
                and not _CURRENT_RETENTION.search(clause)
                and (not _RETENTION_CONDITION.search(clause) or _PENDING_CANCEL.search(clause))
            ):
                # 직전 독립 필드를 가리키는 '그건 취소하고'는 다른 후보를 취소하지 않는다.
                continue
            if names and field not in names:
                # 다른 칸 앞의 목적어 없는 조건·취소는 어느 요청인지 확정하지 않는다.
                prefix = clause[:first_target]
                if _PENDING_CANCEL.search(prefix) or (
                    not before and _unbound_retention_conflict(form, field, prefix, context)
                ):
                    return True
                continue
            if _RETENTION_CONDITION.search(clause):
                return True
            # 현재값 유지의 앞 절에서 이전 제안을 취소하는 것은 일관된 지시다.
            if _PENDING_CANCEL.search(clause) and (retaining_previous or not before):
                return True
            if retaining_previous and _current_retention_reference(form, field, clause):
                return True
            if (
                field in names
                and (
                    _unbound_retention_conflict(form, field, clause, context)
                    or _RETAIN_NEGATED.search(clause)
                )
                and (retaining_previous or not before)
            ):
                return True
            if (
                not before
                and not names
                and (
                    _PENDING_CHANGE.search(clause)
                    or field in ("amount", "crew", "vat")
                    and _unbound_retention_conflict(form, field, clause, context)
                )
                and not _other_boundary_only(form, field, clause)
            ):
                # 날짜 제외·경계 변경의 명시적인 다른 목적어는 보존한다.
                return True
    return False


@dataclass(frozen=True)
class WithdrawalDecision:
    """Validated field-state transition, not a new value or a business undo."""

    field: str
    evidence: str
    fields: frozenset[str]
    definite: bool
    selected_modes: frozenset[str]
    verified_prior_origin: str | None = None
    reference: str = "pending"
    value_evidence: str | None = None


def _withdrawal_value_matches(
    form: FormName,
    field: str,
    value_text: str,
    previous: PreviousCandidate | None,
    context: AssistContext | None,
) -> bool:
    """A named historical value is the identity of a candidate, not a fresh assignment."""
    item = next((x for x in previous.fields if x.field == field), None) if previous else None
    if item is None or item.action != "set":
        return False
    raw = value_text.strip()
    if field in ("title", "reason"):
        if len(raw) >= 2 and (raw[0], raw[-1]) in (('"', '"'), ("'", "'"), ("“", "”"), ("‘", "’")):
            raw = raw[1:-1].strip()
        return isinstance(item.value, str) and normalized(item.value) == normalized(raw)
    if field in ("amount", "crew") or (form, field) == ("quote", "days"):
        tokens = _scalar_tokens(field, raw)
        return (
            len(tokens) == 1
            and tokens[0][2] == item.value
            and tokens[0][0] == 0
            and _scalar_span_evidence(field, raw, tokens[0][0], tokens[0][1]).strip() == raw
        )
    if context is None:
        return False
    if field in ("start", "end", "valid"):
        tokens = date_tokens(raw, context, allow_bare=False)
        return (
            len(tokens) == 1
            and tokens[0][0] == 0
            and tokens[0][1] == len(raw)
            and tokens[0][2].isoformat() == item.value
        )
    if field in ("days", "excluded") and isinstance(item.value, list):
        tokens = date_tokens(raw, context)
        return (
            bool(tokens)
            and date_list_prefix(raw)
            and len(tokens) == len(item.value)
            and {x[2].isoformat() for x in tokens} == set(item.value)
        )
    return False


def _withdrawal_decision(
    form: FormName,
    field: str,
    text: str,
    *,
    previous: PreviousCandidate | None = None,
    require_value_proof: bool = False,
    context: AssistContext | None = None,
) -> WithdrawalDecision | None:
    """취소의 대상과 확정 여부를 돌려준다. 불명확한 대상은 보류한다."""
    raw = normalized(text)
    parts = intent_clauses(form, raw)
    clauses = [raw[part.start : part.end].strip().rstrip(",;.!。").strip() for part in parts]
    preceding_names: set[str] = set()
    for index, clause in enumerate(clauses):
        semantic_clause = mask_bound_text_literals(clause) if form == "extra" else clause
        cancellation = _PENDING_CANCEL.search(semantic_clause)
        if cancellation is None:
            names = set(parts[index].fields)
            if names:
                preceding_names = names
            continue
        lexical = withdrawal_object(form, clause, cancel_at=cancellation.start())
        targets = [
            target for target in field_mentions(form, clause) if target.end <= cancellation.start()
        ]
        selected_modes = {target.selected_mode for target in targets if target.selected_mode}
        names = {name for target in targets for name in target.fields}
        legacy_targets = (
            _prior_field_matches(form, clause[: cancellation.start()]) if not targets else []
        )
        names.update(target.lastgroup for target in legacy_targets)
        scalar_target = _scalar_cancellation_field(form, clause, cancellation.start())
        if scalar_target is not None and not (names - {scalar_target}):
            names.add(scalar_target)
        else:
            scalar_target = None
        if form == "period" and re.search(r"기간\s*[은는이가을를만도]", clause):
            names.update(("start", "end"))
        if form == "period" and _EXCLUDED_DAY_REFERENCE.search(clause):
            names.add("excluded")
        if (
            not names
            and preceding_names
            and re.match(r"(?:그|이)(?:건|\s*(?:요청|제안|변경|수정|내용|것))", clause)
        ):
            names.update(preceding_names)
        if names:
            preceding_names = names
        if names and field not in names:
            continue
        body = clause
        if targets:
            # 실제 긴 필드명과 조사를 지운 뒤 남는 요청 전체를 검증한다.
            for target in reversed(targets):
                suffix = re.match(r"\s*[은는이가을를만도]?", body[target.end :])
                end = target.end + (suffix.end() if suffix else 0)
                body = body[: target.start] + body[end:]
            body = body.strip()
        elif legacy_targets:
            # 필드명을 제거한 나머지가 취소 요청 자체여야 현재값으로 확정한다.
            body = re.sub(
                rf"(?<![가-힣])(?:{_prior_field_pattern(form)})\s*[은는이가을를만도]?",
                "",
                body,
            ).strip()
        typed = (
            lexical is not None
            and lexical.definite
            and lexical.fields == frozenset(names)
            and field in lexical.fields
        )
        value_evidence = (
            clause[slice(*lexical.value_span)].strip()
            if typed and lexical.value_span is not None
            else None
        )
        selected_value = value_evidence is not None and lexical.value_kind == "candidate"
        definite = (
            typed
            and (value_evidence is None or selected_value)
            or complete_withdrawal_body(body, owned=bool(names))
            or scalar_target == field
        ) and not _RETENTION_CONDITION.search(clause)
        if selected_value and require_value_proof:
            definite = definite and _withdrawal_value_matches(
                form, field, value_evidence, previous, context
            )
        if selected_modes:
            # 선택값은 필드 귀속과 실제 철회 승인을 구분한다. 이전값이
            # 없거나 검증에서 탈락했다면 보류만 남기고 현재값 유지로
            # 확정하지 않는다. 다른 칸에는 해당 세금 철회를 전파하지 않는다.
            definite = definite and names == {"vat"} and len(selected_modes) == 1
            if require_value_proof:
                definite = (
                    definite
                    and previous is not None
                    and any(
                        item.field == field
                        and item.action == "set"
                        and item.value in selected_modes
                        for item in previous.fields
                    )
                )
        if any(
            other_index < index
            and parts[other_index].boundary in ("comma", "connective")
            and (
                CONDITIONAL_LINK.search(other)
                or not parts[other_index].fields
                and _RETENTION_CONDITION.search(other)
            )
            for other_index, other in enumerate(clauses)
        ):
            definite = False
        if clause.endswith(("하고", "하고요")) and (
            parts[index].boundary != "connective"
            or index + 1 >= len(parts)
            or not parts[index + 1].fields
            or field in parts[index + 1].fields
        ):
            definite = False
        if not names and len(clauses) != 1:
            definite = False
        if any(
            other != clause and field in parts[other_index].fields
            for other_index, other in enumerate(clauses)
        ):
            definite = False
        if scalar_target == field and any(
            other != clause
            and not _prior_field_matches(form, other)
            and (
                _scalar_tokens(field, other)
                or _PENDING_CHANGE.search(other)
                or _RETENTION_CONDITION.search(other)
            )
            for other in clauses
        ):
            definite = False
        # A subsequent unowned cancellation or a reversal of the same
        # withdrawal is not a new independent field assignment. Do not let
        # the first affirmative clause win before reading the complete request.
        if any(
            other_index > index
            and not parts[other_index].fields
            and (
                _PENDING_CANCEL.search(other)
                or _CURRENT_RETENTION.search(other)
                or _RETENTION_CONDITION.search(other)
                and parts[other_index].boundary != "question"
            )
            for other_index, other in enumerate(clauses)
        ):
            definite = False
        origin = None
        if (selected_modes or selected_value) and definite and previous is not None:
            origin = previous.origins.get(field, previous.request_id)
        return WithdrawalDecision(
            field,
            clause,
            frozenset(names),
            definite,
            frozenset(selected_modes),
            origin,
            lexical.reference if lexical is not None else "pending",
            value_evidence,
        )
    return None


def _pending_cancellation(
    form: FormName,
    field: str,
    text: str,
    *,
    previous: PreviousCandidate | None = None,
    require_value_proof: bool = False,
) -> tuple[str, bool] | None:
    """Compatibility view for historical and local value validation."""
    proof = _withdrawal_decision(
        form, field, text, previous=previous, require_value_proof=require_value_proof
    )
    return (proof.evidence, proof.definite) if proof is not None else None


def resolve_withdrawals(
    data: AssistRequest, previous: PreviousCandidate | None, context: AssistContext
) -> Mapping[str, WithdrawalDecision]:
    """Freeze current-turn withdrawal decisions after previous-value validation."""
    decisions = {}
    for field in FORM_FIELDS[data.form]:
        proof = _withdrawal_decision(
            data.form,
            field,
            data.text,
            previous=previous,
            require_value_proof=True,
            context=context,
        )
        if proof is not None:
            decisions[field] = proof
    return MappingProxyType(decisions)


def _withdrawal_candidate(
    data: AssistRequest, proof: WithdrawalDecision, source: Source
) -> CandidateField:
    assert proof.definite
    return CandidateField(
        field=proof.field,
        action="keep",
        value=None,
        evidence=proof.evidence,
        source=source,
        status="valid",
        alternatives=[],
        intent_kind="retain_current",
        value_source="current",
        origin_request_id=data.request_id,
    )


def _unscoped_retention_control(
    form: FormName,
    field: str,
    text: str,
    *,
    independent_clauses: frozenset[str] = frozenset(),
) -> bool:
    """대상 없는 현재값 유지·조건부 지시는 이전값의 자동 계승을 막는다."""
    raw = normalized_scope(text)
    for part in intent_clauses(form, raw):
        clause = raw[part.start : part.end]
        if clause.strip().rstrip(",;.!。").strip() in independent_clauses:
            continue
        if part.fields:
            continue
        if _RETAIN.search(clause) and (
            _current_retention_reference(form, field, clause)
            or _RETENTION_CONDITION.search(clause)
            or _RETAIN_NEGATED.search(clause)
        ):
            return True
    return False


def _mask_independent_cancellation(
    form: FormName, field: str, raw: str, context: AssistContext
) -> str:
    """확정된 다른 칸의 취소만 숫자 이력의 위험 검사에서 분리한다."""
    parts = _resolved_intent_clauses(form, raw, context)
    if any(part.unbound for part in parts):
        return raw
    masked = raw
    for part in parts:
        clause = part.clause
        if len(part.fields) != 1 or field in part.fields:
            continue
        target = next(iter(part.fields))
        cancellation = _pending_cancellation(form, target, raw)
        if (
            cancellation
            and cancellation[1]
            and cancellation[0] == (raw[clause.start : clause.end].strip().rstrip(",;.!。").strip())
        ):
            masked = (
                masked[: clause.start] + " " * (clause.end - clause.start) + masked[clause.end :]
            )
    return masked


def _canceled_in_history(
    form: FormName, item: FieldIntent, history: str, context: AssistContext
) -> bool:
    """앞선 후보를 취소하거나 현재 입력을 유지한 뒤에는 그 후보를 되살리지 않는다."""
    turns = history.splitlines()
    # 후보 근거가 남은 마지막 발화부터 검사해 새로 확정한 후보와 구분한다.
    matched = [index for index, turn in enumerate(turns) if evidence_matches(turn, item.evidence)]
    if not matched:
        return False
    last = matched[-1]
    proof_turn = normalized(turns[last])
    proof_end = proof_turn.rfind(normalized(item.evidence)) + len(normalized(item.evidence))
    if (
        _PENDING_CANCEL.search(proof_turn[proof_end:])
        and _pending_cancellation(form, item.field, proof_turn) is not None
    ):
        return True
    for turn in turns[last + 1 :]:
        if _pending_cancellation(form, item.field, turn) is not None:
            return True
        if _targeted_retention(
            form,
            item.field,
            turn,
            turn,
            context,
            retaining_previous=False,
            require_current_reference=True,
        ):
            return True
    return False


def _unbound_retention_conflict(
    form: FormName, field: str, text: str, context: AssistContext
) -> bool:
    # 목적어 없는 재정정·취소·조건부 요청을 다른 필드의 말로 추정하지 않는다.
    if (
        _PRIOR_CANCEL_OR_CONDITION.search(text)
        or _RULE_QUESTION_OR_UNDECIDED.search(text)
        or _deletion_requested(text)
        or re.search(r"(?:바꿔|바꾸|고쳐|고치|수정|변경|정정|덮어)", text)
    ):
        return True
    if field == "amount":
        return bool(_amount_mentions(text))
    if field == "crew":
        return bool(crew_mentions(text))
    if field == "vat":
        return bool(_vat_modes(text))
    if (form, field) == ("quote", "days"):
        return bool(_quote_duration_tokens(text))
    if field in ("start", "end", "valid", "excluded", "days"):
        return bool(date_tokens(text, context))
    return False


def _first_retention_origin_verified(previous: PreviousCandidate, retained: FieldIntent) -> bool:
    """최초 후보 참조는 검증된 후보의 원래 근거와 출처로 증명한다."""
    history = previous.text.splitlines()
    return bool(
        history
        and evidence_matches(history[0], retained.evidence)
        and not any(evidence_matches(turn, retained.evidence) for turn in history[1:])
        and (
            len(history) == 1
            or previous.origins.get(retained.field, previous.request_id) != previous.request_id
        )
    )


def _reference_only_retention(
    form: FormName,
    field: str,
    evidence: str,
    text: str,
    context: AssistContext,
    previous: PreviousCandidate,
    retained: FieldIntent,
    *,
    independent_clauses: frozenset[str] = frozenset(),
) -> bool | None:
    """숫자 없는 명시적 이전 참조는 해당 필드의 독립된 긍정 지시로 확인한다."""
    scoped = _scoped_retention(
        form,
        field,
        evidence,
        text,
        context,
        retaining_previous=True,
        independent_clauses=independent_clauses,
    )
    if scoped is not None:
        scope, verified = scoped
        if not verified:
            return False
        # 면세 조건처럼 값도 포함된 대상은 단순한 필드 참조가 아니다.
        # 같은 칸의 검증된 이전 세금 방식과 일치해야 계승할 수 있다.
        if (
            field == "vat" or (form, field) == ("quote", "days")
        ) and _retention_has_different_value(
            form, field, retained.value, normalized(text)[scope.start : scope.end], context
        ):
            return False
        return not scope.first or _first_retention_origin_verified(previous, retained)
    raw, needle = normalized(text), normalized(evidence)
    if not needle or raw.count(needle) != 1:
        return None
    at = raw.index(needle)
    target = _prior_field_word(form, field)
    referent = r"기간|일수|공기" if (form, field) == ("quote", "days") else _PRIOR_REFERENT[field]
    pattern = re.compile(
        rf"(?<![가-힣])(?:{target})\s*[은는이가을를만도]\s*{_PRIOR_REFERENCE}\s*"
        rf"(?:(?:{referent}|걸|것|거|값|내용|안|제안)\s*)?"
        r"(?:그대로|대로|으로|로)"
    )
    matches = [
        match
        for match in pattern.finditer(raw)
        if at <= match.start("when") < at + len(needle) and match.end() <= at + len(needle)
    ]
    if not matches:
        return None
    if len(matches) != 1:
        return False
    match = matches[0]
    directive = _PRIOR_DIRECTIVE.match(raw, match.end())
    end = directive.end() if directive else match.end()
    tail = raw[end:].lstrip()
    if tail and tail[0] not in ",;.!":
        # 접속형 뒤에는 독립적인 다른 필드 지시만 이어질 수 있다.
        following = re.match(
            rf"(?:그리고\s+)?(?:{_prior_field_pattern(form)})\s*[은는이가을를만도]",
            tail,
        )
        if directive is None or not directive.group().endswith(("고", "고요")) or following is None:
            return False
        if following.lastgroup == field:
            return False
    if first_history_reference(match["when"]):
        # 첫 요청 참조는 보존된 근거가 첫 턴에만 있을 때 인정한다.
        history = previous.text.splitlines()
        if not history or not evidence_matches(history[0], retained.evidence):
            return False
        if any(evidence_matches(turn, retained.evidence) for turn in history[1:]):
            return False
        if (
            len(history) > 1
            and previous.origins.get(field, previous.request_id) == previous.request_id
        ):
            return False
    outside = raw[: match.start()] + " ; " + raw[end:]
    if _retention_scope_conflict(
        form,
        field,
        raw,
        match.start(),
        end,
        context,
        independent_clauses=independent_clauses,
    ):
        return False
    for clause in re.split(r"(?<!\d),|,(?!\d)|(?<!\d)\.|\.(?!\d)|[;!?？]", outside):
        if clause.strip() in independent_clauses:
            continue
        targets = _prior_field_matches(form, clause)
        if any(target.lastgroup == field for target in targets):
            return False
        if re.search(
            r"(?:그\s*(?:요청|제안|거|건|것)|방금\s*(?:요청|말)|유지\s*요청)"
            r"\s*[은는을를]?\s*(?:취소|철회|보류)",
            clause,
        ):
            return False
        unbound = clause[: targets[0].start()] if targets else clause
        if _unbound_retention_conflict(form, field, unbound, context):
            return False
    # 동사를 생략한 기존 표현은 기존 검증에 맡긴다.
    return True if directive else None


def _rule_local_clauses(
    form: FormName, field: str, value: Value, text: str, context: AssistContext
) -> list[str]:
    """다른 필드에서 나온 '미정'·부정은 독립적으로 확정한 후보에 전파하지 않는다."""
    clauses = re.split(r"[,;\n]", text)
    return [
        clause
        for clause in clauses
        if clause.strip() and _grounded(form, field, value, clause, context)
    ] or [text]


def _rule_assignment_suffix(suffix: str) -> bool:
    """문자열 값 뒤의 '로 입력해 주세요' 같은 긍정 입력 지시만 허용한다."""
    suffix = suffix.strip().rstrip(".")
    if not suffix:
        return True
    if suffix.startswith("으로"):
        rest = suffix[2:].strip()
    elif suffix.startswith("로"):
        rest = suffix[1:].strip()
    else:
        return False
    return len(rest) <= 36 and rest.startswith(_RULE_ASSIGN_VERBS)


def _assignment_directive_in_value(field: str, value: str, text: str) -> bool:
    """명시적 문자열 대입 절의 입력 지시문 자체를 선택 가능한 값으로 승인하지 않는다."""
    return assignment_directive_in_value(field, normalized(value), normalized(text))


def _correction_gap_connector(link: str) -> bool:
    """선택적인 조사·쉼표를 걷어내고 연결어 자체만 비교한다."""
    part = link.strip()
    if part.endswith(","):
        part = part[:-1].strip()
    for particle in ("은", "는", "이", "가", "을", "를"):
        if part.startswith(particle):
            part = part[len(particle) :].strip()
            break
    return part in ("말고", "아니고", "아니라", "대신", "→", "->")


def _other_boundary_only(form: FormName, field: str, text: str) -> bool:
    """'시작일만'이라는 지시로 종료일의 기존 후보를 새 값처럼 보지 않는다."""
    if form != "period":
        return False
    # 다른 경계만 언급된 단일 날짜는 이 필드의 수정 지시가 아니다.
    # 범위('부터~까지')가 있으면 시작·종료를 모두 지정한 것일 수 있다.
    if not _RULE_DATE_RANGE.search(text):
        has_start = bool(re.search(r"시작|첫날", text))
        has_end = bool(re.search(r"종료|끝나는\s*날|끝날|마지막\s*날", text))
        if field == "start" and has_end and not has_start:
            return True
        if field == "end" and has_start and not has_end:
            return True
    if field == "start":
        return bool(_RULE_END_ONLY.search(text)) and not _RULE_START_ONLY.search(text)
    if field == "end":
        return bool(_RULE_START_ONLY.search(text)) and not _RULE_END_ONLY.search(text)
    return False


def _rule_field_proof(
    form: FormName, rule: CandidateField, text: str, context: AssistContext
) -> bool:
    """모델이 미언급한 필드를 규칙만으로 확정할 때 필요한 독립적인 대상·동작 근거."""
    if (
        rule.status != "valid"
        or rule.action not in ("set", "clear")
        or not evidence_matches(text, rule.evidence)
    ):
        return False
    field = rule.field
    clauses = (
        _rule_local_clauses(form, field, rule.value, text, context)
        if rule.action == "set"
        else [text]
    )
    explicit_text = (
        rule.action == "set"
        and field in ("title", "reason")
        and isinstance(rule.value, str)
        and explicit_text_assignment(field, rule.value, text)
    )
    risk_clauses = (
        [mask_explicit_text_value(field, rule.value, clause) for clause in clauses]
        if explicit_text
        else clauses
    )
    if any(
        _RULE_QUESTION_OR_UNDECIDED.search(polite_execution_risk_text(clause))
        or _RULE_NEGATED_ACTION.search(clause)
        or _RULE_HISTORICAL.search(clause)
        for clause in risk_clauses
    ):
        return False
    if rule.action == "clear":
        return _target_word(form, field, text) and _targeted_clear(form, field, rule.evidence, text)
    value = rule.value
    if field in ("title", "reason"):
        if explicit_text:
            return True
        assignment = _RULE_TITLE_ASSIGN if field == "title" else _RULE_REASON_ASSIGN
        for part in re.split(r"[,;\n]", normalized(text)):
            match = assignment.match(part)
            if match:
                remainder = part[match.end() :].strip()
                selected = normalized(value).strip()
                if remainder.startswith(selected):
                    suffix = remainder[len(selected) :].strip()
                    if _rule_assignment_suffix(suffix):
                        return True
        return False
    if field == "amount":
        amounts = _amount_mentions(text)
        return (
            type(value) is int
            and amounts == {value}
            and (form == "quote" or any(word in text for word in ("추가", "금액", "비용")))
        )
    if field == "vat":
        words = normalized_scope(text)
        modes = set().union(
            *(
                _vat_modes(words[part.clause.start : part.clause.end])
                for part in _resolved_intent_clauses(form, words, context)
                if part.fields == {"vat"}
            )
        )
        return (
            type(value) is str
            and modes == {value}
            and bool(
                scoped_vat_modes(words)
                or any(mention.fields == {"vat"} for mention in field_mentions(form, words))
            )
        )
    if form == "quote" and field == "days":
        return (
            type(value) is int
            and {n for _, _, n in _quote_duration_tokens(normalized(text))} == {value}
            and any("days" in m.fields for m in field_mentions(form, normalized(text)))
        )
    if field == "crew":
        return type(value) is int and crew_mentions(text) == {value}
    if form == "period" and field in ("start", "end"):
        if type(value) is not str or _other_boundary_only(form, field, text):
            return False
        target = (
            ("시작일", "시작 날짜", "첫날") if field == "start" else ("종료일", "끝날", "마지막 날")
        )
        if any(word in text for word in target):
            return True
        tokens = date_tokens(text, context)
        if len(tokens) < 2 or not _RULE_DATE_RANGE.search(text):
            return False
        endpoint = tokens[0][2] if field == "start" else tokens[-1][2]
        return date.fromisoformat(value) == endpoint
    if form == "period" and field == "excluded":
        if not isinstance(value, list) or not value:
            return False
        for raw in value:
            day = date.fromisoformat(raw)
            patterns = (
                rf"(?<!\d){day.day}\s*일(?!\d)",
                rf"(?<!\d){day.month}\s*(?:월|/|-)\s*0?{day.day}(?!\d)",
            )
            found = False
            for pattern in patterns:
                for match in re.finditer(pattern, text):
                    near = text[match.start() : match.end() + 32]
                    if _EXCLUDE_DAY.search(near) and not _RULE_NEGATED_EXCLUSION.search(near):
                        found = True
                        break
                if found:
                    break
            if not found:
                return False
        return True
    if form == "dates" and field == "days":
        return (
            isinstance(value, list)
            and bool(value)
            and any(word in text for word in ("공사", "작업", "날짜", "일정"))
            and set(map(date.fromisoformat, value)) <= date_mentions(text, context, expand=True)
        )
    return False


def _crew_span_covered(raw: str, evidence: str, span: tuple[int, int]) -> bool:
    needle = normalized(evidence)
    if not needle:
        return False
    return any(
        match.start() <= span[0] and match.end() >= span[1]
        for match in re.finditer(re.escape(needle), raw)
    )


def _verified_crew_replacement(
    item: FieldIntent, text: str, context: AssistContext, previous: PreviousCandidate | None
) -> bool:
    raw = normalized(text)
    replacement = crew_replacement(raw)
    relation = item.correction
    if replacement is None or relation is None or relation.kind != "replace" or previous is None:
        return False
    return (
        type(item.value) is int
        and type(relation.rejected_value) is int
        and (relation.rejected_value, item.value) == (replacement.old, replacement.new)
        and 1 <= replacement.old <= context.crew_max
        and 1 <= replacement.new <= context.crew_max
        and any(
            old.field == "crew" and old.action == "set" and old.value == replacement.old
            for old in previous.fields
        )
        and _crew_span_covered(raw, relation.rejected_evidence, replacement.old_span)
        and _crew_span_covered(raw, relation.accepted_evidence, replacement.new_span)
        and _crew_span_covered(raw, item.evidence, replacement.new_span)
    )


def _verified_historical_scalar(
    item: FieldIntent,
    form: FormName,
    text: str,
    context: AssistContext,
    previous: PreviousCandidate | None,
    old_text: str,
    new_text: str,
    before: int,
    after: int,
) -> bool:
    """이미 검증된 동일 필드의 옛 값을 명시적으로 정정한 문장만 인정한다.

    '아까 A라고 했는데 B로 고칠게요'처럼 옛 발화와 새 확정 지시가 모두 필요하다.
    단순한 여러 숫자 나열·마지막 숫자·불확실 문구는 정정 관계가 아니다.
    """
    relation = item.correction
    if (
        previous is None
        or relation is None
        or (form == "period" and item.field == "excluded")
        or (form == "dates" and item.field == "days")
    ):
        return False
    old = next(
        (part for part in previous.fields if part.field == item.field and part.action == "set"),
        None,
    )
    if (
        old is None
        or type(old.value) is not type(relation.rejected_value)
        or old.value != relation.rejected_value
    ):
        return False
    if item.field == "amount":
        return _verified_historical_amount(
            item, form, text, context, old_text, new_text, before, after
        )
    raw = normalized(text)
    start = (
        max(
            raw.rfind(".", 0, before),
            raw.rfind(";", 0, before),
            raw.rfind("\n", 0, before),
        )
        + 1
    )
    link = raw[before + len(old_text) : after]
    tail = raw[after : after + len(new_text) + 36]
    # 보고 동사가 모델의 rejected_evidence 안에 들어가는 경우도 있다.
    # '아까' 같은 부사가 없어도 'A라고 했는데 B로 고칠게요' 자체가 과거 참조다.
    reported = bool(_HISTORICAL_REPORT_LINK.search(old_text + link))
    if not (
        reported
        and len(link) <= 48
        and _HISTORICAL_NEW_ACTION.search(tail)
        and not _HISTORICAL_DENIED_NEW.search(tail)
        and not _UNCERTAIN_CHANGE.search(raw[start : after + len(new_text) + 36])
    ):
        return False
    try:
        check_value(form, item.field, relation.rejected_value)
        return _grounded(
            form,
            item.field,
            relation.rejected_value,
            relation.rejected_evidence,
            context,
            text=text,
        ) and _grounded(
            form, item.field, item.value, relation.accepted_evidence, context, text=text
        )
    except (TypeError, ValueError, KeyError):
        return False


def _verified_date_replacement(
    item: FieldIntent,
    data: AssistRequest,
    context: AssistContext,
    previous: PreviousCandidate | None,
) -> tuple[list[str], CorrectionRelation] | None:
    """검증된 이전 전체 날짜 목록에서 정확히 한 원소만 새날로 바꾼다.

    공급자의 부분 목록은 적용 데이터가 아니다. 검증된 prior와 이번 원문의
    관계로 서버가 전체 목록을 만들며, 관계에는 재검증용 원래 전체 목록을 보존한다.
    """
    if (
        item.action != "set"
        or item.correction is None
        or item.correction.kind != "replace"
        or (data.form, item.field) not in (("dates", "days"), ("period", "excluded"))
    ):
        return None
    relation = item.correction
    if not (
        evidence_matches(data.text, item.evidence)
        and evidence_matches(data.text, relation.rejected_evidence)
        and evidence_matches(data.text, relation.accepted_evidence)
    ):
        return None
    matches = [
        (base, complete)
        for base in _date_edit_bases(data, item.field, previous)
        if (
            complete := validated_date_edit(
                item.field, data.text, base, context, require_affirmative=True
            )
        )
        is not None
    ]
    if len(matches) != 1:
        return None
    base, complete = matches[0]
    removed = set(base) - set(complete)
    added = set(complete) - set(base)
    if len(removed) != 1 or len(added) != 1:
        return None
    rejected, accepted = next(iter(removed)), next(iter(added))
    # 기존 wire에서는 rejected_value가 교체한 하루 또는 그 하루짜리 배열일 수 있다.
    # 검증 성공 후 응답에는 이전 *전체* 목록을 넣어 3턴 이후에도 재검증한다.
    if relation.rejected_value not in (rejected, [rejected], base):
        return None
    if item.value not in (accepted, [accepted], complete):
        return None
    anchor = context.model_copy(update={"base_month": date.fromisoformat(rejected)})
    if date.fromisoformat(rejected) not in date_mentions(
        relation.rejected_evidence, anchor, expand=False
    ) or date.fromisoformat(accepted) not in date_mentions(
        relation.accepted_evidence, anchor, expand=False
    ):
        return None
    return complete, CorrectionRelation(
        kind="replace",
        rejected_value=list(base),
        rejected_evidence=relation.rejected_evidence,
        accepted_evidence=relation.accepted_evidence,
    )


_DATE_EDIT_PRIOR_REFERENCE = re.compile(
    r"(?:앞에서\s*말한|앞서|아까|이전|예전|지난번|방금|조금\s*전|"
    r"먼저\s*말한|전에\s*(?:말한|정한|제안한)|기존\s*제안|"
    r"(?:지금|방금|직전)\s*제안(?:한|했던)\s*(?:날짜?|일|일정|목록)|"
    r"그\s*(?:날짜들?|제안|목록)(?:\s*중)?)"
)
_DATE_EDIT_CURRENT_REFERENCE = re.compile(
    r"(?:현재|지금)\s*(?:폼|화면|입력|등록|설정|선택|저장|달력|캘린더|"
    r"공사\s*날짜|작업\s*날짜|제외\s*날짜)|"
    r"(?:폼|화면|달력|캘린더)에\s*(?:있는|입력된|적힌)|"
    r"직접\s*(?:입력|등록|설정)한"
)
_DATE_EDIT_TOKEN = re.compile(
    r"(?<!\d)(?:20\d{2}\s*(?:년|[-/.])\s*\d{1,2}\s*"
    r"(?:월|[-/.])\s*\d{1,2}\s*일?|"
    r"\d{1,2}\s*(?:월|[-/.])\s*\d{1,2}\s*일?|"
    r"(?:[1-9]|[12]\d|3[01])\s*일)(?!\d)"
)


def _date_edit_reference(field: str, text: str) -> str | None:
    """교체 절의 출처 지시만 읽고 뒤따르는 다른 필드의 참조는 배제한다."""
    clause = date_edit_reference_prefix(field, text)
    if clause is None:
        return None
    prior = bool(_DATE_EDIT_PRIOR_REFERENCE.search(clause))
    current = bool(_DATE_EDIT_CURRENT_REFERENCE.search(clause))
    if prior and current:
        return "ambiguous"
    if current:
        return "current"
    if prior or re.search(r"그\s*중(?:에)?", clause):
        return "previous"
    return None


def _date_edit_bases(
    data: AssistRequest,
    field: str,
    previous: PreviousCandidate | None,
    previous_rejected: dict[str, ReasonCode] | None = None,
) -> list[list[str]]:
    """검증된 이전 목록과 실제 폼 목록에서 출처가 확정된 후보만 고른다."""
    earlier = (
        next(
            (item for item in previous.fields if item.field == field and item.action == "set"),
            None,
        )
        if previous
        else None
    )
    # 실제 폼의 값과 검증된 이전 후보의 값은 별도로 유지한다.
    current = data.current.get(field)
    reference = _date_edit_reference(field, data.text)
    if reference == "ambiguous":
        return []
    if reference == "previous":
        return [earlier.value] if earlier is not None and isinstance(earlier.value, list) else []
    if reference == "current":
        return [current] if isinstance(current, list) else []
    if field in (previous_rejected or {}) or (
        data.previous_candidate is not None
        and any(item.field == field for item in data.previous_candidate.fields)
        and (previous is None or not any(item.field == field for item in previous.fields))
    ):
        # 출처 지시가 없을 때 거부된 이전 후보를 현재 폼으로 재해석하지 않는다.
        return []
    bases = [current] if isinstance(current, list) else []
    if earlier is not None and isinstance(earlier.value, list) and earlier.value != current:
        bases.append(earlier.value)
    return bases


def reviewed_date_edit(
    data: AssistRequest,
    context: AssistContext,
    previous: PreviousCandidate | None,
    previous_rejected: dict[str, ReasonCode],
) -> dict[str, CandidateField]:
    """확정된 단일 날짜 교체를 모델 판단과 독립적으로 검증해 병합 직전에 제공한다.

    이전 제안을 지칭하면 검증된 이전 목록만 사용한다. 현재 폼을 지칭하면
    실제 현재 목록을 사용한다. 둘 다 명시하지 않은 상황에서 두 목록이 다르면
    대상이 불분명하므로 제안을 만들지 않는다.
    """
    field = {"dates": "days", "period": "excluded"}.get(data.form)
    if field is None:
        return {}

    tokens = _DATE_EDIT_TOKEN.findall(data.text)
    matches = []
    for base in _date_edit_bases(data, field, previous, previous_rejected):
        complete = validated_date_edit(field, data.text, base, context, require_affirmative=True)
        if complete is not None:
            matches.append((base, complete))
    if len(matches) != 1:
        return {}
    base, complete = matches[0]
    removed, added = set(base) - set(complete), set(complete) - set(base)
    if len(removed) != 1 or len(added) != 1:
        return {}

    # 두 날짜 표현은 이미 validated_date_edit가 유일성과 순서를 확인했다.
    old_day = date.fromisoformat(next(iter(removed))).day
    new_day = date.fromisoformat(next(iter(added))).day
    old_evidence = [token for token in tokens if int(re.findall(r"\d+", token)[-1]) == old_day]
    new_evidence = [token for token in tokens if int(re.findall(r"\d+", token)[-1]) == new_day]
    if not old_evidence or not new_evidence:
        return {}
    # 여러 번 나온 표기를 임의로 고르지 않는다. 서버가 검증한 전체 문장으로
    # 출처를 묶고, 다음 턴에는 날짜 교체 함수를 다시 재연해 확인한다.
    rejected_text = old_evidence[0] if len(old_evidence) == 1 else data.text
    accepted_text = new_evidence[0] if len(new_evidence) == 1 else data.text
    try:
        check_value(data.form, field, complete)
        projection = (
            _business_projection(data, context, previous, {}) if data.form == "period" else {}
        )
        projection[field] = complete
        if not _business_ok(data.form, field, complete, data, context, projection):
            return {}
    except (ValueError, TypeError, KeyError):
        return {}
    return {
        field: CandidateField(
            field=field,
            action="set",
            value=complete,
            evidence=data.text,
            source="rule",
            status="valid",
            alternatives=[],
            origin_request_id=data.request_id,
            intent_kind="explicit_change",
            value_source="derived",
            correction=CorrectionRelation(
                kind="replace",
                rejected_value=list(base),
                rejected_evidence=rejected_text,
                accepted_evidence=accepted_text,
            ),
        )
    }


def _verified_independent_date_clauses(
    data: AssistRequest,
    context: AssistContext,
    previous: PreviousCandidate | None,
    field: str,
    text: str,
) -> frozenset[str]:
    """실제로 검증한 작업일 교체의 독립 보호 절만 인원 유지 검사에서 분리한다."""
    if (data.form, field) != ("dates", "crew") or text != data.text:
        return frozenset()
    clauses = frozenset(
        clause.strip()
        for clause in _PENDING_CLAUSE.split(normalized(text))
        if date_protection_clause(clause)
    )
    if not clauses or "days" not in reviewed_date_edit(data, context, previous, {}):
        return frozenset()
    return clauses


_EXTRA_AMOUNT_OLD_ERROR = re.compile(
    r"(?:원)?(?:은|는|을|를|이|가)?"
    r"(?:잘못(?:적었|썼|입력했|기재했)|틀리게(?:적었|썼|입력했|기재했))"
    r"(?:고|는데|으며|어서|지만)?"
)
_EXTRA_AMOUNT_NEW_ACTION = re.compile(
    r"(?:원)?(?:으로|로)"
    r"(?:(?:정정|수정|변경)"
    r"(?:해주세요|해줘요|해주십시오|해요|할게요|하겠습니다|합니다)"
    r"|(?:고쳐|바꿔)(?:주세요|줘요|주십시오|요)"
    r"|고칠게요|바꿀게요|고치겠습니다|바꾸겠습니다)"
)
_EXTRA_AMOUNT_UNDECIDED = re.compile(
    r"[?？]|아마|혹시|아직|미정|보류|가능하면|검토|예정|잠정|"
    r"고민|또는|혹은|아니면|수도|지도|것\s*같|(?:할|일|될)까|정도|쯤"
)
_EXTRA_AMOUNT_FOLLOWUP_CANCEL = re.compile(
    rf"취소|철회|보류|미정|{WITHDRAW_IDIOM}|원래대로|그대로두|하지마|안할|다시고민"
)


_HIST_AMOUNT_LABEL = {
    "quote": r"(?:견적(?:가|금액)?|총액|합계|금액|공사비|비용)",
    "extra": r"(?:추가(?:공사)?(?:금액|금|비용|비)|금액|합계|비용)",
}
_HIST_AMOUNT_REPORT_VERB = r"(?:했|말했|말씀드렸|적었|썼|입력했|전달했|기재했)"
_HIST_AMOUNT_REPORT_START = re.compile(
    r"원?(?:이라고|라고|이라|라|으로|로)(?:제가)?"
    rf"(?:{_HIST_AMOUNT_REPORT_VERB}|한|했던|말한|말했던|말씀드린|적은|썼던|입력한|던)"
)
_HIST_AMOUNT_ERROR_SENTENCE = (
    r"(?:제가)?(?:(?:계산|합계|금액|금액계산|견적|숫자|단가)(?:을|를)?)?"
    r"(?:잘못|틀리게)(?:했|봤|보았|계산했|말했|말씀드렸|적었|썼|입력했|전달했|기재했)"
    r"(?:네요|어요|습니다|죠)[.!。]"
)
_HIST_AMOUNT_NEW_ACTION = re.compile(
    r"원?(?:으로|로)"
    r"(?:(?:정정|수정|변경|확정)(?:부탁해요|부탁드립니다|해주세요|해줘요|해주십시오"
    r"|해요|할게요|하겠습니다|합니다)"
    r"|(?:고쳐|바꿔)(?:주세요|줘요|주십시오|요)"
    r"|(?:고칠|바꿀|올릴|내릴)게요|(?:고치|바꾸|올리|내리)겠습니다"
    r"|(?:고칩|바꿉)니다)"
)
_HIST_AMOUNT_OTHER_FIELD = re.compile(
    r"세금|부가세|사유|이유|제목|항목|인원|기간|일수|일정|유효기간"
)
_HIST_AMOUNT_RISK = re.compile(
    r"가능하면|(?:다면|라면|으면|이면|하면)|경우|일때|취소|철회|보류|미정|"
    rf"{WITHDRAW_IDIOM}|원래대로|그대로두|하지마|하지않|안할|아니(?:요|라고|고|라)?|[?？]"
)


def _historical_amount_report(text: str) -> bool:
    """보고된 옛 금액은 이전 후보의 동일 필드로 검증해야 한다."""
    raw = normalized(text)
    return any(
        _HIST_AMOUNT_REPORT_START.match(re.sub(r"\s+", "", raw[end:]))
        for _, end, _ in _amount_tokens(raw)
    )


def _verified_historical_amount(
    item: FieldIntent,
    form: FormName,
    text: str,
    context: AssistContext,
    old_text: str,
    new_text: str,
    before: int,
    after: int,
) -> bool:
    """보고형 옛 금액과 새 확정 지시의 전체 절을 확인한다."""
    relation = item.correction
    label = _HIST_AMOUNT_LABEL.get(form)
    if relation is None or label is None or type(item.value) is not int:
        return False
    raw = normalized(text)
    groups = _amount_tokens(raw)
    if len(groups) != 2:
        return False
    (old_start, old_end, old_amount), (new_start, new_end, new_amount) = groups
    if (
        (old_amount, new_amount) != (relation.rejected_value, item.value)
        or not before <= old_start < old_end <= before + len(old_text)
        or not after <= new_start < new_end <= after + len(new_text)
    ):
        return False
    prefix_clauses = re.split(r"[,;.!?？。]", raw[:old_start])
    prefix = re.sub(r"\s+", "", prefix_clauses[-1])
    if not re.fullmatch(
        rf"(?:(?:아까|앞서|지난번(?:에)?|이전에|예전에|방금|조금전)(?:에)?)?"
        rf"(?:제가)?(?:{label}(?:은|는|이|가|을|를|:)?)?",
        prefix,
    ):
        return False
    link = re.sub(r"\s+", "", raw[old_end:new_start])
    # 보고 동사 뒤에 오류 인정 문장이 오거나, 'A라고 한 금액만'처럼
    # 동일 금액을 명사구로 지칭할 수 있다. 다른 수식어·조건은 추측하지 않는다.
    reported = (
        rf"원?(?:이라고|라고|이라|라|으로|로)?(?:제가)?"
        rf"{_HIST_AMOUNT_REPORT_VERB}(?:는데|다가|지만|고)"
        rf"(?:{_HIST_AMOUNT_ERROR_SENTENCE})?"
        rf"|원?(?:이라고|라고|이라|라)(?:한|했던|말한|말했던|말씀드린|적은|입력한|던)"
        rf"{label}(?:만|은|는|을|를|이|가)?"
    )
    if not re.fullmatch(reported, link):
        return False
    tail_clauses = re.split(r"([,;.!?？。])", raw[new_end:], maxsplit=1)
    if len(tail_clauses) > 1 and tail_clauses[1] in "?？":
        return False
    if not _HIST_AMOUNT_NEW_ACTION.fullmatch(re.sub(r"\s+", "", tail_clauses[0])):
        return False
    other_clauses = prefix_clauses[:-1] + (
        re.split(r"[,;.!?？。]", tail_clauses[2]) if len(tail_clauses) > 2 else []
    )
    for clause in other_clauses:
        compact = re.sub(r"\s+", "", clause)
        if not (
            _HIST_AMOUNT_RISK.search(compact)
            or _EXTRA_AMOUNT_UNDECIDED.search(compact)
            or _HISTORICAL_DENIED_NEW.search(compact)
            or _EXTRA_AMOUNT_FOLLOWUP_CANCEL.search(compact)
        ):
            continue
        if re.search(r"금액|추가금|견적|공사비|총액|합계", compact):
            return False
        if not _HIST_AMOUNT_OTHER_FIELD.search(compact):
            return False
    try:
        check_value(form, item.field, relation.rejected_value)
        return _grounded(
            form,
            item.field,
            relation.rejected_value,
            relation.rejected_evidence,
            context,
            text=text,
        ) and _grounded(
            form, item.field, item.value, relation.accepted_evidence, context, text=text
        )
    except (TypeError, ValueError, KeyError):
        return False


def _verified_extra_amount_error_correction(
    item: FieldIntent,
    text: str,
    context: AssistContext,
    old_text: str,
    new_text: str,
    before: int,
    after: int,
) -> bool:
    """옛 금액 오류 인정 뒤 새 금액으로 확정한 단방향 정정만 허용한다."""
    relation = item.correction
    if relation is None or type(item.value) is not int or type(relation.rejected_value) is not int:
        return False
    try:
        check_value("extra", "amount", relation.rejected_value)
        if not (
            _grounded(
                "extra",
                "amount",
                relation.rejected_value,
                relation.rejected_evidence,
                context,
                text=text,
            )
            and _grounded(
                "extra", "amount", item.value, relation.accepted_evidence, context, text=text
            )
        ):
            return False
    except (TypeError, ValueError, KeyError):
        return False

    raw = normalized(text)
    groups = _amount_tokens(raw)
    if len(groups) != 2:
        return False
    (old_start, old_end, old_amount), (new_start, new_end, new_amount) = groups
    if (
        (old_amount, new_amount) != (relation.rejected_value, item.value)
        or not before <= old_start < before + len(old_text)
        or not after <= new_start < after + len(new_text)
    ):
        return False
    if not _EXTRA_AMOUNT_OLD_ERROR.fullmatch(re.sub(r"\s+", "", raw[old_end:new_start])):
        return False
    sentence_start = (
        max(
            raw.rfind(".", 0, old_start),
            raw.rfind(";", 0, old_start),
            raw.rfind("\n", 0, old_start),
        )
        + 1
    )
    end_marker = re.search(r"[.;!?？\n]", raw[new_end:])
    if end_marker and end_marker[0] in "?？":
        return False
    sentence_end = new_end + end_marker.start() if end_marker else len(raw)
    amount_sentence = raw[sentence_start:sentence_end]
    if _EXTRA_AMOUNT_UNDECIDED.search(amount_sentence) or _RULE_QUESTION_OR_UNDECIDED.search(
        amount_sentence
    ):
        return False
    new_action = re.sub(r"\s+", "", raw[new_end:sentence_end])
    if not _EXTRA_AMOUNT_NEW_ACTION.fullmatch(new_action):
        return False
    if _HISTORICAL_DENIED_NEW.search(amount_sentence) or _DIRECT_REPLACE.search(
        raw[new_end:sentence_end]
    ):
        return False
    # 확정 직후 다른 문장에서 같은 금액 변경을 취소·보류하면 앞 제안을 되살리지 않는다.
    rest = raw[sentence_end + 1 :] if end_marker else ""
    for sentence in re.split(r"[.;!?？\n]", rest):
        compact = re.sub(r"\s+", "", sentence)
        if not _EXTRA_AMOUNT_FOLLOWUP_CANCEL.search(compact):
            continue
        if re.search(r"금액|추가금|공사비|견적", compact):
            return False
        other_field = re.search(r"세금|부가세|사유|이유|제목|항목|인원|기간|일정|프로젝트", compact)
        if not other_field:
            return False
    return True


_QUOTE_REPORTED_AMOUNT_PREFIX = re.compile(
    r"(?:(?:아까|앞서|방금|이전에))?(?:제가)?"
    r"(?:총액|합계|견적금액|견적|금액|공사비)(?:은|는|이|가|을|를|:)?"
)
_QUOTE_REPORTED_AMOUNT_LINK = re.compile(
    r"원?(?:이라고|라고|이라|라)(?:제가)?(?:잘못|틀리게)"
    r"(?:말씀드렸|말했|전달했|적었|입력했|썼)(?:네요|어요|습니다|죠)"
    r"[.!。](?:정확히는|정확한금액은|실제금액은)"
)
_QUOTE_REPORTED_AMOUNT_CONFIRM = re.compile(
    r"원?(?:이에요|예요|입니다|이맞아요|이맞습니다|맞아요|맞습니다)"
    r"[.!。](?:그금액|이금액)(?:으로|로)"
    r"(?:정정부탁해요|정정부탁드립니다|정정해주세요|정정할게요|정정하겠습니다"
    r"|수정해주세요|고쳐주세요)[.!。]?"
)


def _verified_quote_reported_amount_correction(
    item: FieldIntent,
    text: str,
    context: AssistContext,
    old_text: str,
    new_text: str,
    before: int,
    after: int,
) -> bool:
    """옛 총액의 오류 인정, 새 금액의 진술, 같은 금액의 확정 지시를 모두 확인한다."""
    relation = item.correction
    if relation is None or type(relation.rejected_value) is not int or type(item.value) is not int:
        return False
    if not 0 < relation.rejected_value <= AMOUNT_MAX or not 0 < item.value <= AMOUNT_MAX:
        return False
    raw = normalized(text)
    groups = _amount_tokens(raw)
    if len(groups) != 2:
        return False
    (old_start, old_end, old_amount), (new_start, new_end, new_amount) = groups
    if (
        (old_amount, new_amount) != (relation.rejected_value, item.value)
        or not before <= old_start < old_end <= before + len(old_text)
        or not after <= new_start < new_end <= after + len(new_text)
    ):
        return False
    prefix, link, tail = (
        re.sub(r"\s+", "", part)
        for part in (raw[:old_start], raw[old_end:new_start], raw[new_end:])
    )
    if not (
        _QUOTE_REPORTED_AMOUNT_PREFIX.fullmatch(prefix)
        and _QUOTE_REPORTED_AMOUNT_LINK.fullmatch(link)
        and _QUOTE_REPORTED_AMOUNT_CONFIRM.fullmatch(tail)
    ):
        return False
    return _grounded(
        "quote", "amount", relation.rejected_value, relation.rejected_evidence, context, text=text
    ) and _grounded("quote", "amount", item.value, relation.accepted_evidence, context, text=text)


def _verified_correction(
    item: FieldIntent,
    form: FormName,
    text: str,
    context: AssistContext,
    previous: PreviousCandidate | None = None,
) -> CorrectionRelation | None:
    """동일 필드의 거절/채택 값과 명시적 정정 연결을 모두 확인한다."""
    relation = item.correction
    if relation is None or relation.kind != "replace" or item.action != "set":
        return None
    if relation.rejected_value is None:
        return None
    old, new = relation.rejected_evidence, relation.accepted_evidence
    if not evidence_matches(text, old) or not evidence_matches(text, new):
        return None
    normalized_text, old_text, new_text = normalized(text), normalized(old), normalized(new)
    if (
        item.value == relation.rejected_value
        or old_text == new_text
        or normalized_text.count(old_text) != 1
        or normalized_text.count(new_text) != 1
    ):
        return None
    before = normalized_text.find(old_text)
    after = normalized_text.find(new_text, before + len(old_text))
    if before < 0 or after < before + len(old_text):
        return None
    if (
        form == "quote"
        and item.field == "amount"
        and _verified_quote_reported_amount_correction(
            item, text, context, old_text, new_text, before, after
        )
    ):
        return relation
    if _verified_historical_scalar(
        item, form, text, context, previous, old_text, new_text, before, after
    ):
        return relation
    if item.field == "amount" and _historical_amount_report(text):
        return None
    if (
        form == "extra"
        and item.field == "amount"
        and _verified_extra_amount_error_correction(
            item, text, context, old_text, new_text, before, after
        )
    ):
        return relation
    if item.field in ("amount", "crew") or (form, item.field) == ("quote", "days"):
        # 근거가 연결어 앞뒤의 다른 조건을 삼켜도 실제 두 숫자 사이의 관계만 인정한다.
        groups = _scalar_tokens(item.field, normalized_text)
        if len(groups) != 2 or (groups[0][2], groups[1][2]) != (
            relation.rejected_value,
            item.value,
        ):
            return None
        actual_gap = normalized_text[groups[0][1] : groups[1][0]].strip()
        if item.field == "amount":
            actual_gap = re.sub(r"^원\s*", "", actual_gap)
        if actual_gap != "에서" and not _correction_gap_connector(actual_gap):
            return None
        prefix = re.split(r"[,;.!?？]", normalized_text[: groups[0][0]])[-1]
        if (_HISTORICAL_REFERENCE.search(prefix) or _RULE_HISTORICAL.search(prefix)) and not (
            previous
            and any(
                prior.field == item.field
                and prior.action == "set"
                and prior.value == relation.rejected_value
                for prior in previous.fields
            )
        ):
            return None
    link = normalized_text[before + len(old_text) : after]
    if len(link) > 36 or _CORRECTION_CLAUSE_BREAK.search(link) and link.strip() != ",":
        return None
    if re.search(r"\d", link) or _CORRECTION_UNRELATED.search(link):
        return None
    if link.strip() not in ("", ",", "에서") and not _correction_gap_connector(link):
        return None
    # 채택값 근거가 '555만원 아니고'까지 삼키면 555만원은 중간에 폐기한 값이다.
    # 맨 앞 연결어('아니고 555만원')는 허용하되 값 뒤의 추가 폐기·교체는 보류한다.
    accepted_body = _CORRECTION_AT_NEW_START.sub("", new_text, count=1)
    if _DIRECT_REPLACE.search(accepted_body) or _RULE_NEGATED_ACTION.search(accepted_body):
        return None
    # 모델 근거가 '455만 원은 아니고'를 포함하면 연결어가 빈 간격에 남지 않는다.
    direct = (
        _CORRECTION_AT_OLD_END.search(old_text)
        or _correction_gap_connector(link)
        or _CORRECTION_AT_NEW_START.match(new_text)
    )
    end_new = after + len(new_text)
    change_tail = normalized_text[end_new : end_new + 28]
    from_to = (
        _CORRECTION_FROM.search(old_text) or link.strip() == "에서"
    ) and _CORRECTION_TO.search(new_text + change_tail)
    if not (direct or from_to):
        return None

    # 관계 주위의 해당 문장에 미정·질문·선택지가 있으면 채택을 확정할 수 없다.
    begin = before
    while begin > 0 and not _CORRECTION_CLAUSE_BREAK.fullmatch(normalized_text[begin - 1]):
        begin -= 1
    end = end_new
    while end < len(normalized_text) and not _CORRECTION_CLAUSE_BREAK.fullmatch(
        normalized_text[end]
    ):
        end += 1
    if end < len(normalized_text) and normalized_text[end] in "?？":
        return None
    if _UNCERTAIN_CHANGE.search(normalized_text[begin:end]):
        return None
    if re.search(r"(?:말고|아니고|아니라)", normalized_text[end_new:end]):
        return None
    try:
        check_value(form, item.field, relation.rejected_value)
        if not _grounded(form, item.field, relation.rejected_value, old, context, text=text):
            return None
        if not _grounded(form, item.field, item.value, new, context, text=text):
            return None
    except (ValueError, TypeError, KeyError):
        return None
    return relation


def _independent_same_value_rule(
    item: FieldIntent, data: AssistRequest, context: AssistContext, text: str
) -> bool:
    """정정 관계만 입증 실패했을 때 동의하는 유효한 규칙의 값은 별도로 확인한다."""
    if (
        item.correction is None
        or item.correction.kind != "replace"
        or item.action != "set"
        or _RULE_QUESTION_OR_UNDECIDED.search(text)
    ):
        return False
    if item.field == "amount" and _amount_mentions(text) != {item.value}:
        return False
    for rule in data.rule_fields:
        if (
            rule.field != item.field
            or rule.action != "set"
            or rule.value != item.value
            or rule.correction is not None
            or rule.value_source in ("previous", "current")
            or not evidence_matches(text, rule.evidence)
        ):
            continue
        if not _grounded(data.form, item.field, rule.value, rule.evidence, context, text=text):
            continue
        if not _business_ok(
            data.form, item.field, rule.value, data, context, {item.field: rule.value}
        ):
            continue
        checked = CandidateField(
            field=item.field,
            action="set",
            value=item.value,
            evidence=rule.evidence,
            source="rule",
            status="valid",
            alternatives=[],
        )
        if _rule_field_proof(data.form, checked, text, context):
            return True
    return False


_SCALAR_UNCERTAIN = re.compile(
    r"[?？~∼〜–—－−]|아마|혹시|미정|모르|애매|일까(?!지)|할까|할지|거죠|까요|"
    r"또는|아니면|혹은|사이|정도|쯤|약\s|대략|내외|안팎|가량|언저리|"
    rf"고민|생각\s?중|검토|몇\s?명|취소|철회|보류|{WITHDRAW_IDIOM}|원래대로|"
    r"아니|아닙|아님|말고|대신|않|"
    r"지\s?(?:마|말|않|못)|원하지|거부|금지|(?<![가-힣])안\s?(?:할|해|하|넣|적|맞|보내)|"
    r"최대|최소|미만|초과|이상|이하|이내|한도|"
    r"가능하면|(?:라면|다면|으면|이면|하면)|경우|일\s?때|"
    r"마이너스|음수|플러스|잘못|틀렸|"
    r"(?<!\d)[+-]\s?(?=\d)|(?<=\d)\s?[+/]\s?(?=\d)|"
    r"[<>]|(?:\d|원|명|만|억)\s?(?:부터|까지|[+×*])|"
    r"(?:할|넣을|보낼)\s?생각|(?:할|될)\s?수도|것\s?같|"
    rf"{_INDIRECT_INQUIRY}"
)
_SCALAR_PAST = re.compile(
    r"지난|이전|기존|예전|과거|원래|"
    r"(?:말했|말씀드렸|정했|적었|썼|입력했|했)(?:는데|지만|다가)|"
    r"(?:였|이었)습니다|(?:였|이었)어요"
)
_SCALAR_REJECTED_PREFIX = re.compile(
    r"^(?:아니[ ,]*|(?:금액|견적|인원|작업자)(?:은|는|이|가)?\s*"
    r"(?:그대로\s*두지\s*말고|그대로\s*말고)|"
    r"(?:인원(?:은|는)?\s*)?(?:두세|서너|한두|두 세|세 네)\s*명\s*(?:은|는)?\s*(?:아니고|말고))\s*$"
)


def _scalar_tokens(field: str, raw: str) -> list[tuple[int, int, int]]:
    if field == "days":
        return _quote_duration_tokens(raw)
    return _amount_tokens(raw) if field == "amount" else crew_tokens(raw)


def _scalar_span_evidence(field: str, raw: str, start: int, end: int) -> str:
    # 숫자 토큰은 원 단위를 생략해서도 검증되지만 연결어 앞의 '원'은 근거에 남긴다.
    if field == "amount":
        won = re.match(r"\s*원", raw[end:])
        if won:
            end += won.end()
    return raw[start:end]


def _scalar_inferred_relation(
    item: FieldIntent,
    form: FormName,
    text: str,
    context: AssistContext,
    previous: PreviousCandidate | None,
) -> CorrectionRelation | None:
    """선택적 모델 정정 메타데이터가 빠져도 명시적인 두 값의 관계만 복구한다."""
    raw = normalized(text)
    groups = _scalar_tokens(item.field, raw)
    if len(groups) != 2 or groups[0][2] == groups[1][2] or groups[1][2] != item.value:
        return None
    old_start, old_end, old_value = groups[0]
    new_start, new_end, _ = groups[1]
    relation = CorrectionRelation(
        kind="replace",
        rejected_value=old_value,
        rejected_evidence=_scalar_span_evidence(item.field, raw, old_start, old_end),
        accepted_evidence=_scalar_span_evidence(item.field, raw, new_start, new_end),
    )
    return _verified_correction(
        item.model_copy(update={"correction": relation}), form, text, context, previous
    )


def _scalar_set_intent_ok(
    item: FieldIntent,
    text: str,
    correction: CorrectionRelation | None,
    form: FormName,
    context: AssistContext,
) -> bool:
    """원문 숫자와 별개로 확정 지시인지 검사한다. 규칙·모델이 같은 관문을 사용한다."""
    raw = normalized(text)
    if form == "extra" and item.field == "amount":
        raw = mask_owned_text_values(raw)
    groups = _scalar_tokens(item.field, raw)
    if (form, item.field) == ("quote", "days"):
        return _duration_set_intent_ok(item, raw, groups, correction, context)
    # 숫자 자체, 경쟁 값 및 전역 조건은 그대로 검사한다. 별개 필드의
    # 완전한 긍정 유지 절에 포함된 과거 참조만 숫자 확정 검사에서 분리한다.
    if item.field == "amount" and any(
        re.match(r"\s*(?:원\s*)?(?:안|내|범위)\s*(?:으로|로|에서)", raw[end:])
        for _, end, _ in groups
    ):
        # 금액 상한은 정확한 폼 입력값과 다르다. 두 추출기가 동의해도
        # 상한 표현의 숫자를 확정 금액으로 승격하지 않는다.
        return False
    risk_text = raw
    clauses = _resolved_intent_clauses(form, raw, context)
    scopes = _scalar_risk_scopes(clauses)
    if not any(part.unbound for part in clauses):
        for part in clauses:
            clause = part.clause
            # 물음표로 끝난 독립적인 다른 필드 문의는 뒤의 명시 확정
            # 조건이 아니다. 같은 숫자나 대상 없는 조건은 그대로 검사한다.
            if (
                clause.boundary in ("question", "sentence")
                and part.fields
                and item.field not in part.fields
                and not any(a < clause.end and b > clause.start for a, b, _ in groups)
            ):
                risk_text = (
                    risk_text[: clause.start]
                    + " " * (clause.end - clause.start)
                    + risk_text[clause.end :]
                )
            for scope in clause.retentions:
                span = raw[scope.start : scope.end]
                if (
                    scope.compatible
                    and item.field not in scope.fields
                    and (
                        scope.fields
                        or scope.remaining
                        and any(item.field in other.fields for other in clauses if other != part)
                    )
                    and not _RETENTION_CONDITION.search(span)
                    and not _RETAIN_NEGATED.search(span)
                    and not _PENDING_CANCEL.search(span)
                    and not any(a < scope.end and b > scope.start for a, b, _ in groups)
                ):
                    risk_text = (
                        risk_text[: scope.start]
                        + " " * (scope.end - scope.start)
                        + risk_text[scope.end :]
                    )
    if correction is not None:
        if len(groups) != 2 or (groups[0][2], groups[1][2]) != (
            correction.rejected_value,
            item.value,
        ):
            return False
        # 입증한 폐기→채택 연결만 가리고, 앞 조건과 뒤 보류/취소는 원문 전체에서 확인한다.
        start, end = groups[0][0], groups[1][1]
        masked = risk_text[:start] + " " * (end - start) + risk_text[end:]
        return not scalar_clause_risk(
            raw,
            item.field,
            [(a, b) for a, b, _ in groups],
            _SCALAR_UNCERTAIN,
            masked=masked,
            scopes=scopes,
        )
    if len(groups) != 1 or groups[0][2] != item.value:
        return False
    start, end, _ = groups[0]
    prefix = raw[:start]
    if scalar_clause_risk(
        raw, item.field, [(start, end)], _SCALAR_PAST, masked=risk_text, scopes=scopes
    ) or (item.field == "amount" and _historical_amount_report(raw)):
        return False
    masked = risk_text
    if _SCALAR_REJECTED_PREFIX.fullmatch(prefix):
        masked = " " * start + risk_text[start:]
    elif item.field == "amount":
        # 수치 없는 오류 인정 뒤의 단일 새 금액은 옛 수치의 주장과 다르다.
        admission = re.search(
            r"(?:금액|견적|가격)(?:은|는|을|를)?\s*(?:제가\s*)?(?:잘못\s*(?:불렀|말했|말씀드렸|적었)|틀렸)"
            r"(?:네요|어요|습니다|군요)\s*[.!。]\s*$",
            prefix,
        )
        if admission:
            masked = (
                risk_text[: admission.start()]
                + " " * (start - admission.start())
                + risk_text[start:]
            )
    return not scalar_clause_risk(
        raw, item.field, [(start, end)], _SCALAR_UNCERTAIN, masked=masked, scopes=scopes
    )


# A model operation is not a second rule-parser extraction. An independently
# owned value, a real operation marker and the absence of contradictory intent
# suffice; the complete positive sentence grammar is not a prerequisite.
_OPERATION_VERB = re.compile(
    r"(?:확정|변경|수정|정정|제안|입력|설정|반영|등록|선택|결정|조정|"
    r"올려|내려|줄여|늘려|바꿔|잡아|정해|옮겨|적어|기재|발행)"
)
_OPERATION_REQUEST = re.compile(
    r"(?:주세요|줘요|주십시오|주시겠어요|주실래요|주시겠습니까|"
    r"부탁|바랍니다|합니다|하겠습니다|할게요|겠습니다|하고|하며)"
)
_OPERATION_DENIAL = re.compile(
    r"(?:취소|철회|보류|미정|조건부|가능하면|"
    r"하지\s*(?:마|말|않|못)|지\s*(?:마|말|않|못)|"
    r"(?<![가-힣])(?:안|못)\s*(?:하|해|넣|잡|정|바꾸)|"
    r"원하지|거부|금지|말고|아니고|아니라|또는|혹은|아니면)"
)
_EXPLICIT_PREVIEW = re.compile(
    r"(?:후보(?:안)?|초안|제안안|변경안|안)\s*(?:을|를)?\s*"
    r"(?:(?:먼저|우선|한번|한\s*번)\s*)?"
    r"(?:보여|만들어|작성해|제시해)\s*"
    r"(?:주세요|줘요|주십시오|주시겠어요|주실래요|주시겠습니까|주실\s*수\s*있을까요)"
    r"\s*[?？.!。]?\s*$"
)
_PREVIEW_CONTENT = re.compile(
    r"(?:변경|수정|정정|설정|선택|확정|바꾸|바꿀|정하|정할|옮기|옮길)"
    r"(?:하는|할|는|을|ㄹ)?\s*(?:경우의\s*)?$"
)


def _preview_risk_text(clause: str) -> str:
    """명시 후보 생성의 목적어 내부만 조건/문의 종결 검사에서 분리한다."""
    match = _EXPLICIT_PREVIEW.search(clause)
    if match is None:
        return polite_execution_risk_text(clause)
    head = clause[: match.start()]
    # '승인되면 ... 후보를 보여'의 외부 조건을 지우지 않는다.
    # 값에 바로 결합한 관형형의 '경우의'만 후보 내용이다.
    content = _PREVIEW_CONTENT.search(head.rstrip())
    if content is not None:
        head = head[: content.start()] + re.sub(r"경우의", "", head[content.start() :])
    return head + " " * (len(clause) - len(head))


def _local_scalar_operation_ok(
    item: FieldIntent, form: FormName, text: str, context: AssistContext
) -> bool:
    """같은 값의 설명 반복과 독립 대입의 관계만 입증한다.

    다른 값, 오래된 주장, 명시 조건/거부, 대상 없는 제어는 여기서
    복구하지 않는다. 정정 및 파생값은 기존 별도 검증 경로를 쓴다.
    """
    if item.correction is not None or item.action != "set" or type(item.value) is not int:
        return False
    raw = normalized(text)
    if form == "extra":
        raw = mask_owned_text_values(raw)
    needle = normalized(item.evidence)
    if not needle or not evidence_matches(text, item.evidence):
        return False
    tokens = _scalar_tokens(item.field, raw)
    if not tokens or {v for _, _, v in tokens} != {item.value}:
        return False
    if item.field == "amount" and any(
        re.match(r"\s*(?:원\s*)?(?:안|내|범위)\s*(?:으로|로|에서)", raw[end:])
        for _, end, _ in tokens
    ):
        return False
    parts = _resolved_intent_clauses(form, raw, context)
    assignments = []
    for resolved in parts:
        part = resolved.clause
        owned = [m for m in part.mentions if m.fields == {item.field}]
        local_tokens = [(a, b) for a, b, _ in tokens if part.start <= a < part.end]
        if not owned or not local_tokens or resolved.fields != {item.field}:
            continue
        for a, b in local_tokens:
            labels = [m for m in owned if m.end <= a]
            if not labels:
                continue
            start = labels[-1].start
            operation = raw[start : part.end]
            tail = raw[b : part.end]
            if item.field == "days" and duration_quantity_risk(operation):
                continue
            if not (
                _OPERATION_VERB.search(tail)
                and (_OPERATION_REQUEST.search(tail) or _EXPLICIT_PREVIEW.search(tail))
            ):
                continue
            if not any(
                m.start() <= a and b <= m.end() and part.start <= m.start() < part.end
                for m in re.finditer(re.escape(needle), normalized(text))
            ):
                continue
            risk = _preview_risk_text(operation)
            if _SCALAR_UNCERTAIN.search(risk) or _SCALAR_PAST.search(risk):
                continue
            if CONDITIONAL_LINK.search(risk):
                continue
            assignments.append((part, start, a, b))
    if len(assignments) != 1:
        return False
    target, start, _, _ = assignments[0]
    # 앞선 설명은 같은 숫자/무수치이며 실제 변경 지시가 없을 때만
    # 독립 대입과 구분한다. 이어지는 취소/경쟁/질문은 그대로 막는다.
    for resolved in parts:
        part = resolved.clause
        if part == target:
            prefix = raw[part.start : start]
            # 별개 필드의 완료된 절 뒤 '대신'은 경쟁 숫자가 아니다.
            if prefix.strip() == "대신" and part.start > 0:
                prior = next((p for p in parts if p.clause.end == part.start), None)
                if prior and prior.fields and item.field not in prior.fields:
                    continue
            if _OPERATION_DENIAL.search(prefix) or CONDITIONAL_LINK.search(prefix):
                return False
            if _OPERATION_VERB.search(prefix) and _OPERATION_REQUEST.search(prefix):
                return False
            continue
        span = raw[part.start : part.end]
        if resolved.fields and item.field not in resolved.fields:
            if part.boundary not in ("sentence", "question") and CONDITIONAL_LINK.search(span):
                return False
            continue
        if _OPERATION_DENIAL.search(span):
            return False
        if part.end <= target.start:
            if _OPERATION_VERB.search(span) and _OPERATION_REQUEST.search(span):
                return False
            if part.boundary not in ("sentence", "question") and (
                _SCALAR_UNCERTAIN.search(span) or CONDITIONAL_LINK.search(span)
            ):
                return False
        elif (
            _SCALAR_UNCERTAIN.search(span)
            or _SCALAR_PAST.search(span)
            or (
                _OPERATION_VERB.search(span)
                or _RETAIN.search(span)
                or CONDITIONAL_LINK.search(span)
            )
        ):
            return False
    return True


def _duration_set_intent_ok(
    item: FieldIntent,
    raw: str,
    groups: list[tuple[int, int, int]],
    correction: CorrectionRelation | None,
    context: AssistContext,
) -> bool:
    """같은 필드의 정확한 일수만 승인한다. 날짜·금액 등 독립 절은 분리한다."""
    if correction is None:
        if len(groups) != 1 or groups[0][2] != item.value:
            return False
        checked = raw
    else:
        if len(groups) != 2 or [g[2] for g in groups] != [
            correction.rejected_value,
            item.value,
        ]:
            return False
        start, end = groups[0][0], groups[1][0]
        # 입증한 폐기값 및 연결어만 검사에서 제외한다. 채택 수량의
        # 범위/근삿값 및 뒤쪽 조건은 그대로 확인한다.
        checked = raw[:start] + " " * (end - start) + raw[end:]
    if date_set_is_inquiry("quote", "days", raw):
        return False
    for part in _resolved_intent_clauses("quote", raw, context):
        clause = part.clause
        has_value = any(a < clause.end and b > clause.start for a, b, _ in groups)
        if not (has_value or "days" in part.fields or part.unbound):
            continue
        span = checked[clause.start : clause.end]
        if duration_quantity_risk(span) or _PENDING_CANCEL.search(span):
            return False
        if _SCALAR_PAST.search(span) and correction is None:
            return False
        if re.search(
            r"(?<![가-힣])(?:안|못)\s*(?:하|해|잡|정|넣|적|늘|줄|바꾸)|"
            r"지\s*(?:마|말|않|못)|(?:[~∼〜–—－−]|최대|최소|이내|이상|이하|초과|미만|"
            r"정도|쯤|내외|안팎|대략|가량|또는|혹은|아니면|보류|미정|고민|조건부|가능하면)",
            span,
        ):
            return False
        if part.unbound and (
            _RETENTION_CONDITION.search(span)
            or _PENDING_CHANGE.search(span)
            or _RETAIN_NEGATED.search(span)
        ):
            return False
    return True


_VAT_NEGATIVE_ASSIGNMENT = re.compile(
    rf"(?:부가세|세금)\s*[은는을를이가]?\s*{VAT_NEGATIVE_INCLUSION}"
    r"\s*(?:으로|로)\s*(?:확정|설정|지정|정리|선택|처리|적용)"
    r"\s*(?:합니다|하겠습니다|할게요|해\s*(?:주세요|줘요|주십시오))"
)
_VAT_DENIED_DIRECTIVE = re.compile(
    r"(?:부가세|세금)(?:은|는|을|를|이|가)? ?"
    r"(?:(?:포함|제외|면세)(?:하지 ?말라는| ?안 ?하는|하지 ?않는)|"
    r"(?:빼|제외해) ?달라는) ?"
    r"(?:뜻|말|의미|건|것)(?:은|는|이|가)? ?"
    r"(?:아니었어요|아니었습니다|아닙니다|아니에요|아니고)"
)
_VAT_AFFIRM = re.compile(
    r"(?:확정(?:합니다|할게요|하겠습니다|해 ?주세요|해 ?줘요)|"
    r"맞습니다|맞아요|(?:처리해|적용해|변경해|바꿔|해) ?주세요)$"
)
_VAT_CURRENT_REPORT = re.compile(
    r"(?:현재|지금) ?(?:부가세|세금)(?:은|는|이|가)? ?"
    r"(?:포함|별도|미포함|제외|면세)(?:으로|로)? ?"
    r"(?:돼|되어) ?(?:있는데|있어요|있습니다)"
)
_VAT_HISTORICAL_REPORT = re.compile(
    r"(?:(?:지난번|이전|예전|과거|원래)(?:에|엔)?\s*.*(?:했|였|이었|말했|말씀드렸|적었)"
    r"|(?:라고|다고|자고|라는)\s*(?:말했|말씀드렸|정했|요청했|했)(?:었)?)"
)
_VAT_SCOPE_TRANSITION = re.compile(
    r"(?<=[고며]) (?=(?:부가세|세금|면세|견적 ?금액|금액|공사 ?기간|공사 ?일수|"
    r"기간|일수|항목명|항목|제목|사유|이유)(?:은|는|이|가|을|를|만| ))"
)


def _vat_set_intent_ok(data: AssistRequest, item: CandidateField, context: AssistContext) -> bool:
    """병합 후 같은 세금 필드의 확정값만 남기고, 다른 필드의 위험은 전파하지 않는다."""
    raw = normalized_scope(data.text)
    if data.form == "extra":
        raw = mask_owned_text_values(raw)
    checked = raw
    # 이미 검증된 폐기→채택 연결만 가린다. 새 값 뒤의 취소/조건은 계속 검사한다.
    if item.correction is not None and item.correction.kind == "replace":
        old = normalized(item.correction.rejected_evidence)
        new = normalized(item.correction.accepted_evidence)
        start = raw.find(old)
        stop = raw.find(new, start + len(old))
        connector = _CORRECTION_AT_NEW_START.match(new)
        if connector:
            stop += connector.end()
        if 0 <= start < stop:
            discarded = raw[start:stop]
            if (
                _RETENTION_CONDITION.search(discarded)
                or _RULE_QUESTION_OR_UNDECIDED.search(discarded)
                or _PENDING_CANCEL.search(discarded)
            ):
                return False
            checked = raw[:start] + " " * (stop - start) + raw[stop:]

    accepted = False
    denied = False
    for resolved in _resolved_intent_clauses(data.form, raw, context):
        part = resolved.clause
        clause = checked[part.start : part.end].strip().strip(",;.!。").strip()
        names = resolved.fields
        # 값의 어휘 의미와 실제 필드 귀속을 분리한다. 일반 명사의
        # '별도'나 '포함'을 부가세 값으로 승인하지 않는다.
        original_clause = raw[part.start : part.end]
        visible_clause = checked[part.start : part.end]
        modes = {
            token.mode
            for token in scoped_vat_tokens(original_clause)
            if visible_clause[token.start : token.end] == original_clause[token.start : token.end]
        }
        if "vat" not in names and not names:
            modes = standalone_vat_modes(clause)
        if resolved.unbound:
            return False
        if "vat" not in names and names:
            # 앞 절의 실제 조건은 독립된 다른 필드의 문의와 구별한다.
            if (
                not accepted
                and _SCALAR_CONDITION.search(clause)
                and part.boundary not in ("question", "sentence")
            ):
                return False
            continue
        if not clause:
            continue
        if _VAT_DENIED_DIRECTIVE.fullmatch(clause):
            # 이중 부정만으로 반대 세금 방식을 추론하지 않는다.
            denied = True
            accepted = False
            continue
        if _VAT_CURRENT_REPORT.fullmatch(clause) and modes == {data.current.get("vat")}:
            # 검증된 현재 상태의 설명은 새 변경 지시와 구별한다.
            denied = True
            accepted = False
            continue
        if modes and _VAT_HISTORICAL_REPORT.search(clause):
            # A prior report is not a new VAT assignment. A later explicit
            # instruction remains possible, but must have its own current evidence.
            if part.boundary not in ("sentence", "question") or evidence_matches(
                clause, item.evidence
            ):
                return False
            denied = True
            accepted = False
            continue
        # 명시적인 비포함 방식 선택만 긍정 확정으로 해석한다.
        # '포함하지 마', 이중 부정, 질문이나 조건은 기존 거부 경로를 유지한다.
        if modes == {"excluded"} and _VAT_NEGATIVE_ASSIGNMENT.fullmatch(clause):
            if item.value != "excluded":
                return False
            accepted = True
            continue
        if (
            _RETENTION_CONDITION.search(clause)
            or _RULE_QUESTION_OR_UNDECIDED.search(polite_execution_risk_text(clause))
            or _RULE_NEGATED_ACTION.search(clause)
            or re.search(r"않|지 ?(?:마|말|못)|(?<![가-힣])안 ?(?:하|해|할|되)", clause)
        ):
            return False
        if not modes:
            continue
        if modes != {item.value}:
            return False
        if denied and not _VAT_AFFIRM.search(clause):
            return False
        accepted = True
    return accepted


def _reverify_vat_history(
    item: FieldIntent,
    checked: CandidateField,
    history: str,
    data: AssistRequest,
    context: AssistContext,
) -> bool:
    """이전 후보도 최초 채택 근거의 발화에서 같은 세금 의도 검증을 통과해야 한다."""
    turns = history.splitlines()
    matches = [turn for turn in turns if evidence_matches(turn, item.evidence)]
    if not matches:
        return False
    origin = matches[-1]
    origin_data = data.model_copy(update={"text": origin})
    origin_scope = _scoped_retention(
        data.form, "vat", origin, origin, context, retaining_previous=True
    )
    if (
        origin_scope is not None
        and origin_scope[0].reference == "previous"
        and "vat" in origin_scope[0].fields
    ):
        # A reference alone cannot establish the original assignment.
        return False
    relation = _verified_correction(item, data.form, origin, context)
    candidate = checked.model_copy(update={"correction": relation})
    if _vat_set_intent_ok(origin_data, candidate, context):
        return True
    # 이전 후보 계약에서는 일반 정정의 관계가 생략될 수 있다. 실제 두 세금
    # 값과 방향을 기존 정정 검증기로 다시 입증할 수 있을 때만 복원한다.
    if item.correction is not None:
        return False
    raw = normalized(origin)
    tokens = vat_tokens(raw)
    if len(tokens) != 2:
        return False
    old, new = tokens
    old_mode, new_mode = old.mode, new.mode
    if old_mode == new_mode or new_mode != item.value:
        return False
    proposed = CorrectionRelation(
        kind="replace",
        rejected_value=old_mode,
        rejected_evidence=raw[old.start : old.end],
        accepted_evidence=raw[new.start : new.end],
    )
    relation = _verified_correction(
        item.model_copy(update={"correction": proposed}), data.form, origin, context
    )
    return relation is not None and _vat_set_intent_ok(
        origin_data, checked.model_copy(update={"correction": relation}), context
    )


def _business_ok(
    form: FormName,
    field: str,
    value: Value,
    data: AssistRequest,
    context: AssistContext,
    proposed: dict[str, Value],
) -> bool:
    if field == "amount":
        return type(value) is int and 0 <= value <= (
            AMOUNT_MAX if form == "quote" else EXTRA_AMOUNT_MAX
        )
    if field == "vat":
        return type(value) is str and value in context.vat_modes
    if form == "quote":
        if field == "days":
            return type(value) is int and 1 <= value <= context.days_max
        if field == "valid":
            return type(value) is str and date.fromisoformat(value) >= context.today
    if form == "extra":
        return field in ("title", "reason") and type(value) is str and bool(value.strip())
    if form == "period":
        return not period_violations(
            {**data.current, **proposed}, context.today, strict_interior=False
        )
    if form == "dates":
        if field == "crew":
            return type(value) is int and 1 <= value <= context.crew_max
        if field != "days" or not isinstance(value, list) or not value:
            return False
        days = [date.fromisoformat(day) for day in value]
        if len(days) > context.days_max or len(days) != len(set(days)):
            return False
        if context.required_days is not None and len(days) != context.required_days:
            return False
        if min(days) <= context.today or max(days) > context.today + timedelta(days=365):
            return False
        if (max(days) - min(days)).days > JOB_MAX_DAYS:
            return False
        return not any(
            day in context.holidays or context.no_weekend and day.weekday() >= 5 for day in days
        )
    return False


def _competing_retention_assignment(
    data: AssistRequest,
    field: str,
    value: Value,
    context: AssistContext,
    previous: PreviousCandidate | None,
) -> bool:
    """새 대입도 같은 필드의 유지 지시와 경쟁하는지 확인한다.

    완결된 유지절뿐 아니라 대상이 생략된 연결절도 경쟁 검사에만
    사용한다. 유지절을 신규 값의 근거 또는 계승 승인으로 쓰지 않는다.
    """
    raw = normalized(data.text)
    retained = (
        next((item for item in previous.fields if item.field == field), None) if previous else None
    )
    for scope in retention_scopes(data.form, raw, include_linked_prefixes=True):
        if not scope.compatible or field not in scope.fields:
            continue
        conflict = _retention_scope_conflict(
            data.form,
            field,
            raw,
            scope.start,
            scope.end,
            context,
            retaining_previous=scope.reference != "current",
        )
        if not conflict and scope.linked_prefix:
            # 연결절 뒤에 대상이 생략되어도 같은 필드의 완전한 값이
            # 확인되면 '확정' 등 변경 동사 없는 대입도 경쟁 지시이다.
            continuation = _resolved_intent_clauses(data.form, raw[scope.end :].strip(), context)
            conflict = bool(continuation and continuation[0].fields == {field})
        if not conflict:
            continue
        if scope.reference == "current":
            known, original = field in data.current, data.current.get(field)
        elif retained is not None:
            known = not scope.first or _first_retention_origin_verified(previous, retained)
            original = retained.value
        elif scope.reference == "pending" and data.previous_candidate is None:
            known, original = field in data.current, data.current.get(field)
        else:
            known, original = False, None
        # 동일 값의 명시적 재확인은 기존 근거 검증에 맡긴다. 검증되지
        # 않은 과거값이나 서로 다른 새 값은 이력 유지로 우회하지 않는다.
        if not known or original != value:
            return True
    return False


def _local_retention_proof(
    item: FieldIntent,
    data: AssistRequest,
    text: str,
    context: AssistContext,
    previous: PreviousCandidate | None,
) -> str | None:
    """Bind a validated keep intent to its own complete clause and referent.

    The model identifies the operation; field ownership, exact reference,
    negative controls, and the prior candidate are independently checked.
    """
    kind = item.intent_kind
    if kind not in ("retain_current", "retain_previous") or item.action != "keep":
        return None
    raw, needle = normalized(text), normalized(item.evidence)
    if not needle or raw.count(needle) != 1:
        return None
    at = raw.index(needle)
    previous_keep = kind == "retain_previous"
    if item.value_source != ("previous" if previous_keep else "current"):
        return None
    retained = (
        next((f for f in previous.fields if f.field == item.field), None) if previous else None
    )
    if previous_keep and retained is None:
        return None
    clauses = _resolved_intent_clauses(data.form, raw, context)
    owned = [
        part
        for part in clauses
        if part.fields == {item.field}
        and part.clause.start <= at
        and at + len(needle) <= part.clause.end
    ]
    if len(owned) != 1:
        return None
    target = owned[0]
    clause = target.clause
    body = raw[clause.start : clause.end]
    history = re.search(rf"{HISTORY_TIME}\s*{HISTORY_PAST_MODIFIER}", body)
    current_ref = re.search(
        r"현재|지금|기존|원래|저장된|등록된|수정\s*전|처음\s*(?:상태|값|금액|기간|설정)", body
    )
    no_change = re.search(r"(?:수정|변경|조정|교체|갱신)(?:은|는|을|를)?\s*하지\s*말", body)
    control = re.search(r"그대로|유지|돌려놓|돌려줘|(?:으로|로)\s*둘|확정|사용|남겨|진행", body)
    if not (control or no_change):
        return None
    if previous_keep:
        if not history or current_ref or no_change:
            return None
        referent = re.match(
            rf"\s*(?P<referent>{_REFERENT})(?=$|[\s은는이가을를만도]|으로|로|대로)",
            body[history.end() :],
        )
        if referent and not compatible_reference(
            data.form, frozenset({item.field}), referent["referent"]
        ):
            return None
        if any(not scope.compatible for scope in clause.retentions if item.field in scope.fields):
            return None
        if first_history_reference(history.group()) and not _first_retention_origin_verified(
            previous, retained
        ):
            return None
        if _PENDING_CANCEL.search(body):
            return None
        expected = retained.value
    else:
        if not current_ref and not no_change:
            return None
        if current_ref and re.match(r"\s*(?:제안|후보|변경안|요청)", body[current_ref.end() :]):
            return None
        # A current reference must follow an earlier cancellation, not name
        # an earlier proposal as the value to be kept.
        if history and (
            not current_ref
            or history.start() >= current_ref.start()
            or not _PENDING_CANCEL.search(body[: current_ref.start()])
        ):
            return None
        expected = data.current.get(item.field)
    start = (
        history.start()
        if previous_keep
        else current_ref.start()
        if current_ref
        else no_change.start()
    )
    operation = body[start:]
    if no_change and not previous_keep:
        operation = operation.replace(no_change.group(), "", 1)
    if (
        CONDITIONAL_LINK.search(operation)
        or _RULE_QUESTION_OR_UNDECIDED.search(polite_execution_risk_text(operation))
        or _RETAIN_NEGATED.search(operation)
        or _OPERATION_DENIAL.search(operation)
        or re.search(r"말고|아니|않|나중|추후|검토|할지|할까|보류|취소|철회", operation)
        or _retention_has_different_value(data.form, item.field, expected, operation, context)
    ):
        return None
    if isinstance(expected, list):
        dates = {day.isoformat() for _, _, day in date_tokens(operation, context)}
        if dates and dates != set(expected):
            return None
    # Outside the selected clause, never ignore an unbound control, an
    # actual conditional antecedent, or a competing operation on this field.
    for part in clauses:
        if part == target:
            continue
        span = raw[part.clause.start : part.clause.end]
        if part.unbound:
            return None
        if CONDITIONAL_LINK.search(span) and part.clause.boundary not in ("sentence", "question"):
            return None
        if item.field not in part.fields:
            continue
        if not previous_keep and part.clause.end <= clause.start:
            if part.clause.boundary == "question" and not _PENDING_CANCEL.search(span):
                continue
            if _PENDING_CANCEL.search(span) and not _OPERATION_DENIAL.search(
                span.replace("취소", "").replace("철회", "")
            ):
                continue
            if not _OPERATION_VERB.search(span) and not _RETAIN.search(span):
                continue
        return None
    # A retained reference can contain an earlier cancellation of the same
    # pending proposal only for retain_current; no later assignment can hide
    # behind that reference.
    prefix = body[:start]
    if previous_keep and (_PENDING_CANCEL.search(prefix) or _OPERATION_DENIAL.search(prefix)):
        return None
    if _RETENTION_CONDITION.search(prefix) and (previous_keep or _OPERATION_VERB.search(prefix)):
        return None
    if (
        _OPERATION_VERB.search(prefix)
        and not _PENDING_CANCEL.search(prefix)
        and _OPERATION_REQUEST.search(prefix)
    ):
        return None
    return body.strip()


def _review_one(
    item: FieldIntent,
    source: Source,
    data: AssistRequest,
    context: AssistContext,
    proposed: dict[str, Value],
    *,
    text: str,
    previous: PreviousCandidate | None = None,
    defer_period_business: bool = False,
    withdrawals: Mapping[str, WithdrawalDecision] | None = None,
) -> CandidateField:
    common = {
        "field": item.field,
        "source": source,
        "alternatives": [],
        "origin_request_id": data.request_id,
    }
    intent = _intent(item)
    proof = (
        (
            withdrawals if withdrawals is not None else resolve_withdrawals(data, previous, context)
        ).get(item.field)
        if text == data.text
        else None
    )

    def reject(reason: ReasonCode, *, uncertain: bool = False) -> CandidateField:
        return CandidateField(
            **common,
            action="uncertain",
            value=None,
            evidence=item.evidence if evidence_matches(text, item.evidence) else "",
            status="uncertain" if uncertain else "invalid",
            intent_kind="ambiguous",
            reason_code=reason,
        )

    if intent == "no_mention" and item.action == "keep":
        if item.evidence.strip():
            return reject(
                "ambiguous_intent" if evidence_matches(text, item.evidence) else "evidence_missing",
                uncertain=evidence_matches(text, item.evidence),
            )
        return CandidateField(
            **common,
            action="keep",
            value=None,
            evidence="",
            status="valid",
            intent_kind="no_mention",
        )
    if intent == "retain_current":
        if item.action != "keep" or not evidence_matches(text, item.evidence):
            return reject("evidence_missing")
        local = _local_retention_proof(item, data, text, context, previous)
        if local is not None:
            return CandidateField(
                **common,
                action="keep",
                value=None,
                evidence=local,
                intent_kind="retain_current",
                value_source="current",
                status="valid",
            )
        if proof is not None and proof.definite:
            # The producer's evidence must belong to the actual verified
            # withdrawal. A valid independent rule may recover later, but
            # an unrelated model fragment is never certified here.
            needle = normalized(item.evidence).rstrip(",;.!。").strip()
            if needle and proof.evidence in needle and normalized(text).count(needle) == 1:
                return _withdrawal_candidate(data, proof, source)
            return reject("ambiguous_intent")
        independent_clauses = _verified_independent_date_clauses(
            data, context, previous, item.field, text
        )
        retained = (
            next((field for field in previous.fields if field.field == item.field), None)
            if previous
            else None
        )
        scoped = _scoped_retention(
            data.form,
            item.field,
            item.evidence,
            text,
            context,
            retaining_previous=True,
            independent_clauses=independent_clauses,
        )
        if scoped is not None and scoped[0].reference != "current":
            if (
                scoped[0].reference == "pending"
                and retained is not None
                and _reference_only_retention(
                    data.form,
                    item.field,
                    item.evidence,
                    text,
                    context,
                    previous,
                    retained,
                    independent_clauses=independent_clauses,
                )
                is True
            ):
                return _candidate_from_previous(retained, previous)
            if (
                scoped[0].reference == "previous"
                or data.previous_candidate is not None
                and any(part.field == item.field for part in data.previous_candidate.fields)
            ):
                return reject(
                    "ambiguous_intent"
                    if retained is not None
                    else "stale_context"
                    if data.previous_candidate is not None
                    else "reference_missing"
                )
        if not _targeted_retention(
            data.form,
            item.field,
            item.evidence,
            text,
            context,
            retaining_previous=False,
            independent_clauses=independent_clauses,
        ):
            return reject("ambiguous_intent")
        return CandidateField(
            **common,
            action="keep",
            value=None,
            evidence=item.evidence,
            intent_kind="retain_current",
            value_source="current",
            status="valid",
        )
    if intent == "retain_previous":
        if item.action != "keep" or not evidence_matches(text, item.evidence):
            return reject("evidence_missing")
        if proof is not None and proof.definite:
            return reject("ambiguous_intent")
        independent_clauses = _verified_independent_date_clauses(
            data, context, previous, item.field, text
        )
        if _targeted_retention(
            data.form,
            item.field,
            item.evidence,
            text,
            context,
            retaining_previous=False,
            require_current_reference=True,
            independent_clauses=independent_clauses,
        ):
            return CandidateField(
                **common,
                action="keep",
                value=None,
                evidence=item.evidence,
                intent_kind="retain_current",
                value_source="current",
                status="valid",
            )
        retained = (
            next((field for field in previous.fields if field.field == item.field), None)
            if previous
            else None
        )
        if retained is None:
            return reject(
                "stale_context" if data.previous_candidate is not None else "reference_missing"
            )
        if _local_retention_proof(item, data, text, context, previous) is not None:
            return _candidate_from_previous(retained, previous)
        reference_only = _reference_only_retention(
            data.form,
            item.field,
            item.evidence,
            text,
            context,
            previous,
            retained,
            independent_clauses=independent_clauses,
        )
        if reference_only is False:
            return reject("ambiguous_intent")
        if reference_only is True:
            return _candidate_from_previous(retained, previous)
        reaffirmed = (
            _verified_reaffirmation(
                data.form, item.field, retained.value, item.evidence, text, context
            )
            if retained.action == "set"
            else None
        )
        if reaffirmed is False or (
            reaffirmed is None
            and not _targeted_retention(
                data.form,
                item.field,
                item.evidence,
                text,
                context,
                retained_value=retained.value,
                independent_clauses=independent_clauses,
            )
        ):
            return reject("ambiguous_intent")
        if (
            reaffirmed is None
            and retained.action == "set"
            and _retention_has_different_value(
                data.form, item.field, retained.value, item.evidence, context
            )
        ):
            return reject("ambiguous_intent")
        return _candidate_from_previous(retained, previous)
    if intent == "ambiguous" or item.action == "uncertain":
        if item.evidence and not evidence_matches(text, item.evidence):
            return reject("evidence_missing")
        if previous is not None and item.action == "uncertain" and item.correction is None:
            retained = next((field for field in previous.fields if field.field == item.field), None)
            if (
                retained is not None
                and _reference_only_retention(
                    data.form,
                    item.field,
                    item.evidence,
                    text,
                    context,
                    previous,
                    retained,
                    independent_clauses=_verified_independent_date_clauses(
                        data, context, previous, item.field, text
                    ),
                )
                is True
            ):
                return _candidate_from_previous(retained, previous)
        return reject("ambiguous_intent", uncertain=True)
    if intent != "explicit_change" or item.action not in ("set", "clear"):
        return reject("ambiguous_intent")

    evidence_ok = evidence_matches(text, item.evidence)
    if not evidence_ok:
        return reject("evidence_missing")
    if proof is not None and proof.definite:
        # A withdrawn operation cannot also authorize a set/clear of its
        # object, regardless of which producer proposed it.
        return reject("ambiguous_intent", uncertain=True)
    if (
        text == data.text
        and item.action == "set"
        and item.field in ("title", "reason")
        and isinstance(item.value, str)
        and not explicit_text_assignment(item.field, item.value, text)
        and any(
            other.fields != {item.field}
            and item.field not in other.fields
            and normalized(item.value).rstrip(".!。").strip() in other.evidence
            for other in (
                withdrawals
                if withdrawals is not None
                else resolve_withdrawals(data, previous, context)
            ).values()
        )
    ):
        # A canonical other-field withdrawal/report is an operation,
        # not an implicit title/reason. Literal explicit assignments are
        # still independently checked by the original text validator.
        if _field_scope(data.form, item.field, text, context) == "other":
            # Reject the unrelated proposal without inventing a new hold.
            # no_mention does not authorize a retain_current release.
            return CandidateField(
                **common,
                action="keep",
                value=None,
                evidence="",
                status="valid",
                intent_kind="no_mention",
            )
        return reject("ambiguous_intent", uncertain=True)
    if text == data.text and _competing_retention_assignment(
        data, item.field, item.value, context, previous
    ):
        return reject("ambiguous_intent", uncertain=True)
    if item.action == "set" and item.field == "vat" and text == data.text:
        # '면세 조건은 앞서 정한 대로 유지'는 값을 언급했더라도 신규
        # 대입이 아니다. 두 해석기가 set으로 반환해도 검증된 이전값만
        # 계승하고, 이전 후보가 없거나 세금 방식이 다르면 보류한다.
        raw = normalized(text)
        scoped = _scoped_retention(data.form, "vat", text, text, context, retaining_previous=True)
        if (
            scoped is not None
            and scoped[0].reference == "previous"
            and "vat" in scoped[0].fields
            and _vat_modes(raw[scoped[0].start : scoped[0].end])
        ):
            retained = (
                next((part for part in previous.fields if part.field == "vat"), None)
                if previous
                else None
            )
            if retained is None:
                return reject(
                    "stale_context" if data.previous_candidate is not None else "reference_missing"
                )
            if (
                item.correction is None
                and _reference_only_retention(
                    data.form, "vat", text, text, context, previous, retained
                )
                is True
            ):
                return _candidate_from_previous(retained, previous)
            return reject("ambiguous_intent", uncertain=True)
    if item.action == "set":
        # 이전 원문 전체를 이번 요청으로 판단하지 않는다. 과거 후보는
        # 실제 채택 근거가 있는 발화에서 문의 여부를 다시 확인한다.
        origin_turns = (
            [text]
            if text == data.text
            else [turn for turn in text.splitlines() if evidence_matches(turn, item.evidence)]
        )
        if any(date_set_is_inquiry(data.form, item.field, turn) for turn in origin_turns):
            return reject("ambiguous_intent", uncertain=True)
        if (
            item.field in ("title", "reason")
            and isinstance(item.value, str)
            and any(
                text_value_is_clear_instruction(item.field, item.value, turn)
                for turn in origin_turns
            )
        ):
            return reject("unsupported_normalization")
        if (
            item.field in ("title", "reason")
            and isinstance(item.value, str)
            and any(text_set_is_inquiry(item.field, item.value, turn) for turn in origin_turns)
        ):
            return reject("ambiguous_intent", uncertain=True)
    if item.action == "set" and item.field in ("title", "reason") and isinstance(item.value, str):
        raw, needle, value = normalized(text), normalized(item.evidence), normalized(item.value)
        labels = field_mentions(data.form, value)
        if (
            len(labels) == 1
            and labels[0].fields == {item.field}
            and labels[0].start == 0
            and labels[0].end == len(value)
            and raw.count(needle) == 1
            and any(
                item.field in scope.fields
                and scope.start <= raw.index(needle)
                and raw.index(needle) + len(needle) <= scope.end
                for scope in retention_scopes(data.form, raw)
            )
        ):
            # 유지 요청의 필드명 자체는 신규 값이 아니다. 이전 후보가
            # 없거나 모델도 같은 오해를 해도 선택 가능한 값으로 내보내지 않는다.
            return reject("ambiguous_intent")
    if (
        source == "rule"
        and item.action == "set"
        and item.field in ("title", "reason")
        and item.correction is None
        and previous is not None
    ):
        retained = next((part for part in previous.fields if part.field == item.field), None)
        raw, needle = normalized(text), normalized(item.evidence)
        scoped = _scoped_retention(
            data.form, item.field, text, text, context, retaining_previous=True
        )
        if (
            retained is not None
            and scoped is not None
            and scoped[1]
            and raw.count(needle) == 1
            and scoped[0].start <= raw.index(needle)
            and raw.index(needle) + len(needle) <= scoped[0].end
            and _reference_only_retention(
                data.form, item.field, text, text, context, previous, retained
            )
            is True
        ):
            # 유지 절 자체를 항목명/사유 값으로 잘못 읽은 규칙 후보는 대안이 아니다.
            return reject("ambiguous_intent")
    if (
        (data.form, item.field) in (("dates", "days"), ("period", "excluded"))
        and date_replacement_requested(item.field, text)
        and item.correction is None
        and (
            item.action != "set"
            or text != data.text
            or item.field not in reviewed_date_edit(data, context, previous, {})
        )
    ):
        # 이전/현재 목록의 교체 검증이 실패한 경우 날짜 열거로 우회하지 않는다.
        # 이전 derived 후보는 별도의 이력 재연 경로에서만 복원한다.
        return reject("correction_unverified", uncertain=True)
    if item.action == "clear":
        if not _targeted_clear(data.form, item.field, item.evidence, text):
            return reject("ambiguous_intent")
    else:
        if (
            item.field in ("days", "excluded")
            and (data.form, item.field) in (("dates", "days"), ("period", "excluded"))
            and item.correction is not None
        ):
            replaced = _verified_date_replacement(item, data, context, previous)
            if replaced is None:
                return reject(
                    "stale_context"
                    if data.previous_candidate and previous is None
                    else "correction_unverified",
                    uncertain=True,
                )
            complete, relation = replaced
            try:
                check_value(data.form, item.field, complete)
                if not defer_period_business and not _business_ok(
                    data.form,
                    item.field,
                    complete,
                    data,
                    context,
                    {**proposed, item.field: complete},
                ):
                    return reject("business_constraint")
            except (ValueError, TypeError, KeyError):
                return reject("business_constraint")
            return CandidateField(
                **common,
                action="set",
                value=complete,
                evidence=item.evidence,
                status="valid",
                intent_kind="explicit_change",
                value_source="derived",
                correction=relation,
            )
        try:
            check_value(data.form, item.field, item.value)
        except (ValueError, TypeError, KeyError):
            return reject("type_mismatch")
        if item.value_source in ("previous", "current"):
            return reject("reference_missing")
        replacement = (
            crew_replacement(normalized(text))
            if data.form == "dates" and item.field == "crew"
            else None
        )
        if replacement is not None:
            raw = normalized(text)
            a, b = replacement.old_span
            c, d = replacement.new_span
            relation = item.correction or CorrectionRelation(
                kind="replace",
                rejected_value=replacement.old,
                rejected_evidence=raw[a:b],
                accepted_evidence=raw[c:d],
            )
            verified_item = item.model_copy(update={"correction": relation})
            if not _verified_crew_replacement(
                verified_item, text, context, previous
            ) or scalar_clause_risk(
                raw,
                "crew",
                [(a, b), (c, d)],
                _SCALAR_UNCERTAIN,
                scopes=_scalar_risk_scopes(_resolved_intent_clauses(data.form, raw, context)),
            ):
                return reject("correction_unverified", uncertain=True)
            return CandidateField(
                **common,
                action="set",
                value=item.value,
                evidence=item.evidence,
                status="valid",
                intent_kind="explicit_change",
                value_source="utterance",
                correction=relation,
            )
        # 참조 지시 자체를 새 제목으로 추출한 규칙 후보는 실제 제목이 아니다.
        if (
            source == "rule"
            and item.field == "title"
            and isinstance(item.value, str)
            and _REAFFIRM_REFERENCE.match(normalized(item.value))
            and _REAFFIRM_KEEP.search(normalized(item.value))
        ):
            return reject("ambiguous_intent")
        if not _grounded(data.form, item.field, item.value, item.evidence, context, text=text):
            return reject("unsupported_normalization")
        if not defer_period_business and not _business_ok(
            data.form, item.field, item.value, data, context, proposed
        ):
            return reject("business_constraint")
    correction = _verified_correction(item, data.form, text, context, previous)
    if (
        item.correction is not None
        and correction is None
        and (source != "llm" or not _independent_same_value_rule(item, data, context, text))
    ):
        return reject("correction_unverified", uncertain=True)
    if item.action == "set" and (
        item.field in ("amount", "crew") or (data.form, item.field) == ("quote", "days")
    ):
        if item.correction is None:
            correction = _scalar_inferred_relation(item, data.form, text, context, previous)
        if not _scalar_set_intent_ok(item, text, correction, data.form, context) and not (
            correction is None and _local_scalar_operation_ok(item, data.form, text, context)
        ):
            # 폐기된 규칙 후보는 올바르게 입증한 모델 정정을 가로막지 않는다.
            if item.correction is None and correction is None:
                groups = _scalar_tokens(item.field, normalized(text))
                if len(groups) == 2 and item.value == groups[0][2]:
                    accepted = item.model_copy(update={"value": groups[1][2], "evidence": text})
                    verified = _scalar_inferred_relation(
                        accepted, data.form, text, context, previous
                    )
                    if verified and _scalar_set_intent_ok(
                        accepted, text, verified, data.form, context
                    ):
                        return reject("ambiguous_intent")
            return reject(
                "correction_unverified" if item.correction is not None else "ambiguous_intent",
                uncertain=True,
            )
    return CandidateField(
        **common,
        action=item.action,
        value=item.value,
        evidence=item.evidence,
        status="valid",
        intent_kind="explicit_change",
        value_source=item.value_source or "utterance",
        correction=correction,
        reason_code=(
            "correction_unverified" if item.correction is not None and correction is None else None
        ),
    )


def review_fields(
    fields: list[FieldIntent],
    source: Source,
    data: AssistRequest,
    context: AssistContext,
    *,
    text: str | None = None,
    previous: PreviousCandidate | None = None,
    withdrawals: Mapping[str, WithdrawalDecision] | None = None,
) -> tuple[dict[str, CandidateField], list[str]]:
    original = text if text is not None else data.text
    if withdrawals is None and original == data.text:
        withdrawals = resolve_withdrawals(data, previous, context)
    proposed: dict[str, Value] = {}
    warnings: list[str] = []
    # 개별 값에 원문 근거가 없는 필드는 다른 필드의 업무 검증에 사용하지 않는다.
    for item in fields if data.form != "period" else ():
        if (
            item.action == "set"
            and item.field in FORM_FIELDS[data.form]
            and evidence_matches(original, item.evidence)
        ):
            try:
                check_value(data.form, item.field, item.value)
                if _grounded(
                    data.form, item.field, item.value, item.evidence, context, text=original
                ):
                    proposed[item.field] = item.value
            except (ValueError, TypeError, KeyError):
                pass
    checked: dict[str, CandidateField] = {}
    for item in fields:
        if item.field not in FORM_FIELDS[data.form]:
            warnings.append("화면에서 쓸 수 없는 후보를 제외했어요.")
            continue
        result = _review_one(
            item,
            source,
            data,
            context,
            proposed,
            text=original,
            previous=previous,
            defer_period_business=data.form == "period",
            withdrawals=withdrawals,
        )
        if item.field in checked:
            checked[item.field] = CandidateField(
                field=item.field,
                action="uncertain",
                value=None,
                evidence="",
                source=source,
                status="uncertain",
                alternatives=[],
                reason_code="ambiguous_intent",
            )
            warnings.append("한 필드의 중복 후보를 확인해 주세요.")
        else:
            checked[item.field] = result
        if result.status == "invalid":
            warnings.append("원문 근거나 업무 조건을 확인할 수 없는 후보가 있어요.")
    if data.form == "period":
        # Wrong-field date offers must not participate in the combined
        # business projection and invalidate independently grounded exclusions.
        exclusion = checked.get("excluded")
        for field, result in list(checked.items()):
            if (
                exclusion is not None
                and exclusion.status == "valid"
                and exclusion.action == "set"
                and field in ("start", "end")
                and result.status == "valid"
                and result.action == "set"
                and result.intent_kind != "retain_previous"
                and _field_scope(data.form, field, original, context) == "other"
                and not _date_assignment_scope_ok(
                    data.form, field, result.value, result.evidence, original, context
                )
            ):
                checked[field] = _hold(field, "ambiguous_intent", result.source).model_copy(
                    update={"status": "invalid"}
                )
        # 먼저 각 후보의 의도·근거·정정을 확인한 뒤 실제 폼을 기준으로
        # 검증된 미적용 후보와 이번 후보만 업무 조합에 투영한다.
        projected = _projected_candidates(data, context, previous, checked, withdrawals=withdrawals)
        _, rejected = resolve_period_candidates(
            data.current, projected, context.today, strict_interior=False
        )
        for field, result in checked.items():
            if (
                result.status == "valid"
                and result.action == "set"
                and result.intent_kind != "retain_previous"
                and field in rejected
                and projected.get(field) == result
            ):
                checked[field] = result.model_copy(
                    update={
                        "action": "uncertain",
                        "value": None,
                        "status": "invalid",
                        "intent_kind": "ambiguous",
                        "value_source": None,
                        "correction": None,
                        "reason_code": "business_constraint",
                    }
                )
                warnings.append("원문 근거나 업무 조건을 확인할 수 없는 후보가 있어요.")
    return checked, list(dict.fromkeys(warnings))


def _date_history_retained(
    item: FieldIntent,
    line: str,
    history: str,
    data: AssistRequest,
    context: AssistContext,
) -> bool:
    """다른 필드의 변경과 실제 날짜 목록 유지 요청을 구분한다."""
    field = item.field
    if _pending_cancellation(data.form, field, line) is not None:
        return False
    if _unscoped_retention_control(data.form, field, line):
        return False
    if _field_scope(data.form, field, line, context) == "other":
        return True
    return (
        _reference_only_retention(
            data.form,
            field,
            line,
            line,
            context,
            PreviousCandidate(request_id="date-history", text=history, fields=[item]),
            item,
        )
        is True
    )


def _reverify_normalized_date_history(
    item: FieldIntent,
    history: str,
    data: AssistRequest,
    context: AssistContext,
    projection: dict[str, Value],
) -> bool:
    """정규화된 전체 목록은 교체 관계 없이도 그 원문과 이후 전이를 재검증한다."""
    if item.action != "set" or not isinstance(item.value, list) or not item.value:
        return False
    lines = [line.strip() for line in history.splitlines() if line.strip()]
    matching = [index for index, line in enumerate(lines) if evidence_matches(line, item.evidence)]
    if not matching:
        return False
    origin = matching[-1]
    raw = normalized(lines[origin])
    if date_replacement_requested(item.field, raw):
        return False
    clauses = _resolved_intent_clauses(data.form, raw, context)
    if any(part.unbound for part in clauses):
        return False
    relevant = [
        raw[part.clause.start : part.clause.end] for part in clauses if item.field in part.fields
    ]
    if not relevant or any(
        _RULE_QUESTION_OR_UNDECIDED.search(polite_execution_risk_text(part))
        or _RULE_NEGATED_ACTION.search(part)
        or _RULE_HISTORICAL.search(part)
        or _PENDING_CANCEL.search(part)
        for part in relevant
    ):
        return False
    # 월 생략 목록의 문맥을 유지하되 다른 필드의 날짜를 증거에 합치지 않는다.
    target_text = " ".join(relevant)
    expected = set(item.value)
    if expected != {
        day.isoformat() for day in date_mentions(item.evidence, context, expand=True)
    } or expected != {day.isoformat() for day in date_mentions(target_text, context, expand=True)}:
        return False
    origin_request = data.model_copy(update={"text": lines[origin], "previous_candidate": None})
    checked = _review_one(
        item,
        "rule",
        origin_request,
        context,
        projection,
        text=lines[origin],
    )
    if checked.status != "valid" or checked.action != "set" or checked.value != item.value:
        return False
    for index in range(origin + 1, len(lines)):
        line = lines[index]
        if date_replacement_requested(item.field, line):
            return False
        if _date_history_retained(item, line, "\n".join(lines[:index]), data, context):
            continue
        if _field_scope(data.form, item.field, line, context) != "none":
            return False
        if _unbound_retention_conflict(data.form, item.field, line, context):
            return False
    return True


def _reverify_derived_history(
    item: FieldIntent,
    history: str,
    form: FormName,
    context: AssistContext,
    data: AssistRequest,
    verified_siblings: dict[str, CandidateField],
) -> bool:
    """이전 대화 원문에서 최초 전체 목록과 정정 체인을 재연한다.

    브라우저가 보낸 derived 값 자체는 믿지 않는다. 최초 날짜 근거와 모든
    교체 연결이 남아 있고, 마지막 결과가 후보 전체 목록과 일치해야 한다.
    """
    relation = item.correction
    if (
        item.value_source != "derived"
        or item.action != "set"
        or (form, item.field) not in (("dates", "days"), ("period", "excluded"))
        or not isinstance(item.value, list)
        or len(history) > 1000
    ):
        return False
    lines = [line.strip() for line in history.splitlines() if line.strip()]
    if not lines:
        return False
    projection = {
        name: checked.value
        for name, checked in verified_siblings.items()
        if checked.status == "valid" and checked.action in ("set", "clear")
    }
    projection[item.field] = item.value
    if relation is None:
        return _reverify_normalized_date_history(item, history, data, context, projection)
    if relation.kind != "replace" or not isinstance(relation.rejected_value, list):
        return False

    def unverified_change(line: str) -> bool:
        # 지나간 발화 중 날짜 목록을 다시 바꾸거나 지우려다 실패한 단계가
        # 있으면, 더 오래된 derived 후보를 다음 턴의 정답으로 되살리지 않는다.
        field_named = (
            any(word in line for word in ("공사 날짜", "공사할 날", "공사일", "작업일", "일정"))
            if item.field == "days"
            else any(
                word in line
                for word in ("제외", "빼는 날", "빼는 날짜", "안 되는 날", "휴무", "쉬는")
            )
        )
        changed_word = re.search(
            r"(?:대신|말고|→|->|지워|지우|삭제|비워|비우|초기화|없애|"
            r"바꾸|바꿔|변경|수정|정정|옮기|옮겨|옮길|미루|미뤄|미룰|"
            r"당기|당겨|당길|해제|다시|재설정|미정|보류|[?？])",
            line,
        )
        dates_mentioned = bool(re.search(r"(?:\d{1,2}\s*일|20\d{2}-\d{2}-\d{2})", line))
        return bool(
            ("대신" in line or "말고" in line or "→" in line or "->" in line)
            and dates_mentioned
            or field_named
            and (changed_word or dates_mentioned)
        )

    def initial_dates(line: str) -> list[str] | None:
        # 독립적으로 입증한 전체 목록만 이전 정정 체인을 새로 시작할 수 있다.
        raw = normalized(line)
        if date_replacement_requested(item.field, raw):
            return None
        clauses = _resolved_intent_clauses(form, raw, context)
        if any(part.unbound for part in clauses):
            return None
        relevant = [
            raw[part.clause.start : part.clause.end]
            for part in clauses
            if item.field in part.fields
        ]
        # 날짜 열거 뒤에 제외 술어가 오는 경우와 휴무 별칭은 목록 전체가
        # 한 대상으로 확인될 때만 합친다. 기간 경계가 섞인 문장은 제외한다.
        if (
            form == "period"
            and (_EXCLUDE_DAY.search(raw) or re.search(r"휴무", raw))
            and not any(
                mention.fields.intersection(("start", "end"))
                for mention in field_mentions(form, raw)
            )
            and not _RULE_DATE_RANGE.search(raw)
            and all(
                part.fields == {"excluded"}
                or re.fullmatch(
                    r"[\d\s월년일/.,-]+(?:은|는|을|를)?",
                    raw[part.clause.start : part.clause.end].strip(),
                )
                or (
                    (
                        _EXCLUDE_DAY.search(raw[part.clause.start : part.clause.end])
                        or re.search(r"휴무", raw[part.clause.start : part.clause.end])
                    )
                    and not part.clause.mentions
                )
                for part in clauses
            )
        ):
            relevant = [raw]
        if not relevant or any(
            _RULE_QUESTION_OR_UNDECIDED.search(polite_execution_risk_text(part))
            or _RULE_NEGATED_ACTION.search(part)
            or _RULE_HISTORICAL.search(part)
            or _PENDING_CANCEL.search(part)
            for part in relevant
        ):
            return None
        mentions = date_mentions(" ".join(relevant), context, expand=True)
        if not mentions or len(mentions) > min(
            context.days_max, 31 if form == "period" else context.days_max
        ):
            return None
        values = [day.isoformat() for day in sorted(mentions)]
        seed = item.model_copy(update={"value": values, "evidence": line, "correction": None})
        origin_request = data.model_copy(update={"text": line, "previous_candidate": None})
        checked = _review_one(
            seed,
            "rule",
            origin_request,
            context,
            {**projection, item.field: values},
            text=line,
            defer_period_business=form == "period",
        )
        return values if checked.status == "valid" and checked.value == values else None

    last_edit = next(
        (
            index
            for index in reversed(range(len(lines)))
            if evidence_matches(lines[index], relation.rejected_evidence)
            and evidence_matches(lines[index], relation.accepted_evidence)
        ),
        None,
    )
    if last_edit is None or not evidence_matches(lines[last_edit], item.evidence):
        return False

    previous_data = data.previous_candidate
    raw_base = data.current.get(item.field)
    baseline = (
        raw_base
        if isinstance(raw_base, list)
        and previous_data is not None
        and previous_data.baselines.get(item.field) == raw_base
        else None
    )

    def replay(seed: list[str] | None, start: int, *, has_prior: bool) -> bool:
        if seed is None:
            return False
        state = list(seed)
        before_last: list[str] | None = None
        seen_edit = False
        for index in range(start, len(lines)):
            line = lines[index]
            reference = _date_edit_reference(item.field, line)
            if reference == "previous":
                bases = [state] if has_prior else []
            elif reference == "current":
                bases = [baseline] if baseline is not None else []
            elif reference == "ambiguous":
                bases = []
            else:
                bases = [baseline] if baseline is not None else []
                if has_prior and state != baseline:
                    bases.append(state)
            matches = [
                (base, changed)
                for base in bases
                if (
                    changed := validated_date_edit(
                        item.field, line, base, context, require_affirmative=True
                    )
                )
                is not None
            ]
            if len(matches) == 1:
                base, changed = matches[0]
                if index == last_edit:
                    before_last = list(base)
                state = changed
                seen_edit = True
                has_prior = True
            elif date_replacement_requested(item.field, line):
                return False
            elif has_prior and _date_history_retained(
                item.model_copy(update={"value": state}),
                line,
                "\n".join(lines[:index]),
                data,
                context,
            ):
                continue
            elif unverified_change(line) or _field_scope(form, item.field, line, context) in (
                "target",
                "unbound",
            ):
                # 실패한 중간 교체가 있으면 오래된 값을 되살리지 않는다.
                return False
        return seen_edit and state == item.value and before_last == relation.rejected_value

    # 마지막으로 독립 검증된 전체 목록 이후의 정정만 재연한다.
    # 그 이후 변경이 미검증이면 더 오래된 목록이나 화면값으로 돌아가지 않는다.
    verified = False
    found_seed = False
    for index in reversed(range(last_edit)):
        seed = initial_dates(lines[index])
        if seed is not None:
            found_seed = True
            verified = replay(seed, index + 1, has_prior=True)
            break
    # 최초 기준이 화면의 목록인 경우: 동일 필드의 원래 baseline이 지금도
    # 그대로일 때에만 검증한다. correction의 rejected_value를 기준으로 믿지 않는다.
    if not found_seed and baseline:
        verified = replay(baseline, 0, has_prior=False)
    if not verified:
        return False
    return _business_ok(form, item.field, item.value, data, context, projection)


def _reverify_scalar_history(
    item: FieldIntent, history: str, data: AssistRequest, context: AssistContext
) -> bool:
    """이전 턴 숫자는 실제 발화 순서대로 같은 검증을 거쳐 복원한다."""
    prior: FieldIntent | None = None
    matched = False
    lines = history.splitlines()
    for index, utterance in enumerate(lines):
        raw = normalized(utterance)
        groups = _scalar_tokens(item.field, raw)
        replacement = crew_replacement(raw) if item.field == "crew" else None
        if not groups and replacement is None:
            if _pending_cancellation(data.form, item.field, raw) is not None:
                prior, matched = None, False
                continue
            if _field_scope(data.form, item.field, raw, context) == "other":
                continue
            # 같은 필드의 유지 요청만 과거 후보를 계승할 수 있다.
            # 다른 필드의 현재값 참조는 이 후보의 참조 출처를 바꾸지 않는다.
            scoped = _scoped_retention(
                data.form,
                item.field,
                raw,
                raw,
                context,
                retaining_previous=True,
            )
            if (
                prior is not None
                and scoped is not None
                and scoped[1]
                and not scalar_clause_risk(
                    raw,
                    item.field,
                    [],
                    _SCALAR_UNCERTAIN,
                    masked=_mask_independent_cancellation(data.form, item.field, raw, context),
                    scopes=_scalar_risk_scopes(_resolved_intent_clauses(data.form, raw, context)),
                )
                and _reference_only_retention(
                    data.form,
                    item.field,
                    raw,
                    raw,
                    context,
                    PreviousCandidate(
                        request_id="scalar-history",
                        text="\n".join(lines[:index]),
                        fields=[prior],
                        origins={item.field: "scalar-origin"},
                    ),
                    prior,
                )
                is True
            ):
                continue
            # 현재 작업일 목록을 기준으로 검증되는 교체라면, 독립된 날짜
            # 보호 절과 이전 인원 유지의 근거를 과거 턴에서도 구분한다.
            # 현재 기준값이 바뀌었거나 인원 유지가 불확실하면 복원하지 않는다.
            if (
                prior is not None
                and (data.form, item.field) == ("dates", "crew")
                and data.previous_candidate is not None
                and isinstance(data.current.get("days"), list)
                and data.previous_candidate.baselines.get("days") == data.current["days"]
                and _date_edit_reference("days", raw) == "current"
            ):
                history_request = data.model_copy(update={"text": raw, "previous_candidate": None})
                independent_clauses = _verified_independent_date_clauses(
                    history_request, context, None, item.field, raw
                )
                if (
                    independent_clauses
                    and _reference_only_retention(
                        data.form,
                        item.field,
                        raw,
                        raw,
                        context,
                        PreviousCandidate(
                            request_id="scalar-history",
                            text="\n".join(lines[:index]),
                            fields=[prior],
                        ),
                        prior,
                        independent_clauses=independent_clauses,
                    )
                    is True
                ):
                    continue
            if (
                (scoped is not None and item.field in scoped[0].fields)
                or scalar_clause_risk(
                    raw,
                    item.field,
                    [],
                    _SCALAR_UNCERTAIN,
                    scopes=_scalar_risk_scopes(_resolved_intent_clauses(data.form, raw, context)),
                )
                or (
                    _target_word(data.form, item.field, raw)
                    and not (
                        _RETAIN.search(raw)
                        and not _RETAIN_NEGATED.search(raw)
                        and not re.search(r"현재|지금|폼\s*값|화면\s*값", raw)
                    )
                )
            ):
                prior, matched = None, False
            continue
        previous = (
            PreviousCandidate(request_id="scalar-history", text=prior.evidence, fields=[prior])
            if prior is not None
            else None
        )
        values = {replacement.new} if replacement else {value for _, _, value in groups}
        valid = []
        for value in values:
            from_item = value == item.value and evidence_matches(raw, item.evidence)
            evidence = item.evidence if from_item else raw
            relation = item.correction if from_item else None
            if replacement is not None and relation is None:
                a, b = replacement.old_span
                c, d = replacement.new_span
                relation = CorrectionRelation(
                    kind="replace",
                    rejected_value=replacement.old,
                    rejected_evidence=raw[a:b],
                    accepted_evidence=raw[c:d],
                )
            candidate = FieldIntent(
                field=item.field, action="set", value=value, evidence=evidence, correction=relation
            )
            checked = _review_one(candidate, "rule", data, context, {}, text=raw, previous=previous)
            if checked.status == "valid" and checked.action == "set":
                valid.append((candidate, from_item))
        if len(valid) == 1:
            prior, matched = valid[0]
        else:
            prior, matched = None, False
    return matched and prior is not None and prior.value == item.value


def review_previous(
    data: AssistRequest, context: AssistContext
) -> tuple[PreviousCandidate | None, dict[str, ReasonCode]]:
    """이전 원문 근거 실패와 기준값 변경을 서로 다른 사유로 남긴다."""
    previous = data.previous_candidate
    if previous is None:
        return None, {}
    rejections: dict[str, ReasonCode] = {}
    eligible: list[FieldIntent] = []
    for item in previous.fields:
        if item.action not in ("set", "clear"):
            continue
        if (
            item.field not in previous.baselines
            or item.field not in data.current
            or previous.baselines[item.field] != data.current[item.field]
        ):
            rejections[item.field] = "stale_context"
        elif _canceled_in_history(data.form, item, previous.text, context):
            rejections[item.field] = "ambiguous_intent"
        elif (
            item.action == "set"
            and item.field in ("title", "reason")
            and isinstance(item.value, str)
            and not text_value_scope_ok(item.field, item.value, previous.text)
        ) or (
            item.action == "set"
            and item.field in ("start", "end", "valid")
            and not (
                _date_assignment_scope_ok(
                    data.form, item.field, item.value, item.evidence, previous.text, context
                )
            )
        ):
            rejections[item.field] = "reference_missing"
        else:
            eligible.append(item)
    # 견적의 days는 날짜 목록이 아니라 정수 일수이다.
    derived = [
        item
        for item in eligible
        if item.value_source == "derived"
        and (data.form, item.field) in (("dates", "days"), ("period", "excluded"))
    ]
    ordinary = [item for item in eligible if item not in derived]
    checked, _ = review_fields(ordinary, "rule", data, context, text=previous.text)
    for item in ordinary:
        if (
            (data.form, item.field) in (("dates", "days"), ("period", "excluded"))
            and item.action == "set"
            and item.correction is None
            and checked[item.field].reason_code == "correction_unverified"
            and "\n" in previous.text
            and _reverify_normalized_date_history(
                item,
                previous.text,
                data,
                context,
                {
                    **data.current,
                    **{
                        name: candidate.value
                        for name, candidate in checked.items()
                        if candidate.status == "valid" and candidate.action == "set"
                    },
                },
            )
        ):
            # 앞선 교체 이력이 있더라도 그 뒤에 독립적으로 확정한 전체
            # 목록은 실제 원문과 후속 변경 부재가 확인된 경우만 유지한다.
            checked[item.field] = CandidateField(
                field=item.field,
                action="set",
                value=item.value,
                evidence=item.evidence,
                source="rule",
                status="valid",
                alternatives=[],
                value_source="utterance",
            )
        if (
            item.field == "vat"
            and item.action == "set"
            and checked[item.field].status == "valid"
            and not _reverify_vat_history(item, checked[item.field], previous.text, data, context)
        ):
            checked[item.field] = _hold(item.field, "ambiguous_intent", "rule")
        if (
            (item.field in ("amount", "crew") or (data.form, item.field) == ("quote", "days"))
            and item.action == "set"
            and "\n" in previous.text
        ):
            if _reverify_scalar_history(item, previous.text, data, context):
                checked[item.field] = CandidateField(
                    field=item.field,
                    action="set",
                    value=item.value,
                    evidence=item.evidence,
                    source="rule",
                    status="valid",
                    alternatives=[],
                    value_source="utterance",
                )
            elif checked[item.field].status == "valid":
                checked[item.field] = _hold(item.field, "reference_missing", "rule")
    for item in derived:
        if _reverify_derived_history(item, previous.text, data.form, context, data, checked):
            checked[item.field] = CandidateField(
                field=item.field,
                action="set",
                value=item.value,
                evidence=item.evidence,
                source="rule",
                status="valid",
                alternatives=[],
                value_source="derived",
                correction=item.correction,
            )
        else:
            rejections[item.field] = "reference_missing"
    for field, item in checked.items():
        if item.status != "valid":
            rejections[field] = item.reason_code or "reference_missing"
    valid = [
        FieldIntent(
            field=item.field,
            action=item.action,
            value=item.value,
            evidence=item.evidence,
            intent_kind="explicit_change",
            value_source=item.value_source if item.value_source == "derived" else "utterance",
            correction=item.correction if item.value_source == "derived" else None,
        )
        for item in checked.values()
        if item.status == "valid" and item.action in ("set", "clear")
    ]
    if not valid:
        return None, rejections
    return PreviousCandidate(
        request_id=previous.request_id,
        text=previous.text,
        fields=valid,
        sources={item.field: previous.sources.get(item.field, "rule") for item in valid},
        origins={
            item.field: previous.origins.get(item.field, previous.request_id) for item in valid
        },
        baselines={item.field: previous.baselines[item.field] for item in valid},
    ), rejections


def verified_previous(data: AssistRequest, context: AssistContext) -> PreviousCandidate | None:
    return review_previous(data, context)[0]


def _not_action(item: CandidateField | None) -> bool:
    return item is None or (
        item.action == "keep"
        and item.status == "valid"
        and item.intent_kind in (None, "no_mention")
    )


def _candidate_from_previous(item: FieldIntent, previous: PreviousCandidate) -> CandidateField:
    return CandidateField(
        field=item.field,
        action=item.action,
        value=item.value,
        evidence=item.evidence,
        source=previous.sources.get(item.field, "rule"),
        origin_request_id=previous.origins.get(item.field, previous.request_id),
        status="valid",
        alternatives=[],
        intent_kind="retain_previous",
        value_source="derived" if item.value_source == "derived" else "previous",
        correction=item.correction if item.value_source == "derived" else None,
    )


def _explicit_period_end(text: str, context: AssistContext) -> tuple[str, date] | None:
    """종료일 별칭과 단일 날짜를 연결한 완전한 긍정 지정만 인정한다."""
    raw = normalized(text)
    mentions = field_mentions("period", raw)
    tokens = date_tokens(raw, context)
    if (
        len(mentions) != 1
        or mentions[0].fields != {"end"}
        or mentions[0].start != 0
        or len(tokens) != 1
        or _RETENTION_CONDITION.search(raw)
        or _RULE_NEGATED_ACTION.search(raw)
        or _RULE_HISTORICAL.search(raw)
    ):
        return None
    start, end, day = tokens[0]
    if not re.fullmatch(r"\s*[은는을를이가만]?\s*", raw[mentions[0].end : start]):
        return None
    if not re.fullmatch(
        r"\s*(?:까지)?(?:으로|로)?\s*(?:새로\s*|다시\s*)?"
        r"(?:(?:정해|잡아)\s*(?:주세요|줘요|주십시오)|정(?:하겠습니다|합니다|할게요)|잡(?:겠습니다|습니다|을게요)"
        r"|(?:바꿔|고쳐)\s*(?:주세요|줘요|주십시오)|바꾸겠습니다|고치겠습니다"
        r"|(?:확정|설정|지정|변경|조정)(?:\s*부탁(?:합니다|드립니다)?|"
        r"\s*(?:합니다|하겠습니다|할게요|해\s*(?:주세요|줘요|주십시오))))[.!]?",
        raw[end:],
    ):
        return None
    return text.strip(), day


def _independent_date_rule_proof(
    data: AssistRequest, context: AssistContext, candidate: CandidateField | None
) -> bool:
    """유효 규칙의 값을 독립 원문 절에서 다시 증명한다.

    일반 규칙 추출이나 모델의 미언급보다 강한 계약이다. 모델이
    불확실하다고 반환한 경우에도 실제로 완결된 대입 지시가 있어야
    한다. 값 검증, 업무 제약, 최종 조합 검사는 기존 경로를 유지한다.
    """
    if (
        candidate is None
        or candidate.status != "valid"
        or candidate.action != "set"
        or candidate.intent_kind != "explicit_change"
        or candidate.value_source != "utterance"
        or candidate.origin_request_id != data.request_id
        or candidate.correction is not None
        or candidate.field not in ("start", "end", "valid")
        or not isinstance(candidate.value, str)
        or not evidence_matches(data.text, candidate.evidence)
    ):
        return False
    field = candidate.field
    raw = normalized_scope(data.text)
    resolved = _resolved_intent_clauses(data.form, raw, context)
    targets = [part for part in resolved if field in part.fields]
    if (
        len(targets) != 1
        or any(part.unbound for part in resolved)
        or targets[0].fields != {field}
        or date_set_is_inquiry(data.form, field, raw)
        or _single_date_competing_values(data.form, field, raw, context)
    ):
        return False
    part = targets[0].clause
    span = raw[part.start : part.end].strip().rstrip(",; ")
    mentions = field_mentions(data.form, span)
    tokens = date_tokens(span, context, allow_bare=field != "valid")
    if (
        part.retentions
        or len(mentions) != 1
        or mentions[0].fields != {field}
        or mentions[0].start != 0
        or len(tokens) != 1
        or _RETENTION_CONDITION.search(span)
        or _RULE_NEGATED_ACTION.search(span)
        or _RULE_HISTORICAL.search(span)
        or _RULE_QUESTION_OR_UNDECIDED.search(polite_execution_risk_text(span))
    ):
        return False
    start, end, day = tokens[0]
    if day.isoformat() != candidate.value or not re.fullmatch(
        r"\s*[은는을를이가만]?\s*", span[mentions[0].end : start]
    ):
        return False
    tail = span[end:].strip()
    if field == "end":
        tail = re.sub(r"^까지\s*", "", tail)
    return (
        affirmative_assignment_suffix(tail)
        and _grounded(data.form, field, candidate.value, span, context, text=raw)
        and _date_assignment_scope_ok(
            data.form, field, candidate.value, candidate.evidence, raw, context
        )
    )


def _verified_period_end(
    data: AssistRequest, context: AssistContext, previous: PreviousCandidate | None
) -> CandidateField | None:
    """명시적 마감 허용이나 검증된 이전 종료일 유지에만 독립 근거를 부여한다."""
    if data.form != "period":
        return None
    deadline = affirmative_end_deadline(data.text, context) or _explicit_period_end(
        data.text, context
    )
    if deadline is None:
        # 명시적인 다른 필드의 이전/현재 참조와 한 개의 종료일 지정은
        # 분리한다. 참조가 실제로 존재하지 않아도 그 참조만 보류한다.
        # 전역 조건, 같은 필드의 경쟁 지시, 불완전한 독립절은 복구하지 않는다.
        raw = normalized_scope(data.text)
        parts = _resolved_intent_clauses("period", raw, context)
        if (
            len(parts) > 1
            and not any(part.unbound for part in parts)
            and not _RETENTION_CONDITION.search(raw)
            and sum("end" in part.fields for part in parts) == 1
        ):
            end_part = next(part for part in parts if "end" in part.fields)
            if end_part.fields == {"end"} and all(
                part is end_part
                or (
                    bool(part.fields)
                    and "end" not in part.fields
                    and len(part.clause.retentions) == 1
                    and (scope := part.clause.retentions[0]).compatible
                    and scope.reference in ("current", "previous")
                    and scope.fields == part.fields
                    and not raw[part.clause.start : scope.start].strip()
                    and not raw[scope.end : part.clause.end].strip(" ,;.!。")
                )
                for part in parts
            ):
                deadline = _explicit_period_end(
                    raw[end_part.clause.start : end_part.clause.end], context
                )
    if deadline is not None:
        evidence, day = deadline
        value = day.isoformat()
        projection = _business_projection(data, context, previous, {})
        projection["end"] = value
        if not _grounded("period", "end", value, evidence, context) or not _business_ok(
            "period", "end", value, data, context, projection
        ):
            return None
        return CandidateField(
            field="end",
            action="set",
            value=value,
            evidence=evidence,
            source="rule",
            status="valid",
            alternatives=[],
            intent_kind="explicit_change",
            value_source="utterance",
            origin_request_id=data.request_id,
        )
    reference = explicit_prior_end_reference(data.text, context)
    if previous is None or reference is None:
        return None
    retained = next(
        (item for item in previous.fields if item.field == "end" and item.action == "set"), None
    )
    if (
        retained is None
        or not _targeted_retention("period", "end", reference, data.text, context)
        or _retention_has_different_value("period", "end", retained.value, reference, context)
    ):
        return None
    first_deadline = affirmative_end_deadline(retained.evidence, context)
    original = _candidate_from_previous(retained, previous)
    # 과거의 단순 날짜 언급이나 추측을 '이전 제안'으로 위장해 되살리지 않는다.
    if not (
        first_deadline is not None
        and first_deadline[1].isoformat() == retained.value
        or any(
            _rule_field_proof("period", original, clause, context)
            for clause in _rule_local_clauses(
                "period", "end", retained.value, previous.text, context
            )
        )
    ):
        return None
    return original


def _merge_pair(rule: CandidateField | None, llm: CandidateField | None) -> CandidateField | None:
    if llm is None:
        return rule
    if llm.status in ("invalid", "uncertain"):
        # 새 정정이 실패하거나 사용자가 미정이라면 규칙의 옛 숫자로 대체하지 않는다.
        return llm
    if llm.intent_kind == "retain_current" and llm.status == "valid":
        return llm
    if _not_action(llm):
        # 모델의 미언급은 규칙이 실제 값을 추출한 것에 대한 반박이 아니다.
        # 다만 근거 검증에 실패한 규칙은 '미언급'을 덮어쓸 권한이 없다.
        return rule if rule is not None and rule.status == "valid" else llm
    if rule is None or _not_action(rule) or rule.status == "invalid":
        return llm
    if rule.status == "uncertain":
        return CandidateField(
            field=rule.field,
            action="uncertain",
            value=None,
            evidence="",
            source="llm",
            status="uncertain",
            alternatives=[],
            intent_kind="ambiguous",
            reason_code="ambiguous_intent",
        )
    if rule.status == llm.status == "valid":
        if rule.action == llm.action and rule.value == llm.value:
            return llm
        # 검증한 정정에서 폐기한 값과 규칙 후보가 정확히 일치하면 채택 값만 남긴다.
        if (
            llm.correction is not None
            and llm.correction.kind == "replace"
            and rule.action == "set"
            and rule.value == llm.correction.rejected_value
        ):
            return llm
        if (
            rule.correction is not None
            and rule.correction.kind == "replace"
            and llm.action == "set"
            and llm.value == rule.correction.rejected_value
        ):
            return rule
        return CandidateField(
            field=rule.field,
            action="uncertain",
            value=None,
            evidence="",
            source="llm",
            status="conflict",
            intent_kind="ambiguous",
            reason_code="ambiguous_intent",
            alternatives=[
                Alternative(
                    action=rule.action, value=rule.value, evidence=rule.evidence, source="rule"
                ),
                Alternative(
                    action=llm.action, value=llm.value, evidence=llm.evidence, source="llm"
                ),
            ],
        )
    return llm


def _hold(field: str, reason: ReasonCode, source: Source = "rule") -> CandidateField:
    return CandidateField(
        field=field,
        action="uncertain",
        value=None,
        evidence="",
        source=source,
        status="uncertain",
        alternatives=[],
        intent_kind="ambiguous",
        reason_code=reason,
    )


def _final_state(data: AssistRequest, fields: list[CandidateField]) -> dict[str, Value]:
    combined = dict(data.current)
    for item in fields:
        if item.status == "valid" and item.action in ("set", "clear"):
            combined[item.field] = item.value if item.action == "set" else None
    return combined


def _combined_period_ok(state: dict[str, Value], context: AssistContext) -> bool:
    return not period_violations(state, context.today)


def finalize_candidates(
    data: AssistRequest, context: AssistContext, fields: list[CandidateField]
) -> list[CandidateField]:
    """규칙·모델·이전값을 합친 후 실제 폼 조합을 검증한다."""
    active = {
        item.field: item
        for item in fields
        if item.status == "valid" and item.action in ("set", "clear")
    }
    state = _final_state(data, fields)
    failures: set[str] = set()
    # 개별 검증 뒤 조합이 달라져 달력 조건을 깨뜨릴 수 있으므로 다시 검사한다.
    if data.form == "period":
        # 보류한 후보는 실제 현재 값으로 돌아간다. 이 값이 다른 후보를
        # 무효화할 수 있으므로 남은 후보를 다시 결합해 확인한다.
        active, failures = resolve_period_candidates(data.current, active, context.today)
        state = period_state(data.current, active)
        # 한 공급자에서 유효했던 충돌 대안도 최종 조합에서는 유효하지 않을 수 있다.
        # 남은 선택지가 하나여도 사용자의 확정값으로 임의 채택하지 않는다.
        for item in fields:
            if item.status == "conflict" and any(
                not _combined_period_ok({**state, item.field: alternative.value}, context)
                for alternative in item.alternatives
            ):
                failures.add(item.field)
    else:
        proposed = {item.field: item.value for item in active.values() if item.action == "set"}
        for field, item in active.items():
            if item.action == "set" and not _business_ok(
                data.form, field, item.value, data, context, proposed
            ):
                failures.add(field)
    return [
        _hold(item.field, "combined_constraint", item.source) if item.field in failures else item
        for item in fields
    ]


def _verified_implicit_retention(
    data: AssistRequest,
    context: AssistContext,
    previous: PreviousCandidate,
    retained: FieldIntent,
    *,
    independent_clauses: frozenset[str] = frozenset(),
) -> bool:
    field = retained.field
    explicit = _reference_only_retention(
        data.form,
        field,
        data.text,
        data.text,
        context,
        previous,
        retained,
        independent_clauses=independent_clauses,
    )
    if explicit is not None:
        return explicit
    # 모델이 유지 필드를 생략한 경우에도 명시적 이전 후보 지시와 완전한
    # 수치 일치, 같은 필드의 경쟁 지시 부재를 모두 확인한다.
    if field not in ("amount", "crew", "vat") or retained.action != "set":
        return False
    clauses = [
        clause.strip()
        for clause in _PENDING_CLAUSE.split(normalized(data.text))
        if re.search(r"이전|아까|앞서|방금|직전|제안|변경안", clause) and _RETAIN.search(clause)
    ]
    return (
        sum(
            _targeted_retention(
                data.form,
                field,
                clause,
                data.text,
                context,
                retained_value=retained.value,
                independent_clauses=independent_clauses,
            )
            and not _retention_has_different_value(
                data.form, field, retained.value, clause, context
            )
            and _verified_reaffirmation(
                data.form, field, retained.value, clause, data.text, context
            )
            is not False
            for clause in clauses
        )
        == 1
    )


def _verified_group_retention(
    data: AssistRequest,
    context: AssistContext,
    previous: PreviousCandidate,
    retained: FieldIntent,
    *,
    reason: ReasonCode | None,
) -> bool:
    """실제 공동 지시와 확인된 이력으로만 이전 필드 상태를 복구한다.

    모델이 공동 주어를 단독 문장으로 다시 쓴 근거는 신뢰하지 않는다.
    원문에서 독립적으로 발견한 공동 유지 범위와 기존 필드별 증명만
    사용하고, 실제 불명확한 요청/취소/경쟁/최초 참조 실패는 보류한다.
    """
    if reason not in ("ambiguous_intent", "evidence_missing"):
        return False
    raw = normalized(data.text)
    scoped = _scoped_retention(
        data.form, retained.field, raw, raw, context, retaining_previous=True
    )
    if scoped is None or not scoped[1] or scoped[0].reference != "previous":
        return False
    scope = scoped[0]
    # 서로 다른 참조 절에 공동 술어가 붙은 기존 병렬 구조는 이미
    # 독립 대상/참조를 검증한다. 직접 명명한 그룹은 새 구조 증명 사용.
    if not scope.shared and scope not in explicit_group_retention_scopes(
        data.form, raw, retained.field
    ):
        return False
    return (
        _reference_only_retention(data.form, retained.field, raw, raw, context, previous, retained)
        is True
    )


def _pending_transition(
    data: AssistRequest,
    context: AssistContext,
    field: str,
    chosen: CandidateField | None,
    previous: PreviousCandidate | None,
    previous_rejected: dict[str, ReasonCode],
    source: Source,
    *,
    allow_group_recovery: bool = True,
    withdrawals: Mapping[str, WithdrawalDecision] | None = None,
) -> CandidateField | None:
    """검증한 필드 결과와 이전 후보 사이의 유지·철회·보류 전이를 정한다."""
    retained = (
        next((part for part in previous.fields if part.field == field), None) if previous else None
    )
    if _not_action(chosen) or (
        chosen.status in ("invalid", "uncertain") or chosen.intent_kind == "retain_previous"
    ):
        cancellation = (
            withdrawals if withdrawals is not None else resolve_withdrawals(data, previous, context)
        ).get(field)
        if cancellation is not None:
            if cancellation.definite:
                return _withdrawal_candidate(data, cancellation, "rule")
            if retained is not None or _mentions_field(data.form, field, data.text, context):
                return _hold(field, "ambiguous_intent", "rule")
    if (
        allow_group_recovery
        and previous is not None
        and retained is not None
        and chosen is not None
        and chosen.status in ("invalid", "uncertain")
        and _verified_group_retention(data, context, previous, retained, reason=chosen.reason_code)
    ):
        return _candidate_from_previous(retained, previous)
    if not _not_action(chosen):
        return chosen
    if retained is not None:
        independent_clauses = _verified_independent_date_clauses(
            data, context, previous, field, data.text
        )
        if _targeted_retention(
            data.form,
            field,
            data.text,
            data.text,
            context,
            retaining_previous=False,
            require_current_reference=True,
            independent_clauses=independent_clauses,
        ):
            # 브라우저의 보류 해제는 해당 필드의 검증된 유지 절에
            # 결합된다. 여러 독립 지시 전체를 keep 근거로 전달하면
            # 같은 절의 증명과 비교할 수 없다. 이미 검증된 scope
            # 범위만 사용하며 미지원 문장은 기존 근거를 유지한다.
            scoped = _scoped_retention(
                data.form,
                field,
                data.text,
                data.text,
                context,
                retaining_previous=False,
                require_current_reference=True,
                independent_clauses=independent_clauses,
            )
            evidence = (
                normalized(data.text)[scoped[0].start : scoped[0].end]
                if scoped is not None and scoped[1]
                else data.text
            )
            return CandidateField(
                field=field,
                action="keep",
                value=None,
                evidence=evidence,
                source="rule",
                status="valid",
                alternatives=[],
                intent_kind="retain_current",
                value_source="current",
                origin_request_id=data.request_id,
            )
        if _unscoped_retention_control(
            data.form, field, data.text, independent_clauses=independent_clauses
        ):
            return _hold(field, "ambiguous_intent", "rule")
        if _verified_implicit_retention(
            data, context, previous, retained, independent_clauses=independent_clauses
        ):
            return _candidate_from_previous(retained, previous)
        scope = _field_scope(data.form, field, data.text, context)
        if scope == "other":
            return _candidate_from_previous(retained, previous)
        if scope in ("target", "unbound") or _mentions_field(data.form, field, data.text, context):
            return _hold(field, "ambiguous_intent", source)
        return _candidate_from_previous(retained, previous)
    if data.previous_candidate and any(
        item.field == field and item.action in ("set", "clear")
        for item in data.previous_candidate.fields
    ):
        return _hold(field, previous_rejected.get(field, "reference_missing"), source)
    return chosen


def _projected_candidates(
    data: AssistRequest,
    context: AssistContext,
    previous: PreviousCandidate | None,
    checked: dict[str, CandidateField],
    *,
    withdrawals: Mapping[str, WithdrawalDecision] | None = None,
) -> dict[str, CandidateField]:
    """의미/상태 전이를 끝낸 후보만 업무 조합에 전달한다.

    값만 투영하면 이후 제약 위반이 새 변경인지 검증된 계승인지
    구별할 수 없다. 업무 검증까지 원래 후보 객체의 근거를 유지한다.
    """
    projection: dict[str, CandidateField] = {}
    for field in FORM_FIELDS[data.form]:
        chosen = checked.get(field)
        if previous is not None:
            chosen = _pending_transition(
                data, context, field, chosen, previous, {}, "rule", withdrawals=withdrawals
            )
        if chosen is not None and chosen.status == "valid" and chosen.action in ("set", "clear"):
            projection[field] = chosen
    return projection


def _business_projection(
    data: AssistRequest,
    context: AssistContext,
    previous: PreviousCandidate | None,
    checked: dict[str, CandidateField],
) -> dict[str, Value]:
    return {
        field: item.value
        for field, item in _projected_candidates(data, context, previous, checked).items()
    }


def merge_candidates(
    data: AssistRequest,
    context: AssistContext,
    rule: dict[str, CandidateField],
    model: dict[str, CandidateField] | None,
    previous: PreviousCandidate | None,
    *,
    date_edits: dict[str, CandidateField] | None = None,
    decisions: dict[str, dict[str, str | None]] | None = None,
    merged_out: list[CandidateField] | None = None,
    previous_rejected: dict[str, ReasonCode] | None = None,
    withdrawals: Mapping[str, WithdrawalDecision] | None = None,
) -> list[CandidateField]:
    if previous_rejected is None:
        _, previous_rejected = review_previous(data, context)
    if withdrawals is None:
        withdrawals = resolve_withdrawals(data, previous, context)
    if date_edits is None:
        date_edits = reviewed_date_edit(data, context, previous, previous_rejected)
    prior = (
        {item.field: _candidate_from_previous(item, previous) for item in previous.fields}
        if previous
        else {}
    )
    independent_end = _verified_period_end(data, context, previous)
    conditional_fields = _conditional_assignment_fields(data.form, data.text, context)
    unresolved_fields = _unresolved_owned_fields(data, context, previous)
    end_rule = rule.get("end")
    end_model = model.get("end") if model is not None else None
    end_assignment = (
        independent_end is not None
        and independent_end.intent_kind == "explicit_change"
        and (
            end_rule is None
            or end_rule.status != "valid"
            or _not_action(end_rule)
            or (end_rule.action, end_rule.value) == (independent_end.action, independent_end.value)
        )
        and (
            end_model is None
            or _not_action(end_model)
            or (end_model.status == "uncertain" and end_model.reason_code == "ambiguous_intent")
            or (
                end_model.status == "valid"
                and (end_model.action, end_model.value)
                == (independent_end.action, independent_end.value)
            )
        )
    )
    out = []
    for field in FORM_FIELDS[data.form]:
        r = rule.get(field)
        m = model.get(field) if model is not None else None
        rule_scope_invalid = False
        if field in ("title", "reason"):
            # 원문에 있는 문자열이어도 실제 다른 필드의 값/참조라면
            # 병합 대안으로 노출하지 않는다. 유효한 독립 후보는 보존한다.
            def checked_text(item: CandidateField | None, field: str) -> CandidateField | None:
                if (
                    item is not None
                    and item.status == "valid"
                    and item.action == "set"
                    and item.intent_kind != "retain_previous"
                    and isinstance(item.value, str)
                    and not text_value_scope_ok(field, item.value, data.text)
                ):
                    return _hold(field, "ambiguous_intent", item.source).model_copy(
                        update={"status": "invalid"}
                    )
                return item

            scoped_rule = checked_text(r, field)
            rule_scope_invalid = scoped_rule is not r
            # 미언급 모델과 병합할 때는 기존 독립 필드 판정에 원래 규칙
            # 후보를 전달하되, 대상 검증에 실패한 후보 자체는 채택하지 않는다.
            if m is None or not _not_action(m):
                r = scoped_rule
            m = checked_text(m, field)
        chosen = _merge_pair(r, m) if model is not None else r
        if (
            r is not None
            and r.status == "uncertain"
            and r.reason_code == "ambiguous_intent"
            and m is not None
            and m.status == "valid"
            and m.intent_kind == "explicit_change"
            and field in ("amount", "crew", "days")
            and _local_scalar_operation_ok(m, data.form, data.text, context)
        ):
            chosen = m
        if (
            data.form == "period"
            and field in ("start", "end")
            and m is not None
            and m.action == "uncertain"
            and m.status == "uncertain"
            and m.reason_code == "ambiguous_intent"
            and _independent_date_rule_proof(data, context, r)
        ):
            # 모호함 자체는 값에 대한 반증이 아니다. 독립적으로 완결된
            # 원문 대입을 입증한 규칙만 보존하고, 다른 검증 실패 사유나
            # 실제 경쟁 후보는 이 경로에서 승격하지 않는다.
            chosen = r
        if (
            field in ("title", "reason")
            and r is not None
            and r.status == "valid"
            and r.action == "set"
            and r.intent_kind != "retain_previous"
            and isinstance(r.value, str)
            and m is not None
            and m.status == "invalid"
            and m.reason_code == "unsupported_normalization"
            and explicit_text_assignment(field, r.value, data.text)
            and text_value_scope_ok(field, r.value, data.text)
            and _rule_field_proof(data.form, r, data.text, context)
        ):
            # 모델의 값 근거가 틀려도 독립적으로 완결된 동일 필드의
            # 대입이 입증되면 유효 규칙을 보존한다. 문의·취소·모호한
            # 모델 의도나 실제 경쟁 후보는 이 경로에서 승격하지 않는다.
            chosen = r
        if field == "end" and independent_end is not None:
            # 모델의 모호함을 날짜 근거가 독립 검증된 이 두 표현에 한해서만 복구한다.
            model_is_ambiguous = (
                m is not None
                and m.action == "uncertain"
                and m.status == "uncertain"
                and m.reason_code == "ambiguous_intent"
            )
            model_did_not_mention = m is None or _not_action(m)
            conflicting_rule = (
                r is not None
                and r.status == "valid"
                and r.action in ("set", "clear")
                and (r.action != independent_end.action or r.value != independent_end.value)
            )
            if (
                (model is None or model_is_ambiguous or model_did_not_mention)
                and not conflicting_rule
                and (chosen is None or chosen.status == "uncertain" or _not_action(chosen))
            ):
                chosen = independent_end
        if model is not None and m is not None and _not_action(m) and r is not None:
            if r.status == "valid" and r.action in ("set", "clear"):
                # 원문 숫자가 있다는 사실만으로 대상 필드가 실제로 변경됐다고 단정하지 않는다.
                independent = _field_scope(data.form, field, data.text, context) == "other" and (
                    (field != "end" and end_assignment)
                    or any(
                        other.field != field
                        and (
                            other.status == "valid"
                            and (
                                other.action in ("set", "clear")
                                or other.intent_kind == "retain_current"
                            )
                            or field in prior
                            and other.status == "uncertain"
                            and bool(other.evidence)
                            and evidence_matches(data.text, other.evidence)
                            and _field_scope(data.form, other.field, other.evidence, context)
                            == "target"
                            or field in prior
                            and other.status == "invalid"
                            and other.reason_code == "business_constraint"
                            and evidence_matches(data.text, other.evidence)
                            and _field_scope(data.form, other.field, other.evidence, context)
                            == "target"
                            or field in prior
                            and (cancel := withdrawals.get(other.field)) is not None
                            and cancel.definite
                        )
                        and other.intent_kind != "retain_previous"
                        and _field_scope(data.form, other.field, data.text, context) == "target"
                        for other in model.values()
                    )
                )
                chosen = (
                    independent_end
                    if field == "end" and end_assignment
                    else r
                    if not rule_scope_invalid
                    and _rule_field_proof(data.form, r, data.text, context)
                    else m
                    if independent
                    else _hold(field, "ambiguous_intent", "rule")
                )
            elif r.status in ("invalid", "uncertain"):
                chosen = (
                    m
                    if not rule_scope_invalid
                    and (
                        _field_scope(data.form, field, data.text, context) == "other"
                        or not _mentions_field(data.form, field, data.text, context)
                    )
                    else _hold(field, r.reason_code or "ambiguous_intent", "rule")
                )
        # 같은 상태 전이가 생산자별 업무 투영과 병합 후에도 작동한다.
        # 실제로 유효한 반대 지시가 있으면 모호함 복구에 사용하지 않는다.
        prior_item = prior.get(field)
        group_compatible = prior_item is not None and all(
            item is None
            or _not_action(item)
            or (
                item.status in ("invalid", "uncertain")
                and item.reason_code
                in ("ambiguous_intent", "evidence_missing", "unsupported_normalization")
            )
            or (
                item.status == "valid"
                and item.intent_kind == "retain_previous"
                and (item.action, item.value) == (prior_item.action, prior_item.value)
            )
            for item in (r, m)
        )
        chosen = _pending_transition(
            data,
            context,
            field,
            chosen,
            previous,
            previous_rejected,
            "llm" if model else "rule",
            allow_group_recovery=group_compatible,
            withdrawals=withdrawals,
        )
        if chosen is None:
            chosen = CandidateField(
                field=field,
                action="keep",
                value=None,
                evidence="",
                source="llm" if model is not None else "rule",
                status="valid",
                alternatives=[],
                intent_kind="no_mention",
                origin_request_id=data.request_id,
            )
        if field in conditional_fields | unresolved_fields and _not_action(chosen):
            # 값 생산자가 안전하게 값을 생략해도 실제 조건부 요청은
            # 미언급과 다르다. 검증된 명시적 유지/철회는 건드리지 않는다.
            chosen = _hold(field, "ambiguous_intent", chosen.source)
        # 검증한 전체 목록은 모델의 모호함·부분 배열 때문에 잃지 않는다.
        # 독립적인 명시적 반대 지시가 충돌하면 확인을 요구한다.
        edit = date_edits.get(field)
        replacement = (data.form, field) in (
            ("dates", "days"),
            ("period", "excluded"),
        ) and date_replacement_requested(field, data.text)
        if (
            replacement
            and edit is None
            and not (
                chosen.status == "valid"
                and chosen.action == "keep"
                and chosen.intent_kind == "retain_current"
            )
        ):
            # 미언급·일반 열거·충돌 대안으로 실패한 교체를 다시 살리지 않는다.
            chosen = _hold(
                field, previous_rejected.get(field, "correction_unverified"), chosen.source
            )
        if edit is not None:
            before = set(edit.correction.rejected_value)
            complete = set(edit.value)
            added = complete - before
            removed = before - complete
            if chosen.status == "conflict":
                # 전체 목록을 독립 검증했고 모델의 정정 관계도 일치할 때만,
                # 규칙 파서가 열거한 교체 전후의 부분 목록을 흡수한다.
                # 다른 날짜나 역방향 교체를 포함한 충돌은 계속 보류한다.
                same_edit = (
                    r is not None
                    and m is not None
                    and r.status == m.status == "valid"
                    and r.action == m.action == "set"
                    and r.correction is None
                    and isinstance(r.value, list)
                    and set(r.value) in (added, added | removed)
                    and m.intent_kind == "explicit_change"
                    and m.value_source == "derived"
                    and m.value == edit.value
                    and m.correction is not None
                    and m.correction.kind == "replace"
                    and m.correction.rejected_value == edit.correction.rejected_value
                )
                chosen = edit if same_edit else _hold(field, "ambiguous_intent", "rule")
            elif chosen.status in ("uncertain", "invalid"):
                chosen = edit
            elif chosen.action == "keep":
                chosen = (
                    _hold(field, "ambiguous_intent", "rule")
                    if chosen.intent_kind == "retain_current"
                    else edit
                )
            elif chosen.action == "set":
                # 모델이 교체된 하루만 반환해도 나머지 확정 날짜를 지킨다.
                # 규칙 파서의 옛날·새날 열거는 교체 근거가 독립 검증된
                # 경우에만 완전한 목록으로 치환한다.
                supplied = set(chosen.value) if isinstance(chosen.value, list) else set()
                partial = supplied == added or supplied == added | removed
                chosen = (
                    edit
                    if supplied == complete or partial
                    else _hold(field, "ambiguous_intent", "rule")
                )
            else:
                chosen = _hold(field, "ambiguous_intent", "rule")
        if (
            date_set_is_inquiry(data.form, field, data.text)
            and chosen.status in ("valid", "conflict")
            and not (chosen.action == "keep" and chosen.intent_kind == "retain_current")
        ):
            # 독립 날짜 교체/계승도 문의 자체를 확정 후보로 바꾸지 않는다.
            chosen = _hold(field, "ambiguous_intent", chosen.source)
        if (
            field in ("start", "end", "valid")
            and chosen.intent_kind != "retain_previous"
            and (
                (
                    chosen.status == "valid"
                    and chosen.action == "set"
                    and not (
                        (
                            _date_assignment_scope_ok(
                                data.form, field, chosen.value, chosen.evidence, data.text, context
                            )
                            or (
                                field == "end"
                                and independent_end is not None
                                and independent_end.action == "set"
                                and independent_end.value == chosen.value
                            )
                        )
                        and (
                            chosen.correction is not None
                            or not _single_date_competing_values(
                                data.form, field, data.text, context
                            )
                        )
                    )
                )
                or chosen.status == "conflict"
                and any(
                    alternative.action == "set"
                    and not _date_assignment_scope_ok(
                        data.form,
                        field,
                        alternative.value,
                        alternative.evidence,
                        data.text,
                        context,
                    )
                    for alternative in chosen.alternatives
                )
            )
        ):
            chosen = _hold(field, "ambiguous_intent", chosen.source)
        if field == "vat" and chosen.intent_kind != "retain_previous":
            if chosen.status == "valid" and chosen.action == "set":
                if not _vat_set_intent_ok(data, chosen, context):
                    chosen = _hold(field, "ambiguous_intent", chosen.source)
            elif chosen.status == "conflict":
                # 불확정 세금 방식을 선택 가능한 충돌 대안으로도 내보내지 않는다.
                chosen = _hold(field, "ambiguous_intent", chosen.source)
        if chosen.intent_kind != "retain_previous" and (
            chosen.status == "valid"
            and chosen.action in ("set", "clear")
            and _competing_retention_assignment(data, field, chosen.value, context, previous)
            or chosen.status == "conflict"
            and any(
                _competing_retention_assignment(data, field, alt.value, context, previous)
                for alt in chosen.alternatives
            )
        ):
            # 독립 생산자 병합 및 정정 복구 뒤에도 같은 경쟁 지시를
            # 적용한다. 남은 대안을 단독 확정으로 승격하지 않는다.
            chosen = _hold(field, "ambiguous_intent", chosen.source)
        if (
            m is not None
            and _not_action(m)
            # A complete response explicitly classifies every active field.
            # A partial producer's abstention is not proof that the other
            # operation was understood; keep the conservative hold there.
            and set(model) == set(FORM_FIELDS[data.form])
            and chosen.status == "uncertain"
            and not chosen.evidence
            and chosen.reason_code
            in ("ambiguous_intent", "correction_unverified", "business_constraint")
            and field not in prior
            and field not in previous_rejected
            and field not in conditional_fields | unresolved_fields
            and field not in withdrawals
            and not _has_new_field_risk(data, context, field)
        ):
            chosen = m
        out.append(chosen)
        if decisions is not None:
            decisions[field] = {
                "rule": r.status if r else None,
                "model": m.status if m else None,
                "previous": previous_rejected.get(field) or ("valid" if field in prior else None),
                "choice": chosen.status,
                "intent": chosen.intent_kind,
                "reason_code": chosen.reason_code,
                "origin_request_id": chosen.origin_request_id,
            }
    if merged_out is not None:
        merged_out.extend(out)
    final = finalize_candidates(data, context, out)
    if decisions is not None:
        for item in final:
            decisions[item.field]["choice"] = item.status
            decisions[item.field]["reason_code"] = item.reason_code
    return final
