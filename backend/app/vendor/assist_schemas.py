"""업체 도우미의 요청·후보·모델 응답 계약. 폼 변경은 호출자가 명시적으로 수행한다."""

import re
import uuid
from datetime import date
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    StrictStr,
    field_validator,
    model_validator,
)

from app.shared.clock import today_seoul

type FormName = Literal["period", "quote", "dates", "extra"]
type Action = Literal["keep", "set", "clear", "uncertain"]
type Source = Literal["rule", "llm"]
type Status = Literal["valid", "conflict", "invalid", "uncertain"]
type Value = StrictStr | StrictInt | list[StrictStr] | None

FORM_FIELDS: dict[FormName, tuple[str, ...]] = {
    "period": ("start", "end", "excluded"),
    "quote": ("amount", "vat", "days", "valid"),
    "dates": ("days", "crew"),
    "extra": ("title", "reason", "amount", "vat"),
}
INTEGER_FIELDS = frozenset(("amount", "crew"))
DATE_FIELDS = frozenset(("start", "end", "valid"))
ARRAY_FIELDS = frozenset(("excluded", "days"))
VAT_MODES = frozenset(("included", "excluded", "exempt"))
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}\Z")
MAX_EVIDENCE = 1000  # 채팅 전체를 원문 근거로 보내는 기존 규칙 파서와 호환
MAX_TEXT = 1000


def _date_string(value: str) -> bool:
    if not _DATE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def check_value(form: FormName, field: str, value: Value, *, allow_empty: bool = False) -> None:
    """화면별 값의 구조만 확인한다. 원문 근거와 업무 상한은 별도 검증한다."""
    if value is None:
        if allow_empty:
            return
        raise ValueError("값이 비어 있어요.")
    if field not in FORM_FIELDS[form]:
        raise ValueError("이 화면에서 쓸 수 없는 필드예요.")
    if field in INTEGER_FIELDS or (form == "quote" and field == "days"):
        if type(value) is not int or value < 0 or value > 1_000_000_000_000:
            raise ValueError("정수 범위를 확인해 주세요.")
    elif field == "vat":
        if type(value) is not str or value not in VAT_MODES:
            raise ValueError("부가세 선택값이 올바르지 않아요.")
    elif field in DATE_FIELDS:
        if type(value) is not str or not (allow_empty and value == "") and not _date_string(value):
            raise ValueError("날짜는 YYYY-MM-DD 형식이어야 해요.")
    elif field in ARRAY_FIELDS:
        if not isinstance(value, list) or len(value) > 366:
            raise ValueError("날짜 목록 길이를 확인해 주세요.")
        if any(type(item) is not str or not _date_string(item) for item in value):
            raise ValueError("날짜 목록을 확인해 주세요.")
    elif field in ("title", "reason"):
        maximum = 60 if field == "title" else 200
        if type(value) is not str or len(value) > maximum or not (value.strip() or allow_empty):
            raise ValueError("내용 길이를 확인해 주세요.")
    else:
        raise ValueError("허용되지 않은 필드예요.")


class StrictObject(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class FieldIntent(StrictObject):
    field: StrictStr
    action: Action
    value: Value
    evidence: StrictStr

    @model_validator(mode="after")
    def check_intent(self) -> "FieldIntent":
        if not 1 <= len(self.field) <= 32:
            raise ValueError("필드 이름 길이를 확인해 주세요.")
        if len(self.evidence) > MAX_EVIDENCE:
            raise ValueError("근거가 너무 길어요.")
        if self.action == "set":
            if self.value is None or not self.evidence.strip():
                raise ValueError("값 변경에는 값과 원문 근거가 필요해요.")
        elif self.value is not None:
            raise ValueError("유지·삭제·불확실 후보는 값이 없어야 해요.")
        if self.action == "clear" and not self.evidence.strip():
            raise ValueError("삭제 지시의 원문 근거가 필요해요.")
        return self


class Alternative(StrictObject):
    action: Literal["set", "clear"]
    value: Value
    evidence: StrictStr
    source: Source

    @model_validator(mode="after")
    def check_alternative(self) -> "Alternative":
        if not 0 < len(self.evidence) <= MAX_EVIDENCE:
            raise ValueError("대안에는 원문 근거가 필요해요.")
        if (self.action == "set") != (self.value is not None):
            raise ValueError("설정 대안에만 값이 있어야 해요.")
        return self


class CandidateField(FieldIntent):
    source: Source
    status: Status
    alternatives: list[Alternative]

    @model_validator(mode="after")
    def check_status(self) -> "CandidateField":
        if len(self.alternatives) > 4:
            raise ValueError("대안이 너무 많아요.")
        if self.status == "conflict":
            if self.action != "uncertain" or len(self.alternatives) < 2:
                raise ValueError("충돌은 불확실 후보와 복수 대안이어야 해요.")
        elif self.alternatives:
            raise ValueError("충돌 외에는 대안을 넣을 수 없어요.")
        if self.status in ("uncertain", "invalid") and self.action != "uncertain":
            raise ValueError("적용 불가 후보는 불확실 상태여야 해요.")
        if self.status == "valid" and self.action == "uncertain":
            raise ValueError("불확실 후보를 유효로 표시할 수 없어요.")
        return self


class PreviousCandidate(StrictObject):
    request_id: StrictStr = Field(min_length=1, max_length=80)
    text: StrictStr = Field(min_length=1, max_length=MAX_TEXT)
    fields: list[FieldIntent]

    @field_validator("text")
    @classmethod
    def previous_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("이전 요청 원문이 비어 있어요.")
        return value.strip()


class AssistRequest(StrictObject):
    request_id: StrictStr = Field(min_length=1, max_length=80)
    form: FormName
    resource_id: uuid.UUID = Field(strict=False)  # JSON UUID 문자열은 허용하고 값은 UUID로 읽는다
    text: StrictStr = Field(min_length=1, max_length=MAX_TEXT)
    current: dict[StrictStr, Value]
    previous_candidate: PreviousCandidate | None
    rule_fields: list[FieldIntent]

    @field_validator("text")
    @classmethod
    def trim_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("문장을 입력해 주세요.")
        return value.strip()

    @model_validator(mode="after")
    def check_fields(self) -> "AssistRequest":
        allowed = FORM_FIELDS[self.form]
        if len(self.current) > len(allowed) or len(self.rule_fields) > len(allowed):
            raise ValueError("현재 값 또는 규칙 후보가 너무 많아요.")
        for field, value in self.current.items():
            if field not in allowed:
                raise ValueError("화면과 맞지 않는 현재 값이에요.")
            check_value(self.form, field, value, allow_empty=True)
        for fields in (
            self.rule_fields,
            self.previous_candidate.fields if self.previous_candidate else [],
        ):
            if len(fields) > len(allowed) or len({f.field for f in fields}) != len(fields):
                raise ValueError("중복 또는 과도한 후보 필드가 있어요.")
            for item in fields:
                if item.field not in allowed:
                    raise ValueError("화면과 맞지 않는 후보 필드예요.")
                if item.action == "set":
                    check_value(self.form, item.field, item.value)
        return self


class AssistResponse(StrictObject):
    request_id: StrictStr
    form: FormName
    source: Literal["rule", "llm"]
    fields: list[CandidateField]
    warnings: list[StrictStr]


class AssistContext(StrictObject):
    """권한 검사와 업무 조회를 끝낸 뒤 모델에 넘길 최소 업무 제약."""

    today: date = Field(default_factory=today_seoul)
    base_month: date | None = None
    vat_modes: list[StrictStr] = Field(default_factory=lambda: ["included", "excluded", "exempt"])
    days_max: StrictInt = 120
    crew_max: StrictInt = 20
    required_days: StrictInt | None = None
    holidays: list[date] = Field(default_factory=list)
    no_weekend: bool = False


class ModelInterpretation(StrictObject):
    """LLM 구조화 출력의 최소 필수 모양."""

    fields: list[FieldIntent] = Field(max_length=4)
