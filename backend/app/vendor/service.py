"""업체 가입 · 관리자 승인 / 반려 · 업체 정보.

가입은 기본 회원정보 + 사업자등록증만 받는다. 관리자가 등록증을 보고 승인하면 업체 기능을 쓴다.
업체 정보(연락 방식 · 실측 비용 · 자주 묻는 5가지 · 링크 · 사진)는 승인된 업체 본인만 고친다.
고객에게는 public_profile · list_public로 보여 주고, 휴대폰 · 채널 주소는 넣지 않는다.
"""

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Protocol

from fastapi import UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import service as auth_service
from app.auth.models import User
from app.calc import dobae, estimate, mokgong
from app.files import service as files_service
from app.refprice.book import DOBAE_2026H2, MOKGONG_2026H2
from app.shared.clock import now_utc
from app.shared.errors import AppError
from app.shared.ids import uuid7
from app.shared.regions import is_region, region_label
from app.vendor import repository as repo
from app.vendor.models import (
    FAQ_KEYS,
    PORTFOLIO_MAX,
    PRICE_FILES_MAX,
    Vendor,
    VendorPortfolio,
    VendorPriceFile,
    VendorPriceLine,
    VendorPriceSet,
)
from app.vendor.schemas import (
    AreaIn,
    AreaOut,
    EstimateLineOut,
    EstimateOut,
    FaqIn,
    LinkIn,
    PortfolioIn,
    PortfolioMeta,
    PortfolioOut,
    PriceFileIn,
    PriceFileOut,
    PriceLineOut,
    PriceRowOut,
    PriceSetIn,
    PriceSetOut,
    PricesOut,
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


# ---- 가견적 단가: 업체가 근거 파일을 올리고 표준 행 단가를 직접 넣은 뒤 확인한다 ----

EXAMPLE_M2 = Decimal("105.8")  # 고객에게 보이는 모습 예: 32평 아파트(공급면적)
TRADE_NOT_MINE = "맡는 공정의 견적서만 올릴 수 있어요. 공정을 먼저 골라 주세요."
ROW_NAME = {r.key: r.name for rows in estimate.PRICE_ROWS.values() for r in rows}


@dataclass(frozen=True)
class ConfirmedPrices:
    """고객 가견적에 쓰는 확인한 단가 묶음. visit이 실측 요청 때 set_id와 합계를 고정한다."""

    set_id: uuid.UUID
    confirmed_at: datetime
    tax_kind: str
    prices: dict[str, estimate.VendorPrice]
    sources: dict[str, str]  # 단가 키 → 근거 파일 이름 (지운 파일은 없음)

    @property
    def files(self) -> list[str]:
        return sorted(set(self.sources.values()))


class _StdLine(Protocol):
    key: str
    name: str
    qty: Decimal | None
    unit: str
    state: str


def estimate_out(
    trade: str,
    std_lines: Sequence[_StdLine],
    prices: dict[str, estimate.VendorPrice],
    *,
    grade: str | None,
    tax_kind: str,
    sources: dict[str, str],
) -> EstimateOut:
    """가견적 = 업체 단가 × 표준견적서 수량(계산 엔진). std_lines는 표준견적서 줄(저장본 또는 계산 결과)."""
    by_key = {ln.key: ln for ln in std_lines}
    e = estimate.estimate(
        [estimate.StdLine(ln.key, ln.qty, ln.state) for ln in std_lines],
        prices,
        trade=trade,
        grade=grade,
        exempt=tax_kind == "exempt",
    )
    return EstimateOut(
        lines=[
            EstimateLineOut(
                key=x.key,
                price_key=x.price_key,
                name=by_key[x.key].name,
                unit=by_key[x.key].unit,
                qty=x.qty,
                unit_price=x.unit_price,
                included_in=x.included_in,
                included_in_name=ROW_NAME.get(x.included_in) if x.included_in else None,
                amount=x.amount,
                source=sources.get(x.price_key),
            )
            for x in e.lines
        ],
        supply=e.supply,
        vat=e.vat,
        total=e.total,
        missing=list(e.missing),
        left_out=list(e.left_out),
    )


def _example_lines(trade: str) -> list:
    """고객에게 보이는 모습 예: 32평 아파트 · 빈 집 · 도배 기본형(천장 포함) · 목공 몰딩 · 걸레받이"""
    if trade == "dobae":
        inp = dobae.DobaeInput(EXAMPLE_M2, "supply", "basic", True, False)
        return dobae.quote(inp, DOBAE_2026H2).lines
    inp = mokgong.MokgongInput(EXAMPLE_M2, "supply", ("molding", "base"), False)
    return mokgong.quote(inp, MOKGONG_2026H2).lines


def _prices_of(lines: list) -> dict[str, estimate.VendorPrice]:
    return {ln.key: estimate.VendorPrice(ln.unit_price, ln.included_in) for ln in lines}


def _sources_of(lines: list, names: dict[uuid.UUID, str]) -> dict[str, str]:
    return {ln.key: names[ln.source_file_id] for ln in lines if ln.source_file_id in names}


def _file_out(pf: VendorPriceFile, f) -> PriceFileOut:
    return PriceFileOut(
        file_id=pf.file_id,
        trade=pf.trade,
        filename=f.filename,
        content_type=f.content_type,
        size=f.size,
        created_at=pf.created_at,
    )


async def prices_for(s: AsyncSession, vendor: User, trade: str) -> PricesOut:
    """GET /vendor/prices: 단가를 넣는 표준 행 · 근거 파일 · 고치는 중(draft) · 확인한(confirmed) 묶음"""
    sets = await repo.price_sets(s, [vendor.id], trade, ("draft", "confirmed"))
    lines = await repo.price_lines(s, [x.id for x in sets])
    files = await repo.price_files(s, [vendor.id], trade)
    names = {f.id: f.filename for _, f in files}
    v = await s.get(Vendor, vendor.id, populate_existing=True)
    example = _example_lines(trade)
    order = {k: i for i, k in enumerate(estimate.PRICE_KEYS[trade])}

    def out(ps: VendorPriceSet | None) -> PriceSetOut | None:
        if ps is None:
            return None
        rows = sorted(lines[ps.id], key=lambda ln: order.get(ln.key, len(order)))
        return PriceSetOut(
            id=ps.id,
            version=ps.version,
            status=ps.status,
            created_at=ps.created_at,
            confirmed_at=ps.confirmed_at,
            lines=[
                PriceLineOut(
                    key=ln.key,
                    unit_price=ln.unit_price,
                    included_in=ln.included_in,
                    source_file_id=ln.source_file_id,
                )
                for ln in rows
            ],
            example=estimate_out(
                trade,
                example,
                _prices_of(rows),
                grade="basic" if trade == "dobae" else None,
                tax_kind=v.tax_kind,
                sources=_sources_of(rows, names),
            ),
        )

    by = {x.status: x for x in sets}
    return PricesOut(
        trade=trade,
        rows=[PriceRowOut(key=r.key, name=r.name, unit=r.unit) for r in estimate.PRICE_ROWS[trade]],
        files=[_file_out(pf, f) for pf, f in files],
        draft=out(by.get("draft")),
        confirmed=out(by.get("confirmed")),
    )


async def _lock_and_check_trade(s: AsyncSession, vendor: User, trade: str) -> None:
    """같은 업체의 단가 쓰기가 줄 서게 업체 행을 잠그고, 맡는 공정인지 본다."""
    await repo.lock_vendor(s, vendor.id)
    trades, _ = await repo.trades_and_areas(s, [vendor.id])
    if trade not in trades[vendor.id]:
        raise AppError(409, "trade_not_mine", TRADE_NOT_MINE)


async def _commit(s: AsyncSession) -> None:
    try:
        await s.commit()
    except IntegrityError as exc:  # 부분 UQ(draft · confirmed 하나) · 파일 PK가 막은 동시 요청
        await s.rollback()
        raise AppError(409, "stale", STALE) from exc


async def save_prices(s: AsyncSession, vendor: User, trade: str, data: PriceSetIn) -> PricesOut:
    """고치는 중인 묶음(draft)을 통째로 저장한다. 없으면 새 버전으로 만든다. 확인 전에는 고객에게 안 쓴다."""
    await _lock_and_check_trade(s, vendor, trade)
    given = {ln.key: ln for ln in data.lines}
    if any(k not in estimate.PRICE_KEYS[trade] for k in given):
        raise AppError(422, "price_key", "표준 행을 다시 확인해 주세요.")
    if any(
        ln.included_in is not None
        and (ln.included_in not in given or given[ln.included_in].unit_price is None)
        for ln in data.lines
    ):
        raise AppError(422, "included_in", "'다른 줄에 포함'은 단가를 넣은 줄로 골라 주세요.")
    mine = {pf.file_id for pf, _ in await repo.price_files(s, [vendor.id], trade)}
    if any(ln.source_file_id is not None and ln.source_file_id not in mine for ln in data.lines):
        raise AppError(422, "source_file", "근거 파일을 다시 골라 주세요.")
    drafts = await repo.price_sets(s, [vendor.id], trade, ("draft",))
    try:
        if drafts:
            draft = drafts[0]
        else:
            version = await repo.next_price_version(s, vendor.id, trade)
            draft = VendorPriceSet(id=uuid7(), vendor_id=vendor.id, trade=trade, version=version)
            s.add(draft)
            await s.flush()
        await repo.replace_price_lines(
            s,
            draft.id,
            [
                VendorPriceLine(
                    set_id=draft.id,
                    key=ln.key,
                    unit_price=ln.unit_price,
                    included_in=ln.included_in,
                    source_file_id=ln.source_file_id,
                )
                for ln in data.lines
            ],
        )
    except IntegrityError as exc:
        await s.rollback()
        raise AppError(409, "stale", STALE) from exc
    await _commit(s)
    return await prices_for(s, vendor, trade)


async def confirm_prices(s: AsyncSession, vendor: User, trade: str) -> PricesOut:
    """draft → confirmed, 이전 confirmed → retired. 이미 실측을 요청한 고객의 가견적은 그때 묶음으로 남는다."""
    await _lock_and_check_trade(s, vendor, trade)
    drafts = await repo.price_sets(s, [vendor.id], trade, ("draft",))
    if not drafts:
        raise AppError(409, "invalid_state", "확인할 단가가 없어요. 단가를 저장한 뒤 눌러 주세요.")
    draft = drafts[0]
    if not any(
        ln.unit_price is not None for ln in (await repo.price_lines(s, [draft.id]))[draft.id]
    ):
        raise AppError(422, "no_price", "단가를 하나 이상 넣어 주세요.")
    if not await repo.price_files(s, [vendor.id], trade):
        raise AppError(409, "no_file", "가견적에 쓸 견적서를 먼저 올려 주세요.")
    await repo.set_price_status(s, vendor.id, trade, to="retired", allowed_from="confirmed")
    changed = await repo.set_price_status(
        s, vendor.id, trade, to="confirmed", allowed_from="draft", at=now_utc(), set_id=draft.id
    )
    if changed != 1:
        raise AppError(409, "invalid_state", "이미 확인한 단가예요.")
    await _commit(s)
    return await prices_for(s, vendor, trade)


async def add_price_file(s: AsyncSession, vendor: User, data: PriceFileIn) -> PriceFileOut:
    f = (await files_service.owned(s, vendor.id, [data.file_id])).get(data.file_id)
    if f is None or f.kind != "price_doc":
        raise AppError(422, "file", "올린 견적서 파일을 다시 확인해 주세요.")
    await _lock_and_check_trade(s, vendor, data.trade)
    if len(await repo.price_files(s, [vendor.id], data.trade)) >= PRICE_FILES_MAX:
        raise AppError(
            409, "too_many_files", f"견적서는 공정마다 {PRICE_FILES_MAX}개까지 올릴 수 있어요."
        )
    row = VendorPriceFile(file_id=f.id, vendor_id=vendor.id, trade=data.trade, created_at=now_utc())
    s.add(row)
    try:
        await s.commit()
    except IntegrityError as exc:  # 같은 파일을 동시에 두 번 붙이면 파일 PK가 막는다
        await s.rollback()
        raise AppError(409, "file_used", "이미 붙인 파일이에요.") from exc
    return _file_out(row, f)


async def remove_price_file(s: AsyncSession, vendor: User, file_id: uuid.UUID) -> None:
    """근거 파일 빼기. 파일도 지우고, 그 파일을 근거로 적은 단가는 남기되 출처만 비운다(FK SET NULL)."""
    await repo.lock_vendor(s, vendor.id)
    row = await s.get(VendorPriceFile, file_id)
    if row is None or row.vendor_id != vendor.id:
        raise AppError(404, "not_found", "파일을 찾을 수 없어요.")
    await s.delete(row)
    await s.flush()
    key = await files_service.drop(s, vendor.id, file_id)
    await s.commit()
    if key:
        await files_service.remove_stored(key)


async def confirmed_prices(
    s: AsyncSession, vendor_ids: list[uuid.UUID], trade: str
) -> dict[uuid.UUID, ConfirmedPrices]:
    """고객 가견적용: 승인된 업체의 확인한 단가 묶음. 없는 업체는 빠진다('가견적 없음')."""
    if not vendor_ids:
        return {}
    sets = await repo.price_sets(s, vendor_ids, trade, ("confirmed",))
    vendors = await repo.vendors_by_id(s, [x.vendor_id for x in sets])
    sets = [x for x in sets if vendors[x.vendor_id].status == "approved"]
    lines = await repo.price_lines(s, [x.id for x in sets])
    names = {f.id: f.filename for _, f in await repo.price_files(s, vendor_ids)}
    return {
        x.vendor_id: ConfirmedPrices(
            set_id=x.id,
            confirmed_at=x.confirmed_at,
            tax_kind=vendors[x.vendor_id].tax_kind,
            prices=_prices_of(lines[x.id]),
            sources=_sources_of(lines[x.id], names),
        )
        for x in sets
    }
