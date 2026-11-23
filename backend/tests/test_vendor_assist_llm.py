"""업체 도우미 LLM 경계: 역할별 옵션, 한 번의 호출, 원문 분리, 개인정보 가림."""

import asyncio
import importlib
import json
import sys
import types
import uuid
from datetime import date
from types import SimpleNamespace
from typing import ClassVar

import pytest
from pydantic import BaseModel

from app.agent import llm, vendor_assist
from app.config import settings


class Reply(BaseModel):
    text: str


@pytest.fixture
def fake_chat(monkeypatch):
    """실제 API 없이 SDK 인자와 ainvoke 횟수를 관찰한다."""

    class FakeChatOpenAI:
        instances: ClassVar[list] = []
        result = Reply(text="해석")
        error = None
        raw = SimpleNamespace(
            usage_metadata={
                "input_tokens": 24,
                "output_tokens": 8,
                "input_token_details": {"cache_read": 12},
            },
            response_metadata={},
        )

        def __init__(self, **kwargs):
            self.options = kwargs
            self.structured_options = None
            self.calls = 0
            self.instances.append(self)

        def with_structured_output(self, schema, **kwargs):
            self.schema = schema
            self.structured_options = kwargs
            return self

        async def ainvoke(self, messages):
            self.messages = messages
            self.calls += 1
            if self.error is not None:
                raise self.error
            if self.structured_options["include_raw"]:
                return {"raw": self.raw, "parsed": self.result, "parsing_error": None}
            return self.result

    monkeypatch.setitem(
        sys.modules, "langchain_openai", types.SimpleNamespace(ChatOpenAI=FakeChatOpenAI)
    )
    return FakeChatOpenAI


@pytest.fixture
def interpretation_schema(monkeypatch):
    """API 구현을 합치기 전에도 어댑터 경계를 독립 검증한다."""
    try:
        module = importlib.import_module("app.vendor.assist_schemas")
    except ModuleNotFoundError:

        class ModelInterpretation(BaseModel):
            fields: list[dict]

        module = types.ModuleType("app.vendor.assist_schemas")
        module.ModelInterpretation = ModelInterpretation
        monkeypatch.setitem(sys.modules, module.__name__, module)
    return module.ModelInterpretation


def _request():
    return SimpleNamespace(
        form="quote",
        request_id="private-request-id",
        resource_id=uuid.UUID("c1d8ec6d-3fe2-4cc0-aee5-01c25a8df165"),
        text="455만원으로 바꿔 주세요. 연락처 010-1234-5678, test@example.com",
        current={"amount": 3500000, "vat": "included", "hidden": "고객 비밀"},
        previous_candidate=SimpleNamespace(
            request_id="previous-private-id",
            text="기존 355만원, 010-9876-5432",
            fields=[
                SimpleNamespace(field="amount", action="set", value=3550000, evidence="355만원"),
                SimpleNamespace(field="hidden", action="set", value="비밀", evidence="비밀"),
            ],
        ),
        rule_fields=[],
    )


def _context():
    return SimpleNamespace(today=date(2026, 11, 23), base_month=date(2026, 12, 1))


def test_vendor_role_has_independent_config_and_preserves_other_defaults(monkeypatch):
    monkeypatch.setattr(settings, "openai_agent_model", "main-model")
    monkeypatch.setattr(settings, "openai_reasoning_effort", "low")
    monkeypatch.setattr(settings, "openai_light_model", "light-model")
    monkeypatch.setattr(settings, "openai_light_effort", "none")
    monkeypatch.setattr(settings, "openai_light_roles", "trade,vendor_assist")
    monkeypatch.setattr(settings, "openai_vendor_assist_model", "vendor-model")
    monkeypatch.setattr(settings, "openai_vendor_assist_effort", "medium")

    assert llm.model_for("vendor_assist") == ("vendor-model", "medium")
    assert llm.model_for("orchestrator") == ("main-model", "low")
    assert llm.model_for("schedule") == ("main-model", "low")
    assert llm.model_for("trade") == ("light-model", "none")


def test_no_live_call_without_live_flag(monkeypatch, interpretation_schema):
    monkeypatch.setattr(settings, "llm_live", False)
    llm.clear_stubs()
    usage = {}
    try:
        with pytest.raises(llm.LLMError):
            asyncio.run(vendor_assist.interpret(_request(), _context(), usage=usage))
        assert usage == {
            "model": None,
            "input_tokens": None,
            "output_tokens": None,
            "cached_tokens": None,
            "elapsed_ms": None,
        }
        llm.set_stub("vendor_assist", interpretation_schema(fields=[]))
        result = asyncio.run(vendor_assist.interpret(_request(), _context()))
        assert result.fields == []
    finally:
        llm.clear_stubs()


def test_vendor_live_call_is_single_attempt_with_usage(monkeypatch, fake_chat):
    monkeypatch.setattr(settings, "llm_live", True)
    monkeypatch.setattr(settings, "openai_vendor_assist_model", "test-vendor-model")
    monkeypatch.setattr(settings, "openai_vendor_assist_effort", "medium")
    usage = {}

    result = asyncio.run(llm.structured("vendor_assist", Reply, [("user", "데이터")], usage=usage))

    client = fake_chat.instances[0]
    assert result == Reply(text="해석")
    assert len(fake_chat.instances) == 1 and client.calls == 1
    assert client.options["max_retries"] == 0
    assert client.options["model"] == "test-vendor-model"
    assert client.options["reasoning_effort"] == "medium"
    assert client.structured_options == {
        "method": "json_schema",
        "strict": True,
        "include_raw": True,
    }
    assert usage["model"] == "test-vendor-model"
    assert usage["input_tokens"] == 24
    assert usage["output_tokens"] == 8
    assert usage["cached_tokens"] == 12
    assert isinstance(usage["elapsed_ms"], int) and usage["elapsed_ms"] >= 0


def test_existing_role_keeps_retry_policy_and_return_type(monkeypatch, fake_chat):
    monkeypatch.setattr(settings, "llm_live", True)
    result = asyncio.run(llm.structured("orchestrator", Reply, [("user", "질문")]))
    client = fake_chat.instances[0]
    assert result == Reply(text="해석")
    assert client.calls == 1
    assert client.options["max_retries"] == 1
    assert client.structured_options["include_raw"] is False


def test_vendor_failure_has_no_retry_and_reports_elapsed(monkeypatch, fake_chat):
    monkeypatch.setattr(settings, "llm_live", True)
    fake_chat.error = TimeoutError("provider timeout")
    usage = {}
    with pytest.raises(llm.LLMError, match="vendor_assist 호출 실패"):
        asyncio.run(llm.structured("vendor_assist", Reply, [("user", "비공개")], usage=usage))
    assert len(fake_chat.instances) == 1
    assert fake_chat.instances[0].calls == 1
    assert fake_chat.instances[0].options["max_retries"] == 0
    assert usage["input_tokens"] is None and usage["output_tokens"] is None
    assert isinstance(usage["elapsed_ms"], int)


def test_invalid_structured_output_is_error_without_second_call(monkeypatch, fake_chat):
    monkeypatch.setattr(settings, "llm_live", True)
    fake_chat.result = None
    usage = {}
    with pytest.raises(llm.LLMError):
        asyncio.run(llm.structured("vendor_assist", Reply, [("user", "비공개")], usage=usage))
    assert len(fake_chat.instances) == 1 and fake_chat.instances[0].calls == 1
    assert usage["cached_tokens"] == 12


def test_usage_accepts_openai_token_metadata_fallback(monkeypatch, fake_chat):
    monkeypatch.setattr(settings, "llm_live", True)
    fake_chat.raw = SimpleNamespace(
        usage_metadata=None,
        response_metadata={
            "token_usage": {
                "prompt_tokens": 40,
                "completion_tokens": 10,
                "prompt_tokens_details": {"cached_tokens": 8},
            }
        },
    )
    usage = {}
    asyncio.run(llm.structured("vendor_assist", Reply, [("user", "비공개")], usage=usage))
    assert usage["input_tokens"] == 40
    assert usage["output_tokens"] == 10
    assert usage["cached_tokens"] == 8


def test_user_text_stays_in_user_data_with_personal_data_masked():
    messages = [
        ("system", vendor_assist._instructions()),
        ("user", vendor_assist._input(_request(), _context())),
    ]
    payload = json.loads(messages[1][1])
    assert messages[0][0] == "system"
    assert "455만원으로 바꿔 주세요" not in messages[0][1]
    assert "455만원으로 바꿔 주세요" in payload["text"]
    assert "010-1234-5678" not in messages[1][1]
    assert "test@example.com" not in messages[1][1]
    assert "010-9876-5432" not in messages[1][1]
    assert "private-request-id" not in messages[1][1]
    assert "previous-private-id" not in messages[1][1]
    assert "c1d8ec6d" not in messages[1][1]
    assert "고객 비밀" not in messages[1][1]
    assert payload["allowed_fields"] == ["amount", "vat", "days", "valid"]
    assert payload["current"] == {"amount": 3500000, "vat": "included"}
    assert len(payload["previous_candidate"]["fields"]) == 1
    assert payload["today"] == "2026-11-23" and payload["base_month"] == "2026-12-01"


def test_adapter_dispatches_one_structured_call(monkeypatch, interpretation_schema):
    observed = []

    async def structured(role, schema, messages, *, usage):
        observed.append((role, schema, messages, usage))
        return schema(fields=[])

    monkeypatch.setattr(llm, "structured", structured)
    usage = {}
    result = asyncio.run(vendor_assist.interpret(_request(), _context(), usage=usage))
    assert result == interpretation_schema(fields=[])
    assert len(observed) == 1
    assert observed[0][0] == "vendor_assist"
    assert observed[0][1] is interpretation_schema
    assert [kind for kind, _ in observed[0][2]] == ["system", "user"]
    assert observed[0][3] is usage


def test_adapter_does_not_retry_model_error(monkeypatch, interpretation_schema):
    calls = []

    async def failing_structured(role, schema, messages, *, usage):
        calls.append(role)
        raise llm.LLMError("vendor_assist 호출 실패")

    monkeypatch.setattr(llm, "structured", failing_structured)
    with pytest.raises(llm.LLMError):
        asyncio.run(vendor_assist.interpret(_request(), _context()))
    assert calls == ["vendor_assist"]
