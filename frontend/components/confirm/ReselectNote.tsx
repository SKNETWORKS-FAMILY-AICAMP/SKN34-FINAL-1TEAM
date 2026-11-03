// 업체를 다시 고르기로 한 뒤 업체 고르기 · 실측 화면 위 안내 (시안 WVendors '다시 고른 뒤').
// 확정이 없고 이 공정에 다시 고르기로 닫힌 요청(close_reason reselected)이 있을 때 화면이 그린다.
import { tradeLabel } from "@/lib/labels";
import s from "./confirm.module.css";

export function ReselectNote({ trade, again }: { trade: string; again?: boolean }) {
  return (
    <div className={`${s.dealNote} ${s.reNote}`}>
      <p><b>{tradeLabel(trade)} 업체를 다시 고르는 중이에요.</b> 받은 실측 견적서는 닫혔고 서류에 기록으로 남아요. 보이던 연락처도 가려졌어요.</p>
      {again ? <p className="mj-small">같은 업체에도 다시 요청할 수 있어요. 담는 곳은 다시 3곳까지예요.</p> : null}
    </div>
  );
}
