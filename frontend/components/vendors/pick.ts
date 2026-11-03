// 실측 요청에 담은 업체 · 남길 말 (이 기기 sessionStorage, 프로젝트 · 공정마다).
// 휴대폰 번호를 넣고 돌아와도 담은 그대로다. 보내고 나면 보낸 업체를 뺀다.
import type { VTrade } from "./text";

export type Pick = { ids: string[]; msg: string };
const key = (projectId: string, trade: VTrade) => `mj_visit_pick_${projectId}_${trade}`;

export function readPick(projectId: string, trade: VTrade): Pick {
  try {
    const raw = sessionStorage.getItem(key(projectId, trade));
    const v = raw ? JSON.parse(raw) : null;
    const ids = Array.isArray(v?.ids) ? v.ids.filter((x: unknown): x is string => typeof x === "string") : [];
    return { ids, msg: typeof v?.msg === "string" ? v.msg : "" };
  } catch {
    return { ids: [], msg: "" };
  }
}

export function writePick(projectId: string, trade: VTrade, p: Pick) {
  try {
    if (!p.ids.length && !p.msg) sessionStorage.removeItem(key(projectId, trade));
    else sessionStorage.setItem(key(projectId, trade), JSON.stringify(p));
  } catch { /* 저장 못 해도 화면은 그대로 */ }
}
