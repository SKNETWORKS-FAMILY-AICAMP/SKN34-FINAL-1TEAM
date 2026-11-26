import assert from "node:assert/strict";
import { test } from "node:test";
import { AssistSession } from "./session.ts";
import { requestIntent } from "./requestIntent.ts";
import { parseExtra } from "./parse.ts";
import { ruleFields } from "./rules.ts";
import { formKeys, type AssistCurrent, type AssistRequest, type CandidateField, type RuleOptions } from "./protocol.ts";

const opts:RuleOptions={today:"2026-11-25",vatModes:["included","excluded","exempt"],daysMax:120};
const current:AssistCurrent={amount:900000,vat:"included",days:4,valid:"2026-12-31"};
type Offer=Partial<CandidateField>&{field:string};
function response(r:AssistRequest,offers:Offer[]){
  return {source:"llm",form:r.form,request_id:r.request_id,warnings:[],fields:formKeys(r.form).map(field=>{
    const offer=offers.find(o=>o.field===field);
    return offer?{field,source:"llm",action:"set",status:"valid",value:null,evidence:r.text,alternatives:[],
      intent_kind:"explicit_change",value_source:"utterance",origin_request_id:r.request_id,...offer}:
      {field,source:"rule",action:"keep",status:"valid",value:null,evidence:"",alternatives:[],
        intent_kind:"no_mention",value_source:null,origin_request_id:r.request_id};
  })};
}
function harness(){
  const holds:Record<string,string>={};let responder:(r:AssistRequest)=>Offer[]=()=>[];
  const s=new AssistSession("quote","report-dev",async r=>response(r,responder(r)));
  return {s,holds,async send(text:string,offers:(r:AssistRequest)=>Offer[]){
    responder=offers;
    assert(s.send(text,current,opts,d=>{
      for(const [k,v] of Object.entries(d.fieldHolds??{}))if(v===null)delete holds[k];else if(typeof v==="string")holds[k]=v;
    },()=>({current,manualVersions:{}})));
    await new Promise<void>(r=>setImmediate(r));
    assert.equal(s.view.phase,"ready");
  }};
}
const retain=(field:string,evidence:string):Offer=>({field,evidence,action:"keep",value:null,intent_kind:"retain_current",value_source:"current"});
const carried=(r:AssistRequest,field:string):Offer=>{
  const p=r.previous_candidate;
  assert(p);
  const old=p.fields.find(o=>o.field===field);
  assert(old);
  return {...old,field,source:p.sources?.[field],origin_request_id:p.origins?.[field]??p.request_id,intent_kind:"retain_previous",value_source:"previous"};
};
for(const [field,label,seed,value] of [
  ["amount","견적 금액","견적 금액을 120만 원으로 변경해 주세요",1200000],
  ["vat","부가세","부가세를 별도로 청구해 주세요","excluded"],
] as const){
  const currentStem=label+"은 현재 값으로 유지하";
  const report=currentStem+"라고 전달했습니다";
  const keep=currentStem+"겠습니다";
  const priorStem=label+"은 이전 후보를 그대로 유지하";
  const priorReport=priorStem+"라고 전달했습니다";
  const priorKeep=priorStem+"겠습니다";
  test(field+" reported current is not present intent or local action",()=>{
    const i=requestIntent("quote",report);
    assert(!i.currentKeep.has(field));
    assert(!i.linkedCurrentKeep.has(field));
    assert.equal(ruleFields("quote",report,opts).find(o=>o.field===field)?.action,"keep");
  });
  for(const actual of [false,true])test(field+" reported current hold, actual="+actual,async()=>{
    const h=harness();
    try{
      await h.send(label+"은 아직 미정입니다",()=>[{field,action:"uncertain",status:"uncertain",intent_kind:"ambiguous"}]);
      assert(h.holds[field]);
      await h.send(report+"."+(actual?" "+keep+".":""),()=>[retain(field,actual?keep:currentStem)]);
      assert.equal(Boolean(h.holds[field]),!actual);
      assert(!h.s.apply(current,()=>assert.fail("keep cannot mutate")));
    }finally{h.s.dispose();}
  });
  for(const actual of [false,true])test(field+" reported prior carry, actual="+actual,async()=>{
    const h=harness();
    try{
      await h.send(seed,()=>[{field,value,evidence:seed}]);
      assert(h.holds[field]);
      await h.send(priorReport+"."+(actual?" "+priorKeep+".":""),r=>[carried(r,field)]);
      let patch:AssistCurrent={};
      assert.equal(h.s.apply(current,p=>patch=p),actual);
      assert.deepEqual(patch,actual?{[field]:value}:{});
      assert.equal(Boolean(h.holds[field]),!actual);
    }finally{h.s.dispose();}
  });
  test(field+" direct present current and prior retention stay valid",async()=>{
    const h=harness();
    try{
      await h.send(seed,()=>[{field,value,evidence:seed}]);
      await h.send(priorKeep,r=>[carried(r,field)]);
      assert(h.s.apply(current,p=>assert.deepEqual(p,{[field]:value})));
      await h.send(label+"은 아직 미정입니다",()=>[{field,action:"uncertain",status:"uncertain",intent_kind:"ambiguous"}]);
      await h.send(keep,()=>[retain(field,keep)]);
      assert(!h.holds[field]);
    }finally{h.s.dispose();}
  });
}
test("dependent background sentence is not a title or reason after an explicit scalar request",()=>{
  const text="추가 금액을 39만 원으로 수정해 주세요. 협의가 늦어졌기 때문에.";
  const out=parseExtra(text,opts.today);
  assert.equal(out.amount,390000);
  assert.equal(out.reason,undefined);
  assert.equal(out.title,undefined);
});
test("independent shorthand and explicitly owned reason remain supported",()=>{
  assert.equal(parseExtra("현장 확인 중 틈이 벌어져서",opts.today).reason,"현장 확인 중 틈이 벌어져서");
  assert.equal(parseExtra("마감 보수 19만 원, 부가세 별도, 배관이 새어서",opts.today).reason,"배관이 새어서");
  assert.equal(parseExtra("사유는 '배관이 새어서'로 지정해 주세요.",opts.today).reason,"배관이 새어서");
});
