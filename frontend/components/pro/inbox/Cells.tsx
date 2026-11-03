// 작업함 표의 칸 조각. 서버 화면(할 일 · 기다림)과 브라우저 화면(끝남 더 보기)이 같이 쓴다.
import { phoneText } from "@/lib/labels";
import { endLabel, endNote, type Row } from "./text";
import s from "./inbox.module.css";

/** 고객 칸: 이름 · 번호. 연락처가 없으면 요청 번호 + 까닭(열린 요청) 또는 '가려졌어요'(닫힌 요청) */
export function Who({ v, closed }: { v: Row; closed?: boolean }) {
  if (v.customer) {
    return <>{v.customer.name}{v.customer.phone ? <span className={`${s.ph} mj-num`}>{phoneText(v.customer.phone)}</span> : null}</>;
  }
  if (closed) return <span className={s.hid}>가려졌어요</span>;
  return <><span className={s.code}>{v.code}</span><span className={s.ph}>연락처 안 보냄</span></>;
}

export const Dash = () => (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><circle cx="7" cy="7" r="5.5" /><path d="M4.5 7h5" /></svg>
);

/** 기한 지남 (빨간 느낌표, 글자와 함께 쓴다) */
export const Alert = () => (
  <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true"><circle cx="7" cy="7" r="5.8" /><path d="M7 3.8v3.8" strokeLinecap="round" /><path d="M7 10v.2" strokeLinecap="round" strokeWidth="2.2" /></svg>
);

/** 끝남 칸: 어떻게 끝났나 + 닫힌 날 */
export function End({ v }: { v: Row }) {
  return <><span className="mj-st mj-st-off"><Dash />{endLabel(v)}</span><br /><span className="mj-small mj-num">{endNote(v)}</span></>;
}
