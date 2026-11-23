"""업체 도우미 자연어 해석: 규칙 후보·모델 후보를 검증하고 화면용 제안만 돌려준다."""

from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    AssistResponse,
    ModelInterpretation,
)
from app.vendor.assist_validation import merge_candidates, review_fields, verified_previous

FALLBACK_WARNING = "AI 해석을 사용할 수 없어 규칙으로 확인한 후보만 보여 드려요."


async def interpret(data: AssistRequest, context: AssistContext) -> AssistResponse:
    """업무 데이터는 수정하지 않는다. 모델 실패 시에도 유효한 규칙 후보를 돌려준다."""
    rule, warnings = review_fields(data.rule_fields, "rule", data, context)
    previous = verified_previous(data, context)
    safe_request = data.model_copy(update={"previous_candidate": previous})

    try:
        # 모델 호출은 에이전트 모듈 한 곳에서 한다. 업로드·고객·업무 ID는 전달하지 않는다.
        from app.agent import vendor_assist

        result = ModelInterpretation.model_validate(
            await vendor_assist.interpret(safe_request, context)
        )
        model, model_warnings = review_fields(result.fields, "llm", data, context)
    except Exception:  # noqa: BLE001 - 모델 오류는 검증한 규칙 후보로 복구
        # 외부 모델·형식·timeout 오류는 기존 규칙 후보로 복구한다. 원문은 로그에 남기지 않는다.
        return AssistResponse(
            request_id=data.request_id,
            form=data.form,
            source="rule",
            fields=merge_candidates(data, context, rule, None, previous),
            warnings=list(dict.fromkeys([*warnings, FALLBACK_WARNING])),
        )

    return AssistResponse(
        request_id=data.request_id,
        form=data.form,
        source="llm",
        fields=merge_candidates(data, context, rule, model, previous),
        warnings=list(dict.fromkeys([*warnings, *model_warnings])),
    )
