"""업체 도우미 유지 요청의 실제 대상과 참조 범위를 구분한다."""

import re
from collections.abc import Iterator
from dataclasses import dataclass, replace
from typing import Literal

from app.vendor.assist_lexical import skip_space, skip_space_back
from app.vendor.assist_schemas import FORM_FIELDS, FormName
from app.vendor.assist_vat_semantics import (
    VAT_LABEL,
    scoped_vat_tokens,
    vat_selection_mentions,
)

Reference = Literal["previous", "current", "pending"]

# 과거 제외 결정에 붙은 날짜는 새 기간 경계가 아니다.
EXCLUDED_EDIT_REFERENCE = (
    r"(?:제외하|빼|쉬)기로\s*(?:한|정한|했던)"
    r"(?=\s*(?:\d{1,2}\s*월\s*)?\d{1,2}\s*일)"
)


@dataclass(frozen=True)
class FieldMention:
    fields: frozenset[str]
    start: int
    end: int
    selected_mode: str | None = None


@dataclass(frozen=True)
class RetentionScope:
    fields: frozenset[str]
    start: int
    predicate_start: int
    end: int
    reference: Reference
    remaining: bool
    first: bool
    compatible: bool
    shared: bool = False
    linked_prefix: bool = False


@dataclass(frozen=True)
class IntentClause:
    start: int
    end: int
    mentions: tuple[FieldMention, ...]
    retentions: tuple[RetentionScope, ...]
    fields: frozenset[str]
    boundary: Literal["sentence", "question", "comma", "connective", "end"]


# 문자열의 필드명과 값은 다른 문법 단위이다. 명시된 이름/사유
# 별칭은 의도·근거·대입 구간에서 동일한 정의를 사용한다. 명칭이
# 생략된 공사 주어는 추가 공사의 한정어와 실제 조사/구분자가
# 함께 있어야 한다. 일반 작업 명사 자체를 필드로 만들지 않는다.
EXTRA_EXPLICIT_TITLE = (
    r"(?:(?:추가\s*(?:작업|공사|시공)?|공사|시공|작업)\s*)?"
    r"(?:항목\s*(?:제목|이름)|항목명|작업명|공사명|제목|항목|이름)"
)
EXTRA_IMPLICIT_TITLE = (
    r"추가\s*(?:작업|공사|시공)"
    r"(?=\s*(?:[:=]|[은는이가을를](?:\s|$)|"
    r"(?:변경|수정|정정|제안|후보|선택|요청)(?:\s*[은는이가을를])?\s*(?:취소|철회|없(?:던|었던))))"
)
EXTRA_TITLE_LABEL = rf"(?:{EXTRA_EXPLICIT_TITLE}|{EXTRA_IMPLICIT_TITLE})"
EXTRA_REASON_LABEL = (
    r"(?:(?:추가금|추가\s*(?:비용|작업|공사|시공)?)(?:[을를이가])?\s*)?"
    r"(?:(?:(?:하게|발생하게|필요하게)\s*된\s*)?(?:사유|이유|경위|원인)|"
    r"왜\s+[^,;.!?！？。\n]{1,48}는지\s*(?:쓰는|적는|기록하는|입력하는))"
)
# 공백 뒤에 시작하는 일반 명사(항목 이름표 등)의 첫 음절을
# 조사로 읽지 않는다. 붙여 쓰는 조사와 독립 조사/구분자를 구분한다.
EXTRA_LABEL_MARKER = (
    r"(?:(?:\s*(?:칸|입력란|란))?\s*[:=]|"
    r"(?:\s*(?:칸|입력란|란))?(?:에는|[은는을를이가만도에])|"
    r"(?:\s*(?:칸|입력란|란))?\s+(?:에는|[은는을를이가만도에])(?=$|[\s:'\"“‘,.;!?！？。]))"
)

_EXTRA_LEAD_CONDITION = re.compile(
    r"[^,，;.!?！？。\n]{0,48}(?:라면|다면|으면|이면|하면|되면|경우)"
)


def _extra_implicit_lead(prefix: str) -> bool:
    trimmed = prefix.rstrip()
    if trimmed.endswith(("하고", "하고요", "하며", "적고", "쓰고", "기입하고", "주시고")):
        return True
    clause = re.split(r"[,，;.!?！？。\n]", prefix)[-1]
    word = clause.strip()
    if not word:
        return True
    if not clause[-1].isspace():
        return False
    return word in (
        "그리고",
        "또",
        "다음으로",
        "다만",
        "가능하면",
        "만약",
        "조건부로",
        "혹시",
        "아마",
    ) or bool(_EXTRA_LEAD_CONDITION.fullmatch(word))


# 공동 대상 뒤의 수량 표현은 별도 필드나 새 값이 아니다. 명시된
# 대상의 개수와 맞는 경우에만 그 대상을 묶는 양화사로 사용한다.
_GROUP_CARDINALS = {
    "하나": 1,
    "한": 1,
    "둘": 2,
    "두": 2,
    "셋": 3,
    "세": 3,
    "넷": 4,
    "네": 4,
    "다섯": 5,
    "여섯": 6,
    "일곱": 7,
    "여덟": 8,
    "아홉": 9,
    "열": 10,
}
_GROUP_CARDINAL = r"다섯|여섯|일곱|여덟|아홉|열"
_GROUP_QUANTIFIER = (
    rf"(?:(?:하나|둘|셋|넷|{_GROUP_CARDINAL})\s*(?:다|모두|전부)|"
    rf"(?:한|두|세|네|{_GROUP_CARDINAL}|\d{{1,3}})\s*(?:개|항목|필드|가지)"
    r"\s*[은는이가을를]?\s*(?:다|모두|전부))"
)
_GROUP_QUANTIFIER_RE = re.compile(rf"(?<![가-힣\d]){_GROUP_QUANTIFIER}")


def _group_count(quantifier: str) -> int:
    head = re.match(rf"하나|둘|셋|넷|한|두|세|네|{_GROUP_CARDINAL}|\d{{1,3}}", quantifier)
    assert head is not None
    count = head.group()
    return int(count) if count.isdecimal() else _GROUP_CARDINALS[count]


def _subjects(form: FormName) -> dict[str, str]:
    # 실측/방문 가능 여부를 나타내는 수식어까지 필드 이름에 포함한다.
    # 마감일의 앞부분이 별개 문의나 대상 없는 절로 분리되지 않게 한다.
    visit = r"(?:(?:실측|방문)\s*(?:가능(?:한)?\s*)?)?"
    period_qualifier = r"(?:가능(?:한)?\s*기간(?:\s*의)?\s*)?"
    words = {
        "valid": (
            r"(?:견적(?:서)?\s*)?(?:유효\s*(?:기간|기한|날짜|일자|일)|"
            r"만료(?:\s*(?:날짜|일자|일))?|효력(?:\s*(?:기간|기한|날짜|일자|일))?)"
        ),
        "amount": r"(?:견적(?:서)?|추가\s*(?:공사|작업|시공)?|공사|작업|시공)\s*(?:금액|총액|비용|금)|견적액|추가금|금액|가격|비용|총액|공사비|견적",
        "vat": rf"(?:별도\s*)?(?i:{VAT_LABEL})|면세\s*(?:조건|방식|구분)",
        "crew": r"(?:투입(?:할)?|작업|현장)\s*인원(?:\s*수)?|작업자(?:\s*수)?|인원(?:\s*수)?|인력",
        "days": r"(?:공사|작업|시공|방문)\s*(?:날짜|일자|일정|일수|기간|일|할\s*날)|날짜|일정|일수|기간|공기",
        "start": (
            rf"{visit}(?:접수\s*)?{period_qualifier}"
            r"(?:시작(?:\s*(?:날짜|일자|일)|하는\s*날)?|"
            r"첫\s*(?:(?:공사|작업|방문|실측)\s*(?:가능\s*)?(?:날짜|일|날)|날짜|날))"
            r"|(?:착공|착수)(?:\s*(?:날짜|일자|일))?"
        ),
        "end": rf"{visit}(?:접수\s*)?{period_qualifier}(?:종료\s*(?:날짜|일자|일)?|마감\s*(?:날짜|일자|일)?|마지막\s*(?:(?:공사|작업|실측|방문)\s*(?:가능\s*)?(?:날짜|일자|일|날)|날짜|날))|끝나는\s*날|끝날|끝|(?:준공|완공|완료)(?:\s*(?:예정\s*)?(?:날짜|일자|일))?|(?:공사\s*)?마치는\s*날",
        "excluded": (
            # 목록은 실제 제외일 대상의 형식을 수식한다. 별도 필드나
            # 그룹 전체를 가리키는 일반 참조로 분리하지 않는다.
            r"(?:(?:(?:실측|방문)\s*)?(?:제외\s*(?:날짜|일자|일|할\s*날)|불가\s*(?:날짜|일자|일))|"
            r"휴무(?:\s*(?:날짜|일자|일))?|쉬는\s*날|빼는\s*날|"
            r"안\s*되는\s*날|못\s*가는\s*날|작업\s*(?:안\s*하는|하지\s*않는)\s*날)"
            r"(?:\s*목록)?|"
            rf"{EXCLUDED_EDIT_REFERENCE}"
        ),
        "title": EXTRA_TITLE_LABEL,
        "reason": EXTRA_REASON_LABEL,
    }
    if form == "quote":
        words["days"] = (
            r"(?:견적(?:서)?\s*)?(?:공사|작업|시공)\s*(?:일수|기간|소요일(?:수)?)|기간|일수|공기|소요일(?:수)?|공사|시공"
        )
        words["valid"] += r"|견적(?:서)?\s*마감\s*(?:날짜|일자|일|기한)"
        words["amount"] = words["amount"].removesuffix("|견적") + (
            r"|견적(?!\s*(?:공사|작업|시공)\s*(?:일수|기간|소요일)|\s*마감)"
        )
    if form == "extra":
        # 긴 설명형 사유 표지가 생략형 공사명보다 먼저 소유권을 얻는다.
        # 입력란 수식어는 값이 아니라 필드명에 속한다.
        suffix = (
            r"(?:\s*(?:입력\s*란|란|칸|항목)(?=\s*(?:[:=]|에는|[은는을를이가만도에](?:\s|$))))?"
        )
        words["reason"] = rf"(?:{EXTRA_REASON_LABEL}){suffix}"
        words["title"] = rf"(?:{EXTRA_TITLE_LABEL}){suffix}"
        words = {"reason": words.pop("reason"), **words}
    active = {name: pattern for name, pattern in words.items() if name in FORM_FIELDS[form]}
    if form == "period":
        active["period"] = (
            r"(?:(?:실측|방문)\s*(?:가능(?:한)?\s*)?|가능(?:한)?\s*|전체\s*)?"
            r"기간(?!\s*(?:의\s*)?(?:시작|종료|끝|마감|첫|마지막|착공|준공))"
        )
    return active


def _field_set(name: str) -> frozenset[str]:
    return frozenset(("start", "end")) if name == "period" else frozenset((name,))


def field_mentions(form: FormName, raw: str) -> tuple[FieldMention, ...]:
    """긴 필드명 우선. 기간 전체는 두 경계이며 견적의 유효기간과 다르다."""
    pattern = "|".join(f"(?P<{name}>{word})" for name, word in _subjects(form).items())
    quantified = (
        tuple(match.span() for match in _GROUP_QUANTIFIER_RE.finditer(raw))
        if form == "extra"
        else ()
    )
    labels = tuple(
        FieldMention(_field_set(match.lastgroup), match.start(), match.end())
        for match in re.finditer(
            rf"(?<![가-힣])(?:{pattern})(?=$|[\s,:=;.!?！？。은는이가을를만도과와]|하고|이랑|랑|으로|로|에는|에)",
            raw,
        )
        if not (
            form == "extra"
            and match.lastgroup == "title"
            and (
                re.fullmatch(r"추가\s*(?:작업|공사|시공)", match.group())
                and not _extra_implicit_lead(raw[: match.start()])
                or any(a <= match.start() and match.end() <= b for a, b in quantified)
            )
        )
    )
    if "vat" not in FORM_FIELDS[form]:
        return labels
    # 금액과의 관계를 설명하는 부가세 값은 새로운 금액 대상이 아니다.
    # 의미 검증기가 귀속한 토큰 안의 참조만 제거하므로 별개 금액 절은 남는다.
    vat_values = scoped_vat_tokens(raw)
    labels = tuple(
        label
        for label in labels
        if label.fields != {"amount"}
        or not any(token.start <= label.start and label.end <= token.end for token in vat_values)
    )
    selections = tuple(
        FieldMention(frozenset({"vat"}), choice.start, choice.end, choice.mode)
        for choice in vat_selection_mentions(raw)
    )
    return tuple(
        sorted(
            (
                *(
                    label
                    for label in labels
                    if not any(
                        label.start < choice.end and choice.start < label.end
                        for choice in selections
                    )
                ),
                *selections,
            ),
            key=lambda mention: mention.start,
        )
    )


_CONDITIONAL_STATE = (
    r"(?:맞|가능하|확정되|정해지|확인되|승인되|"
    r"충족되|허용되|맞춰지|끝나|완료되|잡히|되|있|없|나)(?:으)?면"
)
CONDITIONAL_LINK = re.compile(
    rf"(?:가능하면|조건부|만약|가정하면|"
    rf"(?:라면|다면|으면|이면|하면|오면|{_CONDITIONAL_STATE})(?=$|[\s,，;.!?！？。])|"
    r"(?:는|할|될)\s*경우|(?:일|할|될)\s*때)"
)
_ANTECEDENT_ONLY = re.compile(rf"\s*[은는이가]?\s*{_CONDITIONAL_STATE}\s*[,，]?\s*$")
_COMPLETED_INQUIRY = re.compile(
    r"(?:[가-힣](?:까요|나요|겠습니까)|"
    r"(?:가능한지|가능할지|되는지|될지|맞는지|괜찮은지|있는지|여부)"
    r"[^,;.!?！？。\n]{0,20}(?:궁금합니다|궁금해요|알려\s*주세요|"
    r"확인\s*(?:부탁합니다|부탁드립니다|해\s*주세요)))$"
)


def intent_target_mentions(form: FormName, raw: str) -> tuple[FieldMention, ...]:
    """값/요청 없는 조건의 주어는 뒤의 실제 대입 대상과 구분한다.

    수량·날짜를 포함한 조건이나 단독 조건은 제거하지 않는다.
    """
    mentions = field_mentions(form, raw)
    return tuple(
        mention
        for index, mention in enumerate(mentions)
        if not (
            index + 1 < len(mentions)
            and mentions[index + 1].fields != mention.fields
            and _ANTECEDENT_ONLY.fullmatch(raw[mention.end : mentions[index + 1].start])
        )
    )


def completed_inquiry(text: str) -> bool:
    """명시된 문의 종결 뒤 새 목적어가 있으면 문장부호 없이도 독립 절이다."""
    return bool(_COMPLETED_INQUIRY.search(text.rstrip()))


# A terminal interrogative honorific can be an execution request only
# when attached to a completed action verb. Consumers still verify field,
# value, direction, condition and negation independently.
_EXECUTION_NOUN = (
    r"확정|변경|수정|정정|제안|입력|등록|설정|반영|선택|교체|제외|연장|단축|지정|결정|조정"
)
_EXECUTION_REQUEST_VERB = (
    r"잡아|넣어|바꿔|고쳐|옮겨|미뤄|당겨|빼|늘려|줄여|정해|두어|둬|맞춰|적어|써|(?<![가-힣])해"
)
# Lexical edit family shared by full-list ownership and single-member
# relation detection. The latter still requires an actual old/new relation.
DATE_CHANGE_ACTION = (
    r"바꾸|바꿔|바꿀|고치|고쳐|고칠|변경|수정|정정|교체|대체|"
    r"옮기|옮겨|옮길|미루|미뤄|미룰|당기|당겨|당길|이동"
)
POLITE_EXECUTION = re.compile(
    rf"(?:(?:{_EXECUTION_NOUN})\s*해|{_EXECUTION_REQUEST_VERB})\s*"
    r"(?P<ending>주시겠어요|주실래요|주실\s*수\s*있을까요|주시겠습니까|주시겠나요)"
    r"\s*[?？]?\s*$"
)


def polite_execution_risk_text(text: str) -> str:
    """Keep earlier inquiries/conditions; remove only a complete polite execution tail."""
    polite = POLITE_EXECUTION.search(text)
    if not polite or re.search(r"(?<![가-힣])(?:안|못)\s*$", text[: polite.start()]):
        return text
    # Preserve the action and all offsets; remove only the honorific
    # interrogative ending. A capability question in the present tense
    # is not a confirmed execution request.
    return text[: polite.start("ending")] + " " * (len(text) - polite.start("ending"))


# Lexical membership, not calendar normalization: only contiguous list
# members can inherit a postposed field marker. Ranges and predicates
# must remain independently owned. Calendar validity is checked later.
_LIST_DATE_ITEM = (
    r"(?:(?:20\d{2}\s*(?:년|[-/.])\s*)?"
    r"\d{1,2}\s*(?:월|[-/.])\s*\d{1,3}\s*일?|"
    r"\d{1,3}\s*일|오늘|내일|모레)"
)
_LIST_DATE_SEPARATOR = r"(?:[,，、·]|하고|이랑|랑|과|와|및|그리고)"
_LIST_DATE_MEMBER = rf"(?:{_LIST_DATE_ITEM})\s*[은는이가을를도만]?"
_LIST_SHORT_MEMBER = r"\d{1,2}\s*[은는이가을를도]?"
_LIST_ANY_MEMBER = rf"(?:{_LIST_DATE_MEMBER}|{_LIST_SHORT_MEMBER})"
_LIST_DATE_PREFIX = re.compile(
    rf"\s*{_LIST_ANY_MEMBER}(?:\s*{_LIST_DATE_SEPARATOR}\s*{_LIST_ANY_MEMBER})*\s*"
    r"(?:방문|실측|접수)?\s*"
)
_LIST_FULL_PREFIX = re.compile(
    rf"\s*{_LIST_DATE_MEMBER}(?:\s*{_LIST_DATE_SEPARATOR}\s*{_LIST_DATE_MEMBER})*\s*"
    r"(?:방문|실측|접수)?\s*"
)
_LIST_DATE_TAIL = re.compile(
    rf"\s*{_LIST_ANY_MEMBER}(?:\s*{_LIST_DATE_SEPARATOR}\s*{_LIST_ANY_MEMBER})*"
    r"\s*(?:으로|로)?\s*(?:(?:새로|다시|모두|전부)\s*)?"
    rf"(?:(?:{_EXECUTION_NOUN}|추가)(?:\s*(?:해|하|합|할|부탁|요청|바랍니다).*)?"
    rf"|(?:{DATE_CHANGE_ACTION}|{_EXECUTION_REQUEST_VERB}|넣|정하|정합|정할).*|"
    r"입니다|이에요|예요)?"
    r"\s*[,，.!?！？。]?\s*$"
)
_LIST_MONTH_ANCHOR = re.compile(r"\d\s*(?:월|[/.-])\s*\d")
_LIST_CONDITION_LEAD = re.compile(
    r"^\s*(?:가능하면|조건부로|[^\d,，;.!?！？。\n]{1,48}(?:라면|다면|으면|이면|하면|되면|경우))\s+"
)

_EXCLUSION_WORD = re.compile(r"제외|휴무|불가|못|안|쉬는|빼는")


def _exclusion_predicate_starts(raw: str) -> Iterator[int]:
    """독립된 술어와 직전 소유 표지의 시작 좌표를 순차적으로 반환한다."""
    for match in _EXCLUSION_WORD.finditer(raw):
        word = match.group()
        if word in ("못", "안"):
            after = skip_space(raw, match.end())
            suffixes = ("가", "하", "받") if word == "못" else ("돼", "되", "가")
            if not raw.startswith(suffixes, after):
                continue
        start = skip_space_back(raw, match.start())
        if start >= 2 and raw[start - 2 : start] in ("방문", "실측", "접수"):
            start -= 2
        yield start


def date_list_prefix(
    text: str,
    *,
    allow_condition: bool = False,
    allow_short_fragment: bool = False,
    allow_context_lead: bool = False,
) -> bool:
    if (
        allow_condition
        and (lead := _LIST_CONDITION_LEAD.match(text))
        and not field_mentions("period", text[: lead.end()])
    ):
        # Ownership is not approval. Keep the original conditional span.
        text = text[lead.end() :]
    if allow_context_lead and not _LIST_DATE_PREFIX.fullmatch(text):
        # 앞의 설명은 날짜 목록의 값이 아니다. 다른 명시 필드나
        # 앞선 날짜·완결 술어가 없는 배경 뒤의 연속 목록만 귀속한다.
        first = re.search(_LIST_DATE_ITEM, text)
        if first:
            lead = text[: first.start()]
            if (
                lead.strip()
                and not field_mentions("period", lead)
                and not re.search(
                    r"[,;.!?！？。\n]|\d|변경|수정|정정|등록|확정|설정|취소|철회", lead
                )
            ):
                text = text[first.start() :]
    if not _LIST_DATE_PREFIX.fullmatch(text):
        return False
    # Bare integers are only meaningful with a month anchor in the
    # same contiguous list. Do not mistake an isolated scalar for a date.
    return bool(
        allow_short_fragment or _LIST_MONTH_ANCHOR.search(text) or _LIST_FULL_PREFIX.fullmatch(text)
    )


def date_list_continuation(prefix: str, text: str) -> bool:
    """A following list member can inherit an explicit exclusion owner."""
    # Ownership can include a declared list count, but never treats that count
    # as a calendar member. Reuse the existing quantity lexer (lazy import:
    # normalization itself consumes the common intent vocabulary).
    from app.vendor.assist_normalize import date_list_declared_counts

    lexical = text
    counts = date_list_declared_counts(text)
    for start, end, _ in reversed(counts):
        lexical = lexical[:start] + " " * (end - start) + lexical[end:]
    if field_mentions("period", lexical) or not _LIST_DATE_TAIL.fullmatch(lexical):
        return False
    return bool(_LIST_MONTH_ANCHOR.search(prefix) or re.match(r"\s*" + _LIST_DATE_ITEM, lexical))


def postposed_exclusion_list(text: str) -> bool:
    """Bind a list to its own trailing exclusion target, not a separate boundary."""
    marks = field_mentions("period", text)
    if marks and any(mark.fields != {"excluded"} for mark in marks):
        return False
    if marks and date_list_prefix(
        text[: marks[0].start], allow_condition=True, allow_context_lead=True
    ):
        return True
    if marks:
        # 날짜 열거 뒤 설명을 붙이고 개수로 다시 지칭한 경우도
        # 동일한 제외 목록에 귀속한다. 개수의 진위와 실행 조건은
        # 정규화 및 검증 단계가 확인한다.
        from app.vendor.assist_normalize import date_list_declared_counts

        before = text[: marks[0].start]
        lead = _LIST_DATE_PREFIX.match(before)
        counts = date_list_declared_counts(before)
        if (
            lead
            and date_list_prefix(lead.group())
            and re.search(_LIST_DATE_SEPARATOR, lead.group())
        ):
            if (
                len(counts) == 1
                and lead.end() <= counts[0][0]
                and re.fullmatch(r"\s*[을를은는이가]?\s*", before[counts[0][1] :])
                and not re.search(r"\d|[,;.!?！？。\n]", before[lead.end() : counts[0][0]])
            ):
                return True
            # 개수 없이 이유를 연결해도, 실제 열거 뒤 단일 인과절은
            # 앞의 쉼표 멤버를 다른 필드의 날짜로 바꾸지 않는다.
            tail = before[lead.end() :]
            if (
                not counts
                and not re.search(r"\d|[,;.!?！？。\n]", tail)
                and re.search(
                    r"(?:으니|니까|라서|여서|어서|아서|해서|이므로|기\s*때문에)\s*$", tail
                )
            ):
                return True
    return any(
        date_list_prefix(text[:start], allow_condition=True, allow_context_lead=True)
        for start in _exclusion_predicate_starts(text)
    )


# 과거 시점과 수식 동사의 의미를 분리하고, 명시적 첫 후보 참조는
# 별도로 보존하여 검증기가 최초 출처를 확인할 수 있게 한다.
RECENT_HISTORY_TIME = (
    r"(?:아까|방금|앞(?:서|에(?:서)?)|이전(?:에)?|지난번(?:에)?|그때|전에|"
    r"(?:좀|조금)\s*전에|직전까지|직전(?:에)?|지금까지)"
)
FIRST_HISTORY_TIME = r"(?:(?:맨\s*)?처음(?:에)?|최초(?:에)?)"
HISTORY_TIME = rf"(?:{FIRST_HISTORY_TIME}|{RECENT_HISTORY_TIME})"
HISTORY_PAST_MODIFIER = (
    r"(?:말씀드린|말한|얘기한|알려드린|"
    r"(?:요청|정|정정|수정|변경|확정|제안|제시|정리|전달|연장|단축|조정|등록|입력|설정|선택|결정|채택)(?:한|했던)|"
    r"고른|골랐던|"
    r"잡은|잡았던|적은|적었던|바꾼|바꿨던|고친|고쳤던|옮긴|옮겼던|"
    r"늘린|늘렸던|줄인|줄였던|올린|올렸던|내린|내렸던|미룬|미뤘던|당긴|당겼던)"
)
_PREVIOUS = (
    rf"(?:{HISTORY_TIME}\s*{HISTORY_PAST_MODIFIER}|"
    r"(?:이전|기존|앞선|앞서|아까|방금|직전|지난)\s*(?:제안|요청|변경안|정정안|후보))"
)


def first_history_reference(text: str | None) -> bool:
    return bool(text and re.match(FIRST_HISTORY_TIME, text))


_CURRENT = (
    r"(?:현재|지금)\s*(?:(?:폼|화면|입력란)(?:에|에서)?\s*)?"
    r"(?:(?:있는|있던|입력한|입력된|등록한|등록된|적은|적힌)\s*)?"
)
_CURRENT_ELLIPSIS = (
    r"(?:현재|지금)\s*(?:폼\s*|화면\s*)?(?:입력\s*값|입력한\s*값|입력된\s*값|값)(?:으로|로)"
)
_REFERENTS = {
    "start": ("날짜", "일자", "일정", "날"),
    "end": ("날짜", "일자", "일정", "날"),
    "excluded": ("날짜", "일자", "목록", "날"),
    "amount": ("금액", "가격", "비용", "총액", "견적액"),
    "vat": ("조건", "방식", "구분", "설정"),
    "days": ("날짜", "일자", "일정", "기간", "일수", "공기", "소요일", "목록", "날"),
    "crew": ("인원", "인력"),
    "title": ("항목", "제목", "작업명", "항목명"),
    "reason": ("사유", "이유"),
    "valid": ("날짜", "일자", "기간", "날"),
}
_GENERIC_REFERENTS = ("입력값", "입력 값", "값", "내용", "안", "제안", "후보", "걸", "것", "거")
_REFERENT = "|".join(
    sorted(set(_GENERIC_REFERENTS).union(*_REFERENTS.values()), key=lambda word: (-len(word), word))
)


def compatible_reference(form: FormName, fields: frozenset[str], referent: str | None) -> bool:
    """동일한 days 키라도 견적 일수와 시공 날짜 목록의 타입은 다르다."""
    if referent is None or referent in _GENERIC_REFERENTS:
        return True
    if fields == {"start", "end"} and referent == "기간":
        return True
    return bool(fields) and all(
        referent
        in (
            ("기간", "일수", "공기", "소요일")
            if form == "quote" and field == "days"
            else ("날짜", "일자", "일정", "목록", "날")
            if form == "dates" and field == "days"
            else _REFERENTS[field]
        )
        for field in fields
    )


_BARE_PREVIOUS = (
    rf"(?:{FIRST_HISTORY_TIME}|아까|방금|앞서|직전|이전|지난번)\s+"
    rf"(?=(?:{_REFERENT})(?=$|[\s은는을를]|으로|로|대로))"
)
# 두다의 본말/준말은 동일한 완결형 유지 술어로 검증한다.
# 긍정 종결이 없는 '두어', 조건·문의·부정은 여기에서 승인하지 않는다.
PLACEMENT_KEEP_DIRECTIVE = (
    r"(?:(?:놔|놓아|남겨|내버려)\s*)?"
    r"(?:두(?:고요?|세요|겠습니다|죠)|둘게요|둡니다|(?:두어|둬)\s*(?:주세요|줘요|주십시오|요))"
)
_DIRECTIVE = (
    r"(?:유지|고정|사용|반영|적용|진행)(?:\s*(?:해\s*(?:주세요|줘요|주십시오|요)|"
    r"하고요?|할게요|하겠습니다|합니다|해요|바랍니다|부탁(?:합니다|드립니다|해요|드려요)?))?"
    r"|(?:해|잡아|맞춰|써)\s*(?:주세요|줘요|주십시오)"
    r"|(?:되)?돌려\s*(?:주세요|줘요|주십시오)|(?:되)?돌리(?:고요?|겠습니다|겠어요|ㅂ니다)|(?:되)?돌릴게요"
    r"|부탁(?:합니다|드립니다|해요|드려요)"
    rf"|가(?:고요?|죠|겠습니다|주세요|요)|{PLACEMENT_KEEP_DIRECTIVE}"
    r"|하고요?|할게요|하겠습니다|쓰고요?|쓰겠습니다|쓸게요|쓰면\s*됩니다"
)
# 참조의 긍정 동일성은 동작형 요청과 다른 완결 술어이다.
# 단독 명사/과거형은 확정으로 바꾸지 않고, 같은 대상/출처/조건 검증을
# 거치는 scope에만 결합한다.
REFERENCE_IDENTITY = r"(?:그대로|대로|이대로)\s*(?:입니다|이에요|예요|이죠|죠)"
_CURRENT_PROTECTION = (
    r"(?:손대|건드리)지\s*(?:않(?:을게요|겠습니다)|마시고|마세요|"
    r"말아\s*(?:주세요|줘요|주십시오))"
)
# 철회 관용구는 대입·이력 검증에서도 같은 의미로 검사한다.
WITHDRAW_IDIOM = r"없(?:던|었던)\s*(?:걸로|일로|것으로)"
WITHDRAW_IDIOM_REQUEST = (
    rf"{WITHDRAW_IDIOM}\s*(?:할게요|하겠습니다|합니다|해요|하고요?|해\s*(?:주세요|줘요|주십시오))"
)
_POSITIVE = (
    rf"(?:{REFERENCE_IDENTITY}|(?:그대로|대로)(?:\s*(?:{_DIRECTIVE}))?|"
    rf"(?:으로|로)\s*(?:{_DIRECTIVE})|(?:{_DIRECTIVE})|"
    rf"(?:{_CURRENT_PROTECTION})|"
    r"(?:전과|이전과|예전과)?\s*같(?:아요|습니다)|동일(?:합니다|해요))"
)


def retention_scopes(
    form: FormName, raw: str, *, include_linked_prefixes: bool = False
) -> tuple[RetentionScope, ...]:
    """값을 추출하지 않고 완전한 유지 술어에 결합된 대상만 반환한다.

    숫자·날짜가 들어간 재확인과 위험 판정은 기존 검증의 책임이다.
    include_linked_prefixes는 뒤에 대상 명칭이 생략된 유지 연결절도
    경쟁 지시 검사에 제공한다. linked_prefix는 유지 승인 근거가 아니다.
    연속 공백은 정규식의 중첩 선택지에서 같은 문법을 여러 번 탐색하게
    한다. 탐색용 문자열만 압축하고 반환 위치는 원문으로 복원한다.
    """
    runs = list(re.finditer(r"\s{2,}", raw))
    if not runs:
        return _retention_scopes(form, raw, include_linked_prefixes=include_linked_prefixes)
    parts = []
    offsets = [0]
    cursor = 0
    for run in runs:
        parts.append(raw[cursor : run.start()])
        offsets.extend(range(cursor + 1, run.start() + 1))
        # 공백의 종류를 검사하는 후처리와 문장 경계를 위해 줄바꿈은
        # 유지한다. 전체 공백 구간의 끝을 원문 오프셋으로 기록한다.
        parts.append("\n" if "\n" in run.group() else " ")
        offsets.append(run.end())
        cursor = run.end()
    parts.append(raw[cursor:])
    offsets.extend(range(cursor + 1, len(raw) + 1))
    compact = "".join(parts)
    return tuple(
        replace(
            scope,
            start=offsets[scope.start],
            predicate_start=offsets[scope.predicate_start],
            end=offsets[scope.end],
        )
        for scope in _retention_scopes(
            form, compact, include_linked_prefixes=include_linked_prefixes
        )
    )


def _retention_scopes(
    form: FormName, raw: str, *, include_linked_prefixes: bool = False
) -> tuple[RetentionScope, ...]:
    subject = "|".join(f"(?:{word})" for word in _subjects(form).values())
    group = rf"(?:{subject})(?:\s*(?:과|와|이랑|랑|및|하고|,|·|/)\s*(?:{subject}))*"
    remaining_subject = r"나머지(?:\s*(?:항목|필드|후보))?|그\s*외|다른\s*(?:항목|필드|후보)"
    pattern = re.compile(
        rf"(?<![가-힣])(?:(?P<prefix_current>(?:현재|지금)\s+)|(?P<prefix_previous>{_PREVIOUS})\s+)?"
        rf"(?P<subjects>{group}|{remaining_subject}|전체|전부|모두)"
        rf"\s*[은는이가을를만도]?\s*"
        rf"(?:(?P<group_quantifier>{_GROUP_QUANTIFIER})\s*|모두\s*|전부\s*|다\s*)?(?P<predicate>"
        rf"(?:(?P<previous>{_PREVIOUS}|{_BARE_PREVIOUS})|(?P<current>{_CURRENT}))?\s*"
        rf"(?(previous)[을를]?\s*)"
        rf"(?:(?P<referent>{_REFERENT})(?P<referent_particle>[은는을를])?)?\s*(?P<positive>{_POSITIVE}))"
    )
    scopes = []
    for match in pattern.finditer(raw):
        # 일반 변경·확정 동사를 유지로 해석하지 않는다.
        positive = match["positive"].strip()
        protection = bool(re.fullmatch(_CURRENT_PROTECTION, positive))
        if not (
            match["previous"]
            or match["prefix_previous"]
            or match["current"]
            or match["prefix_current"]
            or protection
        ) and not (
            positive.startswith(("그대로", "이대로", "유지", "같", "동일"))
            or any(
                positive.startswith(prefix) and positive[len(prefix) :].lstrip().startswith("같")
                for prefix in ("전과", "이전과", "예전과")
            )
        ):
            continue
        end = match.end()
        tail = raw[end:].lstrip()
        linked_prefix = False
        if tail.startswith(("?", "？")):
            end += len(raw[end:]) - len(tail) + 1
        elif tail and tail[0] not in ",;.!。":
            following = re.match(
                rf"(?:그리고\s+)?(?:{subject}|{remaining_subject})(?=$|[\s은는이가을를만도])",
                tail,
            )
            if not positive.endswith(("고", "고요")):
                continue
            if following is None:
                if not include_linked_prefixes:
                    continue
                linked_prefix = True
        subjects = field_mentions(form, match["subjects"])
        fields = frozenset(name for mention in subjects for name in mention.fields)
        if not fields and match["subjects"] in ("전체", "전부", "모두"):
            fields = frozenset(FORM_FIELDS[form])
        remaining = not fields
        referent = match["referent"]
        # 목적격/보조사로 연결된 명사는 명시적인 참조 표현이 있을 때만
        # 허용한다. 일반 명사+대입 동사를 유지 요청으로 승격하지 않는다.
        if match["referent_particle"] and not (
            match["previous"]
            or match["prefix_previous"]
            or match["current"]
            or match["prefix_current"]
            or referent == "후보"
        ):
            continue
        compatible = compatible_reference(form, fields, referent)
        # An immediate antecedent ("승인되면, ...") conditions the entire
        # joint subject. A completed independent clause is not an antecedent.
        if len(subjects) >= 2 and _ANTECEDENT_ONLY.search(raw[: match.start()]):
            compatible = False
        quantifier = match["group_quantifier"]
        if quantifier:
            # '기간과 제외일 두 항목' has two named targets but three fields.
            # Overlapping aliases (금액과 총액) cannot supply two distinct targets.
            compatible = (
                compatible
                and len(subjects) == _group_count(quantifier)
                and len(subjects) >= 2
                and sum(len(mention.fields) for mention in subjects) == len(fields)
            )
        explicit_current = bool(match["current"] or match["prefix_current"] or protection)
        explicit_previous = bool(
            match["previous"]
            or match["prefix_previous"]
            or referent in ("제안", "후보")
            or re.match(r"(?:전과|이전과|예전과)", positive)
        )
        # '다른 휴무는 그대로예요'는 목록 내부의 보존 설명일 수도
        # 있다. 선언형 신규 문법은 명시 참조가 있는 경우에만
        # 필드 전체의 이전/현재 후보 유지로 결합한다.
        if re.fullmatch(REFERENCE_IDENTITY, positive) and not (
            explicit_current or explicit_previous
        ):
            continue
        # '유효기간'의 뒤에 오는 날짜 같은 참조 명사는 대상 목록에 넣지 않는다.
        scopes.append(
            RetentionScope(
                fields=fields,
                start=match.start(),
                predicate_start=match.start("group_quantifier")
                if quantifier
                else match.start("predicate"),
                end=end,
                reference="current"
                if explicit_current
                else "previous"
                if explicit_previous
                else "pending",
                remaining=remaining,
                first=any(
                    first_history_reference(ref)
                    for ref in (match["previous"], match["prefix_previous"])
                ),
                compatible=compatible
                and not (explicit_current and explicit_previous)
                and not (protection and (remaining or referent is not None))
                and not (explicit_current and positive == "대로")
                and (
                    explicit_current
                    or not (
                        positive.startswith(("돌", "되돌"))
                        or any(
                            positive.startswith(prefix)
                            and positive[len(prefix) :].lstrip().startswith(("돌", "되돌"))
                            for prefix in ("으로", "로")
                        )
                    )
                ),
                linked_prefix=linked_prefix,
            )
        )
    # 쉼표로 병렬 연결된 참조는 마지막의 완결된 긍정 술어를 공유한다.
    # 참조 명사를 뒤 절의 공동 주어로 합치지 않고, 필드마다 출처를 보존한다.
    reference_clause = (
        rf"\s*(?P<subjects>{group})\s*[은는이가을를만도]\s*"
        rf"(?P<predicate>(?:(?P<previous>{_PREVIOUS}|{_BARE_PREVIOUS})|"
        rf"(?P<current>{_CURRENT})(?:입력\s*)?)\s*"
        rf"(?P<referent>{_REFERENT})\s*(?:으로|로|대로|[을를])?)\s*"
    )
    elliptical = re.compile(reference_clause)
    terminal = re.compile(
        reference_clause + rf"(?P<directive>{REFERENCE_IDENTITY}|(?:그대로\s*)?(?:{_DIRECTIVE}))\s*"
    )
    finite = re.compile(
        r"(?:주세요|줘요|주십시오|합니다|하겠습니다|할게요|해요|바랍니다|"
        r"드립니다|드려요|세요|죠|겠습니다|둘게요|둡니다|쓰겠습니다|쓸게요|입니다|이에요|예요)$"
    )

    def shared_scope(match: re.Match, offset: int, *, final: bool) -> RetentionScope:
        fields = frozenset(
            name for mention in field_mentions(form, match["subjects"]) for name in mention.fields
        )
        referent = match["referent"]
        return RetentionScope(
            fields=fields,
            start=offset + match.start("subjects"),
            predicate_start=offset + match.start("predicate"),
            end=offset + (match.end("directive") if final else match.end("predicate")),
            reference="previous" if match["previous"] else "current",
            remaining=False,
            first=first_history_reference(match["previous"]),
            compatible=bool(fields) and compatible_reference(form, fields, referent),
            shared=True,
        )

    # 개별 절 전체가 문법에 맞을 때에만 기존 중첩된 group 해석을 교체한다.
    fragments = []
    cursor = 0
    for mark in re.finditer(r"[,;.!?？。\n]", raw):
        pos = mark.start()
        if (
            mark.group() in ",."
            and pos > 0
            and pos + 1 < len(raw)
            and raw[pos - 1].isdecimal()
            and raw[pos + 1].isdecimal()
        ):
            continue
        fragments.append((cursor, pos, mark.group()))
        cursor = mark.end()
    fragments.append((cursor, len(raw), ""))
    for i, (start, end, boundary) in enumerate(fragments):
        if not i or fragments[i - 1][2] != "," or boundary in ("?", "？", ","):
            continue
        match = terminal.fullmatch(raw[start:end])
        if match is None or not finite.search(match["directive"]):
            continue
        last = shared_scope(match, start, final=True)
        occupied = set(last.fields)
        linked = [last]
        j = i - 1
        while j >= 0 and fragments[j][2] == ",":
            a, b, _ = fragments[j]
            earlier = elliptical.fullmatch(raw[a:b])
            if earlier is None:
                break
            candidate = shared_scope(earlier, a, final=False)
            if occupied.intersection(candidate.fields):
                linked = []
                break
            linked.append(candidate)
            occupied.update(candidate.fields)
            j -= 1
        if len(linked) > 1:
            left = min(s.start for s in linked)
            scopes = [s for s in scopes if s.end <= left or s.start >= last.end]
            scopes.extend(linked)
    # 앞 절의 긍정 유지 술어를 병렬 목적어에서 생략한 경우만 인정한다.
    # 독립된 '현재 입력값으로'만으로는 확정하지 않는다.
    for earlier in tuple(scopes):
        if (
            earlier.linked_prefix
            or not earlier.compatible
            or not raw[earlier.start : earlier.end].rstrip().endswith(("고", "고요"))
        ):
            continue
        tail = re.match(
            rf"\s*(?P<subjects>{group})\s*[은는이가을를만도]\s*"
            rf"(?P<predicate>{_CURRENT_ELLIPSIS})(?=\s*(?:$|[.!;。]))",
            raw[earlier.end :],
        )
        if tail is not None:
            fields = frozenset(
                name
                for mention in field_mentions(form, tail["subjects"])
                for name in mention.fields
            )
            scopes.append(
                RetentionScope(
                    fields=fields,
                    start=earlier.end + tail.start("subjects"),
                    predicate_start=earlier.end + tail.start("predicate"),
                    end=earlier.end + tail.end(),
                    reference="current",
                    remaining=False,
                    first=False,
                    compatible=bool(fields) and not (fields & earlier.fields),
                )
            )
    return tuple(sorted(scopes, key=lambda scope: scope.start))


_QUOTED_RETENTION_TEXT = re.compile(r""""[^"]*(?:"|$)|'[^']*(?:'|$)|“[^”]*(?:”|$)|‘[^’]*(?:’|$)""")
_COMPLETE_GROUP_DIRECTIVE = re.compile(
    rf"(?:{REFERENCE_IDENTITY}|{_DIRECTIVE}|{_CURRENT_PROTECTION}|"
    r"(?:전과|이전과|예전과)?\s*같(?:아요|습니다)|동일(?:합니다|해요))\s*$"
)


def explicit_group_retention_scopes(
    form: FormName, raw: str, field: str
) -> tuple[RetentionScope, ...]:
    """Return actual contiguous named-group witnesses for independent verification.

    Model evidence is never an input. A shared history prefix and positive
    predicate can bind multiple real fields, but a quoted, inferred, incomplete
    or merely whole-form reference cannot repair missing model grounding.
    Consumers must still verify competing requests, conditions, the field's
    prior provenance/baseline and the combined business state.
    """
    quoted = tuple(match.span() for match in _QUOTED_RETENTION_TEXT.finditer(raw))
    witnesses = []
    for scope in retention_scopes(form, raw):
        if (
            field not in scope.fields
            or len(scope.fields) < 2
            or scope.reference == "pending"
            or not scope.compatible
            or scope.remaining
            or scope.shared
            or scope.linked_prefix
            or any(a < scope.end and scope.start < b for a, b in quoted)
            or not _COMPLETE_GROUP_DIRECTIVE.search(raw[scope.start : scope.end])
        ):
            continue
        subjects = field_mentions(form, raw[scope.start : scope.predicate_start])
        named = frozenset(name for mention in subjects for name in mention.fields)
        if len(subjects) >= 2 and named == scope.fields:
            witnesses.append(scope)
    return tuple(witnesses)


def intent_clauses(form: FormName, raw: str) -> tuple[IntentClause, ...]:
    """실제 대상과 유지 참조를 원문 오프셋으로 묶은 독립 절.

    숫자 의미·업무 조건·부정/조건의 최종 판정은 호출자가 검증한다.
    """
    mentions = intent_target_mentions(form, raw)
    scopes = retention_scopes(form, raw)
    boundaries: dict[int, Literal["sentence", "question", "comma", "connective", "end"]] = {}
    for mark in re.finditer(r"[,.;!?？。\n]", raw):
        pos = mark.start()
        if (
            mark.group() in ",."
            and pos > 0
            and pos + 1 < len(raw)
            and raw[pos - 1].isdecimal()
            and raw[pos + 1].isdecimal()
        ):
            continue
        if any(scope.start < pos < scope.end - 1 for scope in scopes):
            continue
        boundaries[mark.end()] = (
            "question" if mark.group() in "?？" else "comma" if mark.group() == "," else "sentence"
        )
    # 실제 다음 목적어가 있는 접속형만 분리한다. 숫자 목록의 '하고'는 분리하지 않는다.
    for mention in mentions:
        prefix = raw[: mention.start].rstrip()
        if any(scope.start < mention.start < scope.end for scope in scopes):
            continue
        if completed_inquiry(prefix):
            boundaries.setdefault(mention.start, "question")
            continue
        if not prefix.endswith(("고", "며")):
            continue
        if any(
            other.end < mention.start
            and raw[other.end : mention.start].strip() in ("하고", "과", "와", "및", "랑")
            for other in mentions
        ):
            continue
        boundaries[mention.start] = "connective"
    for scope in scopes:
        if scope.start > 0 and raw[: scope.start].rstrip().endswith(("고", "며")):
            boundaries.setdefault(scope.start, "connective")
        if scope.end < len(raw) and raw[scope.start : scope.end].rstrip().endswith(("고", "고요")):
            boundaries.setdefault(scope.end, "connective")
    boundaries.setdefault(len(raw), "end")
    result = []
    start = 0
    for end, boundary in sorted(boundaries.items()):
        if not raw[start:end].strip():
            start = end
            continue
        selected_scopes = tuple(
            scope for scope in scopes if start <= scope.start and scope.end <= end
        )
        selected_mentions = tuple(
            mention
            for mention in mentions
            if start <= mention.start < end
            and not any(
                scope.predicate_start <= mention.start < scope.end for scope in selected_scopes
            )
        )
        fields = frozenset(
            name for part in (*selected_mentions, *selected_scopes) for name in part.fields
        )
        result.append(
            IntentClause(start, end, selected_mentions, selected_scopes, fields, boundary)
        )
        start = end
    if form == "period":
        # Join a contiguous list before assigning defaults to individual
        # fragments. Only a date-only fragment with a list separator can
        # be inherited; explicit fields, ranges, prior refs and independent
        # predicate clauses are never absorbed.
        owned: list[IntentClause] = []
        for part in result:
            if (
                owned
                and owned[-1].boundary == "comma"
                and owned[-1].fields == {"excluded"}
                and not owned[-1].retentions
                and not part.fields
                and not part.retentions
                and date_list_continuation(
                    raw[owned[-1].start : owned[-1].end],
                    raw[part.start : part.end],
                )
            ):
                previous = owned.pop()
                part = replace(
                    part,
                    start=previous.start,
                    mentions=previous.mentions,
                    fields=previous.fields,
                )
            if not part.retentions and (
                part.fields == {"excluded"}
                or next(_exclusion_predicate_starts(raw[part.start : part.end]), None) is not None
            ):
                first = len(owned)
                while first and (
                    owned[first - 1].boundary == "comma"
                    and not owned[first - 1].fields
                    and not owned[first - 1].retentions
                    and date_list_prefix(
                        raw[owned[first - 1].start : owned[first - 1].end].rstrip().rstrip(",，"),
                        allow_condition=True,
                        allow_short_fragment=True,
                        allow_context_lead=True,
                    )
                ):
                    first -= 1
                if first < len(owned) and postposed_exclusion_list(
                    raw[owned[first].start : part.end]
                ):
                    part = replace(part, start=owned[first].start)
                    del owned[first:]
            owned.append(part)
        result = owned
    return tuple(result)
