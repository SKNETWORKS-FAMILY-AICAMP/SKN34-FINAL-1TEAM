// 이 공정을 이미 확정했을 때 업체 고르기 · 실측 화면 위 안내 (시안 WVendors '도배를 이미 확정했을 때').
// 다시 고르기는 확정한 견적서(비교 화면의 확정 줄 · 확정 화면)에 있다. 공사 날짜는 공사 날짜 화면에서 본다.
import Link from "next/link";
import { Check } from "@/components/vendors/icons";
import { eunNeun } from "@/components/vendors/text";
import { euro } from "@/lib/format";
import { tradeLabel } from "@/lib/labels";
import { canReselect, type Deal } from "./text";
import s from "./confirm.module.css";

export function DealNote({ d, projectId }: { d: Deal; projectId: string }) {
  const tName = tradeLabel(d.trade);
  return (
    <div className={s.dealNote}>
      <p className={`mj-st mj-st-ok ${s.stLine} ${s.stBig}`}><Check size={16} />{eunNeun(tName)} {euro(d.vendor_name)} 확정했어요</p>
      <p>
        {canReselect(d)
          ? "공사 날짜를 수락하기 전이면 확정한 견적서 화면에서 업체를 다시 고를 수 있어요. 지금은 업체를 더 담거나 실측을 요청할 수 없어요."
          : "공사 날짜를 수락해서 업체를 바꿀 수 없어요."}
      </p>
      <div className="mj-btn-row">
        <Link className={`mj-lnk ${s.sm}`} href={`/projects/${projectId}/schedule?trade=${d.trade}`}>공사 날짜 보기</Link>
        <Link className={`mj-lnk ${s.sm}`} href={`/projects/${projectId}/quotes?trade=${d.trade}`}>확정한 견적서 보기</Link>
      </div>
    </div>
  );
}
