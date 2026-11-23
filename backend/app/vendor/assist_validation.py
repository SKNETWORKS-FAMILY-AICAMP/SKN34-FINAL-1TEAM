"""업체 도우미 후보의 원문·타입·업무 한도를 결정적으로 검증한다."""

import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from itertools import pairwise

from app.calc.schedule import JOB_MAX_DAYS
from app.quote.schemas import AMOUNT_MAX
from app.schedule.models import EXTRA_AMOUNT_MAX
from app.vendor.assist_schemas import (
    FORM_FIELDS,
    Alternative,
    AssistContext,
    AssistRequest,
    CandidateField,
    FieldIntent,
    FormName,
    PreviousCandidate,
    Source,
    Value,
    check_value,
)

FULL_DATE = re.compile(
    r"(?<!\d)(20\d{2})\s*(?:년\s*|[-/.])\s*(\d{1,2})\s*"
    r"(?:월\s*|[-/.])\s*(\d{1,2})\s*일?(?!\d)"
)
MONTH_DAY = re.compile(r"(?<!\d)(\d{1,2})\s*(?:월\s*|[-/.])\s*(\d{1,2})\s*일?(?!\d)")
BARE_DAY = re.compile(r"(?<!\d)([1-9]|[12]\d|3[01])\s*일(?!\d)")
RANGE_LINK = re.compile(r"(?:부터|에서|~|〜|－|—|–|-|\.{2}|to)")
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
CREW = re.compile(r"(?<!\d)(\d{1,2})\s*(?:명|인)(?!\d)")
CLEAR = re.compile(r"(?:지워|지우|삭제|비워|비우|없애|제거|초기화)")
CLEAR_NEGATED = re.compile(
    r"(?:지우지|지워지지|삭제하지|비우지|없애지|제거하지|초기화하지|삭제 금지|삭제 말)"
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


def _date(y: int, m: int, d: int) -> date | None:
    try:
        return date(y, m, d)
    except ValueError:
        return None


def _infer_year(month: int, day: int, context: AssistContext) -> int:
    anchor = context.base_month or context.today
    if context.base_month is None:
        candidate = _date(anchor.year, month, day)
        return (
            anchor.year if candidate is not None and candidate >= context.today else anchor.year + 1
        )
    # 희망 기간이 같은 달 후반이어도 앞쪽 날짜를 1년 뒤로 넘기지 않는다.
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


def _date_tokens(
    evidence: str, context: AssistContext, *, allow_bare: bool = True
) -> list[tuple[int, int, date]]:
    found: list[tuple[int, int, date]] = []
    occupied: list[tuple[int, int]] = []

    def add(start: int, end: int, value: date | None) -> None:
        if value is None or any(start < b and end > a for a, b in occupied):
            return
        found.append((start, end, value))
        occupied.append((start, end))

    for m in FULL_DATE.finditer(evidence):
        add(m.start(), m.end(), _date(int(m[1]), int(m[2]), int(m[3])))
    for m in MONTH_DAY.finditer(evidence):
        month, day = int(m[1]), int(m[2])
        add(m.start(), m.end(), _date(_infer_year(month, day, context), month, day))
    if allow_bare and context.base_month is not None:
        for m in BARE_DAY.finditer(evidence):
            anchor = context.base_month
            add(m.start(), m.end(), _date(anchor.year, anchor.month, int(m[1])))
    return sorted(found)


def _date_mentions(
    evidence: str, context: AssistContext, *, expand: bool, allow_bare: bool = True
) -> set[date]:
    tokens = _date_tokens(evidence, context, allow_bare=allow_bare)
    found = {item[2] for item in tokens}
    if expand:
        for left, right in pairwise(tokens):
            bridge = evidence[left[1] : right[0]]
            if not RANGE_LINK.search(bridge):
                continue
            span = (right[2] - left[2]).days
            if 0 <= span <= JOB_MAX_DAYS:
                found.update(left[2] + timedelta(days=n) for n in range(span + 1))
    return found


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
                or re.match(r"\s*(?:일|명|인|월|년|개|주|시|분|%|㎡|평|대)", after)
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
        return type(value) is int and (
            value in {int(x) for x in CREW.findall(evidence)}
            or (
                ("인원" in evidence or "작업자" in evidence)
                and value in {int(x) for x in re.findall(r"(?<!\d)\d{1,2}(?!\d)", evidence)}
            )
        )
    if form == "quote" and field == "days":
        return type(value) is int and value in {int(x) for x in DAYS.findall(evidence)}
    if field == "vat":
        return type(value) is str and any(word in normalized(evidence) for word in VAT_WORDS[value])
    if field in ("start", "end", "valid"):
        return type(value) is str and date.fromisoformat(value) in _date_mentions(
            evidence, context, expand=field in ("start", "end"), allow_bare=field != "valid"
        )
    if field in ("excluded", "days"):
        return type(value) is list and set(map(date.fromisoformat, value)) <= _date_mentions(
            evidence, context, expand=True
        )
    if field in ("title", "reason"):
        return (
            type(value) is str and bool(value.strip()) and normalized(value) in normalized(evidence)
        )
    return False


def _deletion_requested(evidence: str) -> bool:
    return bool(CLEAR.search(evidence)) and not CLEAR_NEGATED.search(evidence)


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
) -> CandidateField:
    common = {
        "field": item.field,
        "source": source,
        "alternatives": [],
        "origin_request_id": data.request_id,
    }
    if item.action == "keep":
        return CandidateField(
            **common,
            action="keep",
            value=None,
            evidence=item.evidence if evidence_matches(text, item.evidence) else "",
            status="valid",
        )
    if item.action == "uncertain":
        return CandidateField(
            **common,
            action="uncertain",
            value=None,
            evidence=item.evidence if evidence_matches(text, item.evidence) else "",
            status="uncertain",
        )
    evidence_ok = evidence_matches(text, item.evidence)
    valid = False
    if evidence_ok and item.field in FORM_FIELDS[data.form]:
        if item.action == "clear":
            valid = _deletion_requested(item.evidence)
        else:
            try:
                check_value(data.form, item.field, item.value)
                valid = _grounded(data.form, item.field, item.value, item.evidence, context)
                valid = valid and _business_ok(
                    data.form, item.field, item.value, data, context, proposed
                )
            except (ValueError, TypeError, KeyError):
                valid = False
    if not valid:
        return CandidateField(
            **common,
            action="uncertain",
            value=None,
            evidence=item.evidence if evidence_ok else "",
            status="invalid",
        )
    return CandidateField(
        **common, action=item.action, value=item.value, evidence=item.evidence, status="valid"
    )


def review_fields(
    fields: list[FieldIntent],
    source: Source,
    data: AssistRequest,
    context: AssistContext,
    *,
    text: str | None = None,
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
        result = _review_one(item, source, data, context, proposed, text=original)
        if item.field in checked:
            checked[item.field] = CandidateField(
                field=item.field,
                action="uncertain",
                value=None,
                evidence="",
                source=source,
                status="uncertain",
                alternatives=[],
            )
            warnings.append("한 필드의 중복 후보를 확인해 주세요.")
        else:
            checked[item.field] = result
        if result.status == "invalid":
            warnings.append("원문 근거나 업무 조건을 확인할 수 없는 후보가 있어요.")
    return checked, list(dict.fromkeys(warnings))


def verified_previous(data: AssistRequest, context: AssistContext) -> PreviousCandidate | None:
    previous = data.previous_candidate
    if previous is None:
        return None
    checked, _ = review_fields(previous.fields, "rule", data, context, text=previous.text)
    valid = [
        FieldIntent(field=item.field, action=item.action, value=item.value, evidence=item.evidence)
        for item in checked.values()
        if item.status == "valid" and item.action in ("set", "clear")
    ]
    if not valid:
        return None
    return PreviousCandidate(
        request_id=previous.request_id,
        text=previous.text,
        fields=valid,
        sources={item.field: previous.sources.get(item.field, "rule") for item in valid},
        origins={
            item.field: previous.origins.get(item.field, previous.request_id) for item in valid
        },
    )


def _not_action(item: CandidateField | None) -> bool:
    return item is None or item.action == "keep" and item.status == "valid"


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
    )


def _merge_pair(rule: CandidateField | None, llm: CandidateField | None) -> CandidateField | None:
    if llm is None:
        return rule
    if rule is None or _not_action(rule):
        return llm
    if _not_action(llm):
        if rule.status == "valid" and rule.action in ("set", "clear"):
            return CandidateField(
                field=rule.field,
                action="uncertain",
                value=None,
                evidence="",
                source="llm",
                status="uncertain",
                alternatives=[],
            )
        return rule
    if llm.status == "invalid":
        return rule if rule.status == "valid" else llm
    if rule.status == "invalid":
        return llm
    if rule.status == "uncertain" or llm.status == "uncertain":
        return CandidateField(
            field=rule.field,
            action="uncertain",
            value=None,
            evidence="",
            source="llm",
            status="uncertain",
            alternatives=[],
        )
    if rule.status == llm.status == "valid":
        if rule.action == llm.action and rule.value == llm.value:
            return llm
        return CandidateField(
            field=rule.field,
            action="uncertain",
            value=None,
            evidence="",
            source="llm",
            status="conflict",
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


def merge_candidates(
    data: AssistRequest,
    context: AssistContext,
    rule: dict[str, CandidateField],
    model: dict[str, CandidateField] | None,
    previous: PreviousCandidate | None,
) -> list[CandidateField]:
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
        if chosen is None or _not_action(chosen):
            chosen = prior.get(field, chosen)
        if chosen is None:
            chosen = CandidateField(
                field=field,
                action="keep",
                value=None,
                evidence="",
                source="llm" if model is not None else "rule",
                status="valid",
                alternatives=[],
            )
        out.append(chosen)
    return out
