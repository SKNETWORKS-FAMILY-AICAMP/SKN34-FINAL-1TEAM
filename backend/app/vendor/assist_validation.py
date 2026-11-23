"""업체 도우미 후보의 원문·타입·업무 한도를 결정적으로 검증한다."""

import re
import unicodedata
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from itertools import pairwise

from app.calc.schedule import JOB_MAX_DAYS
from app.quote.schemas import AMOUNT_MAX
from app.schedule.models import EXTRA_AMOUNT_MAX
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
    return bool(CLEAR.search(evidence)) and not CLEAR_NEGATED.search(evidence)


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
_RETAIN = re.compile(r"(?:그대로|유지|바꾸지|변경하지|수정하지|건드리지)")
_RETAIN_NEGATED = re.compile(
    r"(?:그대로|유지).{0,16}(?:말고|두지\s*말|않|아니|취소|빼고)"
    r"|(?:유지하지|그대로\s*두지)"
)
_UNCERTAIN_CHANGE = re.compile(r"(?:미정|모르|애매|정도|였나|일까|혹시|또는|아니면|사이|[?？])")
_DIRECT_REPLACE = re.compile(r"(?:말고|아니고|아니라|대신|→|->)")
_CHANGE_TARGET = re.compile(
    r"^(?:으로|로)?\s*(?:바꿔|바꾸|변경|수정|고쳐|고치|올려|내려|정정|확정)"
)


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
    if any(word in evidence for word in ("전부", "모두", "전체")):
        return True
    # 근거가 '삭제해 주세요'처럼 동사만 있어도, 바로 앞의 목적어로 필드를 확인한다.
    at = normalized(text).find(normalized(evidence))
    before = normalized(text)[max(at - 35, 0) : at] if at >= 0 else ""
    scope = re.split(r"[,;.!?\n]", before)[-1] + normalized(evidence)
    return _target_word(form, field, scope)


def _targeted_retention(
    form: FormName, field: str, evidence: str, text: str, context: AssistContext
) -> bool:
    if not evidence_matches(text, evidence):
        return False
    raw = normalized(text)
    at = raw.find(normalized(evidence))
    scope = raw[max(at - 16, 0) : at + len(normalized(evidence)) + 20]
    if not _RETAIN.search(evidence) or _RETAIN_NEGATED.search(scope):
        return False
    if _target_word(form, field, evidence):
        return True
    if form == "period" and field in ("start", "end") and "기간" in evidence:
        return True
    # '나머지는 그대로'는 이번 글에서 별도로 바꾼 필드에는 적용하지 않는다.
    if any(word in evidence for word in ("나머지", "다른", "그 외", "전체", "전부", "모두")):
        without_evidence = normalized(text).replace(normalized(evidence), "", 1)
        return not _mentions_field(form, field, without_evidence, context)
    return False


def _verified_correction(
    item: FieldIntent, form: FormName, text: str, context: AssistContext
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
    clause_end = normalized_text.find(",", after + len(new_text))
    if clause_end < 0:
        clause_end = len(normalized_text)
    clause_start = (
        max(
            normalized_text.rfind(",", 0, before),
            normalized_text.rfind(";", 0, before),
        )
        + 1
    )
    if _UNCERTAIN_CHANGE.search(normalized_text[clause_start:clause_end]):
        return None
    link = normalized_text[before + len(old_text) : after]
    tail = normalized_text[after + len(new_text) : after + len(new_text) + 35]
    if len(link) > 60 or not (
        _DIRECT_REPLACE.search(link) or ("에서" in link and _CHANGE_TARGET.search(tail))
    ):
        return None
    if re.search(r"(?:말고|아니고|아니라)", tail):
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

    correction = _verified_correction(item, data.form, text, context)
    if item.correction is not None and correction is None:
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
    checked, _ = review_fields(eligible, "rule", data, context, text=previous.text)
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
            value_source="utterance",
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
        value_source="previous",
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
        return rule if rule is not None else llm
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
        if chosen is None or _not_action(chosen):
            if field in prior:
                if _mentions_field(data.form, field, data.text, context):
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
