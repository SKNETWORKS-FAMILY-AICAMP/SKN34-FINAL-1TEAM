"""부가세 값 토큰과 대상 귀속을 구분한다. 의도·업무 조건은 호출자가 검증한다."""

import re
from dataclasses import dataclass
from itertools import pairwise

from app.vendor.assist_lexical import compact_whitespace

# 기존 공개 내부 별칭 계약. 조합형 값은 아래 문법에서 처리한다.
VAT_MODE_ALIASES = {
    "included": ("포함", "포함된"),
    "excluded": ("별도", "미포함", "제외"),
    "exempt": ("면세", "부가세 없음", "부가세 면제", "부가세 0원"),
}

VAT_LABEL = r"(?:부가(?:가치)?세|세금|vat)(?:\s*(?:조건|방식|구분))?"
_PARTICLE = r"(?:으로|로|은|는|이|가|을|를|만|도|과|와|의|에서|이나)"
# '면세점', '제외일', '포함률', '별도비용'의 일부를 값으로 읽지 않는다.
# 실제 조사·서술어·활용형은 어간 뒤에 붙어도 값 토큰 경계를 유지한다.
_LEFT = r"(?<![가-힣a-zA-Z0-9])"
_RIGHT = (
    r"(?=$|[\s,，;:.!?！？。()\"'“”‘’/·]|"
    rf"{_PARTICLE}|"
    r"입니다|이에요|예요|이고|이며|이라|이라면|인지|이었|라고|라는|라면|"
    r"아닙|아니|된|되는|되어|돼|됐|"
    r"하(?:는|지|기|고|며|면|자|실|려고|겠습니다)|"
    r"해|했|할|함|합|한다|한(?:다|것|다면)?|"
    r"받(?:아|는|기|고|으면|을|습)|"
    r"않|맞|가능)"
)
VAT_LABEL_PATTERN = re.compile(
    rf"{_LEFT}{VAT_LABEL}(?=$|[\s,，;:.!?！？。]|\s*{_PARTICLE})",
    re.IGNORECASE,
)
VAT_NEGATIVE_INCLUSION = r"포함\s*하지\s*않는\s*(?:방식|조건|구분|형태)"
_BILLING_STEM = r"(?:청구|부과|계산|받(?:아|는|기|고|으면|을|습)?)"
_BILLING_VALUE = (
    rf"(?:따로|별개로|"
    r"(?:견적(?:\s*금액)?|공사\s*(?:금액|비용)|금액|총액|공사비)"
    rf"\s*(?:과|와|에서)\s*별개로)\s*{_BILLING_STEM}"
)
_TAX_EXEMPT_VALUE = rf"{VAT_LABEL}\s*(?:[은는이가을를만도]\s*)?(?:없음|면제|0\s*원)"

# 긴 복합 값부터 한 번만 읽는다. '미포함'은 포함의 증거가 아니다.
_TOKEN = re.compile(
    rf"{_LEFT}(?:(?P<negative>{VAT_NEGATIVE_INCLUSION})"
    rf"|(?P<tax_exempt>{_TAX_EXEMPT_VALUE})"
    rf"|(?P<billing>{_BILLING_VALUE})"
    r"|(?P<excluded>미포함|별도|제외|빼고|따로)"
    r"|(?P<included>포함된|포함)"
    rf"|(?P<exempt>면세|면제)){_RIGHT}",
    re.IGNORECASE,
)
_TOKEN_MODES = {
    "negative": "excluded",
    "tax_exempt": "exempt",
    "billing": "excluded",
    "excluded": "excluded",
    "included": "included",
    "exempt": "exempt",
}


@dataclass(frozen=True)
class VatToken:
    start: int
    end: int
    mode: str


def _grammar_fullmatch(pattern: re.Pattern[str], text: str) -> bool:
    return pattern.fullmatch(compact_whitespace(text)[0]) is not None


def _grammar_match_end(pattern: re.Pattern[str], raw: str, start: int) -> int | None:
    compact, offsets = compact_whitespace(raw[start:])
    match = pattern.match(compact)
    return start + offsets[match.end()] if match is not None else None


def _vat_confirmation_inquiry(tail: str) -> bool:
    text = tail.lstrip()
    if text.startswith(("으로", "로")):
        text = text[2 if text.startswith("으로") else 1 :].lstrip()
    return text.startswith(("확인", "검토", "문의", "질문"))


def vat_tokens(raw: str) -> tuple[VatToken, ...]:
    """원문 좌표의 의미 값. 필드 귀속이나 긍정 지시를 보장하지 않는다."""
    return tuple(
        VatToken(match.start(), match.end(), _TOKEN_MODES[match.lastgroup])
        for match in _TOKEN.finditer(raw)
        if match.group() not in ("따로", "별도")
        or _separate_word_is_value(raw, match.end())
        # 명명된 세금 필드에 바로 결합한 의미 토큰은 뒷부분의
        # 실행 동사 문법과 독립적으로 읽는다. 의도는 별도 검증한다.
        or (
            match.group() == "별도"
            and _adjacent_label(
                raw, VatToken(match.start(), match.end(), _TOKEN_MODES[match.lastgroup])
            )
            and not _vat_confirmation_inquiry(raw[match.end() :])
        )
    )


def vat_modes(raw: str) -> set[str]:
    """이미 소유권을 확인한 절 또는 값 근거의 토큰 집합."""
    return {token.mode for token in vat_tokens(raw)}


def vat_value_matches(value: str, raw: str) -> bool:
    """이전 값 재확인의 값 부분 전체가 한 의미 토큰인지 확인한다."""
    text = raw.strip()
    tokens = vat_tokens(text)
    return (
        len(tokens) == 1
        and tokens[0].start == 0
        and tokens[0].mode == value
        and bool(re.fullmatch(r"\s*[은는이가을를도만]?\s*", text[tokens[0].end :]))
    )


# 대상 없는 선택에서 임의 일반 명사를 건너뛰지 않는다. 값 + 서술어/선택
# 종결만 있는 형태를 소유권으로 인정하며, 질문·부정·조건은 별도 검증 대상이다.
_STANDALONE_LEAD = re.compile(
    r"^(?:(?:우선|먼저|다시|그럼|그러면|그냥|최종적으로|이번에는|가능하면|혹시|아|네|예)\s+)*(?:(?:이번|해당|이|그)\s*건(?:은|는|만)\s*)?$"
)
_STANDALONE_HEAD = (
    r"\s?"
    # 청구하는 것/계산할 방식/받는 걸 → 선택의 내용
    r"(?:(?:하(?:는|기|ㄴ)|할|한|받는|는|기)\s?"
    r"(?:것|거|걸|방식|조건|구분|형태)(?:으로|로)?\s?)?"
    r"(?:(?:방식|조건|구분|형태|것|거|걸|처리)(?:으로|로)?\s?)?"
    r"(?:(?:청구|계산|부과)(?:으로|로)\s?)?"
    r"(?:(?:으로|로|은|는|이|가|을|를|만|도)\s?)?"
)
_STANDALONE_END = (
    r"(?:(?:후보(?:안)?|초안|제안(?:안)?|안)(?:으로|로|에|을|를)?\s)?"
    r"(?:"
    r"(?:선택|확정|결정|설정|지정|처리|적용|입력|기입|기재|정리|표시|표기|반영|제안|제시|정|"
    r"변경|수정|정정|등록|추가|사용|채택|유지|청구|계산|부과)"
    r"\s?(?:합니다|해요|할게요|하겠습니다|하고요?|하며|"
    r"해\s?(?:주세요|줘요|주십시오|주시겠어요|주실래요)|"
    r"하지\s?(?:마세요|말아\s?주세요|않겠습니다)|할까요|할지|"
    r"하면|한다면|할\s?경우|부탁(?:합니다|드립니다|드려요|해요)?|바랍니다|바라요)?"
    r"|(?:해|정해|바꿔|골라|받아|넣어|적어|돌려|올려|보여|잡아)\s?(?:주세요|줘요|주십시오|주시겠어요|주실래요)"
    r"|할게요|하겠습니다|합니다|해요|할까요|할지|하면|한다면|"
    r"하지\s?(?:마세요|말아\s?주세요)|"
    r"하고요?|하며|잡고요?|이고|이며|정하고요?|정하며|정합니다|정할게요|정하겠습니다|"
    r"부탁(?:해요|합니다|드립니다)|맞습니다|맞아요|입니다|이에요|예요|아닙니다|아니에요|"
    r"(?:돼|되어)\s?(?:있습니다|있어요|있는데|있음)|"
    r"미정(?:입니다|이에요)?|고민\s?중(?:입니다|이에요)?|"
    r"가능한지\s?(?:확인해|알려)\s?주세요"
    r")?"
    r"\s?[.!?！？。]?\s?$"
)
_STANDALONE_TAIL = re.compile(_STANDALONE_HEAD + _STANDALONE_END)
_STANDALONE_ADVERB = re.compile(r"(?:새로|다시|우선|최종(?:적으로)?|먼저) ")
_STANDALONE_ADVERB_HEAD = re.compile(_STANDALONE_HEAD + rf"(?={_STANDALONE_ADVERB.pattern})")
_STANDALONE_END_PATTERN = re.compile(_STANDALONE_END)


def _standalone_tail_fullmatch(text: str) -> bool:
    compact, _ = compact_whitespace(text)
    if _STANDALONE_TAIL.fullmatch(compact) is not None:
        return True
    head = _STANDALONE_ADVERB_HEAD.match(compact)
    if head is None:
        return False
    # The finite head identifies the adverb boundary. Consume each complete
    # adverb once before testing the finite ending grammar.
    end = head.end()
    while (adverb := _STANDALONE_ADVERB.match(compact, end)) is not None:
        end = adverb.end()
    return _STANDALONE_END_PATTERN.fullmatch(compact, end) is not None


# 같은 절 안에서 실제 연결어로 이어진 값의 소유권만 계승한다.
# 취소·경쟁·정정의 유효성은 해당 검증기가 판단한다.
_MODE_LINK = re.compile(
    r"\s?(?:은|는|이|가|을|를|으로|로)?\s?"
    r"(?:아니고|아니라|말고|대신|에서|또는|혹은|이나|나|및|과|와)"
    r"\s?[,，]?\s?"
)
_LABEL_GAP = re.compile(r"\s*(?:(?:[은는이가을를만도]|:|=)\s*)?")
_REVERSE_LABEL_GAP = re.compile(r"\s*(?:의\s*)?")
_VALUE_CLAUSE_END = re.compile(r"[,，;.!?！？。\n]")


_MODE_NOMINAL = r"(?:방식|조건|구분|형태|처리|청구|계산|부과)"
_MODE_ADVERB = r"(?:먼저|우선|다시|재차|새로|최종(?:적으로)?)"
_MODE_PROPOSAL = r"(?:후보(?:안)?|초안|제안(?:안)?|안)"
_MODE_ACTION = (
    r"(?:선택|확정|결정|설정|지정|처리|적용|입력|기입|기재|정리|표시|표기|"
    r"반영|제안|제시|변경|수정|정정|등록|추가|사용|채택|유지|청구|계산|부과|"
    r"정해|정하(?:고|며|면|는|지|겠)?|정할|정했|정합니다|해|하겠|할|하고|하며|바꿔|골라|받아|넣어|"
    r"적어|돌려|올려|보여|잡아|잡고|부탁)"
)
_MODE_BOUNDARY = r"(?=$|[\s,，;:.!?！？。]|(?:으로|로|에|은|는|이|가|을|를|만|도)|하|해|했|할|합|하고|하며|이고|이며|합니다|드립니다)"
_MODE_HEAD = re.compile(
    rf"^\s*(?:{_MODE_NOMINAL}{_MODE_BOUNDARY}|"
    rf"(?:으로|로|이라고|라고)\s*(?:{_MODE_ADVERB}\s+)*"
    rf"(?:{_MODE_PROPOSAL}(?:으로|로|에|을|를)?\s+)?{_MODE_ACTION}{_MODE_BOUNDARY}|"
    rf"{_MODE_PROPOSAL}(?:으로|로|에|을|를)\s*{_MODE_ACTION}{_MODE_BOUNDARY}|"
    rf"{_MODE_ACTION}{_MODE_BOUNDARY}|"
    r"(?:안\s*|않|아니|아닙|말고|시(?:\s|$)|면|이면|라면|일\s*(?:때|경우)|\(?[Xx×]))"
)

# 인용된 세금 값이 실제 후보 생성 요청의 내용일 때만 값 토큰을 유지한다.
# 대상 없는 선택, 조건/문의 및 최종 의도 검증의 기존 계약은 그대로 둔다.
_QUOTED_PROPOSAL_TAIL = re.compile(
    rf"\s*(?:이라는|라는)\s*{_MODE_PROPOSAL}\s*[을를]\s*"
    r"(?:만들어|작성해|보여)\s*(?:주세요|줘요|주십시오|주시겠어요|주실래요)\s*"
)


def _separate_word_is_value(raw: str, end: int) -> bool:
    """A local value head suffices; scope and actual intent remain independent."""
    tail = _VALUE_CLAUSE_END.split(raw[end:], maxsplit=1)[0]
    if _MODE_HEAD.match(tail):
        return True
    if _standalone_tail_fullmatch(tail):
        return True
    if _QUOTED_PROPOSAL_TAIL.fullmatch(tail):
        return True
    link_end = _grammar_match_end(_MODE_LINK, raw, end)
    if link_end is not None and _TOKEN.match(raw, link_end):
        return True
    if _grammar_match_end(_SELECTION_TAIL, raw, end) is not None:
        return True
    return bool(
        re.match(rf"\s+{VAT_LABEL}(?=$|[\s,，;:.!?！？。]|{_PARTICLE})", tail, re.IGNORECASE)
    )


def standalone_vat_modes(raw: str) -> set[str]:
    """필드명 생략형도 완결된 선택 구문에 한해 VAT 대상에 귀속한다."""
    text = raw.strip()
    tokens = vat_tokens(text)
    if not tokens or not _STANDALONE_LEAD.fullmatch(text[: tokens[0].start]):
        return set()
    if any(
        not _grammar_fullmatch(_MODE_LINK, text[old.end : new.start])
        for old, new in pairwise(tokens)
    ):
        return set()
    tail = text[tokens[-1].end :]
    if not _standalone_tail_fullmatch(tail) and not any(
        _standalone_tail_fullmatch(tail[: j.end()])
        and re.match(
            r"나머지|다른\s*(?:후보|값|칸)|(?:추가(?:된)?\s*)?(?:사유|이유|제목|항목(?:명)?|금액|총액|비용|공사\s*기간|기간|일수|유효기간|만료일|부가세|세금)",
            tail[j.end() :],
        )
        for j in re.finditer(r"(?:하고요?|하며|잡고요?|이고|이며)\s+", tail)
    ):
        return set()
    first_word = text[tokens[0].start : tokens[0].end].casefold()
    # 부사 '따로'와 일반 면제는 대상 또는 실제 청구 동사 없이는 모호하다.
    if first_word in ("따로", "면제", "빼고"):
        return set()
    return {token.mode for token in tokens}


def _adjacent_label(raw: str, token: VatToken) -> re.Match[str] | None:
    for label in VAT_LABEL_PATTERN.finditer(raw):
        if (
            label.end() <= token.start and _LABEL_GAP.fullmatch(raw[label.end() : token.start])
        ) or (
            token.end <= label.start()
            and _REVERSE_LABEL_GAP.fullmatch(raw[token.end : label.start()])
        ):
            return label
    return None


def scoped_vat_tokens(raw: str) -> tuple[VatToken, ...]:
    """실제 부가세 표지와 연결된 값 또는 독립 선택 구문의 값만 반환한다."""
    tokens = vat_tokens(raw)
    if standalone_vat_modes(raw):
        return tokens
    scoped = []
    for token in tokens:
        # 부가세 없음/0원/면제 자체에 표지가 포함된 경우.
        word = raw[token.start : token.end]
        if (
            VAT_LABEL_PATTERN.match(word)
            or _adjacent_label(raw, token)
            or (scoped and _grammar_fullmatch(_MODE_LINK, raw[scoped[-1].end : token.start]))
        ):
            scoped.append(token)
        elif (
            word == "면세"
            and _STANDALONE_LEAD.fullmatch(raw[: token.start])
            and (
                len(tokens) == 1
                and _standalone_tail_fullmatch(raw[token.end :])
                or any(
                    _grammar_fullmatch(_MODE_LINK, raw[token.end : other.start])
                    for other in tokens
                    if other.start > token.end
                )
            )
        ):
            # 명확한 면세 표지로 시작하는 정정/선택은 부가세 대상이다.
            scoped.append(token)
    return tuple(scoped)


def scoped_vat_modes(raw: str) -> set[str]:
    return {token.mode for token in scoped_vat_tokens(raw)}


@dataclass(frozen=True)
class VatSelection:
    start: int
    end: int
    mode: str


_SELECTION_TAIL = re.compile(
    r"\s?(?:(?:하(?:는|기)|할|한)\s?(?:것|거|걸|방식|조건|구분|형태))?"
    r"\s?(?:으로|로)?\s?"
    r"(?:선택|후보|제안|변경안|정정안|조건|방식|구분)"
    r"(?=\s?[은는이가을를만도]?\s?(?:전부\s?|모두\s?|다\s?)?(?:취소|철회|없(?:던|었던)\s?))"
)


def vat_selection_mentions(raw: str) -> tuple[VatSelection, ...]:
    """값을 지목한 철회의 대상만 돌려준다. 철회 자체의 확정은 하지 않는다."""
    selections = []
    for token in vat_tokens(raw):
        label = _adjacent_label(raw, token)
        word = raw[token.start : token.end]
        if not (label or VAT_LABEL_PATTERN.match(word) or word == "면세"):
            continue
        tail_end = _grammar_match_end(_SELECTION_TAIL, raw, token.end)
        if tail_end is not None:
            selections.append(
                VatSelection(
                    min(token.start, label.start()) if label else token.start,
                    tail_end,
                    token.mode,
                )
            )
    return tuple(selections)
