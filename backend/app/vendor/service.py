"""업체 가입 · 관리자 승인 / 반려 · 업체 정보.

가입은 기본 회원정보 + 사업자등록증만 받는다. 관리자가 등록증을 보고 승인하면 업체 기능을 쓴다.
업체 정보(연락 방식 · 실측 비용 · 자주 묻는 5가지 · 링크 · 사진)는 승인된 업체 본인만 고친다.
고객에게는 public_profile · list_public로 보여 주고, 휴대폰 · 채널 주소는 넣지 않는다.
"""

import uuid
from datetime import date

from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service as auth_service
from app.auth.models import User
from app.files import service as files_service
from app.shared.clock import now_utc
from app.shared.errors import AppError
from app.shared.regions import is_region, region_label
from app.vendor import repository as repo
from app.vendor.models import FAQ_KEYS, PORTFOLIO_MAX, Vendor, VendorPortfolio
from app.vendor.schemas import (
    AreaIn,
    AreaOut,
    FaqIn,
    LinkIn,
    PortfolioIn,
    PortfolioMeta,
    PortfolioOut,
    ProfileIn,
    ProfileOut,
    PublicCardOut,
    PublicProfileOut,
    VendorAdminOut,
    VendorSignupIn,
)

STALE = "다른 곳에서 먼저 바꿨어요. 다시 열어 주세요."


async def signup(
    s: AsyncSession, data: VendorSignupIn, license_file: UploadFile, ip: str
) -> tuple[User, str]:
    await auth_service.signup_limit(ip)
    lic = await files_service.prepare(
        "license", license_file
    )  # 검사 · 위치 정보 지우기는 DB 밖에서
    user = await auth_service.create_user(
        s, email=data.email, name=data.name, password=data.password, role="vendor"
    )
    row = await files_service.persist(s, user.id, lic)
    try:
        s.add(Vendor(user_id=user.id, license_file_id=row.id))
        await s.flush()
        token = await auth_service.start_session(s, user)
        await s.commit()
    except Exception:
        files_service.discard(row)  # 가입이 안 됐으면 사업자등록증도 남기지 않는다
        raise
    return user, token


async def _out(s: AsyncSession, pairs: list[tuple[Vendor, User]]) -> list[VendorAdminOut]:
    trades, areas = await repo.trades_and_areas(s, [v.user_id for v, _ in pairs])
    return [
        VendorAdminOut(
            user_id=v.user_id,
            name=u.name,
            email=u.email,
            biz_name=v.biz_name,
            owner_name=v.owner_name,
            biz_no=v.biz_no,
            phone=v.phone,
            address=v.address,
            site_url=v.site_url,
            license_file_id=v.license_file_id,
            trades=sorted(trades[v.user_id]),
            areas=[AreaIn(sido=a, sgg=b) for a, b in areas[v.user_id]],
            intro=v.intro,
            status=v.status,
            reject_reason=v.reject_reason,
            created_at=v.created_at,
            reviewed_at=v.reviewed_at,
        )
        for v, u in pairs
    ]


async def list_for_admin(s: AsyncSession, status: str | None) -> list[VendorAdminOut]:
    return await _out(s, await repo.list_with_users(s, status))


async def get_for_admin(s: AsyncSession, vendor_id: uuid.UUID) -> VendorAdminOut:
    pair = await repo.get_with_user(s, vendor_id)
    if pair is None:
        raise AppError(404, "not_found", "업체를 찾을 수 없어요.")
    return (await _out(s, [pair]))[0]


async def approve(s: AsyncSession, admin: User, vendor_id: uuid.UUID) -> VendorAdminOut:
    ok = await repo.set_status(
        s,
        vendor_id,
        to="approved",
        allowed_from=("pending", "rejected"),
        reason=None,
        by=admin.id,
        at=now_utc(),
    )
    if not ok:
        await get_for_admin(s, vendor_id)  # 없으면 404
        raise AppError(409, "invalid_state", "이미 승인한 업체예요.")
    await s.commit()
    return await get_for_admin(s, vendor_id)


async def reject(s: AsyncSession, admin: User, vendor_id: uuid.UUID, reason: str) -> VendorAdminOut:
    ok = await repo.set_status(
        s,
        vendor_id,
        to="rejected",
        allowed_from=("pending", "approved"),
        reason=reason.strip(),
        by=admin.id,
        at=now_utc(),
    )
    if not ok:
        await get_for_admin(s, vendor_id)
        raise AppError(409, "invalid_state", "이미 반려한 업체예요.")
    await s.commit()
    return await get_for_admin(s, vendor_id)


def _card(v: Vendor, trades: list[str], areas: list[tuple[str, str]]) -> dict:
    return {
        "vendor_id": v.user_id,
        "biz_name": v.biz_name,
        "trades": sorted(trades),
        "areas": [AreaOut(sido=a, sgg=b, label=region_label(a, b)) for a, b in sorted(areas)],
        "verified_at": v.reviewed_at,
        "contact_method": v.contact_method,
        "call_start": v.call_start,
        "call_end": v.call_end,
        "visit_fee": v.visit_fee,
        "visit_fee_refund": v.visit_fee_refund,
        "tax_kind": v.tax_kind,
    }


def _photo(p: VendorPortfolio) -> PortfolioOut:
    return PortfolioOut(
        id=p.id,
        file_id=p.file_id,
        title=p.title,
        space=p.space,
        pyeong=p.pyeong,
        taken_on=p.taken_on,
    )


async def _detail(s: AsyncSession, v: Vendor) -> dict:
    trades, areas = await repo.trades_and_areas(s, [v.user_id])
    faqs, links = await repo.faqs_links(s, v.user_id)
    photos = await repo.portfolio_of(s, v.user_id)
    return dict(
        **_card(v, trades[v.user_id], areas[v.user_id]),
        owner_name=v.owner_name,
        biz_no=v.biz_no,
        opened_on=v.opened_on,
        address_area=" ".join(v.address.split()[:2]) if v.address else None,
        interior_license=v.interior_license,
        insurance=v.insurance,
        faqs=[FaqIn(key=k, answer=faqs[k]) for k in FAQ_KEYS if k in faqs],
        links=[LinkIn(label=link.label, url=link.url) for link in links],
        portfolio=[_photo(p) for p in photos],
        updated_at=v.updated_at,
    )


async def get_profile(s: AsyncSession, vendor: User) -> ProfileOut:
    v = await s.get(Vendor, vendor.id, populate_existing=True)
    if v is None:
        raise AppError(404, "not_found", "업체를 찾을 수 없어요.")
    return ProfileOut(
        **await _detail(s, v),
        phone=v.phone,
        address=v.address,
        site_url=v.site_url,
        contact_url=v.contact_url,
    )


def _check_taken_on(items: list[PortfolioMeta], today: date) -> None:
    if any(p.taken_on is not None and p.taken_on > today for p in items):
        raise AppError(422, "taken_on", "시공한 때는 오늘까지만 고를 수 있어요.")


async def save_profile(s: AsyncSession, vendor: User, data: ProfileIn, today: date) -> ProfileOut:
    """통째로 저장. 화면이 읽은 updated_at과 지금 값이 다르면 409(다른 곳에서 먼저 저장)."""
    if not all(is_region(a.sido, a.sgg) for a in data.areas):
        raise AppError(422, "region", "활동 지역을 다시 골라 주세요.")
    if data.portfolio is not None:
        _check_taken_on(data.portfolio, today)
    url_needed = data.contact_method in ("kakao", "web")
    values = {
        "phone": data.phone,
        "address": data.address,
        "site_url": data.site_url,
        "contact_method": data.contact_method,
        "contact_url": data.contact_url if url_needed else None,  # 전화 · 문자는 휴대폰으로 연락
        "call_start": data.call_start,
        "call_end": data.call_end,
        "visit_fee": data.visit_fee,
        "visit_fee_refund": data.visit_fee_refund,
        "tax_kind": data.tax_kind,
        "interior_license": data.interior_license,
        "insurance": data.insurance,
        "updated_at": now_utc(),
    }
    if not await repo.save_if_unchanged(s, vendor.id, data.updated_at, values):
        raise AppError(409, "stale", STALE)
    await repo.replace_parts(
        s,
        vendor.id,
        trades=sorted(set(data.trades)),
        areas=list(dict.fromkeys((a.sido, a.sgg) for a in data.areas)),
        faqs=[(f.key, f.answer) for f in data.faqs if f.answer],
        links=[(link.label, link.url) for link in data.links],
    )
    if data.portfolio is not None:
        rows = {p.id: p for p in await repo.portfolio_of(s, vendor.id)}
        if set(rows) != {e.id for e in data.portfolio}:
            raise AppError(409, "stale", STALE)
        for i, e in enumerate(data.portfolio):
            row = rows[e.id]
            row.title, row.space, row.pyeong, row.taken_on = e.title, e.space, e.pyeong, e.taken_on
            row.sort = i
    await s.commit()
    return await get_profile(s, vendor)


async def add_portfolio(
    s: AsyncSession, vendor: User, data: PortfolioIn, today: date
) -> PortfolioOut:
    _check_taken_on([data], today)
    f = (await files_service.owned(s, vendor.id, [data.file_id])).get(data.file_id)
    if f is None or f.kind != "portfolio":
        raise AppError(422, "file", "올린 시공 사진을 다시 확인해 주세요.")
    await repo.lock_vendor(s, vendor.id)
    count, sort = await repo.portfolio_count_and_next(s, vendor.id)
    if count >= PORTFOLIO_MAX:
        raise AppError(409, "portfolio_full", f"시공 사진은 {PORTFOLIO_MAX}장까지 올릴 수 있어요.")
    row = VendorPortfolio(
        vendor_id=vendor.id,
        file_id=data.file_id,
        title=data.title,
        space=data.space,
        pyeong=data.pyeong,
        taken_on=data.taken_on,
        sort=sort,
    )
    s.add(row)
    try:
        await s.commit()
    except IntegrityError as exc:  # 같은 사진을 동시에 두 번 더하면 file_id UNIQUE가 막는다
        await s.rollback()
        raise AppError(409, "file_used", "이미 더한 사진이에요.") from exc
    return _photo(row)


async def remove_portfolio(s: AsyncSession, vendor: User, item_id: uuid.UUID) -> None:
    """사진을 빼면 고객 화면에서도 사라진다. 파일 행 · 저장소의 내용도 지운다."""
    row = await s.get(VendorPortfolio, item_id)
    if row is None or row.vendor_id != vendor.id:
        raise AppError(404, "not_found", "사진을 찾을 수 없어요.")
    await s.delete(row)
    await s.flush()
    key = await files_service.drop(s, vendor.id, row.file_id)
    await s.commit()
    if key:
        await files_service.remove_stored(key)


async def public_profile(s: AsyncSession, vendor_id: uuid.UUID) -> PublicProfileOut:
    """고객이 보는 업체 자세히. 승인된 업체만, 휴대폰 · 채널 주소 · 상세 주소는 넣지 않는다."""
    v = await s.get(Vendor, vendor_id)
    if v is None or v.status != "approved":
        raise AppError(404, "not_found", "업체를 찾을 수 없어요.")
    return PublicProfileOut(**await _detail(s, v))


async def list_public(
    s: AsyncSession, trade: str | None, sido: str | None, sgg: str | None
) -> list[PublicCardOut]:
    """그 공정 · 지역에서 일하는 승인된 업체 목록 (연락 방식을 정한 업체만)."""
    ids = await repo.approved_ids(s, trade, sido, sgg)
    if not ids:
        return []
    rows = await repo.vendors_by_id(s, ids)
    trades, areas = await repo.trades_and_areas(s, ids)
    return [PublicCardOut(**_card(rows[i], trades[i], areas[i])) for i in ids]
