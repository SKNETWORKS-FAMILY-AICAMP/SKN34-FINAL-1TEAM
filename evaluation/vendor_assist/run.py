"""업체 도우미 규칙/하이브리드 비교 러너.

    python evaluation/vendor_assist/run.py --mode rule --split dev
    python evaluation/vendor_assist/run.py --mode hybrid --split dev
    python evaluation/vendor_assist/run.py --mode hybrid --split heldout --final

기본값은 LLM_LIVE=false이며 --live 없이는 유료 요청을 허용하지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import math
import os
import subprocess
import sys
import time
import uuid
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.vendor_assist.metrics import (
    FIELDS,
    format_report,
    load_cases,
    requires_final,
    summarize,
)


def rule_outputs(cases: list[dict], *, parser_path: Path | None = None) -> list[dict]:
    """TS 원본 함수를 Node에서 직접 호출한다. Python 파서 복제는 없다."""
    args = [
        "node",
        "--experimental-strip-types",
        "--no-warnings",
        str(HERE / "rule_bridge.mts"),
    ]
    if parser_path is not None:
        args.append(str(parser_path.resolve()))
    payload = "".join(
        json.dumps(
            {
                key: case[key]
                for key in ("id", "form", "text", "today", "base_month", "context")
            },
            ensure_ascii=False,
        )
        + "\n"
        for case in cases
    )
    try:
        done = subprocess.run(
            args,
            input=payload,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Node 규칙 평가 실행 실패: {exc}") from exc
    if done.returncode:
        raise RuntimeError(
            f"기존 TS 파서 실행 실패({done.returncode}): {done.stderr[-1000:]}"
        )
    try:
        results = [
            json.loads(line) for line in done.stdout.splitlines() if line.strip()
        ]
    except json.JSONDecodeError as exc:
        raise RuntimeError("Node 파서 응답이 JSONL 형식이 아닙니다") from exc
    if len(results) != len(cases) or any(
        a["id"] != b.get("id") for a, b in zip(cases, results, strict=True)
    ):
        raise RuntimeError("Node 파서 응답 건수/순서가 맞지 않습니다")
    for row in results:
        row.update({"usage": None, "warnings": [], "timeout": False, "source": "rule"})
    return results


def _request(case: dict, rule: dict):
    from app.vendor.assist_schemas import AssistContext, AssistRequest

    # 서비스/화면에서 사용하는 동일 wire shape. 식별자는 평가용 안정 UUID다.
    request = AssistRequest.model_validate(
        {
            "request_id": case["id"],
            "form": case["form"],
            "resource_id": uuid.uuid5(uuid.NAMESPACE_URL, f"mitjip-eval:{case['id']}"),
            "text": case["text"],
            "current": case["current"],
            "previous_candidate": case["previous_candidate"],
            "rule_fields": rule["fields"],
        }
    )
    options = dict(case["context"])
    options["today"] = date.fromisoformat(case["today"])
    options["base_month"] = (
        date.fromisoformat(case["base_month"]) if case["base_month"] else None
    )
    options["holidays"] = [date.fromisoformat(day) for day in options["holidays"]]
    context = AssistContext(**options)
    return request, context


def _provider_failure(exc: Exception, fault: dict) -> None:
    """서비스가 예외를 규칙 후보로 복구해도 공급자 오류를 분리해서 남긴다."""
    if fault.get("llm_error"):
        return
    seen = set()
    current: BaseException | None = exc
    timeout = False
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        timeout = (
            timeout
            or isinstance(current, TimeoutError)
            or "timeout" in type(current).__name__.lower()
        )
        current = current.__cause__ or current.__context__
    fault["llm_error"] = type(exc).__name__
    fault["llm_timeout"] = timeout


def _instrument_usage(original, original_agent, usage: dict, fault: dict):
    """단일 사례 전용 함수를 만들며 각 호출의 usage를 고정한다."""
    adapter_supports_usage = "usage" in inspect.signature(original_agent).parameters

    async def measured(*args, **kwargs):
        fault["adapter_invoked"] = True
        kwargs["usage"] = usage
        try:
            return await original(*args, **kwargs)
        except Exception as exc:
            _provider_failure(exc, fault)
            raise

    async def measured_agent(*args, **kwargs):
        if adapter_supports_usage:
            kwargs["usage"] = usage
        try:
            return await original_agent(*args, **kwargs)
        except Exception as exc:
            _provider_failure(exc, fault)
            raise

    return measured, measured_agent


async def hybrid_outputs(
    cases: list[dict], rules: list[dict], *, live: bool, timeout: float
) -> list[dict]:
    """assist_service.interpret를 실제 호출한다. DB 라우터나 저장 동작은 거치지 않는다."""
    # 가져오기 전에 명시한다. 외부 환경에 LLM_LIVE=true여도 기본 실행은 오프라인이다.
    os.environ["LLM_LIVE"] = "true" if live else "false"
    backend = str(ROOT / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from app.agent import llm, vendor_assist
    from app.vendor import assist_service

    rows = []
    for case, rule in zip(cases, rules, strict=True):
        if rule.get("error"):
            rows.append(
                {
                    "id": case["id"],
                    "fields": None,
                    "latency_ms": 0.0,
                    "usage": None,
                    "warnings": [],
                    "error": f"규칙 파서 실패: {rule['error']}",
                    "timeout": False,
                    "source": None,
                    "llm_error": None,
                    "llm_timeout": False,
                    "adapter_invoked": False,
                }
            )
            continue
        usage: dict = {}
        fault: dict = {}
        original = llm.structured
        original_agent = vendor_assist.interpret
        measured, measured_agent = _instrument_usage(
            original, original_agent, usage, fault
        )
        llm.structured = measured
        vendor_assist.interpret = measured_agent
        started = time.perf_counter()
        try:
            request, context = _request(case, rule)
            response = await asyncio.wait_for(
                assist_service.interpret(request, context), timeout=timeout
            )
            fields = [item.model_dump(mode="json") for item in response.fields]
            rows.append(
                {
                    "id": case["id"],
                    "fields": fields,
                    "latency_ms": (time.perf_counter() - started) * 1000,
                    "usage": usage or None,
                    "warnings": response.warnings,
                    "error": None,
                    "timeout": False,
                    "source": response.source,
                    **fault,
                }
            )
        except TimeoutError:
            rows.append(
                {
                    "id": case["id"],
                    "fields": None,
                    "latency_ms": (time.perf_counter() - started) * 1000,
                    "usage": usage or None,
                    "warnings": [],
                    "error": "TimeoutError",
                    "timeout": True,
                    "source": None,
                    **fault,
                }
            )
        # 평가 중 한 사례의 예기치 않은 실패도 기록해 전체 오류율을 계산한다.
        except Exception as exc:  # noqa: BLE001
            rows.append(
                {
                    "id": case["id"],
                    "fields": None,
                    "latency_ms": (time.perf_counter() - started) * 1000,
                    "usage": usage or None,
                    "warnings": [],
                    "error": f"{type(exc).__name__}: {exc}",
                    "timeout": False,
                    "source": None,
                    **fault,
                }
            )
        finally:
            llm.structured = original
            vendor_assist.interpret = original_agent
        if live and len(rows) % 12 == 0:
            print(f"실모델 진단 {len(rows)}/{len(cases)}건 완료", file=sys.stderr, flush=True)
    return rows


def price_flags(args: argparse.Namespace) -> dict[str, Decimal | None] | None:
    values = {
        "input": args.input_price,
        "output": args.output_price,
        "cached": args.cached_price,
    }
    if any(x is not None and (not x.is_finite() or x < 0) for x in values.values()):
        raise ValueError("토큰 단가는 유한한 0 이상의 값이어야 합니다")
    if all(x is None for x in values.values()):
        return None
    if values["input"] is None or values["output"] is None:
        raise ValueError(
            "비용 계산에는 --input-price와 --output-price가 모두 필요합니다"
        )
    return values


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="업체 도우미 #178 고정 평가")
    parser.add_argument(
        "--mode",
        choices=["rule", "hybrid"],
        default="rule",
        help="규칙 단독/서비스 하이브리드",
    )
    parser.add_argument("--split", choices=["dev", "heldout"], default="dev")
    parser.add_argument("--data", type=Path, help="분할 파일 대신 사용할 JSONL")
    parser.add_argument("--form", choices=sorted(FIELDS), help="해당 화면만 실행")
    parser.add_argument(
        "--parser-path", type=Path, help="기존 프런트 parse.ts 위치(#176 통합 전 비교)"
    )
    parser.add_argument("--final", action="store_true", help="held-out 최종 평가 허가")
    parser.add_argument(
        "--live",
        action="store_true",
        help="실제 LLM API 호출을 명시적으로 허가(유료 가능)",
    )
    parser.add_argument(
        "--allow-unreviewed",
        action="store_true",
        help="미검수 AI 정답을 이용한 dev 전용 실모델 진단 허가",
    )
    parser.add_argument(
        "--timeout", type=float, default=40, help="하이브리드 사례별 응답 제한(초)"
    )
    parser.add_argument("--input-price", type=Decimal, help="입력 100만 토큰당 USD")
    parser.add_argument("--output-price", type=Decimal, help="출력 100만 토큰당 USD")
    parser.add_argument(
        "--cached-price", type=Decimal, help="캐시 입력 100만 토큰당 USD"
    )
    parser.add_argument("--show-wrong", type=int, default=10)
    parser.add_argument("--json", type=Path, help="상세 JSON 평가 결과 저장")
    args = parser.parse_args(argv)
    if args.live and args.mode != "hybrid":
        parser.error("--live는 --mode hybrid에서만 사용합니다")
    if not math.isfinite(args.timeout) or args.timeout <= 0 or args.show_wrong < 0:
        parser.error("--timeout은 양수이고 --show-wrong은 0 이상이어야 합니다")
    try:
        prices = price_flags(args)
        path = args.data or HERE / f"{args.split}.jsonl"
        cases = load_cases(path)
        if requires_final(path, cases, args.final):
            print(
                "held-out은 마지막 평가에서 한 번만 봅니다. --final을 명시해 주세요.",
                file=sys.stderr,
            )
            return 2
        if (
            args.live
            and any(not case["human_verified"] for case in cases)
            and (
                not args.allow_unreviewed
                or any(case["split"] != "dev" for case in cases)
            )
        ):
            print(
                "미검수 정답의 유료 평가는 dev에서만 --allow-unreviewed로 진단할 수 있습니다.",
                file=sys.stderr,
            )
            return 2
        if args.form:
            cases = [case for case in cases if case["form"] == args.form]
            if not cases:
                raise ValueError(f"{args.form}: 평가 문장이 없습니다")
        rules = rule_outputs(cases, parser_path=args.parser_path)
        outcomes = (
            rules
            if args.mode == "rule"
            else asyncio.run(
                hybrid_outputs(cases, rules, live=args.live, timeout=args.timeout)
            )
        )
        report = summarize(
            cases, outcomes, runner=f"{args.mode}/{path.name}", prices=prices
        )
        report["gold_status"] = (
            "human_verified"
            if all(case["human_verified"] for case in cases)
            else "ai_draft_unreviewed"
        )
        report["llm_live"] = bool(args.live)
    except (ImportError, ValueError, RuntimeError, OSError) as exc:
        print(f"평가 실행 실패: {exc}", file=sys.stderr)
        return 2
    print(format_report(report, show_wrong=args.show_wrong))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
