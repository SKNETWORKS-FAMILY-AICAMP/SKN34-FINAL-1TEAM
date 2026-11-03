"""이 집 기준 업체 고르기 · 업체 자세히 · 실측 요청.

업체는 승인됐고 그 공정을 하고 이 집 지역(구)에서 일하는 곳만 보인다.
가견적은 업체가 확인한 단가 × 이 집 표준견적서 수량(계산 엔진)이다. 확인한 단가가 없거나 이 집에
필요한 줄의 단가가 하나라도 빠졌으면 '가견적 없음'이다. 요청할 때 그 값을 사본으로 고정한다.
실측 요청은 연결만 한다: 업체 연락처를 보여 주고, 고객 연락처는 고객이 고른 업체에만 간다.
연락처를 보여도 되는지는 이 파일의 contact_visible 한 곳에서 정한다.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.models import User
from app.calc.quote_diff import percent_vs
from app.calc.schedule import years_since
from app.files import service as files_service
from app.notify import service as notify_service
from app.project import service as project_service
from app.project.schemas import ProjectOut
from app.quote import service as quote_service
from app.quote.models import StandardQuote, StandardQuoteLine
from app.shared.clock import SEOUL, now_utc
from app.shared.errors import AppError
from app.shared.ids import uuid7
from app.vendor import service as vendor_service
from app.vendor.schemas import ContactOut, EstimateOut
from app.visit import repository as repo
from app.visit.models import LIVE, SLOTS, Visit, VisitEvent
from app.visit.schemas import (
    Direction,
    EstimateDetailOut,
    FailedOut,
    RequestTextOut,
    VendorDetailOut,
    VendorListOut,
    VendorRowOut,
    VisitBrief,
    VisitCreateIn,
    VisitCreateOut,
    VisitListOut,
    VisitOut,
)

NOT_LISTED = "이 집에서 고를 수 있는 업체가 아니에요."
# 연락처 보내기 동의 문구 버전. 문구를 바꾸면 올리고, 화면은 요청할 때 이 값을 그대로 보낸다
CONSENT_VER = "2026-11-01"
TRADE_LABEL = {"dobae": "도배", "mokgong": "목공"}
GRADE_LABEL = {"economy": "저가형", "basic": "기본형"}
MOK_LABEL = {
    "molding": "천장 몰딩",
    "base": "걸레받이",
    "door": "방문 교체",
    "flatten": "천장 평탄화",
}
BASIS_SHORT = {"supply": "공급", "exclusive": "전용"}
WEEKDAY = "월화수목금토일"


def contact_visible(status: str, contact_until: date | None, today: date) -> bool:
    """연락처를 서로 보여도 되는지: 요청함 · 견적서 옴이면 보이고, 확정이면 contact_until까지
    (없으면 보임), 취소 · 못 받음 · 닫힘이면 가린다. 고객 쪽 · 업체 쪽이 같이 쓴다."""
    if status in ("requested", "quoted"):
        return True
    if status == "chosen":
        return contact_until is None or today <= contact_until
    return False


def customer_contact_visible(v: Visit, today: date) -> bool:
    """업체에게 고객 연락처(이름 · 휴대폰 · 동 · 호수)를 보여도 되는지: 고객이 보내기로 고른 요청만."""
    return v.share_contact and contact_visible(v.status, v.contact_until, today)


def _brief(v: Visit | None) -> VisitBrief | None:
    return VisitBrief(id=v.id, status=v.status, decline_code=v.decline_code) if v else None


def _day(at: datetime | None) -> date | None:
    return at.astimezone(SEOUL).date() if at else None


async def _standard(
    s: AsyncSession, user: User, project_id: uuid.UUID, trade: str
) -> tuple[ProjectOut, StandardQuote, list[StandardQuoteLine]]:
    """내 프로젝트(남의 것은 404)와 그 공정의 최신 표준견적서 · 줄"""
    p = await project_service.get_for(s, user, project_id)
    got = await quote_service.standard_trade(s, p, trade)
    if got is None:
        raise AppError(422, "trade", "이 집에서 고른 공정이 아니에요.")
    return p, *got


def _estimate(
    trade: str,
    std: StandardQuote,
    lines: list[StandardQuoteLine],
    cp: vendor_service.ConfirmedPrices | None,
) -> EstimateOut | None:
    """확인한 단가가 없거나 이 집에 필요한 줄의 단가가 빠졌으면 가견적 없음(None)"""
    if cp is None:
        return None
    e = vendor_service.estimate_out(
        trade, lines, cp.prices, grade=std.grade, tax_kind=cp.tax_kind, sources=cp.sources
    )
    return None if e.missing or not e.lines else e


def _vs(total: int, standard: int) -> tuple[Decimal | None, Direction | None]:
    if standard <= 0:
        return None, None
    pct = percent_vs(total, standard)
    return pct, "less" if pct < 0 else "more" if pct > 0 else "same"


async def vendors_for(
    s: AsyncSession, user: User, project_id: uuid.UUID, trade: str
) -> VendorListOut:
    """업체 고르기: 가견적 낮은 순, 가견적 없음은 맨 뒤, 같으면 상호 순."""
    p, std, lines = await _standard(s, user, project_id, trade)
    cards = await vendor_service.list_public(s, trade, p.sido, p.sgg)
    ids = [c.vendor_id for c in cards]
    prices = await vendor_service.confirmed_prices(s, ids, trade)
    visits = await repo.latest_by_vendor(s, p.id, trade, ids) if ids else {}
    rows = []
    for c in cards:
        e = _estimate(trade, std, lines, prices.get(c.vendor_id))
        pct, way = _vs(e.total, std.total) if e else (None, None)
        rows.append(
            VendorRowOut(
                vendor_id=c.vendor_id,
                biz_name=c.biz_name,
                trades=c.trades,
                areas=c.areas,
                verified_on=_day(c.verified_at),
                visit_fee=c.visit_fee,
                visit_fee_refund=c.visit_fee_refund,
                contact_method=c.contact_method,
                call_start=c.call_start,
                call_end=c.call_end,
                estimate_total=e.total if e else None,
                vs_standard=pct,
                vs_direction=way,
                visit=_brief(visits.get(c.vendor_id)),
            )
        )
    rows.sort(key=lambda r: (r.estimate_total is None, r.estimate_total or 0, r.biz_name or ""))
    return VendorListOut(
        project_id=p.id,
        trade=trade,
        region=p.region,
        area_name=p.sgg,
        standard_total=std.total,
        slots_left=SLOTS - len(await repo.live(s, p.id, trade)),
        consent_ver=CONSENT_VER,
        vendors=rows,
    )


async def vendor_detail(
    s: AsyncSession,
    user: User,
    project_id: uuid.UUID,
    vendor_id: uuid.UUID,
    trade: str,
    today: date,
) -> VendorDetailOut:
    p, std, lines = await _standard(s, user, project_id, trade)
    if not await vendor_service.listed(s, vendor_id, trade, p.sido, p.sgg):
        raise AppError(404, "not_found", NOT_LISTED)
    profile = await vendor_service.public_profile(s, vendor_id)
    cp = (await vendor_service.confirmed_prices(s, [vendor_id], trade)).get(vendor_id)
    e = _estimate(trade, std, lines, cp)
    estimate = None
    if e is not None and cp is not None:
        pct, way = _vs(e.total, std.total)
        estimate = EstimateDetailOut(
            **e.model_dump(),
            set_id=cp.set_id,
            confirmed_on=_day(cp.confirmed_at),
            confirmed_at=cp.confirmed_at,
            exempt=cp.tax_kind == "exempt",
            vs_standard=pct,
            vs_direction=way,
        )
    left = set(e.left_out) if e else set()
    not_included = [ln.name for ln in lines if ln.state == "after_visit" or ln.key in left]
    return VendorDetailOut(
        project_id=p.id,
        trade=trade,
        standard_total=std.total,
        profile=profile,
        verified_on=_day(profile.verified_at),
        years_open=years_since(profile.opened_on, today) if profile.opened_on else None,
        in_area=True,  # 목록 조건(이 집 구가 활동 지역에 있음)을 지난 업체만 여기까지 온다
        area_name=p.sgg,
        dong=None,  # 프로젝트에 읍 · 면 · 동 칸이 아직 없다
        estimate=estimate,
        visit=_brief((await repo.latest_by_vendor(s, p.id, trade, [vendor_id])).get(vendor_id)),
        not_included=not_included,
    )


async def vendor_photo(
    s: AsyncSession, user: User, project_id: uuid.UUID, vendor_id: uuid.UUID, file_id: uuid.UUID
) -> Response:
    """이 집에서 고를 수 있는 업체의 시공 사진만 내준다. 근거 파일 · 등록증 · 남의 사진은 404."""
    p = await project_service.get_for(s, user, project_id)
    trades = [t for t in ("dobae", "mokgong") if getattr(p.trades, t) is not None]
    if not any([await vendor_service.listed(s, vendor_id, t, p.sido, p.sgg) for t in trades]):
        raise AppError(404, "not_found", NOT_LISTED)
    f = await vendor_service.portfolio_file(s, vendor_id, file_id)
    if f is None:
        raise AppError(404, "not_found", "사진을 찾을 수 없어요.")
    return await files_service.respond(f)


def _md(d: date) -> str:
    return f"{d.month:02d}.{d.day:02d}({WEEKDAY[d.weekday()]})"


def _number(x: Decimal) -> str:
    """32.0 → 32, 30.8 → 30.8"""
    return format(x.normalize(), "f")


def _when(p: ProjectOut) -> str:
    w = p.when
    if w.kind == "date" and w.start:
        return f"{_md(w.start)} ~ {_md(w.end)}" if w.end else f"{_md(w.start)}부터"
    if w.kind == "movein" and w.start:
        return f"이사 {_md(w.start)} 전까지"
    return "1~2달 안" if w.kind == "months" else "아직 몰라요"


def request_text(p: ProjectOut, std: StandardQuote) -> str:
    """업체에 연락할 때 보낼 문구. 평 · 면적은 표준견적서(계산 엔진)에 저장한 값을 쓴다.
    고객 이름 · 연락처 · 동 · 호수는 넣지 않는다. 동네(법정동) 칸이 없어 구까지만 쓴다."""
    if std.trade == "dobae":
        work = f"도배 {GRADE_LABEL.get(std.grade or '', '')}".strip()
    else:
        items = p.trades.mokgong.items if p.trades.mokgong else []
        work = " ".join(["목공", " · ".join(MOK_LABEL[i] for i in items if i in MOK_LABEL)]).strip()
    home = (
        f"{p.sgg} 아파트 {_number(std.pyeong)}평"
        f"({BASIS_SHORT.get(std.area_basis, '')} {_number(std.area_m2)}㎡)"
    )
    occupied = "짐이 있는 집이에요." if p.occupied else "빈 집이에요."
    return (
        f"[믿집] 실측 요청이에요. 요청 번호 {p.code} · {work} · {home}"
        f" · 희망 시기 {_when(p)} · {occupied}"
    )


def _visit_out(v: Visit, contacts: dict[uuid.UUID, ContactOut], today: date) -> VisitOut:
    visible = contact_visible(v.status, v.contact_until, today)
    return VisitOut(
        id=v.id,
        trade=v.trade,
        vendor_id=v.vendor_id,
        vendor_name=v.vendor_name,
        status=v.status,
        slot=v.slot,
        created_at=v.created_at,
        share_contact=v.share_contact,
        message=v.message,
        decline_code=v.decline_code,
        estimate_total=v.estimate_total,
        contact_visible=visible,
        contact=contacts.get(v.vendor_id) if visible and v.vendor_id else None,
    )


async def request_visits(
    s: AsyncSession, user: User, project_id: uuid.UUID, data: VisitCreateIn, today: date
) -> VisitCreateOut:
    """실측 요청 보내기. 프로젝트 행을 잠가 같은 집의 요청이 줄 서고, 공정마다 3곳까지만 받는다.
    지금 고를 수 없는 업체 · 이미 요청한 업체는 만들지 않고 failed로 돌려준다."""
    p, std, lines = await _standard(s, user, project_id, data.trade)
    share = set(data.share_contact_ids)
    if share:
        if not user.phone:
            raise AppError(422, "phone_required", "업체에 보내려면 휴대폰 번호가 필요해요.")
        if not (data.apt_name and data.dong and data.ho):
            raise AppError(422, "address_required", "아파트 이름 · 동 · 호수를 적어 주세요.")
        if data.consent_ver != CONSENT_VER:
            raise AppError(409, "consent_changed", "동의 문구가 바뀌었어요. 다시 확인해 주세요.")
    cards = {c.vendor_id: c for c in await vendor_service.list_public(s, data.trade, p.sido, p.sgg)}
    failed = [FailedOut(vendor_id=i, code="unavailable") for i in data.vendor_ids if i not in cards]
    ok = [i for i in data.vendor_ids if i in cards]
    prices = await vendor_service.confirmed_prices(s, ok, data.trade) if ok else {}

    unit = (data.apt_name, data.dong, data.ho) if share else None
    await project_service.lock_for_visit(s, p.id, unit)  # 첫 요청이면 집 정보 고치기를 막는다
    live = await repo.live(s, p.id, data.trade)
    taken = {v.vendor_id for v in live}
    failed += [FailedOut(vendor_id=i, code="already") for i in ok if i in taken]
    new = [i for i in ok if i not in taken]
    if not new:
        if all(f.code == "already" for f in failed):
            raise AppError(409, "already", "이미 실측을 요청한 업체예요.")
        raise AppError(409, "unavailable", "지금 믿집에서 일할 수 없는 업체예요.")
    free = [n for n in range(1, SLOTS + 1) if n not in {v.slot for v in live}]
    if len(new) > len(free):
        raise AppError(409, "slots_full", "3곳까지 담을 수 있어요. 하나를 빼면 담을 수 있어요.")

    now = now_utc()
    rows: list[Visit] = []
    for vid, slot in zip(new, free, strict=False):
        cp = prices.get(vid)
        e = _estimate(data.trade, std, lines, cp)
        shared = vid in share
        rows.append(
            Visit(
                id=uuid7(),
                project_id=p.id,
                trade=data.trade,
                vendor_id=vid,
                vendor_name=cards[vid].biz_name or "업체",
                slot=slot,
                status="requested",
                share_contact=shared,
                consent_ver=CONSENT_VER if shared else None,
                consent_at=now if shared else None,
                message=data.message,
                estimate_set_id=cp.set_id if e and cp else None,
                estimate_total=e.total if e else None,
                created_at=now,
            )
        )
    try:
        s.add_all(rows)
        await s.flush()
        s.add_all(
            [
                VisitEvent(
                    visit_id=v.id, from_status=None, to_status="requested", by_id=user.id, at=now
                )
                for v in rows
            ]
        )
        for v in rows:  # 업체 알림: 고객 이름 · 연락처는 넣지 않는다
            await notify_service.add(
                s,
                user_id=v.vendor_id,
                kind="visit_requested",
                body=f"새 실측 요청이 왔어요 · {TRADE_LABEL[data.trade]}",
                link=f"/vendor/requests/{v.id}",
                dedup_key=f"visit:{v.id}",
            )
        await s.commit()
    except IntegrityError as exc:  # 칸 · 업체 부분 UQ: 다른 곳에서 먼저 같은 칸을 채웠다
        await s.rollback()
        raise AppError(409, "stale", "다른 곳에서 먼저 요청했어요. 다시 열어 주세요.") from exc
    contacts = await vendor_service.contacts(s, new)
    return VisitCreateOut(created=[_visit_out(v, contacts, today) for v in rows], failed=failed)


async def visits_for(
    s: AsyncSession, user: User, project_id: uuid.UUID, trade: str | None, today: date
) -> VisitListOut:
    """이 집 실측 요청 목록 · 공정마다 보낼 문구 · 남은 칸"""
    p = await project_service.get_for(s, user, project_id)
    trades = [t for t in ("mokgong", "dobae") if getattr(p.trades, t) is not None]
    if trade is not None and trade not in trades:
        raise AppError(422, "trade", "이 집에서 고른 공정이 아니에요.")
    every = await repo.of_project(s, p.id, None)
    rows = [v for v in every if trade is None or v.trade == trade]
    contacts = await vendor_service.contacts(s, [v.vendor_id for v in rows if v.vendor_id])
    texts = []
    for t in [trade] if trade else trades:
        got = await quote_service.standard_trade(s, p, t)
        if got is not None:
            texts.append(RequestTextOut(trade=t, text=request_text(p, got[0])))
    return VisitListOut(
        project_id=p.id,
        consent_ver=CONSENT_VER,
        slots_left={
            t: SLOTS - sum(1 for v in every if v.trade == t and v.status in LIVE) for t in trades
        },
        request_texts=texts,
        visits=[_visit_out(v, contacts, today) for v in rows],
    )
