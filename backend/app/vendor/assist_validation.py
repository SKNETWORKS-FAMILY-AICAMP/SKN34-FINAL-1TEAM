"""업체 도우미 후보의 원문·타입·업무 한도를 결정적으로 검증한다."""

import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from itertools import pairwise

from app.calc.schedule import JOB_MAX_DAYS
from app.quote.schemas import AMOUNT_MAX
from app.schedule.models import EXTRA_AMOUNT_MAX
from app.vendor.assist_date_edit import validated_date_edit
from app.vendor.assist_normalize import crew_mentions, date_mentions, date_tokens
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

MONEY = re.compile(
    r"(?<!\d)(\d{1,3}(?:,\d{3})+|\d+(?:\.\d+)?)\s*"
    r"(천만원|백만원|십만원|억원|만원|천원|백원|억|만|천|백|원)?"
)
MONEY_UNIT = {
    "억원": 100_000_000,
    "억": 100_000_000,
    "천만원": 10_000_000,
    "백만원": 1_000_000,
    "십만원": 100_000,
    "만원": 10_000,
    "만": 10_000,
    "천원": 1_000,
    "천": 1_000,
    "백원": 100,
    "백": 100,
    "원": 1,
    "": 1,
}
DAYS = re.compile(r"(?<!\d)(\d{1,3})\s*(?:일간|일)(?!\d)")
CLEAR = re.compile(r"(?:지워|지우|삭제|비워|비우|없애|제거|초기화)")
CLEAR_NEGATED = re.compile(
    r"(?:지우지|지워지지|삭제하지|비우지|없애지|제거하지|초기화하지|삭제 금지|삭제 말)"
)
_CLEAR_DENIAL = re.compile(
    r"(?:지워서는\s*안|삭제는\s*하지|삭제를\s*하지|삭제\s*안|"
    r"지우는\s*건\s*안|지우기를\s*하지|지우\s*안|하지\s*않)"
)
VAT_WORDS = {
    "included": ("포함", "포함된"),
    "excluded": ("별도", "미포함", "제외"),
    "exempt": ("면세", "부가세 없음", "부가세 면제", "부가세 0원"),
}


def normalized(text: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def evidence_matches(text: str, evidence: str) -> bool:
    return bool(evidence.strip()) and normalized(evidence) in normalized(text)


def _amount_mentions(evidence: str) -> set[int]:
    out: set[int] = set()
    tokens: list[tuple[int, int, int, int]] = []
    for item in MONEY.finditer(evidence):
        unit = item[2] or ""
        # 날짜 조각·공사 일수·인원 숫자를 금액으로 재활용하지 않는다.
        before = evidence[item.start() - 1 : item.start()] if item.start() else ""
        after = evidence[item.end() : item.end() + 4]
        if before in ("-", "−", "/") or (
            not unit
            and (
                after.startswith(("-", "/", "."))
                or after.lstrip().startswith(
                    ("일", "명", "인", "월", "년", "개", "주", "시", "분", "%", "㎡", "평", "대")
                )
            )
        ):
            continue
        if not unit:
            # '견적 5000'처럼 숫자와 바로 연결된 금액 문구만 허용한다.
            nearby = evidence[max(item.start() - 28, 0) : item.start()]
            nearby = re.split(r"[,;.!?\n]", nearby)[-1]
            if not re.search(
                r"(?:견적|금액|가격|비용|공사비|추가\s*금액|합계|총액|단가|청구)"
                r"(?:은|는|이|가|을|를|도|만|으로|:)?\s*$",
                nearby,
            ):
                continue
        multiplier = MONEY_UNIT[unit]
        try:
            n = Decimal(item[1].replace(",", "")) * multiplier
        except InvalidOperation:
            continue
        if n != n.to_integral_value():
            continue
        amount = int(n)
        out.add(amount)
        if unit:
            tokens.append((item.start(), item.end(), amount, multiplier))

    # '1억 2천만원'처럼 단위가 감소하며 이어진 금액은 합한 값도 원문 근거다.
    for i, token in enumerate(tokens):
        total = token[2]
        end, scale = token[1], token[3]
        for next_token in tokens[i + 1 :]:
            if evidence[end : next_token[0]].strip() or next_token[3] >= scale:
                break
            total += next_token[2]
            out.add(total)
            end, scale = next_token[1], next_token[3]
    return out


def _grounded(
    form: FormName, field: str, value: Value, evidence: str, context: AssistContext
) -> bool:
    if field == "amount":
        return type(value) is int and value in _amount_mentions(evidence)
    if field == "crew":
        return type(value) is int and value in crew_mentions(evidence)
    if form == "quote" and field == "days":
        return type(value) is int and value in {int(x) for x in DAYS.findall(evidence)}
    if field == "vat":
        return type(value) is str and any(word in normalized(evidence) for word in VAT_WORDS[value])
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
        return set(map(date.fromisoformat, value)) <= date_mentions(evidence, context, expand=True)
    if field in ("title", "reason"):
        return (
            type(value) is str and bool(value.strip()) and normalized(value) in normalized(evidence)
        )
    return False


_EXCLUDE_DAY = re.compile(r"(?:안\s*돼|안\s*되|안\s*됨|못\s*가|불가능|제외|빼|어려)")


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
    "valid": ("유효기간", "만료"),
}
_RETAIN = re.compile(
    r"(?:그대로|유지|바꾸지|변경하지|수정하지|건드리지|"
    r"전과\s*같|이전과\s*같|예전과\s*같|"
    r"같아요|같습니다|같아|같은\s*상태|동일)"
)
_RETAIN_NEGATED = re.compile(
    r"(?:그대로|유지).{0,16}(?:말고|두지\s*말|않|아니|취소|빼고)"
    r"|(?:유지하지|그대로\s*두지)"
    r"|(?:같지\s*않|같은\s*건\s*아니|같은\s*게\s*아니|동일하지\s*않)"
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
_RULE_TITLE_ASSIGN = re.compile(
    r"^\s{0,8}(?:추가\s{1,8})?(?:시공\s{1,8})?"
    r"(?:항목명|항목|제목|작업명)\s{0,8}(?::|=|은|는|을|를)\s{0,8}"
)
_RULE_REASON_ASSIGN = re.compile(r"^\s*(?:사유|이유)\s*(?::|=|은|는|을|를)\s*")
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
    if form == "period" and field == "excluded":
        # 기간 끝날이 언급됐다는 이유만으로 기존 제외일을 버리지 않는다.
        return bool(_EXCLUDE_DAY.search(text)) or "제외" in text
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
        # '유효기간'은 견적 효력 날짜이며 공사 일수의 변경 시도가 아니다.
        remaining = re.sub(r"유효\s*기간", "", text)
        return bool(DAYS.search(remaining)) or any(
            word in remaining for word in ("공사", "기간", "일수", "작업 날짜")
        )
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
    if form == "quote" and field == "days":
        text = re.sub(r"유효\s*기간", "", text)
    return any(word in text for word in _CLEAR_TARGETS[field])


def _targeted_clear(form: FormName, field: str, evidence: str, text: str) -> bool:
    if not _deletion_requested(evidence):
        return False
    # 근거가 '삭제해 주세요'처럼 동사만 있어도, 바로 앞의 목적어로 필드를 확인한다.
    at = normalized(text).find(normalized(evidence))
    before = normalized(text)[max(at - 35, 0) : at] if at >= 0 else ""
    after = normalized(text)[at + len(normalized(evidence)) : at + len(normalized(evidence)) + 35]
    scope = re.split(r"[,;.!?\n]", before)[-1] + normalized(evidence) + after
    if _CLEAR_DENIAL.search(scope) or CLEAR_NEGATED.search(scope):
        return False
    if any(word in evidence for word in ("전부", "모두", "전체")):
        return True
    return _target_word(form, field, scope)


def _targeted_retention(
    form: FormName, field: str, evidence: str, text: str, context: AssistContext
) -> bool:
    if not evidence_matches(text, evidence):
        return False
    raw = normalized(text)
    at = raw.find(normalized(evidence))
    scope = raw[max(at - 16, 0) : at + len(normalized(evidence)) + 20]
    if (
        not _RETAIN.search(evidence)
        or _RETAIN_NEGATED.search(scope)
        or _RULE_QUESTION_OR_UNDECIDED.search(scope)
    ):
        return False
    if _target_word(form, field, evidence):
        return True
    if form == "period" and field in ("start", "end") and "기간" in evidence:
        return True
    # 모델이 '전과 같아요'처럼 동사만 evidence로 잘라도 바로 앞의 명시적
    # 필드 목적어를 확인한다. 다른 필드가 더 가까우면 유지 의도로 해석하지 않는다.
    head = re.split(r"[,;.!?？\n]", raw[:at])[-1][-48:]
    targets: list[tuple[int, int, str]] = []
    for name in FORM_FIELDS[form]:
        words = _CLEAR_TARGETS[name]
        if form == "period" and name in ("start", "end"):
            words = (*words, "기간")
        for word in words:
            for match in re.finditer(re.escape(word), head):
                targets.append((match.end(), len(word), name))
    generic_scope = any(
        word in evidence for word in ("나머지", "다른", "그 외", "전체", "전부", "모두")
    )
    if targets and not generic_scope:
        nearest = max((end, length) for end, length, _ in targets)
        if any((end, length) == nearest and name == field for end, length, name in targets):
            return True
    # '나머지는 그대로'는 이번 글에서 별도로 바꾼 필드에는 적용하지 않는다.
    if generic_scope:
        without_evidence = normalized(text).replace(normalized(evidence), "", 1)
        if _other_boundary_only(form, field, without_evidence):
            return True
        return not _mentions_field(form, field, without_evidence, context)
    return False


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
    if any(
        _RULE_QUESTION_OR_UNDECIDED.search(clause)
        or _RULE_NEGATED_ACTION.search(clause)
        or _RULE_HISTORICAL.search(clause)
        for clause in clauses
    ):
        return False
    if rule.action == "clear":
        return _target_word(form, field, text) and _targeted_clear(form, field, rule.evidence, text)
    value = rule.value
    if field in ("title", "reason"):
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
        words = normalized(text)
        modes = {
            mode
            for mode, phrases in VAT_WORDS.items()
            if any(phrase in words for phrase in phrases)
        }
        return (
            type(value) is str
            and modes == {value}
            and any(word in words for word in ("부가세", "면세", "vat"))
        )
    if form == "quote" and field == "days":
        remaining = re.sub(r"유효\s*기간", "", text)
        return (
            type(value) is int
            and {int(item) for item in DAYS.findall(remaining)} == {value}
            and any(word in remaining for word in ("공사", "기간", "일수"))
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
            form, item.field, relation.rejected_value, relation.rejected_evidence, context
        ) and _grounded(form, item.field, item.value, relation.accepted_evidence, context)
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
        previous is None
        or item.action != "set"
        or item.correction is None
        or item.correction.kind != "replace"
        or (data.form, item.field) not in (("dates", "days"), ("period", "excluded"))
    ):
        return None
    old = next(
        (value for value in previous.fields if value.field == item.field and value.action == "set"),
        None,
    )
    if old is None or not isinstance(old.value, list):
        return None
    relation = item.correction
    if not (
        evidence_matches(data.text, item.evidence)
        and evidence_matches(data.text, relation.rejected_evidence)
        and evidence_matches(data.text, relation.accepted_evidence)
    ):
        return None
    complete = validated_date_edit(item.field, data.text, old.value, context)
    if complete is None:
        return None
    removed = set(old.value) - set(complete)
    added = set(complete) - set(old.value)
    if len(removed) != 1 or len(added) != 1:
        return None
    rejected, accepted = next(iter(removed)), next(iter(added))
    # 기존 wire에서는 rejected_value가 교체한 하루 또는 그 하루짜리 배열일 수 있다.
    # 검증 성공 후 응답에는 이전 *전체* 목록을 넣어 3턴 이후에도 재검증한다.
    if relation.rejected_value not in (rejected, [rejected], old.value):
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
        rejected_value=list(old.value),
        rejected_evidence=relation.rejected_evidence,
        accepted_evidence=relation.accepted_evidence,
    )


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
    if _verified_historical_scalar(
        item, form, text, context, previous, old_text, new_text, before, after
    ):
        return relation
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
        if not _grounded(form, item.field, relation.rejected_value, old, context):
            return None
        if not _grounded(form, item.field, item.value, new, context):
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
        if not _grounded(data.form, item.field, rule.value, rule.evidence, context):
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


def _speculative_rule_amount(text: str, value: Value) -> bool:
    """모델 실패 시에도 미정·질문인 금액을 규칙 단독 확정값으로 채택하지 않는다."""
    return any(
        value in _amount_mentions(clause) and bool(_RULE_QUESTION_OR_UNDECIDED.search(clause))
        for clause in re.split(r"[,;\n]", text)
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
        merged = {**data.current, **proposed}
        start_raw, end_raw = merged.get("start"), merged.get("end")
        start = date.fromisoformat(start_raw) if isinstance(start_raw, str) and start_raw else None
        end = date.fromisoformat(end_raw) if isinstance(end_raw, str) and end_raw else None
        if start and start <= context.today:
            return False
        if start and end and (end < start or (end - start).days > 62):
            return False
        excluded = merged.get("excluded")
        return not (
            isinstance(excluded, list)
            and (
                len(excluded) > 31
                or len(set(excluded)) != len(excluded)
                or not start
                or not end
                or any(not start <= date.fromisoformat(day) <= end for day in excluded)
            )
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


def _review_one(
    item: FieldIntent,
    source: Source,
    data: AssistRequest,
    context: AssistContext,
    proposed: dict[str, Value],
    *,
    text: str,
    previous: PreviousCandidate | None = None,
) -> CandidateField:
    common = {
        "field": item.field,
        "source": source,
        "alternatives": [],
        "origin_request_id": data.request_id,
    }
    intent = _intent(item)

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
        if not _targeted_retention(data.form, item.field, item.evidence, text, context):
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
        if not _targeted_retention(data.form, item.field, item.evidence, text, context):
            return reject("ambiguous_intent")
        retained = (
            next((field for field in previous.fields if field.field == item.field), None)
            if previous
            else None
        )
        if retained is None:
            return reject(
                "stale_context" if data.previous_candidate is not None else "reference_missing"
            )
        return _candidate_from_previous(retained, previous)
    if intent == "ambiguous" or item.action == "uncertain":
        if item.evidence and not evidence_matches(text, item.evidence):
            return reject("evidence_missing")
        return reject("ambiguous_intent", uncertain=True)
    if intent != "explicit_change" or item.action not in ("set", "clear"):
        return reject("ambiguous_intent")

    evidence_ok = evidence_matches(text, item.evidence)
    if not evidence_ok:
        return reject("evidence_missing")
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
                if not _business_ok(
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
        if not _grounded(data.form, item.field, item.value, item.evidence, context):
            return reject("unsupported_normalization")
        if not _business_ok(data.form, item.field, item.value, data, context, proposed):
            return reject("business_constraint")
        if (
            source == "rule"
            and item.field == "amount"
            and _speculative_rule_amount(text, item.value)
        ):
            return reject("ambiguous_intent", uncertain=True)

    correction = _verified_correction(item, data.form, text, context, previous)
    if (
        item.correction is not None
        and correction is None
        and (source != "llm" or not _independent_same_value_rule(item, data, context, text))
    ):
        return reject("correction_unverified", uncertain=True)
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
) -> tuple[dict[str, CandidateField], list[str]]:
    original = text if text is not None else data.text
    proposed: dict[str, Value] = {}
    warnings: list[str] = []
    # 개별 값에 원문 근거가 없는 필드는 다른 필드의 업무 검증에 사용하지 않는다.
    for item in fields:
        if (
            item.action == "set"
            and item.field in FORM_FIELDS[data.form]
            and evidence_matches(original, item.evidence)
        ):
            try:
                check_value(data.form, item.field, item.value)
                if _grounded(data.form, item.field, item.value, item.evidence, context):
                    proposed[item.field] = item.value
            except (ValueError, TypeError, KeyError):
                pass
    checked: dict[str, CandidateField] = {}
    for item in fields:
        if item.field not in FORM_FIELDS[data.form]:
            warnings.append("화면에서 쓸 수 없는 후보를 제외했어요.")
            continue
        result = _review_one(
            item, source, data, context, proposed, text=original, previous=previous
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
    return checked, list(dict.fromkeys(warnings))


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
        or relation is None
        or relation.kind != "replace"
        or (form, item.field) not in (("dates", "days"), ("period", "excluded"))
        or not isinstance(item.value, list)
        or not isinstance(relation.rejected_value, list)
        or len(history) > 1000
    ):
        return False
    lines = [line.strip() for line in history.splitlines() if line.strip()]
    if not 2 <= len(lines) <= 12:
        return False

    def unverified_change(line: str) -> bool:
        # 지나간 발화 중 날짜 목록을 다시 바꾸거나 지우려다 실패한 단계가
        # 있으면, 더 오래된 derived 후보를 다음 턴의 정답으로 되살리지 않는다.
        field_named = (
            any(word in line for word in ("공사 날짜", "공사할 날", "공사일", "작업일", "일정"))
            if item.field == "days"
            else any(word in line for word in ("제외", "빼는 날", "빼는 날짜", "안 되는 날"))
        )
        changed_word = re.search(
            r"(?:대신|말고|→|->|지워|지우|삭제|비워|비우|초기화|없애|"
            r"바꾸|바꿔|변경|수정|정정|다시|재설정|미정|보류|[?？])",
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
        if item.field == "days":
            if not any(word in line for word in ("공사", "날짜", "일정", "작업")):
                return None
            source = line
        else:
            if not any(word in line for word in ("제외", "빼", "안 되는")):
                return None
            # 기간 경계가 함께 적힌 문장에서는 제외일 절만 증거로 쓴다.
            source = line.split("가능")[-1].strip(" ,")
        mentions = date_mentions(source, context, expand=True)
        if not mentions or len(mentions) > context.days_max:
            return None
        return [day.isoformat() for day in sorted(mentions)]

    state: list[str] | None = None
    seen_edit = False
    for line in lines:
        if state is None:
            state = initial_dates(line)
            continue
        changed = validated_date_edit(item.field, line, state, context)
        if changed is not None:
            state = changed
            seen_edit = True
        elif unverified_change(line):
            # 모호하거나 검증되지 않은 중간 날짜 변경이 있으면 뒤 값도 신뢰할 수 없다.
            return False
    if not seen_edit or state != item.value:
        return False
    # 저장한 마지막 교체 전 목록을 확인해 과거 근거를 다른 목록에 전용하지 못한다.
    last_edit = next(
        (
            index
            for index in reversed(range(len(lines)))
            if evidence_matches(lines[index], relation.rejected_evidence)
            and evidence_matches(lines[index], relation.accepted_evidence)
        ),
        None,
    )
    if last_edit is None or last_edit == 0:
        return False
    before_last = initial_dates(lines[0])
    if before_last is None:
        return False
    for line in lines[1:last_edit]:
        derived = validated_date_edit(item.field, line, before_last, context)
        if derived is not None:
            before_last = derived
        elif unverified_change(line):
            return False
    if before_last != relation.rejected_value:
        return False
    if validated_date_edit(item.field, lines[last_edit], before_last, context) != item.value:
        return False
    if not evidence_matches(lines[last_edit], item.evidence):
        return False
    effective = dict(data.current)
    effective.update(
        {
            name: checked.value
            for name, checked in verified_siblings.items()
            if checked.status == "valid" and checked.action == "set"
        }
    )
    validation_data = data.model_copy(update={"current": effective})
    return _business_ok(
        form, item.field, item.value, validation_data, context, {item.field: item.value}
    )


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
        else:
            eligible.append(item)
    ordinary = [
        item
        for item in eligible
        if not (item.value_source == "derived" and item.field in ("days", "excluded"))
    ]
    derived = [
        item
        for item in eligible
        if item.value_source == "derived" and item.field in ("days", "excluded")
    ]
    checked, _ = review_fields(ordinary, "rule", data, context, text=previous.text)
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
    start_raw, end_raw = state.get("start"), state.get("end")
    excluded = state.get("excluded")
    if start_raw is not None and not isinstance(start_raw, str):
        return False
    if end_raw is not None and not isinstance(end_raw, str):
        return False
    start = date.fromisoformat(start_raw) if start_raw else None
    end = date.fromisoformat(end_raw) if end_raw else None
    if end and not start:
        return False
    if start and start <= context.today:
        return False
    if start and end and (end < start or (end - start).days > 62):
        return False
    if excluded is None:
        return True
    if not isinstance(excluded, list) or len(excluded) > 31 or len(set(excluded)) != len(excluded):
        return False
    if excluded and (not start or not end):
        return False
    return all(start < date.fromisoformat(value) < end for value in excluded)


def _period_combination_failures(
    state: dict[str, Value], active: dict[str, CandidateField], context: AssistContext
) -> set[str]:
    """기간 조합을 깬 필드만 보류하고 독립적인 이전 제외일은 보존한다."""
    start_raw, end_raw = state.get("start"), state.get("end")
    start = date.fromisoformat(start_raw) if isinstance(start_raw, str) and start_raw else None
    end = date.fromisoformat(end_raw) if isinstance(end_raw, str) and end_raw else None
    failures: set[str] = set()
    if start and start <= context.today:
        failures.add("start")
    if end and not start:
        failures.update(("start", "end"))
    if start and end and (end < start or (end - start).days > 62):
        failures.update(("start", "end"))
    if failures:
        return failures & active.keys()
    excluded = state.get("excluded")
    invalid_excluded = excluded is not None and (
        not isinstance(excluded, list)
        or len(excluded) > 31
        or len(set(excluded)) != len(excluded)
        or bool(excluded)
        and (start is None or end is None)
        or isinstance(excluded, list)
        and bool(excluded)
        and start is not None
        and end is not None
        and any(not start < date.fromisoformat(day) < end for day in excluded)
    )
    if invalid_excluded:
        changed_edges = {field for field in ("start", "end") if field in active}
        if changed_edges and (
            "excluded" not in active or active["excluded"].value_source == "previous"
        ):
            failures.update(changed_edges)
        else:
            failures.add("excluded")
    return failures & active.keys()


def finalize_candidates(
    data: AssistRequest, context: AssistContext, fields: list[CandidateField]
) -> list[CandidateField]:
    """규칙·모델·이전값을 합친 후 실제 폼 조합을 검증한다."""
    active = {
        item.field: item
        for item in fields
        if item.status == "valid" and item.action in ("set", "clear")
    }
    if not active:
        return fields
    state = _final_state(data, fields)
    failures: set[str] = set()
    # 개별 검증 뒤 조합이 달라져 달력 조건을 깨뜨릴 수 있으므로 다시 검사한다.
    if data.form == "period":
        if not _combined_period_ok(state, context):
            failures.update(_period_combination_failures(state, active, context))
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


def merge_candidates(
    data: AssistRequest,
    context: AssistContext,
    rule: dict[str, CandidateField],
    model: dict[str, CandidateField] | None,
    previous: PreviousCandidate | None,
    *,
    decisions: dict[str, dict[str, str | None]] | None = None,
    merged_out: list[CandidateField] | None = None,
    previous_rejected: dict[str, ReasonCode] | None = None,
) -> list[CandidateField]:
    if previous_rejected is None:
        _, previous_rejected = review_previous(data, context)
    prior = (
        {item.field: _candidate_from_previous(item, previous) for item in previous.fields}
        if previous
        else {}
    )
    out = []
    for field in FORM_FIELDS[data.form]:
        r = rule.get(field)
        m = model.get(field) if model is not None else None
        chosen = _merge_pair(r, m) if model is not None else r
        if model is not None and m is not None and _not_action(m) and r is not None:
            if r.status == "valid" and r.action in ("set", "clear"):
                # 원문 숫자가 있다는 사실만으로 대상 필드가 실제로 변경됐다고 단정하지 않는다.
                chosen = (
                    r
                    if _rule_field_proof(data.form, r, data.text, context)
                    else _hold(field, "ambiguous_intent", "rule")
                )
            elif r.status in ("invalid", "uncertain"):
                chosen = (
                    m
                    if _other_boundary_only(data.form, field, data.text)
                    or not _mentions_field(data.form, field, data.text, context)
                    else _hold(field, r.reason_code or "ambiguous_intent", "rule")
                )
        if chosen is None or _not_action(chosen):
            if field in prior:
                if _other_boundary_only(data.form, field, data.text):
                    chosen = prior[field]
                elif _mentions_field(data.form, field, data.text, context):
                    chosen = _hold(field, "ambiguous_intent", "llm" if model else "rule")
                else:
                    chosen = prior[field]
            elif data.previous_candidate and any(
                item.field == field and item.action in ("set", "clear")
                for item in data.previous_candidate.fields
            ):
                chosen = _hold(
                    field,
                    previous_rejected.get(field, "reference_missing"),
                    "llm" if model else "rule",
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
            )
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
