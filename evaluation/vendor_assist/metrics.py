"""업체 도우미 평가 입력 검증과 필드·후보·비용 지표. 유료 호출을 하지 않는다."""

from __future__ import annotations

import json
import math
import statistics
from collections import Counter, defaultdict
from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

FIELDS: dict[str, tuple[str, ...]] = {
    "period": ("start", "end", "excluded"),
    "quote": ("amount", "vat", "days", "valid"),
    "dates": ("days", "crew"),
    "extra": ("title", "reason", "amount", "vat"),
}
ACTIONS = {"keep", "set", "clear", "uncertain"}
STATUSES = {"valid", "conflict", "invalid", "uncertain"}
KINDS = {
    "simple",
    "multi_field",
    "partial",
    "negation",
    "double_negation",
    "correction",
    "previous_correction",
    "previous_keep",
    "clear",
    "ambiguous",
    "conflict",
    "no_action",
}
REQUIRED_TAGS = {
    "amount_correction",
    "duration_correction",
    "vat_negation",
    "double_negation",
    "multi_date_exclusion",
    "previous_correction",
    "clear",
    "no_action",
}
VAT_MODES = {"included", "excluded", "exempt"}


def _date(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 10:
        return False
    try:
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def valid_value(form: str, field: str, value: object) -> bool:
    """금액·날짜를 만들지 않고 정답/응답 자료형만 검사한다."""
    if field in {"amount", "crew"} or (form == "quote" and field == "days"):
        return type(value) is int and value > 0
    if field == "vat":
        return isinstance(value, str) and value in VAT_MODES
    if field in {"start", "end", "valid"}:
        return _date(value)
    if form == "period" and field == "excluded":
        return (
            isinstance(value, list)
            and all(_date(x) for x in value)
            and len(value) == len(set(value))
        )
    if form == "dates" and field == "days":
        return (
            isinstance(value, list)
            and bool(value)
            and all(_date(x) for x in value)
            and len(value) == len(set(value))
        )
    if form == "extra" and field in {"title", "reason"}:
        maximum = 60 if field == "title" else 200
        return isinstance(value, str) and bool(value.strip()) and len(value) <= maximum
    return False


def _assert_intent(form: str, field: str, intent: Any, *, place: str) -> None:
    if not isinstance(intent, dict):
        raise ValueError(f"{place}: 필드 의도가 객체가 아닙니다")  # noqa: TRY004
    if not {"action", "value"}.issubset(intent):
        raise ValueError(f"{place}: action과 value가 필요합니다")
    action = intent["action"]
    value = intent["value"]
    if action not in ACTIONS:
        raise ValueError(f"{place}: 알 수 없는 action {action!r}")
    if action == "set" and not valid_value(form, field, value):
        raise ValueError(f"{place}: 허용하지 않는 set 값 {value!r}")
    if action != "set" and value is not None:
        raise ValueError(f"{place}: {action}의 value는 null이어야 합니다")


def validate_case(case: dict[str, Any]) -> None:
    where = str(case.get("id", "(id 없음)"))
    required = {
        "schema_version",
        "id",
        "split",
        "form",
        "kind",
        "tags",
        "text",
        "today",
        "base_month",
        "current",
        "previous_candidate",
        "context",
        "expected",
        "gold_origin",
        "human_verified",
        "reviewed_by",
    }
    if set(case) != required:
        raise ValueError(f"{where}: 키 누락/추가 {sorted(set(case) ^ required)}")
    if case["schema_version"] != 1 or type(case["schema_version"]) is not int:
        raise ValueError(f"{where}: schema_version은 정수 1")
    if not isinstance(case["id"], str) or not case["id"].startswith("VA-"):
        raise ValueError(f"{where}: id 형식 오류")
    if case["form"] not in FIELDS or case["kind"] not in KINDS:
        raise ValueError(f"{where}: 알 수 없는 form/kind")
    if case["split"] not in {"dev", "heldout"}:
        raise ValueError(f"{where}: split은 dev/heldout")
    if not isinstance(case["text"], str) or not 0 < len(case["text"].strip()) <= 1000:
        raise ValueError(f"{where}: text 길이 오류")
    if not _date(case["today"]) or not _date(case["base_month"]):
        raise ValueError(f"{where}: 오늘/기준 월 오류")
    if (
        not isinstance(case["tags"], list)
        or not case["tags"]
        or not all(isinstance(x, str) for x in case["tags"])
    ):
        raise ValueError(f"{where}: tags 오류")
    if case["kind"] not in case["tags"]:
        raise ValueError(f"{where}: tags에 kind가 없습니다")
    if (
        case["gold_origin"] not in {"ai_draft", "human_reviewed"}
        or type(case["human_verified"]) is not bool
    ):
        raise ValueError(f"{where}: 정답 출처·검수 상태 오류")
    if not case["human_verified"] and (
        case["reviewed_by"] is not None or case["gold_origin"] != "ai_draft"
    ):
        raise ValueError(f"{where}: 미검수 정답에 검수자 이름이 있습니다")
    if case["human_verified"] and (
        not isinstance(case["reviewed_by"], str)
        or not case["reviewed_by"].strip()
        or case["gold_origin"] != "human_reviewed"
    ):
        raise ValueError(f"{where}: 검수 표시에는 실제 검수자가 필요합니다")
    fields = set(FIELDS[case["form"]])
    if not isinstance(case["current"], dict) or set(case["current"]) != fields:
        raise ValueError(f"{where}: current 필드가 맞지 않습니다")
    for field, value in case["current"].items():
        if (
            value is not None
            and value != ""
            and value != []
            and not valid_value(case["form"], field, value)
        ):
            raise ValueError(f"{where}: current.{field} 자료형 오류")
    if not isinstance(case["expected"], dict) or set(case["expected"]) != fields:
        raise ValueError(f"{where}: 모든 expected 필드에 action/value가 필요합니다")
    for field, intent in case["expected"].items():
        if not isinstance(intent, dict) or set(intent) != {"action", "value"}:
            raise ValueError(f"{where}: expected.{field} 키 오류")
        _assert_intent(case["form"], field, intent, place=f"{where}.expected.{field}")
    previous = case["previous_candidate"]
    if previous is not None:
        if not isinstance(previous, dict) or set(previous) != {
            "request_id",
            "text",
            "fields",
        }:
            raise ValueError(f"{where}: previous_candidate 구조 오류")
        if (
            not previous["request_id"]
            or not isinstance(previous["text"], str)
            or not previous["text"].strip()
        ):
            raise ValueError(f"{where}: 이전 요청/문장이 없습니다")
        if not isinstance(previous["fields"], list) or len(previous["fields"]) != len(
            fields
        ):
            raise ValueError(f"{where}: 이전 후보도 모든 필드가 필요합니다")
        seen = set()
        for candidate in previous["fields"]:
            if not isinstance(candidate, dict) or set(candidate) != {
                "field",
                "action",
                "value",
                "evidence",
            }:
                raise ValueError(f"{where}: 이전 필드 구조 오류")
            field = candidate["field"]
            if field not in fields or field in seen:
                raise ValueError(f"{where}: 이전 필드 중복/범위 초과")
            seen.add(field)
            _assert_intent(
                case["form"], field, candidate, place=f"{where}.previous.{field}"
            )
            evidence = candidate["evidence"]
            if not isinstance(evidence, str):
                raise ValueError(f"{where}: previous.{field}.evidence는 문자열")  # noqa: TRY004
            if candidate["action"] != "keep" and (
                not evidence or evidence not in previous["text"]
            ):
                raise ValueError(f"{where}: 이전 후보 근거가 이전 원문에 없습니다")
    context = case["context"]
    if not isinstance(context, dict) or set(context) != {
        "vat_modes",
        "days_max",
        "crew_max",
        "required_days",
        "holidays",
        "no_weekend",
    }:
        raise ValueError(f"{where}: context 구조 오류")
    if not isinstance(context["vat_modes"], list) or not set(
        context["vat_modes"]
    ).issubset(VAT_MODES):
        raise ValueError(f"{where}: vat_modes 오류")
    if type(context["days_max"]) is not int or context["days_max"] <= 0:
        raise ValueError(f"{where}: days_max 오류")
    if type(context["crew_max"]) is not int or context["crew_max"] <= 0:
        raise ValueError(f"{where}: crew_max 오류")
    if context["required_days"] is not None and (
        type(context["required_days"]) is not int or context["required_days"] <= 0
    ):
        raise ValueError(f"{where}: required_days 오류")
    if not isinstance(context["holidays"], list) or not all(
        _date(x) for x in context["holidays"]
    ):
        raise ValueError(f"{where}: holidays 오류")
    if type(context["no_weekend"]) is not bool:
        raise ValueError(f"{where}: no_weekend 오류")


def load_cases(path: Path) -> list[dict[str, Any]]:
    cases = []
    for line_no, line in enumerate(
        path.read_text(encoding="utf-8-sig").splitlines(), 1
    ):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
            if not isinstance(case, dict):
                raise ValueError("객체 한 개가 필요합니다")  # noqa: TRY004
            validate_case(case)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{path}:{line_no}: {exc}") from exc
        cases.append(case)
    if not cases:
        raise ValueError(f"{path}: 빈 데이터셋")
    ids = [x["id"] for x in cases]
    if len(set(ids)) != len(ids):
        raise ValueError(f"{path}: 중복 id")
    return cases


def validate_corpus(dev: list[dict], heldout: list[dict]) -> dict[str, Any]:
    """최소 분량과 2:1 비율은 전체·폼별로 동시에 지킨다."""
    all_cases = [*dev, *heldout]
    if len(all_cases) < 120 or len(dev) != 2 * len(heldout):
        raise ValueError("전체 120건 이상, dev:heldout=2:1이 필요합니다")
    if any(x["split"] != "dev" for x in dev) or any(
        x["split"] != "heldout" for x in heldout
    ):
        raise ValueError("파일의 split 표기 오류")
    if len({x["id"] for x in all_cases}) != len(all_cases) or len(
        {x["text"] for x in all_cases}
    ) != len(all_cases):
        raise ValueError("id 또는 문장이 중복되었습니다")
    counts = Counter(x["form"] for x in all_cases)
    for form in FIELDS:
        a = sum(x["form"] == form for x in dev)
        b = sum(x["form"] == form for x in heldout)
        if a + b < 30 or a != 2 * b:
            raise ValueError(f"{form}: 30건 이상, 2:1 분할이 필요합니다")
    tags = {tag for x in all_cases for tag in x["tags"]}
    if not REQUIRED_TAGS.issubset(tags):
        raise ValueError(f"필수 평가 범주 누락: {sorted(REQUIRED_TAGS - tags)}")
    if {x["kind"] for x in all_cases} != KINDS:
        raise ValueError("필수 12개 평가 종류 누락")
    return {
        "total": len(all_cases),
        "dev": len(dev),
        "heldout": len(heldout),
        "per_form": dict(counts),
    }


def requires_final(path: Path, cases: list[dict], final: bool) -> bool:
    """파일 이름을 바꿔도 내부 split=heldout이면 마지막 평가 허가를 요구한다."""
    is_heldout = "heldout" in path.name.lower().replace("_", "").replace("-", "")
    return not final and (is_heldout or any(x["split"] == "heldout" for x in cases))


def normalize_fields(form: str, fields: list[dict] | dict) -> dict[str, dict]:
    """검증 불가·충돌 후보는 적용 가능한 후보로 계산하지 않는다."""
    result = {field: {"action": "keep", "value": None} for field in FIELDS[form]}
    if isinstance(fields, dict):
        items = [{"field": k, **v} for k, v in fields.items()]
    elif isinstance(fields, list):
        items = fields
    else:
        raise ValueError("fields는 리스트/필드 맵이어야 합니다")  # noqa: TRY004
    seen = set()
    for candidate in items:
        if not isinstance(candidate, dict) or candidate.get("field") not in result:
            raise ValueError("허용하지 않는 응답 필드")
        field = candidate["field"]
        if field in seen:
            raise ValueError("응답 필드 중복")
        seen.add(field)
        status = candidate.get("status", "valid")
        if status not in STATUSES:
            raise ValueError("후보 상태 오류")
        if status != "valid":
            result[field] = {"action": "uncertain", "value": None}
        else:
            _assert_intent(form, field, candidate, place=f"응답.{field}")
            result[field] = {"action": candidate["action"], "value": candidate["value"]}
    return result


def _same(gold: dict, pred: dict) -> bool:
    if gold["action"] != pred["action"]:
        return False
    a, b = gold["value"], pred["value"]
    if isinstance(a, list) and isinstance(b, list):
        return sorted(a) == sorted(b)
    return type(a) is type(b) and a == b


def percentile(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(p / 100 * len(ordered)) - 1)
    return ordered[index]


def _breakdown(rows: list[dict], key: str) -> dict[str, dict]:
    groups: dict[str, list[bool]] = defaultdict(list)
    for row in rows:
        names = row["tags"] if key == "tags" else [row[key]]
        for name in names:
            groups[name].append(row["exact"])
    return {
        name: {
            "count": len(hits),
            "exact_cases": sum(hits),
            "exact_rate": sum(hits) / len(hits),
        }
        for name, hits in sorted(groups.items())
    }


def price_for_usage(
    usage: dict | None, prices: dict[str, Decimal | None] | None
) -> float | None:
    """알려지지 않은 토큰·가격은 0원으로 간주하지 않는다."""
    if not isinstance(usage, dict) or not isinstance(prices, dict):
        return None
    input_tokens, output_tokens, cached_tokens = (
        usage.get(k) for k in ("input_tokens", "output_tokens", "cached_tokens")
    )
    if any(
        type(n) is not int or n < 0
        for n in (input_tokens, output_tokens, cached_tokens)
    ):
        return None
    if (
        cached_tokens > input_tokens
        or prices.get("input") is None
        or prices.get("output") is None
    ):
        return None
    cached_rate = prices.get("cached")
    if cached_tokens and cached_rate is None:
        return None
    value = (
        Decimal(input_tokens - cached_tokens) * prices["input"]
        + Decimal(output_tokens) * prices["output"]
        + Decimal(cached_tokens) * (cached_rate or Decimal(0))
    ) / Decimal(1_000_000)
    return float(value)


def summarize(
    cases: list[dict], outcomes: list[dict], *, runner: str, prices: dict | None = None
) -> dict[str, Any]:
    """잘못된 후보 / 누락 비율의 분모와 오류 처리 의미를 함께 보존한다."""
    if len(cases) != len(outcomes) or any(
        a["id"] != b["id"] for a, b in zip(cases, outcomes, strict=True)
    ):
        raise ValueError("정답과 응답의 건수/순서/ID가 다릅니다")
    total_fields = field_correct = actionable_gold = actionable_pred = (
        false_candidates
    ) = omissions = 0
    errors = timeouts = 0
    conflict_fields = conflict_options = conflict_option_mismatches = 0
    per_field: dict[str, dict[str, int]] = defaultdict(
        lambda: {"total": 0, "correct": 0, "false_candidates": 0, "omissions": 0}
    )
    latency = []
    scored = []
    usage_totals: dict[str, int | None] = {}
    for case, outcome in zip(cases, outcomes, strict=True):
        raw_fields = outcome.get("fields")
        for candidate in raw_fields if isinstance(raw_fields, list) else []:
            if not isinstance(candidate, dict) or candidate.get("status") != "conflict":
                continue
            conflict_fields += 1
            gold = case["expected"].get(candidate.get("field"))
            alternatives = candidate.get("alternatives")
            for option in alternatives if isinstance(alternatives, list) else []:
                if not isinstance(option, dict):
                    continue
                conflict_options += 1
                if gold and gold["action"] in {"set", "clear"}:
                    conflict_option_mismatches += not _same(
                        gold,
                        {"action": option.get("action"), "value": option.get("value")},
                    )
        error = outcome.get("error")
        errors += bool(error)
        timeouts += bool(outcome.get("timeout"))
        elapsed = outcome.get("latency_ms")
        if (
            isinstance(elapsed, (int, float))
            and not isinstance(elapsed, bool)
            and elapsed >= 0
        ):
            latency.append(float(elapsed))
        pred = outcome.get("fields") if not error else None
        if pred is not None:
            try:
                pred = normalize_fields(case["form"], pred)
            except ValueError as exc:
                error = f"InvalidCandidate: {exc}"
                errors += 1
                pred = None
        if pred is None:
            pred = {
                field: {"action": "keep", "value": None}
                for field in FIELDS[case["form"]]
            }
        wrong = []
        for field in FIELDS[case["form"]]:
            gold = case["expected"][field]
            got = pred[field]
            correct = not error and _same(gold, got)
            is_action_gold = gold["action"] in {"set", "clear"}
            is_action_pred = not error and got["action"] in {"set", "clear"}
            false = is_action_pred and not _same(gold, got)
            # 잘못된 set 값도 올바른 값을 추출하지 못한 것이므로 FP와 FN에 함께 센다.
            missing = is_action_gold and (not is_action_pred or not _same(gold, got))
            total_fields += 1
            field_correct += bool(correct)
            actionable_gold += is_action_gold
            actionable_pred += is_action_pred
            false_candidates += false
            omissions += missing
            stats = per_field[f"{case['form']}.{field}"]
            stats["total"] += 1
            stats["correct"] += bool(correct)
            stats["false_candidates"] += false
            stats["omissions"] += missing
            if not correct:
                wrong.append(field)
        scored.append(
            {
                "id": case["id"],
                "form": case["form"],
                "kind": case["kind"],
                "tags": case["tags"],
                "exact": not wrong,
                "wrong_fields": wrong,
                "predicted": pred,
                "expected": case["expected"],
                "latency_ms": outcome.get("latency_ms"),
                "usage": outcome.get("usage"),
                "cost_usd": price_for_usage(outcome.get("usage"), prices),
                "error": error,
                "timeout": bool(outcome.get("timeout")),
                "warnings": outcome.get("warnings", []),
                "source": outcome.get("source"),
                "candidate_fields": raw_fields,
                "llm_error": outcome.get("llm_error"),
                "llm_timeout": bool(outcome.get("llm_timeout")),
                "adapter_invoked": bool(outcome.get("adapter_invoked")),
            }
        )
    for key in ("input_tokens", "output_tokens", "cached_tokens"):
        values = [(item.get("usage") or {}).get(key) for item in outcomes]
        usage_totals[key] = (
            sum(values) if all(type(x) is int and x >= 0 for x in values) else None
        )
    all_costs = [row["cost_usd"] for row in scored]
    total_cost = sum(all_costs) if all(x is not None for x in all_costs) else None
    field_ratios = {
        name: {**stats, "accuracy": stats["correct"] / stats["total"]}
        for name, stats in sorted(per_field.items())
    }
    return {
        "runner": runner,
        "cases": len(cases),
        "exact_cases": sum(x["exact"] for x in scored),
        "exact_case_rate": sum(x["exact"] for x in scored) / len(cases)
        if cases
        else None,
        "field_total": total_fields,
        "field_correct": field_correct,
        "field_accuracy": field_correct / total_fields if total_fields else None,
        "actionable_gold": actionable_gold,
        "actionable_predicted": actionable_pred,
        "false_candidates": false_candidates,
        "conflict_fields": conflict_fields,
        "conflict_options": conflict_options,
        "conflict_option_mismatches": conflict_option_mismatches,
        "false_candidate_rate": false_candidates / actionable_pred
        if actionable_pred
        else None,
        "omissions": omissions,
        "omission_rate": omissions / actionable_gold if actionable_gold else None,
        "latency_ms": {
            "count": len(latency),
            "mean": statistics.fmean(latency) if latency else None,
            "p50": percentile(latency, 50),
            "p95": percentile(latency, 95),
        },
        "usage": {
            **usage_totals,
            "cases_with_usage": sum(bool(x.get("usage")) for x in outcomes),
        },
        "cost_usd": total_cost,
        "error_count": errors,
        "timeout_count": timeouts,
        "error_rate": errors / len(cases) if cases else None,
        "llm_adapter_invocations": sum(
            bool(item.get("adapter_invoked")) for item in outcomes
        ),
        "llm_error_count": sum(bool(item.get("llm_error")) for item in outcomes),
        "llm_timeout_count": sum(bool(item.get("llm_timeout")) for item in outcomes),
        "llm_error_rate": sum(bool(item.get("llm_error")) for item in outcomes)
        / len(cases)
        if cases
        else None,
        "llm_timeout_rate": sum(bool(item.get("llm_timeout")) for item in outcomes)
        / len(cases)
        if cases
        else None,
        "source_counts": dict(
            Counter(item.get("source") or "unknown" for item in outcomes)
        ),
        "per_field": field_ratios,
        "per_form": _breakdown(scored, "form"),
        "per_kind": _breakdown(scored, "kind"),
        "per_tag": _breakdown(scored, "tags"),
        "rows": scored,
    }


def format_report(report: dict, *, show_wrong: int = 10) -> str:
    def n(value: float | None, suffix: str = "") -> str:
        return "N/A" if value is None else f"{value:.3f}{suffix}"

    latency = report["latency_ms"]
    lines = [
        f"## {report['runner']} · {report['cases']}건",
        "",
        f"정답 검수: {report.get('gold_status', '미표기')} (AI 초안은 실성능 승인 근거가 아님)",
        "",
        "| 필드 정확도 | 전체 정확 | 잘못된 후보 | 후보 오류율 | 추출 누락 | 누락률 | 오류 | 시간초과 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
        f"| {n(report['field_accuracy'])} ({report['field_correct']}/{report['field_total']}) | {report['exact_cases']}/{report['cases']} | {report['false_candidates']} | {n(report['false_candidate_rate'])} | {report['omissions']} | {n(report['omission_rate'])} | {report['error_count']} | {report['timeout_count']} |",
        "",
        f"지연: 평균 {n(latency['mean'])} ms · p50 {n(latency['p50'])} ms · p95 {n(latency['p95'])} ms",
        f"토큰: 입력 {report['usage']['input_tokens']} · 출력 {report['usage']['output_tokens']} · 캐시 {report['usage']['cached_tokens']} (None=미계측)",
        f"비용: {report['cost_usd'] if report['cost_usd'] is not None else 'null (요금/사용량 미확인)'} USD",
        f"해석 출처: {report['source_counts']} (rule=규칙 또는 모델 실패 대체, llm=모델)",
        f"별도 확인이 필요한 충돌: {report['conflict_fields']}필드 · {report['conflict_options']}대안 · 단일 정답과 다른 대안 {report['conflict_option_mismatches']}개 (단일 적용 후보 오류율과 별도)",
        f"서비스 오류: {report['error_count']}/{report['cases']} (시간초과 {report['timeout_count']}) · 모델 경로 예외: {report['llm_error_count']}/{report['cases']} (시간초과 {report['llm_timeout_count']}, 오프라인 스텁 오류도 포함 가능)",
        "",
        "| 화면 | 문장 수 | 문장 전체 정확률 |",
        "| --- | ---: | ---: |",
    ]
    for form, stat in report["per_form"].items():
        lines.append(f"| {form} | {stat['count']} | {n(stat['exact_rate'])} |")
    lines.extend(["", "| 표현 유형 | 건수 | 전체 정확률 |", "| --- | ---: | ---: |"])
    for kind, stat in report["per_kind"].items():
        lines.append(f"| {kind} | {stat['count']} | {n(stat['exact_rate'])} |")
    wrong = [row for row in report["rows"] if not row["exact"]]
    if show_wrong and wrong:
        lines.extend(["", f"오류 예시 (앞 {min(show_wrong, len(wrong))}건)", ""])
        for row in wrong[:show_wrong]:
            lines.append(
                f"- {row['id']}: {','.join(row['wrong_fields'])} · {row['error'] or '필드 불일치'}"
            )
    return "\n".join(lines)
