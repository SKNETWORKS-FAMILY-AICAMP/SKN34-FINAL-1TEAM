import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { formKeys, type AssistCurrent, type AssistForm, type AssistRequest, type CandidateField, type RuleOptions } from "./protocol.ts";
import { foreignExtraTextScope, parseExtra, readCrew } from "./parse.ts";
import { referencedPeriodExclusion, ownsCurrentRetentionEvidence } from "./requestIntent.ts";

const opts:RuleOptions={today:"2026-11-25",baseMonth:"2026-12-01",crewMax:6,daysMax:120,requiredDays:2,vatModes:["included","excluded","exempt"]};
const current:Record<AssistForm,AssistCurrent>={
  extra:{title:"기존 항목",reason:"기존 사유",amount:210000,vat:"included"},
  dates:{crew:2,days:["2026-12-08","2026-12-17"]},
  period:{start:"2026-12-02",end:"2026-12-29",excluded:["2026-12-08"]},
  quote:{amount:3500000,vat:"included",days:5,valid:"2026-12-27"},
};
type Offer=Partial<CandidateField>&{field:string};
function envelope(r:AssistRequest,offers:Offer[]){
  return {source:"llm",form:r.form,request_id:r.request_id,warnings:[],fields:formKeys(r.form).map(name=>{
    const value=offers.find(f=>f.field===name);
    return value?{field:name,source:"llm",action:"set",status:"valid",value:null,evidence:r.text,alternatives:[],
      intent_kind:"explicit_change",value_source:"utterance",origin_request_id:r.request_id,...value}:
      {field:name,source:"rule",action:"keep",status:"valid",value:null,evidence:"",alternatives:[],
      intent_kind:"no_mention",value_source:null,origin_request_id:r.request_id};
  })};
}
async function send(s:AssistSession,text:string,form:AssistForm,holds:Record<string,string>,value=structuredClone(current[form])){
  assert(s.send(text,value,opts,delta=>{
    for(const [k,v] of Object.entries(delta.fieldHolds??{}))if(v===null)delete holds[k];else if(typeof v==="string")holds[k]=v;
  },()=>({current:value,manualVersions:{}})));
  assert(!s.apply(value,()=>assert.fail("before response")));
  await new Promise<void>(r=>setImmediate(r));
  assert.equal(s.view.phase,"ready");
  return value;
}
const actionable=(s:AssistSession,field:string)=>s.view.response?.fields.some(f=>f.field===field&&f.status==="valid"&&f.action==="set");
for(const [name,text,evidence] of [
  ["causal prefix","단열 자재를 교체해서 추가 금액은 28만원으로 변경합니다.","단열 자재를 교체해서"],
  ["separate descriptive report","현장에서 배관 상태가 달라졌습니다. 추가 금액은 28만원으로 변경합니다.","현장에서 배관 상태가 달라졌습니다"],
] as const)test("unowned "+name+" cannot become a reason",async()=>{
  const holds:Record<string,string>={};
  const s=new AssistSession("extra","r14",async r=>envelope(r,[
    {field:"reason",value:evidence,evidence},
    {field:"amount",value:280000,evidence:"추가 금액은 28만원으로 변경합니다"},
  ]));
  try{
    const v=await send(s,text,"extra",holds);
    assert(!actionable(s,"reason"));assert(actionable(s,"amount"));
    let patch:AssistCurrent={};assert(s.apply(v,p=>patch=p));assert.deepEqual(patch,{amount:280000});
    assert(!holds.reason);
  }finally{s.dispose();}
});
test("independently owned reason and scalar remain valid",async()=>{
  const text="사유는 배관 부식으로 수정해 주세요, 추가 금액은 28만원으로 변경합니다.";
  const holds:Record<string,string>={};
  const s=new AssistSession("extra","r14",async r=>envelope(r,[
    {field:"reason",value:"배관 부식",evidence:"사유는 배관 부식으로 수정해 주세요"},
    {field:"amount",value:280000,evidence:"추가 금액은 28만원으로 변경합니다"},
  ]));
  try{const v=await send(s,text,"extra",holds);assert(actionable(s,"reason"));assert(actionable(s,"amount"));let patch:AssistCurrent={};assert(s.apply(v,p=>patch=p));assert.deepEqual(patch,{reason:"배관 부식",amount:280000});}finally{s.dispose();}
});
test("nominal title and standalone implicit reason stay distinct from reports",()=>{
  assert.equal(parseExtra("욕실 실리콘 보수. 추가 금액은 28만원으로 변경합니다.",opts.today).title,"욕실 실리콘 보수");
  assert.equal(parseExtra("벽 안쪽에 습기가 차서",opts.today).reason,"벽 안쪽에 습기가 차서");
  assert(!foreignExtraTextScope("욕실 실리콘 보수. 추가 금액은 28만원으로 변경합니다.","title","욕실 실리콘 보수","욕실 실리콘 보수"));
});
for(const [name,text,evidence,value,pass] of [
  ["complete proposal","작업 인원 4명으로 변경하는 안을 먼저 보여 주시겠어요?","작업 인원 4명으로 변경",4,true],
  ["conditional proposal","승인이 나면 작업 인원 4명으로 변경하는 안을 먼저 보여 주세요.","작업 인원 4명으로 변경",4,false],
  ["wrong preview value","작업 인원 4명으로 변경하는 안을 먼저 보여 주세요.","작업 인원 4명으로 변경",5,false],
  ["explicit increment","현장 인력 두 명 더 추가해서 총 4명으로 수정해 주세요.","총 4명으로 수정해 주세요",4,true],
  ["inconsistent increment","현장 인력 두 명 더 추가해서 총 5명으로 수정해 주세요.","총 5명으로 수정해 주세요",5,false],
  ["explicit decrement","현장 인력 한 명 줄여서 총 1명으로 수정해 주세요.","총 1명으로 수정해 주세요",1,true],
] as const)test("owned quantity "+name,async()=>{
  const holds:Record<string,string>={};const s=new AssistSession("dates","r14",async r=>envelope(r,[{field:"crew",value,evidence}]));
  try{await send(s,text,"dates",holds);assert.equal(!!actionable(s,"crew"),pass);assert(holds.crew);}finally{s.dispose();}
});
for(const bad of [false,true])test("prior-bound correction "+(bad?"mismatch":"verified"),async()=>{
  const holds:Record<string,string>={};let turn=0;
  const s=new AssistSession("dates","r14",async r=>envelope(r,++turn===1?[{field:"crew",value:5,evidence:r.text}]:[
    {field:"crew",value:3,evidence:"작업 인원 3명으로 수정",correction:{kind:"replace",rejected_value:bad?4:5,rejected_evidence:"다섯 명까지는 필요 없고",accepted_evidence:"작업 인원 3명으로 수정"}},
  ]));
  try{
    await send(s,"작업 인원을 5명으로 변경해 주세요.","dates",holds);
    assert(actionable(s,"crew"));
    const v=await send(s,"다섯 명까지는 필요 없고 작업 인원 3명으로 수정해 주세요.","dates",holds);
    assert.equal(!!actionable(s,"crew"),!bad);
    let patch:AssistCurrent={};assert.equal(s.apply(v,p=>patch=p),!bad);assert.deepEqual(patch,bad?{}:{crew:3});
  }finally{s.dispose();}
});
test("reference must have an immediately preceding complete list",()=>{
  assert(referencedPeriodExclusion("출입 제한일은 12월 7일, 12일입니다. 이 날짜들을 제외일로 넣어 주세요."));
  assert.equal(referencedPeriodExclusion("시작일은 12월 7일, 종료일은 12일입니다. 이 날짜들을 제외일로 넣어 주세요."),undefined);
  assert.equal(referencedPeriodExclusion("가능하면 12월 7일, 12일입니다. 이 날짜들을 제외일로 넣어 주세요."),undefined);
});
test("current identity evidence must actually own that field",()=>{
  const text="투입 인원은 지금 등록된 대로 하고 작업 날짜는 12월 7일과 14일로 변경해 주세요.";
  assert(ownsCurrentRetentionEvidence("dates",text,"crew","투입 인원은 지금 등록된 대로"));
  assert(!ownsCurrentRetentionEvidence("dates",text,"days","투입 인원은 지금 등록된 대로"));
});
