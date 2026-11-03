import uuid
from datetime import date, datetime, time
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.auth.schemas import clean_name, normalize_email, normalize_phone
from app.vendor.models import LINKS_MAX, PORTFOLIO_MAX

Trade = Literal["dobae", "mokgong"]
# 전화 · 문자 · 카카오톡 채널 · 홈페이지 상담
ContactMethod = Literal["phone", "sms", "kakao", "web"]
FaqKey = Literal["process", "pricing", "extra_cost", "duration", "as_refund"]
Text = Annotated[str, StringConstraints(strip_whitespace=True)]
VISIT_FEE_MAX = 1_000_000


def clean_url(v: str | None) -> str | None:
    """비면 None. 'www.example.com'처럼 적으면 https://를 붙인다. http · https만 받는다."""
    v = (v or "").strip()
    if not v:
        return None
    if "://" not in v:
        v = "https://" + v
    try:
        parts = urlsplit(v)
        ok = parts.scheme in ("http", "https") and "." in (parts.hostname or "")
    except ValueError:  # 대괄호가 깨진 주소 같은 것
        ok = False
    if not ok or any(c.isspace() for c in v):
        raise ValueError("주소를 확인해 주세요. 예: https://www.example.com")
    if len(v) > 300:
        raise ValueError("주소는 300자까지 적을 수 있어요.")
    return v


def _blank_none(v: str | None) -> str | None:
    return v or None


def _local_time(v: time | None) -> time | None:
    if v is not None and v.tzinfo is not None:
        raise ValueError("시각만 적어 주세요. 예: 09:00")
    return v


class AreaIn(BaseModel):
    sido: str
    sgg: str


class VendorSignupIn(BaseModel):
    """업체 회원가입. 기본 회원정보만 받고, 사업자등록증 파일은 따로 받는다.

    상호 · 사업자번호 · 공정 · 지역 같은 업체 정보는 승인 뒤 업체 정보 관리에서 받는다.
    """

    name: str = Field(max_length=50, description="담당자 이름")
    email: str = Field(max_length=254)
    password: str = Field(min_length=8, max_length=128)

    _email = field_validator("email")(normalize_email)
    _name = field_validator("name")(clean_name)


class RejectIn(BaseModel):
    reason: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


class VendorAdminOut(BaseModel):
    """관리자 업체관리 · 가입 승인 화면용. 업체 정보 칸은 승인 뒤 채워진다."""

    user_id: uuid.UUID
    name: str
    email: str
    license_file_id: uuid.UUID
    status: str
    reject_reason: str | None
    created_at: datetime
    reviewed_at: datetime | None
    biz_name: str | None
    owner_name: str | None
    biz_no: str | None
    phone: str | None
    address: str | None
    site_url: str | None
    trades: list[str]
    areas: list[AreaIn]
    intro: str


class AreaOut(AreaIn):
    label: str  # 서울 노원구


class FaqIn(BaseModel):
    key: FaqKey
    answer: Annotated[Text, Field(max_length=200)]  # 비우면 그 질문은 지운다


class LinkIn(BaseModel):
    label: Annotated[Text, Field(max_length=40)] | None = None
    url: str

    _label = field_validator("label")(_blank_none)

    @field_validator("url")
    @classmethod
    def _url(cls, v: str) -> str:
        out = clean_url(v)
        if out is None:
            raise ValueError("주소를 적어 주세요.")
        return out


class PortfolioMeta(BaseModel):
    title: Annotated[Text, Field(min_length=1, max_length=60)]
    space: Annotated[Text, Field(max_length=20)] | None = None
    pyeong: int | None = Field(default=None, ge=1, le=200)
    taken_on: date | None = None

    _space = field_validator("space")(_blank_none)


class PortfolioIn(PortfolioMeta):
    """시공 사진 더하기. 사진은 먼저 POST /files (kind=portfolio)로 올린다."""

    file_id: uuid.UUID


class PortfolioEdit(PortfolioMeta):
    id: uuid.UUID


class PortfolioOut(PortfolioMeta):
    id: uuid.UUID
    file_id: uuid.UUID


class ProfileIn(BaseModel):
    """업체 정보 통째로 저장. 상호 · 대표자 · 사업자번호 · 개업일은 심사 대상이라 여기서 바꾸지 않는다."""

    updated_at: AwareDatetime | None = Field(
        description="GET에서 받은 값 그대로. 그 사이 다른 곳에서 저장했으면 409"
    )
    phone: str = Field(max_length=20)
    address: Annotated[Text, Field(max_length=200)] | None = None
    site_url: str | None = None
    trades: list[Trade] = Field(min_length=1, max_length=2)
    areas: list[AreaIn] = Field(min_length=1, max_length=100)
    contact_method: ContactMethod
    contact_url: str | None = Field(
        default=None, description="카카오톡 채널 · 홈페이지 상담일 때 필수"
    )
    call_start: time | None = None
    call_end: time | None = None
    visit_fee: int = Field(ge=0, le=VISIT_FEE_MAX, description="원, 부가세 포함. 0 = 무료")
    visit_fee_refund: bool = False
    tax_kind: Literal["general", "exempt"] = "general"
    interior_license: Annotated[Text, Field(max_length=100)] | None = None
    insurance: Annotated[Text, Field(max_length=100)] | None = None
    faqs: list[FaqIn] = Field(default_factory=list, max_length=5)
    links: list[LinkIn] = Field(default_factory=list, max_length=LINKS_MAX)
    portfolio: list[PortfolioEdit] | None = Field(
        default=None,
        max_length=PORTFOLIO_MAX,
        description="보내면 사진 설명 · 순서를 바꾼다. 지금 있는 사진을 모두 담는다",
    )

    _phone = field_validator("phone")(normalize_phone)
    _site = field_validator("site_url", "contact_url")(clean_url)
    _blank = field_validator("address", "interior_license", "insurance")(_blank_none)
    _time = field_validator("call_start", "call_end")(_local_time)

    @model_validator(mode="after")
    def _check(self) -> "ProfileIn":
        if self.contact_method in ("kakao", "web") and self.contact_url is None:
            raise ValueError("채널 · 상담 주소를 넣어 주세요.")
        if (self.call_start is None) != (self.call_end is None):
            raise ValueError("통화 가능 시간은 시작과 끝을 함께 골라 주세요.")
        if self.call_start is not None and self.call_start >= self.call_end:
            raise ValueError("통화 가능 시간의 끝을 시작보다 늦게 골라 주세요.")
        if self.visit_fee_refund and self.visit_fee == 0:
            raise ValueError("출장비를 받을 때만 '계약하면 빼 줘요'를 고를 수 있어요.")
        if len({f.key for f in self.faqs}) != len(self.faqs):
            raise ValueError("같은 질문의 답을 두 번 적었어요.")
        if self.portfolio is not None and len({p.id for p in self.portfolio}) != len(
            self.portfolio
        ):
            raise ValueError("같은 사진이 두 번 들어 있어요.")
        return self


class PublicCardOut(BaseModel):
    """고객이 보는 업체 목록 한 줄. 휴대폰 · 채널 주소는 넣지 않는다(실측 요청 뒤 visit이 보여 준다)."""

    vendor_id: uuid.UUID
    biz_name: str | None
    trades: list[str]
    areas: list[AreaOut]
    verified_at: datetime | None = Field(description="믿집이 사업자를 확인(승인)한 때")
    contact_method: str | None
    call_start: time | None
    call_end: time | None
    visit_fee: int | None
    visit_fee_refund: bool
    tax_kind: str


class PublicProfileOut(PublicCardOut):
    """고객이 보는 업체 자세히. '업체가 적음' 칸(실내건축 · 보험)이 비면 '확인 안 됨'으로 보인다."""

    owner_name: str | None
    biz_no: str | None
    opened_on: date | None
    address_area: str | None = Field(description="사업장 주소의 앞 두 마디 (서울 도봉구)")
    interior_license: str | None
    insurance: str | None
    faqs: list[FaqIn]
    links: list[LinkIn]
    portfolio: list[PortfolioOut]
    updated_at: datetime | None


class ProfileOut(PublicProfileOut):
    """업체 본인이 보는 업체 정보 (고치는 화면)"""

    phone: str | None
    address: str | None
    site_url: str | None
    contact_url: str | None
