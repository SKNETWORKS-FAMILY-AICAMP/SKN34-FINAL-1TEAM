"""네 폼의 편집 시나리오를 실제 AssistSession과 해석 서비스로 실행한다."""

import argparse
import asyncio
import json
import os
import sys
import uuid
from collections import Counter
from datetime import date
from importlib import import_module
from pathlib import Path

from evaluation.vendor_assist.scenarios.oracle import advance, compare

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "backend"))


async def evaluate(scenarios, live=False):
    from app.vendor.assist_schemas import AssistContext, AssistRequest
    from app.vendor.assist_service import interpret

    bridge = await asyncio.create_subprocess_exec(
        "node",
        "--experimental-strip-types",
        "--no-warnings",
        "--experimental-loader",
        (ROOT / "frontend/components/pro/assist/test-loader.mjs").as_uri(),
        str(Path(__file__).with_name("session_bridge.mts")),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
    )

    async def write(value):
        bridge.stdin.write((json.dumps(value) + "\n").encode())
        await bridge.stdin.drain()

    async def read():
        line = await asyncio.wait_for(bridge.stdout.readline(), 20)
        if not line:
            raise RuntimeError("화면 세션이 결과를 반환하지 않았습니다")
        return json.loads(line)

    rows = []
    try:
        for case in scenarios:
            form = case["form"]
            current, expected = case["current"], case["current"]
            settings = {
                "today": date(2026, 11, 23),
                "base_month": date(2026, 12, 1),
                "days_max": 30,
                "crew_max": 6,
                **case.get("context", {}),
            }
            for key in ("today", "base_month"):
                if isinstance(settings[key], str):
                    settings[key] = date.fromisoformat(settings[key])
            if "holidays" in settings:
                settings["holidays"] = [
                    date.fromisoformat(v) if isinstance(v, str) else v
                    for v in settings["holidays"]
                ]
            context = AssistContext(**settings)
            options = {
                "today": context.today.isoformat(),
                "baseMonth": context.base_month.isoformat(),
                "holidays": [v.isoformat() for v in context.holidays],
                "noWeekend": context.no_weekend,
                "crewMax": context.crew_max,
                "daysMax": context.days_max,
                "requiredDays": context.required_days,
                "vatModes": context.vat_modes,
            }
            await write(
                {
                    "form": form,
                    "resource": str(uuid.uuid5(uuid.NAMESPACE_URL, case["id"])),
                    "turns": len(case["steps"]),
                    "options": options,
                }
            )
            for index, step in enumerate(case["steps"], 1):
                current, expected = advance(current, expected, step["ops"])
                await write({"text": step["text"], "current": current})
                request = AssistRequest.model_validate((await read())["request"])
                response = await interpret(request, context, use_model=live)
                await write(response.model_dump(mode="json"))
                view = await read()
                preview = {
                    f["field"]: f["value"]
                    for f in view["fields"]
                    if f["status"] == "valid" and f["action"] in ("set", "clear")
                }
                applied = view["changes"] if index == len(case["steps"]) else None
                rows.append(
                    {
                        "case": case["id"],
                        "turn": index,
                        "form": form,
                        "text": step["text"],
                        "current": current,
                        "expected": expected,
                        "preview": preview,
                        "preview_score": compare(form, current, expected, preview),
                        "applied": applied,
                        "apply_score": None
                        if applied is None
                        else compare(form, current, expected, applied),
                        "source": response.source,
                        "warnings": response.warnings,
                        "apply_message": view["message"],
                    }
                )
        await write({"stop": True})
        bridge.stdin.close()
        if await asyncio.wait_for(bridge.wait(), 10):
            raise RuntimeError("화면 세션이 비정상 종료했습니다")
    finally:
        if bridge.returncode is None:
            bridge.kill()
            await bridge.wait()
    return rows


def counts(rows):
    result = {}
    for metric in ("preview_score", "apply_score"):
        totals = Counter()
        for row in rows:
            score = row[metric]
            if score is None:
                continue
            totals["turns"] += 1
            for key in ("wrong", "missed", "correct"):
                totals[key] += len(score[key])
            for key in ("wrong", "missed"):
                totals[key + "_turns"] += bool(score[key])
            totals["exact"] += not score["wrong"] and not score["missed"]
        result[metric] = dict(totals)
    return result


def load_scenarios(form=None):
    forms = (form,) if form else ("period", "quote", "dates", "extra")
    scenarios = [
        case
        for name in forms
        for case in import_module(f"{__package__}.{name}").scenarios()
    ]
    if len({c["id"] for c in scenarios}) != len(scenarios) or not all(
        c["steps"] for c in scenarios
    ):
        raise ValueError("시나리오 식별자 또는 빈 대화를 확인하세요")
    return scenarios


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live", action="store_true", help="업체 도우미 실제 모델 호출"
    )
    parser.add_argument("--json", type=Path)
    parser.add_argument("--form", choices=("period", "quote", "dates", "extra"))
    args = parser.parse_args()
    os.environ["LLM_LIVE"] = "true" if args.live else "false"
    scenarios = load_scenarios(args.form)
    rows = asyncio.run(evaluate(scenarios, args.live))
    summary = {
        "scenarios": len(scenarios),
        "mode": "hybrid" if args.live else "rule",
        "fallback_turns": sum(args.live and r["source"] == "rule" for r in rows),
        "forms": {
            f: counts([r for r in rows if r["form"] == f])
            for f in ("period", "quote", "dates", "extra")
        },
        "overall": counts(rows),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps({**summary, "rows": rows}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )


if __name__ == "__main__":
    main()
