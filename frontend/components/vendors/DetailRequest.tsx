"use client";
// 업체 자세히의 '실측 요청' 버튼 (시안 WVendorDetail). 업체 고르기와 같은 확인 창으로 이 업체 하나만 요청하고,
// 보낸 뒤에는 ?sent=1로 다시 그려 실측 · 연락 카드를 펼친다.
import { useState } from "react";
import { useRouter } from "next/navigation";
import { RequestDialog } from "./RequestDialog";
import type { VTrade } from "./text";

type Props = { projectId: string; trade: VTrade; vendorId: string; name: string; consentVer: string; full: boolean; idp: string; hintId?: string };

export function DetailRequest({ projectId, trade, vendorId, name, consentVer, full, idp, hintId }: Props) {
  const router = useRouter();
  const [open, setOpen] = useState(false);
  const here = `/projects/${projectId}/vendors/${vendorId}?trade=${trade}`;
  return (
    <>
      <button type="button" className="mj-btn mj-btn-pri" aria-haspopup="dialog" disabled={full} aria-describedby={hintId} onClick={() => setOpen(true)}>실측 요청</button>
      <RequestDialog
        open={open}
        onClose={() => setOpen(false)}
        onSent={() => { setOpen(false); router.replace(`${here}&sent=1`, { scroll: false }); }}
        projectId={projectId}
        trade={trade}
        vendors={[{ id: vendorId, name }]}
        consentVer={consentVer}
        backTo={here}
        idp={idp}
      />
    </>
  );
}
