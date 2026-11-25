"""업체 도우미 자연어 해석: 규칙 후보·모델 후보를 검증하고 화면용 제안만 돌려준다."""

from app.vendor.assist_schemas import (
    AssistContext,
    AssistRequest,
    AssistResponse,
    ModelInterpretation,
)
from app.vendor.assist_validation import (
    merge_candidates,
    resolve_withdrawals,
    review_fields,
    review_previous,
    reviewed_date_edit,
)

FALLBACK_WARNING = "AI 해석을 사용할 수 없어 규칙으로 확인한 후보만 보여 드려요."


async def interpret(
    data: AssistRequest,
    context: AssistContext,
    *,
    use_model: bool = True,
    trace: dict | None = None,
) -> AssistResponse:
    """규칙 전용과 하이브리드가 동일 검증·병합 경로를 쓴다."""
    if trace is not None:
        trace.clear()
        trace.update(
            rule_raw=[item.model_dump(mode="json") for item in data.rule_fields],
            model_raw=None,
            previous_raw=(
                data.previous_candidate.model_dump(mode="json")
                if data.previous_candidate is not None
                else None
            ),
            previous_verified=None,
            previous_rejected={},
            rule_reviewed=[],
            model_reviewed=None,
            date_edit_reviewed=[],
            merged=[],
            final=[],
            decisions={},
        )
    previous, previous_rejected = review_previous(data, context)
    withdrawals = resolve_withdrawals(data, previous, context)
    if trace is not None and previous is not None:
        trace["previous_verified"] = previous.model_dump(mode="json")
    if trace is not None:
        trace["previous_rejected"] = dict(previous_rejected)
    safe_request = data.model_copy(update={"previous_candidate": previous})
    # 실제 폼 값과 검증된 미적용 후보는 서로 다른 상태다.
    # 후보 조합의 업무 검증은 review_fields/merge_candidates에 맡기고
    # 원래 요청의 current 및 거부된 이전 후보 정보는 바꾸지 않는다.
    rule, warnings = review_fields(
        data.rule_fields, "rule", data, context, previous=previous, withdrawals=withdrawals
    )
    if trace is not None:
        trace["rule_reviewed"] = [item.model_dump(mode="json") for item in rule.values()]

    model = None
    rejected_relations: set[str] = set()
    source: str = "rule"
    if use_model:
        try:
            # 모델은 한 번만 호출하고, 수동 적용 전에는 업무 데이터를 바꾸지 않는다.
            from app.agent import vendor_assist

            result = ModelInterpretation.model_validate(
                await vendor_assist.interpret(safe_request, context)
            )
            if trace is not None:
                trace["model_raw"] = [item.model_dump(mode="json") for item in result.fields]
            model, model_warnings = review_fields(
                result.fields, "llm", data, context, previous=previous, withdrawals=withdrawals
            )
            rejected_relations = {
                item.field
                for item in result.fields
                if item.correction is not None
                and item.field in model
                and model[item.field].status != "valid"
            }
            warnings.extend(model_warnings)
            source = "llm"
        except Exception:  # noqa: BLE001 - 외부 오류는 같은 규칙 경로로 복구
            warnings.append(FALLBACK_WARNING)
    if trace is not None and model is not None:
        trace["model_reviewed"] = [item.model_dump(mode="json") for item in model.values()]

    date_edits = reviewed_date_edit(data, context, previous, previous_rejected)
    for field in rejected_relations:
        # 모델이 구체적으로 지목한 교체 관계가 틀렸다면 별도 규칙으로 덮지 않는다.
        date_edits.pop(field, None)
    if trace is not None:
        trace["date_edit_reviewed"] = [item.model_dump(mode="json") for item in date_edits.values()]
    merged_out: list = []
    decisions: dict = {}
    fields = merge_candidates(
        data,
        context,
        rule,
        model,
        previous,
        date_edits=date_edits,
        decisions=decisions if trace is not None else None,
        merged_out=merged_out if trace is not None else None,
        previous_rejected=previous_rejected,
        withdrawals=withdrawals,
    )
    if any(item.reason_code == "combined_constraint" for item in fields):
        warnings.append("후보를 함께 적용하면 업무 조건이 맞지 않아 확인이 필요해요.")
    if trace is not None:
        trace["merged"] = [item.model_dump(mode="json") for item in merged_out]
        trace["final"] = [item.model_dump(mode="json") for item in fields]
        trace["decisions"] = decisions
    return AssistResponse(
        request_id=data.request_id,
        form=data.form,
        source=source,
        fields=fields,
        warnings=list(dict.fromkeys(warnings)),
    )
