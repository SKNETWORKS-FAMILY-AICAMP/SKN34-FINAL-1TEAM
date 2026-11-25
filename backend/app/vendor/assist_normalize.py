"""업체 도우미 숫자·날짜 근거 정규화. 추출 토큰은 원문 위치를 보존한다.

날짜 목록은 열거된 날짜만, 범위는 두 끝날이 확인된 경우에만 확장한다.
모호한 월·연도와 기간 일수를 날짜로 추정하지 않는다.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import date, timedelta
from itertools import pairwise
from typing import TYPE_CHECKING

from app.calc.schedule import JOB_MAX_DAYS
from app.vendor.assist_intent import (
    CONDITIONAL_LINK,
    WITHDRAW_IDIOM,
    field_mentions,
    intent_clauses,
    polite_execution_risk_text,
)
from app.vendor.assist_lexical import skip_space, skip_space_back
from app.vendor.assist_schemas import FormName

if TYPE_CHECKING:
    from app.vendor.assist_schemas import AssistContext


_FULL = re.compile(
    r"(?<!\d)(\d{4})\s*(?:년|[-/.])\s*(\d+)\s*"
    r"(?:월|[-/.])\s*(\d+)\s*일?(?!\d)"
)
_MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})\s*(?:월|[-/.])\s*(\d{1,2})\s*일?(?!\d)")
_BARE_DAY = re.compile(
    r"(?<!\d)([1-9]|[12]\d|3[01])\s*일"
    r"(?!\s*(?:간|동안|정도|째|걸|소요|후|전|차|뒤))"
)
# 단독 숫자는 앞에서 월을 명시한 목록의 중간이나 기호 범위의 끝에서만 인정한다.
_SHORT_DAY = re.compile(r"(?<![\d/.월\-])([1-9]|[12]\d|3[01])(?![\d/.\-가-힣])")
_LIST_GAP = re.compile(r"^\s*(?:까지\s*)?(?:(?:[,，、·]|과|와|이랑|랑|및|하고|그리고)\s*)+$")
_RANGE_GAP = re.compile(r"^\s*(?:부터|에서|~|∼|〜|－|—|–|-|\.{2}|to)\s*$", re.IGNORECASE)
_SHORT_RANGE_GAP = re.compile(r"^\s*[~∼〜－—–-]\s*$")
_SHORT_RANGE_TAIL = re.compile(
    r"^\s*(?:$|(?:현장|공사|작업|시공|방문|일정|가능|불가능|까지|예정)(?=\s|$|[가-힣.,]))"
)
# 날짜 수와 인원 수에 공통으로 쓰이는 완전한 고유어 수사.
_DAY_WORDS = {
    "하루": 1,
    "이틀": 2,
    "사흘": 3,
    "나흘": 4,
    "닷새": 5,
    "엿새": 6,
    "이레": 7,
    "여드레": 8,
    "아흐레": 9,
    "열흘": 10,
}
_DAY_WORDS.update(
    {"열" + word: 10 + value for word, value in tuple(_DAY_WORDS.items()) if value < 10}
)
_DAY_WORDS["보름"] = 15
_COUNTER_ONES = {
    "한": 1,
    "두": 2,
    "세": 3,
    "네": 4,
    "다섯": 5,
    "여섯": 6,
    "일곱": 7,
    "여덟": 8,
    "아홉": 9,
}
_COUNTER_TENS = {
    "열": 10,
    "스물": 20,
    "서른": 30,
    "마흔": 40,
    "쉰": 50,
    "예순": 60,
    "일흔": 70,
    "여든": 80,
    "아흔": 90,
}
_NATIVE_CREW = {**_COUNTER_ONES, **_COUNTER_TENS, "스무": 20}
_NATIVE_CREW.update(
    {ten + one: n + m for ten, n in _COUNTER_TENS.items() for one, m in _COUNTER_ONES.items()}
)


def _alternation(words: dict[str, int]) -> str:
    return "|".join(sorted(map(re.escape, words), key=len, reverse=True))


_SMALL_COUNTER = _alternation(_COUNTER_ONES)
_TENS_COUNTER = _alternation(_COUNTER_TENS)
_COUNTER_WORDS = (
    rf"(?:(?:{_TENS_COUNTER})[ \t]*(?:{_SMALL_COUNTER})|{_TENS_COUNTER}|스무|{_SMALL_COUNTER})"
)
_DAY_WORD_PATTERN = rf"(?:열[ \t]*(?:{_alternation({k: v for k, v in _DAY_WORDS.items() if v < 10})})|{_alternation(_DAY_WORDS)})"


def _native_count(raw: str, words: dict[str, int]) -> int | None:
    return words.get(re.sub(r"[ \t]+", "", raw))


# 공백만으로 나열된 날짜는 완전한 날짜 개수가 일치할 때만 연결한다.
_LIST_COUNT_BODY = (
    rf"(?:(?P<days>{_DAY_WORD_PATTERN})|"
    rf"(?P<count>{_COUNTER_WORDS}|[1-9]\d*)\s*(?:개\s*의?\s*)?(?:날짜|일자|날))"
    r"(?=$|[.,!?;]|[ \t]*(?:으로|로|이고|입니다|이에요|만))"
)
_LIST_COUNT = re.compile(r"^[ \t]*" + _LIST_COUNT_BODY)

# 수사의 일부를 따로 읽거나 단위 뒤의 분량/배수 접미사를 수량으로 읽지 않는다.
_CREW_WORDS = _COUNTER_WORDS
_CREW_UNIT = (
    r"(?:명(?!\d|분|당|씩|단|짜리|치|함|칭|대)"
    r"|인(?=$|[\s.,!?;~/∼〜–—－-]|으로|로|[은는을를도가과와만]|부터|에서|입니다|이에요|이나|나))"
)
_CREW = re.compile(rf"(?<!\d)(?<!\d[,.])(?:(\d{{1,2}})|(?<![가-힣])({_CREW_WORDS}))\s*{_CREW_UNIT}")
_CREW_COUNT = rf"(?:(?<!\d)\d+|(?<![가-힣])(?:{_CREW_WORDS}))"
_CREW_RANGE_LINK = r"(?:[~\-–—－∼〜/]|부터|에서|또는|혹은|아니면|이나|나)"
_CREW_RANGE = re.compile(
    rf"{_CREW_COUNT}\s*(?:{_CREW_UNIT}\s*)?{_CREW_RANGE_LINK}\s*{_CREW_COUNT}\s*{_CREW_UNIT}"
    rf"|(?<![가-힣])(?:{_SMALL_COUNTER})\s*(?:{_SMALL_COUNTER})\s*{_CREW_UNIT}"
)
# 사람/분은 고객, 동행인, 시간 단위에도 쓰인다. 작업자 명시 대상에
# 직접 연결된 수량만 인정하고, 단위만 잘라 낸 근거는 전체 원문에서 검증한다.
_CREW_PEOPLE_UNIT = (
    r"(?:사람|분)(?=$|[\s.,!?！？;。:~∼〜–—－/\-]"
    r"|으로|로|은|는|이|가|을|를|만|도|부터|에서|입니다|이에요|이고|이나|나|과|와|랑)"
)
_CREW_PEOPLE = re.compile(
    rf"(?<!\d)(?<!\d[,.])(?:(\d{{1,2}})|(?<![가-힣])({_CREW_WORDS}))\s*{_CREW_PEOPLE_UNIT}"
)
_CREW_PEOPLE_PREFIX = re.compile(
    r"(?<![가-힣])(?:(?:현장|투입|시공|공사|작업)\s*)?"
    r"(?:인원(?:\s*수)?|작업자(?:\s*수)?|인력|인부)"
    r"\s*(?:[은는이가을를만도:]\s*)?(?:총\s*|정확히\s*)?$"
)
_CREW_PEOPLE_OTHER_UNIT = re.compile(
    r"^\s*(?:(?:전|후|뒤)(?:에|부터|까지|(?=$|[\s,.!?]))|"
    r"간|동안|당|씩|짜리|량|치|분량|시간|초|째|만에|이내|의)"
)
_CREW_PEOPLE_RANGE = re.compile(
    rf"{_CREW_COUNT}\s*(?:(?:{_CREW_UNIT}|{_CREW_PEOPLE_UNIT})\s*)?"
    rf"{_CREW_RANGE_LINK}\s*{_CREW_COUNT}\s*(?:{_CREW_UNIT}|{_CREW_PEOPLE_UNIT})"
    rf"|(?<![가-힣])(?:{_SMALL_COUNTER})\s*(?:{_SMALL_COUNTER})\s*{_CREW_PEOPLE_UNIT}"
)
_PARTIAL_COUNT_PREFIX = re.compile(
    rf"(?:\d+|(?<![가-힣])(?:{_COUNTER_WORDS}|[십백천만억]|서|대))\s+$"
)
_SIGNED_QUANTITY_PREFIX = re.compile(r"(?:^|[^\d])[+\-−]\s*$")


def _partial_quantity_prefix(text: str, start: int) -> bool:
    prefix = text[:start]
    return bool(_PARTIAL_COUNT_PREFIX.search(prefix) or _SIGNED_QUANTITY_PREFIX.search(prefix))


# 일수와 달력의 '일'은 다른 값이다. 날짜가 아닌 완전한 수사·일 단위
# 토큰만 반환한다. 범위/조건/정정 의도와 업무 상한은 별도 검증한다.
_DURATION = re.compile(
    rf"(?:(?<!\d)(?<!\d[,.])(?P<arabic>\d{{1,3}})\s*일"
    rf"|(?<![가-힣\d])(?P<days>{_DAY_WORD_PATTERN})"
    rf"|(?<![가-힣\d])(?P<count>{_COUNTER_WORDS})[ \t]*(?:날|일(?=\s*(?:간|동안))))"
)
_DURATION_TAIL = re.compile(
    r"(?=$|[\s.,!?！？;。:~∼〜–—－+/\-]"
    r"|으로|로|은|는|이|가|을|를|만|도|과|와|이나|나|랑|부터|까지|에서|입니다|"
    r"간|동안|정도|가량|쯤|내외|안팎|이내|이상|이하|미만|초과|"
    r"하고|걸리|걸려|소요|예요|예정)"
)
_DURATION_NON_QUANTITY = re.compile(
    r"^\s*(?:째|이후|이전|전(?:에|부터|까지|$)|후(?:에|부터|까지|$)|뒤(?:에|부터|까지|$)|"
    r"당|씩|치|분량|요일|짜리|날짜)|^(?:자(?:로|는|가|를|$)|정(?!도))"
)


def _calendar_prefix(prefix: str) -> bool:
    text = prefix.rstrip()
    if not text or text[-1] not in "월년달해":
        return False
    before = text[:-1].rstrip()
    return (text[-1] in "월년" and before[-1:].isdecimal()) or before.endswith(
        ("이번", "다음", "지난", "매")
    )


_DURATION_MARKER = re.compile(r"^\s*(?:간|동안)")
_DURATION_APPROX_PREFIX = re.compile(r"(?:최대|최소|대략|약|적어도|기껏해야)\s*$")
_DURATION_ATTACHED_APPROX = re.compile(
    rf"(?<![가-힣])(?:최대|최소|대략|약|적어도|기껏해야)(?:{_DAY_WORD_PATTERN})"
)
_DURATION_APPROX_TAIL = re.compile(
    r"^\s*(?:정도|가량|쯤|내외|안팎|이내|이상|이하|미만|초과|"
    r"안으로|범위|까지|부터|사이|[~∼〜–—－/+\-]|또는|혹은|아니면|이나|나(?=\s)|"
    r"반(?:나절)?(?=$|[\s,.!?]|으로|로|은|는|이|가)|"
    rf"(?:\d+(?:[.,]\d+)?|{_COUNTER_WORDS})\s*(?:시간|분|주|개월|달))"
)
# 일반 근삿수사는 시간 단위나 단독 수량 경계에서만 일수의 위험
# 근거이다. 다른 명사를 세는 표현은 정확한 일수 토큰과 경쟁하지 않는다.
# 일수 자체를 뜻하는 합성어는 별도로 보존한다.
_DURATION_APPROX_COUNTER_END = (
    rf"(?=\s*(?:(?:날|일|시간|분|주|개월|달|년){_DURATION_TAIL.pattern}|"
    r"$|[.,!?！？;。:~∼〜–—－+/\-]|"
    r"(?:으로|로|은|는|이|가|을|를|만|도)(?=$|[\s,.!?！？;。])|"
    r"입니다|이에요|예요|이고|정도|가량|쯤|내외|안팎|이내|이상|이하|미만|초과))"
)
_DURATION_COMPOUND_RANGE = re.compile(
    rf"(?<![가-힣])(?:하루\s*이틀|이틀\s*사흘|사흘\s*나흘|사나흘|이삼일|삼사일|"
    rf"(?:한두|두세|서너|대여섯|예닐곱){_DURATION_APPROX_COUNTER_END}|"
    rf"(?:{_SMALL_COUNTER})\s*(?:{_SMALL_COUNTER})\s*(?:날|일){_DURATION_TAIL.pattern})"
)
_DURATION_RANGE_PREFIX = re.compile(
    rf"(?:\d+|{_COUNTER_WORDS}|{_DAY_WORD_PATTERN})\s*(?:[~∼〜–—－/\-]|또는|혹은|아니면|이나|나)\s*$"
)

_DIGIT_RUN = re.compile(r"\d+")


def _duration_malformed(text: str) -> bool:
    """부호·소수점·큰 수의 위험 판정은 완전한 숫자 단위로 검사한다."""
    if "반나절" in text:
        return True
    for digits in _DIGIT_RUN.finditer(text):
        start, end = digits.span()
        left = skip_space_back(text, start)
        if (
            left > 0
            and text[left - 1] in "+-−"
            and (left == 1 or not (text[left - 2].isdecimal() or text[left - 2] in ".,"))
            and text.startswith("일", skip_space(text, end))
        ):
            return True
        if start and (text[start - 1].isdecimal() or text[start - 1] in ".,"):
            continue
        if end - start >= 4 and text.startswith("일", skip_space(text, end)):
            return True
        # 쉼표와 소수점으로 연결된 완전한 숫자열은 한 번만 읽는다.
        last = end
        while last + 1 < len(text) and text[last] in ".," and text[last + 1].isdecimal():
            last += 2
            while last < len(text) and text[last].isdecimal():
                last += 1
        if last > end and text.startswith("일", skip_space(text, last)):
            return True
    return False


_DURATION_OTHER_UNIT_PREFIX = re.compile(
    rf"(?:\d+(?:[.,]\d+)?|{_COUNTER_WORDS})\s*(?:시간|분|주|개월|달)"
    r"\s*(?:하고|와|과|\+)?\s*$"
)


def duration_tokens(evidence: str) -> list[tuple[int, int, int]]:
    """인쇄된 정수 일수와 원문 위치. 명시 달력 날짜/일자 조각은 제외한다."""
    calendar = sorted(
        [
            (m.start(), m.end())
            for pattern in (_FULL, _MONTH_DAY)
            for m in pattern.finditer(evidence)
        ]
    )
    compounds = [m.span() for m in _DURATION_COMPOUND_RANGE.finditer(evidence)]
    result = []
    for match in _DURATION.finditer(evidence):
        a, b = match.span()
        prefix, tail = evidence[:a], evidence[b:]
        if (
            _partial_quantity_prefix(evidence, a)
            or _calendar_prefix(prefix)
            or not _DURATION_TAIL.match(tail)
            or _DURATION_NON_QUANTITY.match(tail)
            or any(a < end and b > start for start, end in calendar)
            or any(a < end and b > start for start, end in compounds)
        ):
            continue
        # 명시 월을 생략한 다음 목록 날짜도 일수로 바꾸지 않는다.
        # '12/8부터 3일간'처럼 명확한 기간 단위가 있으면 기간 수량이다.
        prior = max((end for _, end in calendar if end <= a), default=None)
        if prior is not None and not _DURATION_MARKER.match(tail):
            gap = evidence[prior:a]
            if gap.isspace() or _LIST_GAP.fullmatch(gap) or _RANGE_GAP.fullmatch(gap):
                # 연결된 생략 월 날짜의 다음 날짜도 동일한 달력 목록이다.
                calendar.append((a, b))
                continue
        number = (
            int(match["arabic"])
            if match["arabic"]
            else _native_count(match["days"], _DAY_WORDS)
            if match["days"]
            else _native_count(match["count"], _NATIVE_CREW)
        )
        if number is not None:
            result.append((a, b, number))
    return result


def duration_mentions(evidence: str) -> set[int]:
    """일수 토큰의 값 집합. 실제 대상·확정성은 호출자가 검증한다."""
    return {number for _, _, number in duration_tokens(evidence)}


def duration_quantity_risk(evidence: str) -> bool:
    """일수 절에서 범위·상한·근삿값·부분 단위를 정확값으로 채택하지 않는다.

    여러 완전한 값의 정정 관계는 호출자가 검증한다. 별개 필드 절에
    이 함수를 적용하지 않으며, 정정 검증 뒤 폐기 구간을 마스킹할 수 있다.
    """
    # '2.5일'은 일수 절에서는 소수 수량일 수 있어 월/일 표기로 가리지 않는다.
    without_calendar = _FULL.sub(" ", evidence)
    if (
        _DURATION_COMPOUND_RANGE.search(evidence)
        or _duration_malformed(without_calendar)
        or _DURATION_ATTACHED_APPROX.search(evidence)
    ):
        return True
    for a, b, _ in duration_tokens(evidence):
        prefix, tail = evidence[:a], evidence[b:]
        tail = re.sub(r"^\s*(?:간|동안)\s*", "", tail, count=1)
        if (
            _DURATION_APPROX_PREFIX.search(prefix)
            or _DURATION_RANGE_PREFIX.search(prefix)
            or _DURATION_OTHER_UNIT_PREFIX.search(prefix)
            or _DURATION_APPROX_TAIL.match(tail)
        ):
            return True
    return False


_CREW_REPLACEMENT = re.compile(
    rf"(?:(?:아까|앞서|방금|이전에)\s*)?"
    rf"(?:(?:작업\s*인원|인원(?:수)?|작업자(?:\s*수)?|인부|사람)(?:은|는|만|을|를|이|가|:)?\s*)?"
    rf"(?P<old>{_CREW_COUNT})\s*(?:{_CREW_UNIT})?\s*에서\s*"
    rf"(?P<new_token>(?P<new>{_CREW_COUNT})\s*{_CREW_UNIT})\s*(?:으로|로)\s*"
    r"(?:(?:정정|수정|변경|확정)\s*(?:할게요|하겠습니다|해\s*주세요|해\s*줘요|합니다|해요)"
    r"|(?:바꿔|고쳐)\s*(?:주세요|줘요|요)|바꿀게요|고칠게요|바꾸겠습니다|고치겠습니다)"
)
_CREW_CORRECTION_UNCERTAIN = re.compile(
    r"[?？]|아마|혹시|미정|보류|검토|취소|철회|고민|가능하면|예정|잠정|"
    rf"모르|애매|정도|쯤|또는|아니면|사이|원래대로|{WITHDRAW_IDIOM}|하지\s?마|하지\s?않"
)

# 숫자 안의 소수점·천 단위 쉼표는 문장 경계가 아니다.
# 아래 검색 패턴은 공백 연속열을 한 칸으로 축약한 절에서만 사용한다.
_SCALAR_AMOUNT_TARGET = re.compile(r"금액|견적|비용|가격|공사비|총액|합계|추가금|추가비")
_SCALAR_CREW_TARGET = re.compile(
    r"인원|작업자|인부|사람|(?:\d|한|두|세|네|다섯|여섯|일곱|여덟|아홉|열)\s?명"
)
_SCALAR_OTHER_TARGET = re.compile(
    r"부가세|세금|면세|기간|일수|일정|작업일|날짜|유효기간|종료일|시작일|항목|제목|사유|이유|"
    r"\d{1,2}\s?(?:월|/)\s?\d{1,2}|\d\s?일"
)
_SCALAR_CONDITION = re.compile(
    rf"{CONDITIONAL_LINK.pattern}|(?:라면|다면|으면|이면|하면)|경우|일\s?때"
)


def _scalar_clause_ends(text: str) -> list[int]:
    """이전 오프셋 계약용 어댑터. 실제 검증은 intent_clauses를 사용한다."""
    ends: list[int] = []
    pos = 0
    size = len(text)
    while pos < size:
        char = text[pos]
        if char in ",.":
            if not (
                pos > 0
                and text[pos - 1].isdecimal()
                and pos + 1 < size
                and text[pos + 1].isdecimal()
            ):
                ends.append(pos + 1)
        elif char in ";!?？。\n":
            ends.append(pos + 1)
        elif char.isspace() and pos > 0 and text[pos - 1] in "고며":
            end = pos + 1
            while end < size and text[end].isspace():
                end += 1
            tail = text[end:]
            if any(
                mention.start == 0 and re.match(r"\s*[은는이가을를]", tail[mention.end :])
                for form in ("quote", "dates", "period", "extra")
                for mention in field_mentions(form, tail)
            ):
                ends.append(end)
                pos = end
                continue
        pos += 1
    ends.append(size)
    return ends


@dataclass(frozen=True)
class ScalarRiskScope:
    """원문 절과 상위 소유권을 하위 위험 검사에 전달한다."""

    start: int
    end: int
    fields: frozenset[str]
    unbound: bool = False


def _scalar_lexical_scopes(
    text: str, field: str, form: FormName | None
) -> tuple[ScalarRiskScope, ...]:
    # 한글 금액 등 문맥 없는 어휘 정규화도 같은 명명/절 경계를 사용한다.
    # 폼을 아는 호출자는 반드시 그 폼을 넘긴다.
    if form is None:
        form = (
            "dates"
            if field == "crew"
            else "extra"
            if any(m.fields & {"title", "reason"} for m in field_mentions("extra", text))
            else "quote"
        )
    return tuple(ScalarRiskScope(p.start, p.end, p.fields) for p in intent_clauses(form, text))


def scalar_clause_risk(
    text: str,
    field: str,
    spans: list[tuple[int, int]],
    risk: re.Pattern[str],
    *,
    masked: str | None = None,
    form: FormName | None = None,
    scopes: Iterable[ScalarRiskScope] | None = None,
) -> bool:
    """같은 숫자 필드와 대상 없는 보류·조건만 검사하고 독립 필드는 구분한다.

    risk는 공백 연속열을 한 칸으로 축약한 절에 대한 검색 패턴이다.
    """
    own, other = (
        (_SCALAR_AMOUNT_TARGET, _SCALAR_CREW_TARGET)
        if field == "amount"
        else (_SCALAR_CREW_TARGET, _SCALAR_AMOUNT_TARGET)
    )
    checked = text if masked is None else masked
    # 마스킹된 문자열에서 경계나 소유권을 다시 추론하지 않는다.
    parts = tuple(scopes) if scopes is not None else _scalar_lexical_scopes(text, field, form)
    first = min((a for a, _ in spans), default=len(text))
    for part in parts:
        start, end = part.start, part.end
        clause = re.sub(r"\s+", " ", text[start:end])
        candidate = polite_execution_risk_text(re.sub(r"\s+", " ", checked[start:end]))
        has_value = any(a < end and b > start for a, b in spans)
        if (risk.search(candidate) or CONDITIONAL_LINK.search(candidate)) and (
            has_value
            or part.unbound
            or field in part.fields
            or not part.fields
            and (
                own.search(clause)
                or not (other.search(clause) or _SCALAR_OTHER_TARGET.search(clause))
            )
            or (end <= first and _SCALAR_CONDITION.search(candidate))
        ):
            return True
    return False


@dataclass(frozen=True)
class CrewReplacement:
    old: int
    new: int
    old_span: tuple[int, int]
    new_span: tuple[int, int]


def crew_replacement(text: str) -> CrewReplacement | None:
    """명시적인 인원 정정에서만 '에서'를 범위 대신 교체 연결로 읽는다."""
    matches = []
    for part in intent_clauses("dates", text):
        if part.fields and part.fields != {"crew"}:
            continue
        clause = text[part.start : part.end]
        body = clause.strip().rstrip(",;.!。").rstrip()
        offset = part.start + len(clause) - len(clause.lstrip())
        # 복합 명칭도 공통 소유권이 입증한 원문 구간만 제거한다.
        # 새 수량과 정정 술어의 완결성은 기존 문법으로 독립 검증한다.
        marks = [m for m in part.mentions if m.fields == {"crew"} and m.start == offset]
        if len(marks) == 1:
            lead = marks[0].end - offset
            particle = re.match(r"\s*[은는이가을를만:]?\s*", body[lead:])
            lead += particle.end() if particle else 0
            body = body[lead:]
            offset += lead
        match = _CREW_REPLACEMENT.fullmatch(body)
        if match is not None:
            matches.append((offset, body, match))
    if len(matches) != 1:
        return None
    offset, body, match = matches[0]
    if text.count(body) != 1:
        return None
    if scalar_clause_risk(
        text,
        "crew",
        [(offset, offset + len(body))],
        _CREW_CORRECTION_UNCERTAIN,
        form="dates",
    ):
        return None
    other = text[:offset] + " " + text[offset + len(body) :]
    if _CREW.search(other) or _CREW_RANGE.search(other):
        return None
    numbers = []
    for raw in (match["old"], match["new"]):
        native = _native_count(raw, _NATIVE_CREW)
        if native is not None:
            numbers.append(native)
        elif re.fullmatch(r"[1-9]\d?", raw):
            numbers.append(int(raw))
        else:
            return None
    old, new = numbers
    if old == new:
        return None
    return CrewReplacement(
        old,
        new,
        (offset + match.start("old"), offset + match.end("old")),
        (offset + match.start("new_token"), offset + match.end("new_token")),
    )


@dataclass(frozen=True)
class _Token:
    start: int
    end: int
    day: int
    value: date | None
    kind: str  # 명시적 날짜, 'N일', 목록 중간의 숫자
    month: int | None = None
    year: int | None = None


def complete_range_evidence(evidence: str, text: str) -> bool:
    """근거가 '12/14~19명'의 단위 앞에서 잘리면 19를 날짜로 승격하지 않는다."""
    if evidence == text or not re.search(r"(?:[~∼〜－—–]\s*|-\s+)\d{1,2}$", evidence):
        return True
    return any(
        _SHORT_RANGE_TAIL.match(text[match.end() :])
        for match in re.finditer(re.escape(evidence), text)
    )


def _date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _infer_year(month: int, day: int, context: "AssistContext") -> int:
    """기존 검증기의 연도 추정 정책을 그대로 사용한다."""
    anchor = context.base_month or context.today
    if context.base_month is None:
        candidate = _date(anchor.year, month, day)
        return (
            anchor.year if candidate is not None and candidate >= context.today else anchor.year + 1
        )
    candidates = [
        found
        for year in (anchor.year - 1, anchor.year, anchor.year + 1)
        if (found := _date(year, month, day)) is not None
    ]
    return (
        min(candidates, key=lambda value: abs((value - anchor).days)).year
        if candidates
        else anchor.year
    )


def _link(gap: str) -> str | None:
    if _LIST_GAP.fullmatch(gap):
        return "list"
    if _RANGE_GAP.fullmatch(gap):
        return "range"
    return None


def _list_count_value(match: re.Match[str]) -> int | None:
    raw = match["days"] or match["count"]
    return (
        int(raw)
        if raw.isdecimal()
        else _native_count(raw, _DAY_WORDS if match["days"] else _NATIVE_CREW)
    )


def _counted_whitespace_lists(evidence: str, tokens: list[_Token], links: list[str | None]) -> None:
    """개수가 확인된 인접 날짜 열거의 공백 경계만 목록으로 표시한다."""
    i = 0
    while i < len(tokens):
        first = tokens[i]
        if (
            first.kind != "explicit"
            or first.value is None
            or not evidence[first.start : first.end].rstrip().endswith("일")
        ):
            i += 1
            continue
        j = i + 1
        while (
            j < len(tokens)
            and tokens[j].kind == "bare"
            and re.fullmatch(r"[ \t]+", evidence[tokens[j - 1].end : tokens[j].start])
        ):
            j += 1
        if j == i + 1:
            i += 1
            continue
        match = _LIST_COUNT.match(evidence[tokens[j - 1].end :])
        if match:
            count = _list_count_value(match)
            days = [token.day for token in tokens[i:j]]
            if (
                count == len(days) == len(set(days))
                and all(_date(first.value.year, first.value.month, day) for day in days)
                and (i == 0 or links[i - 1] is None)
                and (j == len(tokens) or links[j - 1] is None)
            ):
                links[i : j - 1] = ["list"] * (j - i - 1)
        i = j


def date_tokens(
    evidence: str, context: "AssistContext", *, allow_bare: bool = True
) -> list[tuple[int, int, date]]:
    """원문 [start:end]에 대응하는 날짜 토큰을 반환한다.

    생략한 월은 연결된 *앞의 명시적 날짜*에서만 상속한다.
    양쪽 명시 월이 다르면 해당 생략 날짜는 보류한다. 독립적인 'N일'은
    기존 계약처럼 base_month가 있는 경우에만 해석한다.
    """
    tokens: list[_Token] = []
    occupied: list[tuple[int, int]] = []

    def add(
        start: int,
        end: int,
        day: int,
        value: date | None,
        kind: str,
        *,
        month: int | None = None,
        year: int | None = None,
    ) -> None:
        if any(start < b and end > a for a, b in occupied):
            return
        occupied.append((start, end))  # 잘못된 명시 날짜도 부분 토큰으로 다시 읽지 않는다.
        tokens.append(_Token(start, end, day, value, kind, month, year))

    for match in _FULL.finditer(evidence):
        y, m, d = (int(group) for group in match.groups())
        add(match.start(), match.end(), d, _date(y, m, d), "explicit", month=m, year=y)
    for match in _MONTH_DAY.finditer(evidence):
        m, d = (int(group) for group in match.groups())
        add(
            match.start(),
            match.end(),
            d,
            _date(_infer_year(m, d, context), m, d),
            "explicit",
            month=m,
        )

    if allow_bare:
        for match in _BARE_DAY.finditer(evidence):
            add(match.start(), match.end(), int(match[1]), None, "bare")
        for match in _SHORT_DAY.finditer(evidence):
            add(match.start(), match.end(), int(match[1]), None, "short")

    tokens.sort(key=lambda token: token.start)
    links = [_link(evidence[left.end : right.start]) for left, right in pairwise(tokens)]
    _counted_whitespace_lists(evidence, tokens, links)

    # 연도가 명시된 날짜와 목록·범위로 연결된 월/일은 같은 연도를 사용한다.
    # 연결되지 않은 다른 절의 날짜는 기존 추정 정책을 유지한다.
    anchor_year: int | None = None
    for i, token in enumerate(tokens):
        if i > 0 and links[i - 1] is None:
            anchor_year = None
        if token.year is not None:
            anchor_year = token.year
        elif anchor_year is not None and token.month is not None:
            tokens[i] = replace(token, value=_date(anchor_year, token.month, token.day))

    def nearest_explicit(index: int, direction: int) -> _Token | None:
        cursor = index
        while 0 <= cursor + direction < len(tokens):
            edge = cursor if direction > 0 else cursor - 1
            if links[edge] is None:
                break
            cursor += direction
            if tokens[cursor].kind == "explicit":
                return tokens[cursor]
        return None

    found: list[tuple[int, int, date]] = []
    for i, token in enumerate(tokens):
        if token.kind == "explicit":
            if token.value is not None:
                found.append((token.start, token.end, token.value))
            continue

        left = nearest_explicit(i, -1)
        right = nearest_explicit(i, 1)
        # 단독 숫자는 목록 중간 또는 명시 날짜 바로 뒤의 기호 범위 끝에서만 사용한다.
        if token.kind == "short":
            list_middle = (
                left is not None and 0 < i < len(tokens) - 1 and links[i - 1] == links[i] == "list"
            )
            range_end = (
                i > 0
                and tokens[i - 1].kind == "explicit"
                and left is not None
                and left.value is not None
                and token.day >= left.value.day
                and _SHORT_RANGE_GAP.fullmatch(evidence[left.end : token.start])
                and _SHORT_RANGE_TAIL.match(evidence[token.end :])
            )
            if not (list_middle or range_end):
                continue
        if left and left.value is None or right and right.value is None:
            continue
        if (
            left
            and right
            and left.value
            and right.value
            and (left.value.year, left.value.month) != (right.value.year, right.value.month)
        ):
            continue

        # '12/8부터 3일'은 기간일 수 있다. '~9일'은 명시적 범위다.
        if (
            token.kind == "bare"
            and i > 0
            and evidence[tokens[i - 1].end : token.start].strip() in ("부터", "에서")
            and not evidence[token.end :].lstrip().startswith("까지")
        ):
            continue

        anchor = left.value if left else None
        if anchor is None and token.kind == "bare" and context.base_month is not None:
            # 같은 원문에 명시 월이 있는데 문맥 연결을 확인 못한 날은
            # 기준월로 덮어 해석하지 않는다('12/11,32,13일' 등).
            if any(item.kind == "explicit" for item in tokens):
                continue
            base = context.base_month
            anchor = date(base.year, base.month, 1)
        if anchor is None:
            continue
        value = _date(anchor.year, anchor.month, token.day)
        if value is not None:
            found.append((token.start, token.end, value))
    return found


def date_mentions(
    evidence: str, context: "AssistContext", *, expand: bool, allow_bare: bool = True
) -> set[date]:
    """원문 날짜 집합. 목록은 열거만, 명시적 범위만 최대 JOB_MAX_DAYS일 확장한다."""
    tokens = date_tokens(evidence, context, allow_bare=allow_bare)
    found = {value for _, _, value in tokens}
    if expand:
        for left, right in pairwise(tokens):
            if _link(evidence[left[1] : right[0]]) != "range":
                continue
            span = (right[2] - left[2]).days
            if 0 <= span <= JOB_MAX_DAYS:
                found.update(left[2] + timedelta(days=n) for n in range(span + 1))
    return found


_COUNTED_DATE_END = re.compile(r"(?<!\d)\d{1,3}\s*일(?:\s*까지)?")
_DECLARED_DATE_COUNT = re.compile(r"(?<![가-힣\d])" + _LIST_COUNT_BODY)
_DECLARED_COUNTER_COUNT = re.compile(
    rf"(?<![가-힣\d])(?:(?:총|합계)\s*(?P<days>[1-9]\d*)\s*일"
    rf"|(?P<count>{_COUNTER_WORDS}|[1-9]\d*)\s*개)"
    r"(?=$|[.,!?;]|[ \t]*(?:으로|로|이고|입니다|이에요|만))"
)


def date_list_declared_counts(evidence: str) -> list[tuple[int, int, int]]:
    """명시 날짜 개수 토큰만 반환한다. 호출자가 실제 목록 필드의 범위를 제공한다."""
    result = []
    for match in sorted(
        [*_DECLARED_DATE_COUNT.finditer(evidence), *_DECLARED_COUNTER_COUNT.finditer(evidence)],
        key=lambda match: (match.start(), -match.end()),
    ):
        a, b = match.span()
        if _partial_quantity_prefix(evidence, a) or any(
            a < stop and b > start for start, stop, _ in result
        ):
            continue
        raw = match["days"] or match["count"]
        count = int(raw) if raw.isdecimal() else _list_count_value(match)
        if count is not None:
            result.append((a, b, count))
    return result


def date_list_leading_count_mismatch(
    count_text: str, dates_text: str, context: "AssistContext"
) -> bool:
    """선행 개수와 같은 필드의 실제 열거 목록이 일치하는지 검사한다.

    서로 다른 문장이더라도 호출자가 실제 대상과 연결을 입증한 범위만
    넘긴다. 개수만으로 날짜를 생성하지 않으며, 미열거·중복도 거부한다.
    """
    counts = {value for _, _, value in date_list_declared_counts(count_text)}
    if not counts:
        return False
    tokens = date_tokens(dates_text, context)
    if len(tokens) != len({day for _, _, day in tokens}):
        return True
    return counts != {len(date_mentions(dates_text, context, expand=True))}


def date_list_count_mismatch(evidence: str, context: "AssistContext") -> bool:
    """명시한 개수와 실제 연결된 날짜 목록이 다르면 부분 목록을 거부한다.

    날짜 열거가 있는 절에만 적용한다. 날짜가 아닌 기간이나 독립 필드의
    수량은 호출자가 제외한다. 기존 date_tokens의 보수적 부분 추출은 유지한다.
    """
    endings = {
        match.end()
        for pattern in (_FULL, _MONTH_DAY, _COUNTED_DATE_END)
        for match in pattern.finditer(evidence)
    }
    for end in sorted(endings):
        count_match = _LIST_COUNT.match(evidence[end:])
        if not count_match:
            continue
        count = _list_count_value(count_match)
        tokens = date_tokens(evidence[: end + count_match.end()], context)
        before = [part for part in tokens if part[1] <= end]
        if not before or evidence[before[-1][1] : end].strip() not in ("", "까지"):
            return True
        first = len(before) - 1
        while first > 0:
            gap = evidence[before[first - 1][1] : before[first][0]]
            if not (gap.isspace() or _link(gap)):
                break
            first -= 1
        selected = before[first:]
        named = {day for _, _, day in selected}
        expanded = date_mentions(
            evidence[selected[0][0] : end + count_match.end()], context, expand=True
        )
        if len(named) != len(selected) or count != len(expanded):
            return True
    return False


def crew_tokens(evidence: str, *, allow_people: bool = False) -> list[tuple[int, int, int]]:
    """범위·'두세 명'을 제외한 인원 수와 원문 위치를 반환한다."""
    uncertain = [(m.start(), m.end()) for m in _CREW_RANGE.finditer(evidence)]
    people = list(_CREW_PEOPLE.finditer(evidence))
    if people:
        uncertain.extend(
            m.span()
            for m in _CREW_PEOPLE_RANGE.finditer(evidence)
            if any(m.start() < person.end() and m.end() > person.start() for person in people)
        )
    values = []
    matches = [(m, False) for m in _CREW.finditer(evidence)] + [(m, True) for m in people]
    for match, is_people in sorted(matches, key=lambda pair: pair[0].start()):
        if is_people and (
            not (allow_people or _CREW_PEOPLE_PREFIX.search(evidence[: match.start()]))
            or _CREW_PEOPLE_OTHER_UNIT.match(evidence[match.end() :])
        ):
            continue
        if _partial_quantity_prefix(evidence, match.start()) or any(
            match.start() < b and match.end() > a for a, b in uncertain
        ):
            continue
        number = int(match[1]) if match[1] else _native_count(match[2], _NATIVE_CREW)
        if number is not None:
            values.append((match.start(), match.end(), number))
    return values


def crew_mentions(evidence: str) -> set[int]:
    """'네 명' 등 인원 단위가 있는 수량만 반환한다. 범위·'두세 명'은 보류한다."""
    return {value for _, _, value in crew_tokens(evidence)}
