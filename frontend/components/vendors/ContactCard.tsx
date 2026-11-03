// 실측 요청한 업체 카드 (시안 WVisit 카드 · WVendorDetail '요청한 뒤'). 연락처는 API가 공개 규칙을 지난 때만 준다.
// 연락 방식 버튼은 업체가 정한 하나만: 전화 걸기 · 문자 보내기 · 카카오톡 채널 열기 · 홈페이지 상담 열기.
import Link from "next/link";
import type { ReactNode } from "react";
import { phoneText } from "@/lib/labels";
import { CancelVisit } from "./CancelVisit";
import { CopyButton } from "./CopyButton";
import { Check, Lock, VisitState, WayIcon } from "./icons";
import { contactLine, declineText, iGa, isLive, telDigits, webUrl, when, type VTrade, type Visit } from "./text";
import c from "./ContactCard.module.css";

const OPEN_LABEL: Record<string, string> = { phone: "전화 걸기", sms: "문자 보내기", kakao: "카카오톡 채널 열기", web: "홈페이지 상담 열기" };
const OPEN_HINT: Record<string, string> = { kakao: "카카오톡 채널이 새 창으로 열려요", web: "홈페이지 상담 창이 새 창으로 열려요" };

function Way({ v }: { v: Visit }) {
  const ct = v.contact;
  if (!ct) return null;
  const m = ct.contact_method ?? "";
  const label = OPEN_LABEL[m];
  const tel = telDigits(ct.phone);
  const url = webUrl(ct.contact_url);
  const hintId = `way-${v.id}`;
  let btn: ReactNode = null;
  if ((m === "phone" || m === "sms") && tel) {
    btn = <a className="mj-btn mj-btn-pri" href={`${m === "phone" ? "tel" : "sms"}:${tel}`} aria-label={`${v.vendor_name}에 ${label}`}><WayIcon method={m} />{label}</a>;
  } else if ((m === "kakao" || m === "web") && url) {
    btn = <a className="mj-btn mj-btn-pri" href={url} target="_blank" rel="noopener noreferrer" aria-describedby={hintId}><WayIcon method={m} />{label}</a>;
  }
  return (
    <>
      <div className={c.acts}>
        {btn}
        {ct.phone ? <CopyButton text={ct.phone} label="번호 복사" aria={`${v.vendor_name} 번호 복사`} /> : null}
      </div>
      {btn && url && (m === "kakao" || m === "web") ? <p id={hintId} className={`mj-small ${c.url}`}>{OPEN_HINT[m]} · {url}</p> : null}
    </>
  );
}

/** 열린 요청(요청함 · 견적서 옴)의 연락처 · 버튼 · 내 연락처 줄 */
function Open({ v, requestText }: { v: Visit; requestText?: string }) {
  const ct = v.contact;
  return (
    <>
      {ct ? (
        <>
          <p className="mj-num">대표 {ct.owner_name ?? "이름 없음"}{ct.phone ? <> · <b className={c.phone}>{phoneText(ct.phone)}</b></> : null}</p>
          <p className="mj-small mj-num">{contactLine(ct)}</p>
          <Way v={v} />
        </>
      ) : <p className="mj-small">연락처를 불러오지 못했어요. 잠시 뒤 다시 열어 주세요.</p>}
      {requestText ? (
        <div className={c.rq}>
          <p className="mj-label">연락할 때 보낼 문구</p>
          <p className={`mj-num ${c.rqt}`}>{requestText}</p>
          <CopyButton text={requestText} label="문구 복사" small />
        </div>
      ) : null}
      <p className={`mj-small ${c.ln}`}>
        {v.share_contact
          ? <><span className={c.ok}><Check /></span>내 연락처를 보냈어요 · 업체가 먼저 연락할 수도 있어요</>
          : <><Lock />내 연락처는 보내지 않았어요 · 위 문구를 보내며 먼저 연락해 주세요</>}
      </p>
    </>
  );
}

/** 닫힌 요청(취소함 · 못 받음 · 닫힘): 연락처를 가린 까닭과 다음 할 일 */
function Shut({ v, pickHref }: { v: Visit; pickHref: string }) {
  const name = v.vendor_name;
  return (
    <>
      {v.status === "declined" ? (
        <>
          <p>{iGa(name)} 이번 요청을 받지 못해요{declineText(v.decline_code) ? ` · ${declineText(v.decline_code)}` : ""}{v.closed_at ? <> · <span className="mj-num">{when(v.closed_at)}</span></> : null}</p>
          <p className="mj-small">보이던 연락처는 바로 가려졌어요. 담을 칸이 하나 비었어요.</p>
        </>
      ) : v.status === "cancelled" ? (
        <p className="mj-small">연락처가 가려졌어요.{v.share_contact ? ` 보낸 내 연락처도 ${name}에서 바로 가려졌어요.` : ""}{v.closed_at ? <> <span className="mj-num">{when(v.closed_at)}</span> 취소</> : null}</p>
      ) : (
        <p className="mj-small">닫힌 요청이에요. 연락처가 가려졌어요.</p>
      )}
      <Link className={`mj-lnk ${c.sm}`} href={pickHref}>다른 업체 담기</Link>
    </>
  );
}

type Props = {
  v: Visit;
  projectId: string;
  trade: VTrade;
  /** 실측 화면: 업체 이름(업체 자세히 링크)을 머리에 둔다 */
  titled?: boolean;
  /** 업체 자세히: 카드 안에 '연락할 때 보낼 문구'를 둔다 */
  requestText?: string;
  /** 업체 자세히: '요청한 업체 모두 보기' */
  allHref?: string;
};

export function ContactCard({ v, projectId, trade, titled, requestText, allHref }: Props) {
  const open = isLive(v.status) && v.contact_visible;
  const pickHref = `/projects/${projectId}/vendors?trade=${trade}`;
  const detail = v.vendor_id ? `/projects/${projectId}/vendors/${v.vendor_id}?trade=${trade}` : null;
  return (
    <>
      <div className={c.head}>
        {titled ? (
          <h3 className="mj-h3">{detail ? <Link className={c.vn} href={detail} aria-label={`${v.vendor_name} 업체 자세히 보기`}>{v.vendor_name}</Link> : v.vendor_name}</h3>
        ) : null}
        <VisitState status={v.status} />
        {titled ? null : <span className="mj-small mj-num">{when(v.created_at)}</span>}
      </div>
      {open ? <Open v={v} requestText={requestText} /> : isLive(v.status) ? <p className="mj-small">연락처를 보일 수 있는 때가 지났어요.</p> : <Shut v={v} pickHref={pickHref} />}
      <div className={c.foot}>
        {allHref ? <Link className={`mj-lnk ${c.sm}`} href={allHref}>요청한 업체 모두 보기</Link> : <span className="mj-small mj-num">{when(v.created_at)} 요청</span>}
        {v.status === "requested" ? <CancelVisit projectId={projectId} visitId={v.id} name={v.vendor_name} shared={v.share_contact} /> : null}
      </div>
    </>
  );
}
