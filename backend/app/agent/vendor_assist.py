"""업체 도우미 자연어 해석. 업무 데이터의 검증과 적용은 vendor 서비스가 맡는다."""

from __future__ import annotations

import json
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING

from app.agent import llm
from app.agent.v3.check import mask

if TYPE_CHECKING:
    from app.vendor.assist_schemas import AssistContext, AssistRequest, ModelInterpretation


_FIELDS: dict[str, tuple[str, ...]] = {
    "period": ("start", "end", "excluded"),
    "quote": ("amount", "vat", "days", "valid"),
    "dates": ("days", "crew"),
    "extra": ("title", "reason", "amount", "vat"),
}
_PROMPT = Path(__file__).with_name("prompts") / "vendor_assist_v2.md"


@cache
def _instructions() -> str:
    return _PROMPT.read_text(encoding="utf-8")


def _private_value(value: object) -> object:
    """LLM에 보낼 데이터에 기존 AI 개인정보 가림을 동일하게 적용한다."""
    if isinstance(value, str):
        return mask(value)
    if isinstance(value, list):
        return [_private_value(item) for item in value]
    return value


def _input(data: AssistRequest, context: AssistContext) -> str:
    """허용 필드와 날짜 해석에 필요한 문맥만 보낸다. 요청·업무 식별자는 제외한다."""
    form = str(data.form)
    allowed = _FIELDS[form]
    current = {
        field: _private_value(data.current[field]) for field in allowed if field in data.current
    }
    previous = None
    if data.previous_candidate is not None:
        previous = {
            "text": mask(data.previous_candidate.text),
            "fields": [
                {
                    "field": field.field,
                    "action": field.action,
                    "value": _private_value(field.value),
                    "evidence": mask(field.evidence),
                }
                for field in data.previous_candidate.fields
                if field.field in allowed
            ],
        }
    payload = {
        "form": form,
        "allowed_fields": allowed,
        "today": context.today.isoformat(),
        "base_month": context.base_month.isoformat() if context.base_month else None,
        "current": current,
        "previous_candidate": previous,
        "text": mask(data.text),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


async def interpret(
    data: AssistRequest, context: AssistContext, *, usage: dict[str, object] | None = None
) -> ModelInterpretation:
    """모델 전용 필수 키 구조를 하나의 호출로 받고 공개 후보 계약으로 옮긴다."""
    from app.vendor.assist_schemas import FieldIntent, ModelInterpretation, ModelInterpretationV2

    messages = [("system", _instructions()), ("user", _input(data, context))]
    raw = await llm.structured("vendor_assist", ModelInterpretationV2, messages, usage=usage)
    structured = ModelInterpretationV2.model_validate(
        raw.model_dump() if hasattr(raw, "model_dump") else raw
    )
    return ModelInterpretation(
        fields=[FieldIntent.model_validate(item.model_dump()) for item in structured.fields]
    )
