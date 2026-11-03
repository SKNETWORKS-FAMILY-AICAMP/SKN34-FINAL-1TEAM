import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.vendor.schemas import AreaOut, EstimateOut, PublicProfileOut

Direction = Literal["less", "more", "same"]  # 표준견적서보다 적어요 · 많아요 · 같아요


class VendorRowOut(BaseModel):
    """업체 고르기 한 줄. 연락처(휴대폰 · 채널 주소)는 실측 요청 뒤에만 보인다."""

    vendor_id: uuid.UUID
    biz_name: str | None
    trades: list[str]
    areas: list[AreaOut] = Field(description="활동 지역 (화면이 처음 2곳 + 외 N곳으로 줄인다)")
    verified_on: date | None = Field(description="사업자 확인(승인)한 날, 서울 날짜")
    visit_fee: int | None = Field(description="실측 출장비(원, 부가세 포함). 0 = 무료")
    visit_fee_refund: bool
    contact_method: str | None
    call_start: time | None
    call_end: time | None
    estimate_total: int | None = Field(
        description="가견적 합계(고객이 낼 금액). 없으면 '가견적 없음'"
    )
    vs_standard: Decimal | None = Field(description="표준견적서 대비 % (음수면 적어요)")
    vs_direction: Direction | None


class VendorListOut(BaseModel):
    """이 집 업체 고르기: 그 공정을 하고 이 집 지역(구)에서 일하는 승인된 업체"""

    project_id: uuid.UUID
    trade: str
    region: str = Field(description="이 집 지역 (서울 노원구)")
    area_name: str = Field(description="이 집 시 · 군 · 구 (노원구)")
    standard_total: int = Field(description="그 공정 표준견적서 합계 (부가세 포함)")
    vendors: list[VendorRowOut]


class EstimateDetailOut(EstimateOut):
    """가견적 '어떻게 계산했나요': 업체가 확인한 단가 × 이 집 표준견적서 수량 (믿집 계산)"""

    set_id: uuid.UUID = Field(description="쓴 단가 묶음 (실측 요청 때 고정한다)")
    confirmed_on: date = Field(description="업체가 단가를 확인한 날, 서울 날짜")
    confirmed_at: datetime
    exempt: bool = Field(description="면세 업체면 부가세 0")
    vs_standard: Decimal | None
    vs_direction: Direction | None


class VendorDetailOut(BaseModel):
    """이 집 기준 업체 자세히"""

    project_id: uuid.UUID
    trade: str
    standard_total: int
    profile: PublicProfileOut
    verified_on: date | None
    years_open: int | None = Field(description="업력: 개업일부터 오늘까지 꽉 찬 햇수")
    in_area: bool = Field(description="이 집이 출장 지역 안인지")
    area_name: str = Field(description="이 집 시 · 군 · 구 (노원구)")
    dong: str | None = Field(description="이 집 읍 · 면 · 동 (프로젝트에 있을 때만)")
    estimate: EstimateDetailOut | None = Field(description="가견적 없음이면 비어 있다")
    not_included: list[str] = Field(
        description="가견적에 빠진 줄 이름 (실측 후 줄 · 업체 단가 칸이 없는 줄)"
    )
