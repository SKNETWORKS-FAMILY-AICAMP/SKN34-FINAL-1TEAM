import assert from "node:assert/strict";
import { test } from "node:test";
import type { AssistCurrent, AssistRequest, CandidateField, RuleOptions } from "./protocol.ts";

const baseline = process.env.QA179_FE_BASELINE === "1";
const root = baseline ? "../../../../../qa179-r14-s3-master/frontend/components/pro/assist/" : "./";
const { AssistSession } = await import(root+"session.ts") as typeof import("./session.ts");
const { parseExtra } = await import(root+"parse.ts") as typeof import("./parse.ts");
const { formKeys } = await import(root+"protocol.ts") as typeof import("./protocol.ts");
const opts:RuleOptions = {today:"2026-11-25",vatModes:["included","excluded","exempt"]};
const current:AssistCurrent={title:"기존 마감",reason:"기존 사유",amount:210000,vat:"included"};
type Offer=Partial<CandidateField>&{field:string};
function reply(r:AssistRequest, offers:Offer[]){
  return {source:"llm",form:r.form,request_id:r.request_id,warnings:[],fields:formKeys(r.form).map(field=>{
    const offer=offers.find(x=>x.field===field);
    return offer?{field,source:"llm",action:"set",status:"valid",value:null,evidence:r.text,alternatives:[],
      intent_kind:"explicit_change",value_source:"utterance",origin_request_id:r.request_id,...offer}:
      {field,source:"rule",action:"keep",status:"valid",value:null,evidence:"",alternatives:[],
      intent_kind:"no_mention",value_source:null,origin_request_id:r.request_id};
  })};
}
function harness(offline:boolean){
  const holds:Record<string,string>={};
  let offers:Offer[]=[];
  const s=new AssistSession("extra","developer-ownership",async r=>{
    if(offline)throw new Error("offline");
    return reply(r,offers);
  });
  return {s,holds,async send(text:string,fields:Offer[]){
    offers=fields;
    const value=structuredClone(current);
    assert(s.send(text,value,opts,delta=>{
      for(const [key,msg] of Object.entries(delta.fieldHolds??{}))if(msg===null)delete holds[key];else if(typeof msg==="string")holds[key]=msg;
    },()=>({current:value,manualVersions:{}})));
    assert(!s.apply(value,()=>assert.fail("before ready")));
    await new Promise<void>(r=>setImmediate(r));
    assert.equal(s.view.phase,"ready");
    return value;
  }};
}
const amountClause="추가 금액은 43만 원으로 변경해 주세요";
const amount:Offer={field:"amount",value:430000,evidence:amountClause};
for(const offline of [false,true])for(const prior of [false,true]){
  test("causal lead belongs to scalar, not a new reason hold: "+offline+"/"+prior,async()=>{
    const h=harness(offline);
    try{
      if(prior){
        await h.send("추가 사유는 아직 미정입니다.",[{field:"reason",status:"uncertain",action:"uncertain",evidence:"추가 사유는 아직 미정입니다"}]);
        assert(h.holds.reason);
      }
      const causal="수급 문제가 생겼기 때문에";
      const value=await h.send(causal+" "+amountClause+".",[{field:"reason",value:causal,evidence:causal},amount]);
      let applied:AssistCurrent={};
      assert(h.s.apply(value,changes=>applied=changes));
      assert.deepEqual(applied,{amount:430000});
      assert.equal(Boolean(h.holds.reason),prior);
    }finally{h.s.dispose();}
  });
}
for(const offline of [false,true])test("trailing report is not a title operation: "+offline,async()=>{
  const h=harness(offline);
  try{
    const report="현장에서 자재 상태를 확인했습니다";
    const value=await h.send(amountClause+". "+report+".",[amount,{field:"title",value:report,evidence:report}]);
    let applied:AssistCurrent={};
    assert(h.s.apply(value,changes=>applied=changes));
    assert.deepEqual(applied,{amount:430000});
    assert(!h.holds.title);
  }finally{h.s.dispose();}
});
test("nominal title and causal shorthand remain supported",()=>{
  assert.equal(parseExtra("현관 문턱 마감 보수. "+amountClause+".",opts.today).title,"현관 문턱 마감 보수");
  assert.equal(parseExtra("창틀 보완 17만 원, 부가세 별도, 빗물이 스며들어서",opts.today).reason,"빗물이 스며들어서");
  assert.equal(parseExtra("빗물이 스며들어서",opts.today).reason,"빗물이 스며들어서");
});
for(const offline of [false,true])test("explicit reason and independent amount retain their own values: "+offline,async()=>{
  const h=harness(offline);
  try{
    const clause="추가 사유는 자재 손상으로 기재해 주세요";
    const value=await h.send(clause+", "+amountClause+".",[{field:"reason",value:"자재 손상",evidence:clause},amount]);
    let applied:AssistCurrent={};
    assert(h.s.apply(value,changes=>applied=changes));
    assert.deepEqual(applied,{reason:"자재 손상",amount:430000});
  }finally{h.s.dispose();}
});
