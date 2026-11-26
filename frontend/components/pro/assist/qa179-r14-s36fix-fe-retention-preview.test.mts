import assert from "node:assert/strict";
import test from "node:test";
import {requestIntent,resolvedIntentSegments,ownsCurrentRetentionEvidence,ownsLinkedCurrentRetentionEvidence,periodBoundaryPreview} from "./requestIntent.ts";
import {ruleFields} from "./rules.ts";
const opts={today:"2026-11-26",baseMonth:"2026-12-01",crewMax:6,daysMax:120};

test("owned original-value identity is distinct from unbound cancellation",()=>{
  const keep="작업 소요일은 원래대로 두세요.";
  const text="아까 말씀드린 9일은 취소할게요. "+keep;
  const local=requestIntent("quote",keep), full=requestIntent("quote",text);
  assert.ok(local.currentKeep.has("days")&&!local.blocked.has("days"));
  assert.ok(full.currentKeep.has("days")&&!full.blocked.has("days"));
  assert.ok(full.unboundWithdrawal,"earlier unnamed cancellation still requires verified prior binding");
  assert.ok(ownsCurrentRetentionEvidence("quote",keep,"days",keep));
  assert.equal(ownsCurrentRetentionEvidence("quote",text,"days",keep),false);
  for(const t of ["작업 소요일은 원래대로.","작업 소요일 후보는 원래대로 두세요.",
    "작업 소요일은 원래대로 두면 될까요?","가능하면 작업 소요일은 원래대로 두세요.",
    "작업 소요일은 원래대로 두지 마세요."]){
    assert.equal(requestIntent("quote",t).currentKeep.has("days"),false,t);
    assert.equal(ownsCurrentRetentionEvidence("quote",t,"days",t),false,t);
  }
  assert.ok(requestIntent("extra","사유는 수정 전 상태 그대로 유지해 주세요.").currentKeep.has("reason"));
});

test("linked current identity resolves only against the full utterance and exact owner",()=>{
  const lead="투입 인원은 지금 등록된 대로 하고";
  const text=lead+" 방문 날짜만 12월 11일, 12월 18일로 변경해 주세요.";
  assert.ok(requestIntent("dates",text).linkedCurrentKeep.has("crew"));
  assert.equal(requestIntent("dates",lead).linkedCurrentKeep.has("crew"),false);
  assert.ok(ownsLinkedCurrentRetentionEvidence("dates",text,"crew",lead));
  assert.ok(ownsLinkedCurrentRetentionEvidence("dates",text,"crew",text));
  assert.equal(ownsLinkedCurrentRetentionEvidence("dates",text,"days",lead),false);
  for(const t of [lead,lead+". 방문 날짜는 12월 11일로 변경해 주세요.",
    "가능하면 "+text,text+" 인원은 4명으로 변경해 주세요.",
    "투입 인원 후보는 지금 등록된 대로 하고 방문 날짜는 12월 11일로 변경해 주세요."]){
    assert.equal(ownsLinkedCurrentRetentionEvidence("dates",t,"crew",lead),false,t);
  }
});

test("closed recommendation preview is an owned server-only candidate, not execution condition",()=>{
  const recommendation="현장 준비를 생각하면 12월 5일에 시작하는 걸로 변경하는 게 좋겠어요.";
  const text=recommendation+" 변경안 보여 주세요.";
  const proof=periodBoundaryPreview(text);
  assert.equal(proof?.field,"start");
  assert.equal(proof?.body,"12월 5일에 시작하는 걸로 변경");
  const intent=requestIntent("period",text);
  assert.ok(intent.requested.has("start")&&intent.serverOnly.has("start")&&!intent.blocked.has("start"));
  assert.equal(resolvedIntentSegments("period",text)[0].dependent,false);
  assert.equal(ruleFields("period",text,opts).find(f=>f.field==="start")?.action,"keep");
  const alternate="현장 정리를 고려하면 12월 22일에 마감하는 걸로 수정하는 편이 좋겠습니다. 수정안을 보여 주세요.";
  assert.equal(periodBoundaryPreview(alternate)?.field,"end");
  for(const t of [recommendation,"승인이 나면 12월 5일에 시작하는 걸로 변경하는 게 좋겠어요. 변경안 보여 주세요.",
    "승인 여부를 생각하면 12월 5일에 시작하는 걸로 변경하는 게 좋겠어요. 변경안 보여 주세요.",
    "현장 준비를 생각하면 12월 5일이나 12월 7일에 시작하는 걸로 변경하는 게 좋겠어요. 변경안 보여 주세요.",
    recommendation+" 변경안 보여 줄 수 있나요?",recommendation+" 변경안 보여 주세요. 시작일은 미정이에요.",
    "현장 준비를 생각하면 12월 5일에 시작하는 걸로 변경하지 않는 게 좋겠어요. 변경안 보여 주세요."]){
    assert.equal(periodBoundaryPreview(t),undefined,t);
  }
});
