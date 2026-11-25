"""명시적인 원 단위 한글 정수 금액만 결정적으로 읽는다."""

import re

from app.vendor.assist_intent import WITHDRAW_IDIOM
from app.vendor.assist_normalize import _scalar_lexical_scopes, scalar_clause_risk

_DIGIT = dict(zip("일이삼사오육칠팔구", range(1, 10), strict=True))
_SMALL = {"천": 1000, "백": 100, "십": 10}
_MAJOR = {"억": 100_000_000, "만": 10_000}
# 잘못된 영/공/조/경 및 아라비아 숫자도 같은 금액의 일부로 묶어 잘린 값을 막는다.
_CHARS = "일이삼사오육칠팔구영공십백천만억조경0-9"
_RUN = re.compile(
    rf"(?<![가-힣0-9])(?<![0-9][.,])(?!공사(?:\s|$)|[0-9]+\s*일\s)"
    rf"(?P<body>[{_CHARS}](?:\s*[{_CHARS}]|[,.](?=[0-9]))*)(?P<won>\s*원)?"
)
_WRITTEN_DIGIT = re.compile(r"[일이삼사오육칠팔구영공조경]")
_HANGUL_RUN = re.compile(r"[일이삼사오육칠팔구영공십백천만억]+")
_ATTACHED_UNIT = re.compile(r"[십백천]|[일이삼사오육칠팔구영공십백천][만억]")
_ATTACHED_LABEL = re.compile(
    r"(?:견적|금액|가격|비용|공사비|총액|추가금(?:액)?|합계)[은는이가을를]"
)
_HANGUL_NUMBER_CHARS = frozenset("일이삼사오육칠팔구영공십백천만억")
_ASCII_DIGITS = frozenset("0123456789")
_ASCII_NUMBER_CHARS = _ASCII_DIGITS | frozenset(",.")
_OTHER_WRITTEN = re.compile(r"[일이삼사오육칠팔구영공십백천만억조경]\s*원")
_SPLIT_NUMBER = re.compile(r"[일이삼사오육칠팔구영공십백천만억0-9]\s*[,./·:점]\s*$")
_RANGE_SEPARATORS = (*"~∼〜–—－−/.,·:-", "부터", "에서", "또는", "혹은", "아니면", "점")
_END = re.compile(
    r"^(?:$|[\s,.;!?？。:)]|으로|로|이에요|예요|입니다|이요|이네요|이죠|이고|이며|"
    r"이라고|라고|[은는이가을를도만](?=$|\s|[,.;!?？。]))"
)
_MONEY_TARGET = re.compile(
    r"(?:견적|금액|가격|비용|공사비|총액|추가금(?:액)?|합계|계약금|잔금)"
    r"\s*(?:[은는이가을를:=]\s*)?$"
)
_SHORT_OMITTED_END = re.compile(r"^(?:$|[,.;!?？。]|으로|로|입니다|이에요|예요|요)")
_OTHER_MONEY = re.compile(
    r"[0-9]\s*[억천백만원]|"
    r"(?:견적|금액|가격|비용|공사비|총액|추가금(?:액)?|합계|단가|청구)"
    r"\s*(?:[은는이가을를:=]\s*)?[0-9]|[0-9](?=$|\s|[,.;!?])"
)
_UNCERTAIN = re.compile(
    r"[?？~∼〜–—－−]|아마|혹시|미정|모르|애매|일까|할까|할지|거죠|까요|"
    r"또는|아니면|혹은|사이|정도|쯤|약\s|대략|내외|안팎|가량|언저리|"
    rf"고민|생각\s?중|검토\s?중|몇|취소|철회|{WITHDRAW_IDIOM}|보류|않|아니|아닙|말고|대신|"
    r"지\s?(?:마|말|않|못)|원하지|거부|금지|안\s?(?:할|해|하|넣|적|맞)|"
    r"최대|최소|미만|초과|이상|이하|이내|한도|라면|이면|조건|가능하면|경우|"
    r"마이너스|음수|플러스|잘못|틀렸|지난|이전|기존|예전|과거|원래|"
    r"(?:말했|말씀드렸|정했|적었|썼|입력했|했)(?:는데|지만|다가)"
)
_RANGE = re.compile(r"부터|까지|에서|[+/<>-]")


def _skip_chars(text: str, pos: int, chars: frozenset[str]) -> int:
    while pos < len(text) and text[pos] in chars:
        pos += 1
    return pos


def _skip_spaces(text: str, pos: int) -> int:
    while pos < len(text) and text[pos].isspace():
        pos += 1
    return pos


def _attached_end(text: str, pos: int) -> bool:
    pos = _skip_chars(text, pos, frozenset(" \t"))
    return pos < len(text) and text[pos] in "0123456789원"


def _written_range_end(text: str, pos: int) -> bool:
    pos = _skip_spaces(text, pos)
    separator = next((s for s in _RANGE_SEPARATORS if text.startswith(s, pos)), None)
    if separator is None:
        return False
    pos = _skip_spaces(text, pos + len(separator))
    if pos >= len(text) or text[pos] not in _ASCII_DIGITS:
        return False
    pos = _skip_spaces(text, _skip_chars(text, pos, _ASCII_NUMBER_CHARS))
    return pos < len(text) and text[pos] in "억천백십만원"


def _attached_label_number(text: str, pos: int) -> bool:
    end = _skip_chars(text, pos, _HANGUL_NUMBER_CHARS)
    if end == pos:
        return False
    end = _skip_spaces(text, end)
    return end < len(text) and text[end] in _ASCII_DIGITS


def _attached_or_mixed(text: str, *, include_range: bool = False) -> bool:
    """한글 숫자 묶음을 한 번씩 읽고, 끝 위치에서만 인접 단위·숫자를 확인한다."""
    for match in _HANGUL_RUN.finditer(text):
        start, end = match.span()
        body = match[0]
        before = text[start - 1] if start else ""
        # 낱말에 붙은 숫자나 숫자 묶음 중간부터 잘라 읽은 후보는 보류한다.
        suffix = body if "가" <= before <= "힣" else body[1:]
        if _ATTACHED_UNIT.search(suffix) and _attached_end(text, end):
            return True
        if (
            include_range
            and not ("가" <= before <= "힣" or "0" <= before <= "9")
            and "만" not in body
            and "억" not in body
            and _written_range_end(text, end)
        ):
            return True
    return any(
        _attached_label_number(text, label.end()) for label in _ATTACHED_LABEL.finditer(text)
    )


def _coefficient(body: str) -> int | None:
    total = 0
    pending = 0
    limit = 10_000
    for char in body:
        if char in _DIGIT:
            if pending:
                return None
            pending = _DIGIT[char]
        elif char in _SMALL:
            scale = _SMALL[char]
            if scale >= limit:
                return None
            total += (pending or 1) * scale
            pending = 0
            limit = scale
        else:
            return None
    total += pending
    return total if 0 < total < 10_000 else None


def _value(body: str) -> int | None:
    total = 0
    start = 0
    limit = 1_000_000_000
    for i, char in enumerate(body):
        if char not in _MAJOR:
            continue
        scale = _MAJOR[char]
        coefficient = _coefficient(body[start:i])
        if coefficient is None or scale >= limit:
            return None
        total += coefficient * scale
        start = i + 1
        limit = scale
    if start < len(body):
        tail = _coefficient(body[start:])
        if tail is None:
            return None
        total += tail
    return total if 0 < total <= 1_000_000_000 else None


def written_money_tokens(text: str) -> list[tuple[int, int, int]] | None:
    """None은 기존 아라비아 숫자 경로, 빈 목록은 한글 금액을 보류한다."""
    # 호출자가 정규화한 원문 기준 위치를 사용한다.
    mentions = []
    omitted_short = False
    for match in _RUN.finditer(text):
        body = re.sub(r"\s+", "", match["body"])
        # 원을 생략한 기존 숫자 표기의 조사와 다음 낱말의 첫 음절을 숫자로 합치지 않는다.
        suffix = text[match.end() :]
        if (
            not match["won"]
            and suffix
            and (
                re.search(r"[억만천백십]이$", match["body"])
                or re.match(r"[가-힣]", suffix)
                and re.search(r"[억만천백십]\s+[일이삼사오육칠팔구영공조경]$", match["body"])
            )
        ):
            body = body[:-1]
        # 아라비아 숫자만 계수로 쓰는 기존 혼합 단위 표기는 기존 검증기에 맡긴다.
        written = (
            bool(_WRITTEN_DIGIT.search(body))
            or body[0] in "십백천만억"
            or bool(re.search(r"[억만][십백천만억]", body))
        )
        if not written:
            continue
        if not match["won"]:
            if not re.search(r"[만억]", body):
                continue
            if body in ("만", "일만", "이만", "오만") and not (
                _MONEY_TARGET.search(text[: match.start()])
                and _SHORT_OMITTED_END.match(text[match.end() :])
            ):
                omitted_short |= bool(_SHORT_OMITTED_END.match(suffix))
                continue
        mentions.append((match, body))
    if not mentions:
        return [] if _attached_or_mixed(text, include_range=True) else None
    if len(mentions) != 1 or omitted_short:
        return []
    match, body = mentions[0]
    scopes = _scalar_lexical_scopes(text, "amount", None)
    if scalar_clause_risk(text, "amount", [match.span()], _UNCERTAIN, scopes=scopes):
        return []
    if not match["won"] or not _END.match(text[match.end() :]):
        return []
    if match.start() and text[match.start() - 1] in "+-−/":
        return []
    before = text[: match.start()]
    after = text[match.end() :]
    outside = before + " " + after
    if (
        _OTHER_MONEY.search(outside)
        or _attached_or_mixed(outside)
        or _OTHER_WRITTEN.search(outside)
    ):
        return []
    if _SPLIT_NUMBER.search(before) or re.search(r"(?:^|\s)(?:한|두|세|네|두세|서너)\s*$", before):
        return []
    if len(body) == 1 and match["won"] == "원" and not _MONEY_TARGET.search(before):
        return []
    # 범위의 다른 끝이나 산식의 일부를 한 금액으로 채택하지 않는다.
    owner = next(
        (scope for scope in scopes if scope.start <= match.start() < match.end() <= scope.end),
        None,
    )
    if owner is None or _RANGE.search(text[owner.start : owner.end]):
        return []
    value = _value(body)
    return [] if value is None else [(match.start(), match.end(), value)]
