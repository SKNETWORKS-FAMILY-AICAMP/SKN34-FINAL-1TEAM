"""업체 도우미 후보의 원문·타입·업무 한도를 결정적으로 검증한다."""

import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from itertools import pairwise

from app.calc.schedule import JOB_MAX_DAYS
from app.quote.schemas import AMOUNT_MAX
from app.schedule.models import EXTRA_AMOUNT_MAX
from app.vendor.assist_boundary import affirmative_end_deadline, explicit_prior_end_reference
from app.vendor.assist_date_edit import date_edit_reference_prefix, validated_date_edit
from app.vendor.assist_normalize import (
    complete_range_evidence,
    crew_mentions,
    crew_replacement,
    crew_tokens,
    date_mentions,
    date_tokens,
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


def _scalar_grounded(field: str, value: Value, evidence: str, text: str | None) -> bool:
    if type(value) is not int:
        return False
    needle = normalized(evidence)
    tokens = _amount_tokens if field == "amount" else crew_tokens
    mentions = [(start, end) for start, end, number in tokens(needle) if number == value]
    if not mentions:
        return False
    if text is None:
        return True
    raw = normalized(text)
    # 같은 숫자가 다른 곳에 있어도 잘린 근거를 통과시키지 않는다.
    full = {(start, end) for start, end, number in tokens(raw) if number == value}
    return any(
        (match.start() + start, match.start() + end) in full
        for match in re.finditer(re.escape(needle), raw)
        for start, end in mentions
    )


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
        return _scalar_grounded(field, value, evidence, text)
    if form == "quote" and field == "days":
        return type(value) is int and value in {int(x) for x in DAYS.findall(evidence)}
    if field == "vat":
        return type(value) is str and any(word in normalized(evidence) for word in VAT_WORDS[value])
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
        return set(map(date.fromisoformat, value)) <= date_mentions(evidence, context, expand=True)
    if field in ("title", "reason"):
        return (
            type(value) is str and bool(value.strip()) and normalized(value) in normalized(evidence)
        )
    return False


_EXCLUDE_DAY = re.compile(r"(?:안\s*돼|안\s*되|안\s*됨|못\s*가|불가능|제외|빼|어려)")
# 이전에 쉬거나 빼기로 한 날짜는 '제외일'이라는 단어가 없어도 같은 필드를 가리킨다.
# 날짜/날과 연결된 표현만 인정해 공사 시작·종료일의 일반 언급과 구분한다.
_EXCLUDED_DAY_REFERENCE = re.compile(
    r"(?:빼(?:둔|놓은|놨던|기로\s*한|는)|뺀|뺐던|"
    r"제외(?:한|했던|된|해\s*둔)?|안\s*되는|"
    r"쉬(?:는|기로\s*한|기로\s*했던)|"
    r"쉰다고\s*(?:한|했던)|못\s*간다고\s*(?:한|했던))"
    r"\s*(?:날짜|날|일자|일)"
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
    "valid": ("유효기간", "만료"),
}
_RETAIN = re.compile(
    r"(?:그대로|유지|바꾸지|변경하지|수정하지|건드리지|"
    r"전과\s*같|이전과\s*같|예전과\s*같|"
    r"같아요|같습니다|같아|같은\s*상태|동일|"
    r"살려|이어서\s*사용|폼대로|"
    r"(?:아까|앞서|이전|방금|지난)[^,;.!?？\n]{0,24}대로|"
    r"(?:제안|변경|정리|정한|앞서|아까|방금|이전|지난)"
    r"[^,;.!?？\n]{0,28}(?:안으로|걸로|내용으로)\s*(?:두|둘))"
)
_RETAIN_NEGATED = re.compile(
    r"(?:그대로|유지).{0,16}(?:말고|두지\s*말|않|아니|취소|빼고)"
    r"|(?:유지하지|그대로\s*두지)"
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
    form: FormName, field: str, evidence: str, text: str, context: AssistContext
) -> bool:
    if not evidence_matches(text, evidence):
        return False
    raw = normalized(text)
    needle = normalized(evidence)
    # 같은 근거가 긍정/부정의 두 절에 반복되면 어떤 절인지 확정할 수 없다.
    if raw.count(needle) != 1:
        return False
    at = raw.find(needle)
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
        scope = raw[scope_start:right]
        if _RETAIN_NEGATED.search(scope) or _RULE_QUESTION_OR_UNDECIDED.search(scope):
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
    elif field == "vat":
        words = normalized(evidence)
        mentioned = {
            mode for mode, phrases in VAT_WORDS.items() if any(p in words for p in phrases)
        }
        # '미포함'에 들어 있는 '포함' 두 글자는 포함 지시가 아니다.
        if "미포함" in words or "포함하지" in words:
            mentioned.discard("included")
    elif field in ("start", "end", "valid"):
        mentioned = {day for _, _, day in date_tokens(evidence, context)}
        value = date.fromisoformat(value) if isinstance(value, str) else value
    else:
        return False
    return bool(mentioned) and value not in mentioned


# 앞서 실제로 말한 값을 다시 확인하는 표현은 동사만 잘라낸 모델 근거가
# 아니라 원문의 같은 절에서 대상, 값, 유지 지시를 함께 확인한다.
_REAFFIRM_REFERENCE = re.compile(
    r"(?:방금|아까|앞서|좀\s*전에|조금\s*전에|이전에|지난번에|그때|전에)"
    r"\s*(?:말씀드린|말한|얘기한|알려드린|정한|잡은|확정한|제안한|정리한|적은|전달한)\s*"
)
_REAFFIRM_DIRECTIVE = (
    r"(?:가(?:고요?|주세요|주십시오|요|겠습니다)|"
    r"두(?:고요?|세요|겠습니다)|둘게요|둡니다|"
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
        return isinstance(value, str) and any(
            re.fullmatch(rf"{re.escape(word)}\s*[은는이가을를도만]?\s*", body)
            for word in VAT_WORDS[value]
        )
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
    return not _reaffirmation_conflicts(form, field, outside, context, value)


_PRIOR_FIELD_WORDS = {
    "start": r"착공일?|시작\s*(?:날짜|일자|일)|첫\s*(?:(?:공사|작업)\s*일|날짜|날)",
    "end": r"준공일?|마감\s*(?:날짜|일자|일)?|종료\s*(?:날짜|일자|일)|끝나는\s*날|마지막\s*(?:(?:공사|작업)\s*일|날짜|날)|끝날",
    "excluded": r"제외\s*(?:날짜|일자|일)|쉬는\s*날|빼는\s*날|작업\s*안\s*하는\s*날",
    "amount": r"견적\s*(?:금액|총액)|추가\s*(?:금액|비용|금)|금액|가격|비용|총액|공사비",
    "vat": r"부가세(?:\s*(?:조건|방식|구분))?|세금(?:\s*(?:조건|방식|구분))?",
    "days": r"(?:공사|작업)\s*(?:날짜|일자|일수|기간|일정|일)|날짜|일정|일수|기간|공기",
    "crew": r"(?:투입|작업)\s*인원|인원|인력|작업자",
    "title": r"항목명|항목|제목|작업명",
    "reason": r"사유|이유",
    "valid": r"(?:견적\s*)?유효\s*기간|만료일",
}
_PRIOR_REFERENCE = (
    r"(?P<when>아까|방금|앞서|앞에서|이전에|지난번에|그때|처음에|전에|"
    r"좀\s*전에|조금\s*전에)\s*"
    r"(?:말씀드린|말한|얘기한|알려드린|요청한|요청했던|정한|잡은|확정한|제안한|정리한|적은|전달한)"
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
    r"|가(?:고요?|죠|겠습니다|주세요|요)|두(?:고요?|세요|겠습니다)|둘게요|둡니다"
    r"|(?:유지|사용|반영|적용)\s*(?:하고요?|할게요|하겠습니다)"
    r"|하고요?|할게요|하겠습니다|쓰면\s*됩니다))"
)
_PRIOR_CANCEL_OR_CONDITION = re.compile(
    r"(?:취소|철회|보류|없던\s*걸로|미정|검토|고민|나중에|추후|혹시|아마|"
    r"가능하면|경우|조건부|하면|라면|으면|다면|할지|할까|않|아니|말고|지\s*마|지\s*말)"
)


def _prior_field_word(form: FormName, field: str) -> str:
    if (form, field) == ("quote", "days"):
        return r"(?:공사|작업)\s*(?:일수|기간)|기간|일수|공기"
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
        return any(word in text for words in VAT_WORDS.values() for word in words)
    if (form, field) == ("quote", "days"):
        return bool(DAYS.search(text))
    if field in ("start", "end", "valid", "excluded", "days"):
        return bool(date_tokens(text, context))
    return False


def _reference_only_retention(
    form: FormName,
    field: str,
    evidence: str,
    text: str,
    context: AssistContext,
    previous: PreviousCandidate,
    retained: FieldIntent,
) -> bool | None:
    """숫자 없는 명시적 이전 참조는 해당 필드의 독립된 긍정 지시로 확인한다."""
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
    if match["when"] == "처음에":
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
    for clause in re.split(r"(?<!\d),|,(?!\d)|(?<!\d)\.|\.(?!\d)|[;!?？]", outside):
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
    # 서비스의 업무 검증 문맥에는 이전 후보가 합쳐져 있다. 검증된 baseline은
    # 원래 폼 값과 일치하므로 이 값으로 폼 참조를 복원한다.
    current = (
        previous.baselines[field]
        if previous is not None and field in previous.baselines
        else data.current.get(field)
    )
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
        effective = dict(data.current)
        if previous:
            effective.update(
                {item.field: item.value for item in previous.fields if item.action == "set"}
            )
        validation_data = data.model_copy(update={"current": effective})
        if not _business_ok(
            data.form, field, complete, validation_data, context, {field: complete}
        ):
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
    r"취소|철회|보류|미정|없던걸로|원래대로|그대로두|하지마|안할|다시고민"
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
    r"없던걸로|원래대로|그대로두|하지마|하지않|안할|아니(?:요|라고|고|라)?|[?？]"
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
        if re.search(r"(?:현재|지금)\s*(?:폼|화면|입력)|폼\s*값\s*대로", item.evidence):
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
        reference_only = _reference_only_retention(
            data.form, item.field, item.evidence, text, context, previous, retained
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
            and not _targeted_retention(data.form, item.field, item.evidence, text, context)
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
                    data.form, item.field, item.evidence, text, context, previous, retained
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
        if data.form == "dates" and item.field == "crew" and crew_replacement(normalized(text)):
            if not _verified_crew_replacement(item, text, context, previous):
                return reject("correction_unverified", uncertain=True)
            return CandidateField(
                **common,
                action="set",
                value=item.value,
                evidence=item.evidence,
                status="valid",
                intent_kind="explicit_change",
                value_source="utterance",
                correction=item.correction,
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
    if not 1 <= len(lines) <= 12:
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
        # 이미 교체로 읽힌 절을 새 전체 목록의 출발점으로 사용하지 않는다.
        if (
            date_edit_reference_prefix(item.field, line) is not None
            or _RULE_QUESTION_OR_UNDECIDED.search(line)
            or _RULE_NEGATED_ACTION.search(line)
        ):
            return None
        if item.field == "days":
            if not any(word in line for word in ("공사", "날짜", "일정", "작업")):
                return None
            source = line
        else:
            if not any(word in line for word in ("제외", "빼", "안 되는", "휴무", "쉬는")):
                return None
            # 기간 경계가 함께 적힌 문장에서는 제외일 절만 증거로 쓴다.
            source = line.split("가능")[-1].strip(" ,")
        mentions = date_mentions(source, context, expand=True)
        if not mentions or len(mentions) > context.days_max:
            return None
        return [day.isoformat() for day in sorted(mentions)]

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
            elif date_edit_reference_prefix(item.field, line) is not None or unverified_change(
                line
            ):
                # 실패한 중간 교체가 있으면 오래된 값을 되살리지 않는다.
                return False
        return seen_edit and state == item.value and before_last == relation.rejected_value

    # 최초 제안이 대화에 있던 경우: 최초 원문으로 전체 목록을 재구성한다.
    # 앞 턴의 다른 필드 발화는 날짜 목록의 시작 근거가 아니다. 첫 날짜
    # 목록이 등장할 때까지 해당 필드의 변경 이력이 없는 경우만 건너뛴다.
    verified = False
    for index in range(last_edit):
        seed = initial_dates(lines[index])
        if seed is not None:
            verified = replay(seed, index + 1, has_prior=True)
            break
        if unverified_change(lines[index]):
            break
    # 최초 기준이 화면의 목록인 경우: 동일 필드의 원래 baseline이 지금도
    # 그대로일 때에만 검증한다. correction의 rejected_value를 기준으로 믿지 않는다.
    if not verified and baseline:
        verified = replay(baseline, 0, has_prior=False)
    if not verified:
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


def _reverify_crew_history(item: FieldIntent, history: str, context: AssistContext) -> bool:
    """단위가 생략된 인원 정정은 이전 발화 순서에서 같은 값의 교체를 재검증한다."""
    value = None
    matched = False
    for utterance in history.splitlines():
        raw = normalized(utterance)
        replacement = crew_replacement(raw)
        if replacement is not None:
            if value != replacement.old or not 1 <= replacement.new <= context.crew_max:
                value, matched = None, False
                continue
            value = replacement.new
            matched = value == item.value and _crew_span_covered(
                raw, item.evidence, replacement.new_span
            )
            if matched and item.correction is not None:
                relation = item.correction
                matched = (
                    relation.kind == "replace"
                    and relation.rejected_value == replacement.old
                    and _crew_span_covered(raw, relation.rejected_evidence, replacement.old_span)
                    and _crew_span_covered(raw, relation.accepted_evidence, replacement.new_span)
                )
            continue
        counts = crew_mentions(raw)
        if counts:
            value, matched = None, False
            if len(counts) != 1:
                continue
            count = next(iter(counts))
            candidate = CandidateField(
                field="crew",
                action="set",
                value=count,
                evidence=raw,
                source="rule",
                status="valid",
                alternatives=[],
            )
            if 1 <= count <= context.crew_max and _rule_field_proof(
                "dates", candidate, raw, context
            ):
                value = count
        elif _target_word("dates", "crew", raw) and not (
            _RETAIN.search(raw)
            and not _RETAIN_NEGATED.search(raw)
            and not _RULE_QUESTION_OR_UNDECIDED.search(raw)
        ):
            value, matched = None, False
    return matched and value == item.value


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
    # 첫 발화의 날짜 열거를 ISO로 바꾼 후보도 모델이 derived로 표시할 수 있다.
    # 교체 이력이 없는 최초 요청에서 원문 근거가 목록 전체를 재현할 때만 일반 검증한다.
    for item in derived.copy():
        if (
            data.form == "dates"
            and item.field == "days"
            and item.correction is None
            and isinstance(item.value, list)
            and previous.origins.get(item.field, previous.request_id) == previous.request_id
            and "\n" not in previous.text
            and not _DIRECT_REPLACE.search(previous.text)
            and not _RULE_QUESTION_OR_UNDECIDED.search(previous.text)
            and not _RULE_NEGATED_ACTION.search(previous.text)
            and set(item.value)
            == {day.isoformat() for day in date_mentions(item.evidence, context, expand=False)}
        ):
            ordinary.append(item.model_copy(update={"value_source": "utterance"}))
            derived.remove(item)
    checked, _ = review_fields(ordinary, "rule", data, context, text=previous.text)
    for item in ordinary:
        if (
            data.form == "dates"
            and item.field == "crew"
            and sum(part.field == "crew" for part in previous.fields) == 1
            and checked["crew"].status != "valid"
            and item.action == "set"
            and _reverify_crew_history(item, previous.text, context)
        ):
            checked["crew"] = CandidateField(
                field="crew",
                action="set",
                value=item.value,
                evidence=item.evidence,
                source="rule",
                status="valid",
                alternatives=[],
                value_source="utterance",
            )
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


def _verified_period_end(
    data: AssistRequest, context: AssistContext, previous: PreviousCandidate | None
) -> CandidateField | None:
    """명시적 마감 허용이나 검증된 이전 종료일 유지에만 독립 근거를 부여한다."""
    if data.form != "period":
        return None
    deadline = affirmative_end_deadline(data.text, context)
    if deadline is not None:
        evidence, day = deadline
        value = day.isoformat()
        if not _grounded("period", "end", value, evidence, context) or not _business_ok(
            "period", "end", value, data, context, {"end": value}
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
    date_edits: dict[str, CandidateField] | None = None,
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
    independent_end = _verified_period_end(data, context, previous)
    out = []
    for field in FORM_FIELDS[data.form]:
        r = rule.get(field)
        m = model.get(field) if model is not None else None
        chosen = _merge_pair(r, m) if model is not None else r
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
        # 검증한 전체 목록은 모델의 모호함·부분 배열 때문에 잃지 않는다.
        # 독립적인 명시적 반대 지시가 충돌하면 확인을 요구한다.
        edit = (date_edits or {}).get(field)
        if edit is not None:
            if chosen.status == "conflict":
                pass  # 서로 다른 명시적 대안은 임의로 한 쪽을 선택하지 않는다.
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
                added = set(edit.value) - set(edit.correction.rejected_value)
                partial = isinstance(chosen.value, list) and set(chosen.value) == added
                chosen = (
                    edit
                    if chosen.value == edit.value or partial
                    else _hold(field, "ambiguous_intent", "rule")
                )
            else:
                chosen = _hold(field, "ambiguous_intent", "rule")
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
