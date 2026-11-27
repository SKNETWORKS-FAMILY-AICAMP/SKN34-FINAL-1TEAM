import assert from "node:assert/strict";
import {test} from "node:test";
import {requestIntent} from "./requestIntent.ts";
import {calendarEvidenceDates, readExplicitPeriodExclusions} from "./parse.ts";
import {ruleFields} from "./rules.ts";
import {AssistSession} from "./session.ts";
import type {AssistCurrent, AssistRequest, CandidateField, RuleOptions} from "./protocol.ts";

const opts:RuleOptions={today:"2026-11-25",baseMonth:"2026-12-01",daysMax:120};
const initial={start:"2026-12-02",end:"2026-12-24",excluded:[]};
const dates=["2026-12-07","2026-12-14","2026-12-21"];
const phrase="12월 7일, 14일, 21일은 현장에 들어갈 수 없으니 세 날짜를 제외일로 지정해 주세요.";
test("causally linked exclusion owns the whole enumeration",()=>{
  for(const text of [phrase,"12월 7일, 14일, 21일은 현장에 들어갈 수 없으니 제외일로 지정해 주세요.","12월 7일, 14일, 21일은 방문하기 어려우니 3개 날짜를 제외일로 지정해 주세요."]){
    assert.deepEqual([...requestIntent("period",text).requested],["excluded"],text);
    assert.deepEqual(readExplicitPeriodExclusions(text,opts.today,opts.baseMonth),{invalid:false,dates},text);
    assert.deepEqual(calendarEvidenceDates(text,opts.today,opts.baseMonth),dates,text);
    const rules=Object.fromEntries(ruleFields("period",text,opts,initial).map(f=>[f.field,f]));
    assert.equal(rules.start.action,"keep",text);
    assert.equal(rules.end.action,"keep",text);
    assert.deepEqual(rules.excluded.value,dates,text);
  }
});
test("declared count does not truncate or authorize a contradictory list",()=>{
  const text=phrase.replace("세 날짜","두 날짜");
  assert.deepEqual(readExplicitPeriodExclusions(text,opts.today,opts.baseMonth),{invalid:true,dates});
  assert.equal(calendarEvidenceDates(text,opts.today,opts.baseMonth),undefined);
  assert.notEqual(ruleFields("period",text,opts,initial).find(f=>f.field==="excluded")?.action,"set");
});
async function replay(text:string, offered:string[], evidence:string, expected:AssistCurrent){
  const current=structuredClone(initial) as AssistCurrent, holds:Record<string,string>={};
  const s=new AssistSession("period","regression-h2-period",async(r:AssistRequest)=>{
    const keep=(field:string):CandidateField=>({field,action:"keep",value:null,status:"valid",source:"rule",evidence:"",alternatives:[],origin_request_id:r.request_id,intent_kind:"no_mention",value_source:null});
    return {request_id:r.request_id,form:r.form,source:"llm",warnings:[],fields:[keep("start"),keep("end"),{...keep("excluded"),action:"set",value:offered,evidence,intent_kind:"explicit_change",value_source:"utterance",source:"llm"}]};
  });
  try{
    assert(s.send(text,current,opts,x=>{for(const [k,v] of Object.entries(x.fieldHolds??{})){if(v===null)delete holds[k];else if(typeof v==="string")holds[k]=v;}},()=>({current,manualVersions:{}})));
    await new Promise<void>(resolve=>setImmediate(resolve));
    let patch:AssistCurrent={};
    assert.equal(s.apply(current,c=>{patch=structuredClone(c);Object.assign(current,c)}),Object.keys(expected).length>0);
    assert.deepEqual(patch,expected);
    assert.equal(holds.start,undefined);
    assert.equal(holds.end,undefined);
    assert.equal(Boolean(holds.excluded),Object.keys(expected).length===0);
  }finally{s.dispose()}
}
test("server full list passes; truncated value or mismatched declared count is held",async()=>{
  await replay(phrase,dates,phrase,{excluded:dates});
  await replay(phrase,dates.slice(2),phrase.slice(phrase.indexOf("21일")),{});
  await replay(phrase.replace("세 날짜","두 날짜"),dates,phrase.replace("세 날짜","두 날짜"),{});
});
