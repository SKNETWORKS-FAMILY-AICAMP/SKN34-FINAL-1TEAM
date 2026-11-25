// Independent offline controls, frozen before comparison. No provider or business writes.
import assert from "node:assert/strict";
import {test} from "node:test";
import {resolve} from "node:path";
import {pathToFileURL} from "node:url";
import type {AssistForm,AssistCurrent,AssistRequest,AssistValue,CandidateField,RuleOptions} from "./protocol.ts";
const source=process.env.QA179_R6SPAN_FE_SOURCE;
const local=(name:string)=>source?pathToFileURL(resolve(source,name+".ts")).href:new URL(name+".ts",import.meta.url).href;
const {AssistSession}=await import(local("session"));
const {requestIntent,scopedFieldParts}=await import(local("requestIntent"));
const {formKeys}=await import(local("protocol"));
for(const name of ["session","requestIntent","parse","rules","protocol"])console.log("SOURCE_IMPORT",local(name));
const opts:RuleOptions={today:"2026-11-25",baseMonth:"2026-12-01",requiredDays:2,daysMax:30,crewMax:6,vatModes:["included","excluded","exempt"]};
const d=(n:number)=>"2026-12-"+String(n).padStart(2,"0");
const period:AssistCurrent={start:d(2),end:d(28),excluded:[d(9),d(16)]};
const dates:AssistCurrent={days:[d(9),d(16)],crew:2};
const quote:AssistCurrent={amount:4550000,days:3,vat:"included",valid:d(17)};
const extra:AssistCurrent={title:"보수",reason:"현장 추가",amount:1000000,vat:"included"};
const set=(r:AssistRequest,field:string,value:AssistValue,evidence=r.text):CandidateField=>({field,action:"set",value,evidence,status:"valid",source:"llm",alternatives:[],intent_kind:"explicit_change",value_source:"utterance",origin_request_id:r.request_id});
const noMention=(r:AssistRequest,field:string):CandidateField=>({field,action:"keep",value:null,evidence:"",status:"valid",source:"rule",alternatives:[],intent_kind:"no_mention",origin_request_id:r.request_id});
const offered=(f:CandidateField)=>f.status==="conflict"||f.status==="valid"&&["set","clear"].includes(f.action);
function carry(r:AssistRequest,field:string):CandidateField{
  const p=r.previous_candidate,f=p?.fields.find(x=>x.field===field);assert(p&&f);
  return {...f,status:"valid",source:p.sources![field],origin_request_id:p.origins![field],alternatives:[],intent_kind:"retain_previous",value_source:f.value_source==="derived"?"derived":"previous"};
}
function fixture(form:AssistForm,initial:AssistCurrent){
  let current=structuredClone(initial),writes=0,pending=false;
  const holds:Record<string,string>={},versions:Record<string,number>={};
  let job:{r:AssistRequest;resolve:(v:unknown)=>void;reject:(e:Error)=>void};
  const session=new AssistSession(form,"offline-r6span",(r:AssistRequest)=>new Promise((resolve,reject)=>job={r:structuredClone(r),resolve,reject}));
  const live=()=>({current:structuredClone(current),manualVersions:{...versions}});
  function start(text:string){
    const before=structuredClone(current),n=writes;
    assert(session.send(text,current,opts,(issues:{pending?:boolean;fieldHolds?:Record<string,string|null>})=>{
      if(typeof issues.pending==="boolean")pending=issues.pending;
      for(const [k,v] of Object.entries(issues.fieldHolds??{}))if(v===null)delete holds[k];else holds[k]=v;
    },live));
    assert(pending);assert.equal(session.apply(current,()=>assert.fail("early Apply")),false);
    assert.deepEqual(current,before);assert.equal(writes,n);return job.r;
  }
  async function reply(fields:CandidateField[]=[],offline=false){
    const before=structuredClone(current),n=writes;
    if(offline)job.reject(new Error("offline"));else job.resolve({form,request_id:job.r.request_id,source:"llm",warnings:[],
      fields:formKeys(form).map((f:string)=>fields.find(x=>x.field===f)??noMention(job.r,f))});
    await Promise.resolve();await Promise.resolve();assert.equal(session.view.phase,"ready");assert(!pending);assert.deepEqual(current,before);assert.equal(writes,n);
  }
  async function send(text:string,fn:(r:AssistRequest)=>CandidateField[]){const r=start(text);await reply(fn(r));return r;}
  function field(name:string):CandidateField{return session.view.response!.fields.find((f:CandidateField)=>f.field===name)!;}
  function apply(expected:AssistCurrent,selected?:string[]){
    if(selected)for(const f of session.view.response!.fields)session.select(f.field,selected.includes(f.field));
    const n=writes;let patch:AssistCurrent={};
    const changed=Object.keys(expected).length>0;
    assert.equal(session.apply(current,(p:AssistCurrent)=>{writes++;patch=structuredClone(p);Object.assign(current,p);}),changed);
    assert.deepEqual(patch,expected);assert.equal(writes,n+Number(changed));if(changed)assert.equal(session.apply(current,()=>assert.fail("duplicate")),false);
  }
  function manual(field:string,value:AssistValue){current[field]=value;versions[field]=(versions[field]??0)+1;delete holds[field];}
  return {session,start,reply,send,field,apply,manual,holds,live};
}
const priorPeriod={start:d(3),end:d(27)};
async function seedPeriod(h:ReturnType<typeof fixture>){return h.send("실측 접수는 12월 3일부터 27일까지 가능합니다.",r=>[set(r,"start",priorPeriod.start,"12월 3일부터"),set(r,"end",priorPeriod.end,"27일까지")]);}
const lists=[
  ["12월 8일, 15일, 23일은 방문 제외 날짜로 지정 부탁합니다.",[d(8),d(15),d(23)]],
  ["12월 7일，13일，20일은 실측 제외일로 정합니다.",[d(7),d(13),d(20)]],
  ["12/7、12/13·12/20은 방문 제외일로 등록합니다.",[d(7),d(13),d(20)]],
  ["2026-12-07, 2026-12-13, 2026-12-20은 제외일로 정합니다.",[d(7),d(13),d(20)]],
  ["12.7, 12.13, 12.20은 제외일로 정합니다.",[d(7),d(13),d(20)]],
  ["12월 7일하고 13일, 20일은 방문 불가일로 지정합니다.",[d(7),d(13),d(20)]],
  ["방문 제외일은 12월 7일, 13일, 20일로 정합니다.",[d(7),d(13),d(20)]],
  ["제외일은 2026-12-07, 2026-12-13, 2026-12-20입니다.",[d(7),d(13),d(20)]],
  ["12/7, 13, 20일은 제외일로 정합니다.",[d(7),d(13),d(20)]],
] as const;
for(const [text,value] of lists)for(const offline of [false,true])test("owned excluded list "+text+" offline="+offline,async()=>{
  const h=fixture("period",period);
  try{
    const seed=await seedPeriod(h);
    assert.deepEqual([...requestIntent("period",text).requested].sort(),["excluded"]);
    assert.equal(scopedFieldParts("period",text,"start").length,0);
    assert.equal(scopedFieldParts("period",text,"end").length,0);
    const r=h.start(text);assert.deepEqual(Object.keys(h.holds).sort(),["end","excluded","start"]);
    const proposed=r.rule_fields.filter(f=>f.action==="set");assert.deepEqual(proposed.map(f=>[f.field,f.value]),[["excluded",value]]);
    if(offline){await h.reply([],true);h.apply({excluded:value as unknown as string[]});}
    else {
      await h.reply([carry(r,"start"),carry(r,"end"),set(r,"excluded",[...value],text)]);
      assert.equal(h.field("start").origin_request_id,seed.request_id);
      assert.equal(h.field("end").origin_request_id,seed.request_id);
      h.apply({...priorPeriod,excluded:[...value]});
      assert.deepEqual(h.holds,{});
    }
  }finally{h.session.dispose();}
});
for(const [text,fields,blocked] of [
  ["시작일은 12월 5일로 확정합니다, 12월 8일, 15일은 방문 제외일로 지정합니다.",["excluded","start"],[]],
  ["종료일은 12월 26일로 확정합니다. 12월 8일, 15일은 방문 제외일로 지정합니다.",["end","excluded"],[]],
  ["12월 5일부터 26일까지 가능합니다, 12월 8일, 15일은 방문 제외일로 지정합니다.",["end","excluded","start"],[]],
  ["12월 8일, 15일은 방문 제외일로 지정할까요?",["excluded"],["excluded"]],
  ["12월 8일, 15일은 방문 제외일로 지정하지 마세요.",["excluded"],["excluded"]],
  ["12월 8일, 15일은 방문 제외일로 지정할게요. 제외 요청은 취소합니다.",["excluded"],["excluded"]],
  ["12월 8일, 15일은 방문 제외일로 지정할 생각입니다.",["excluded"],["excluded"]],
  ["12월 8일, 15일은 방문 제외일로 지정하면 종료일은 12월 26일로 정합니다.",["end","excluded"],["end","excluded"]],
  ["12월 8일. 15일은 방문 제외일로 지정합니다.",["end","excluded","start"],[]],
  ["12월 8일; 15일은 방문 제외일로 지정합니다.",["end","excluded","start"],[]],
  ["12월 8일\n15일은 방문 제외일로 지정합니다.",["end","excluded","start"],[]],
  ["12월 8일, 15일은 방문 제외일로 지정합니다. 시작일은 처음 후보 그대로입니다.",["excluded","start"],[]],
] as [string,string[],string[]][])test("ownership boundary "+text,()=>{
  const i=requestIntent("period",text);
  assert.deepEqual([...i.requested].sort(),fields);
  assert.deepEqual([...i.blocked].sort(),blocked);
});
for(const [text,value] of [
  ["12월 8일, 15일은 방문 제외일로 지정할까요?",[d(8),d(15)]],
  ["12월 8일, 15일은 방문 제외일로 지정하지 마세요.",[d(8),d(15)]],
  ["12월 8일, 15일은 방문 제외일로 지정할게요. 제외 요청은 취소합니다.",[d(8),d(15)]],
  ["12월 8일, 15일은 방문 제외일로 지정할 생각입니다.",[d(8),d(15)]],
] as const)test("risky excluded independent carry "+text,async()=>{
  const h=fixture("period",period);try{
    await seedPeriod(h);
    const r=h.start(text);
    assert.equal(r.rule_fields.find(f=>f.field==="excluded")?.action,"keep");
    await h.reply([carry(r,"start"),carry(r,"end"),set(r,"excluded",[...value])]);
    assert(!offered(h.field("excluded")));assert.ok(h.holds.excluded);
    h.apply(priorPeriod);assert.ok(h.holds.excluded);
  }finally{h.session.dispose();}
});

for(const [text,expected,blocked] of [
  ["방문 제외 날짜는 12월 7일, 14일, 19일 사흘로 정해주세요.",[d(7),d(14),d(19)],false],
  ["방문 제외일은 12월 7일, 14일, 19일 세 날로 지정합니다.",[d(7),d(14),d(19)],false],
  ["방문 제외일은 12월 7일, 14일, 19일 총 3일로 확정합니다.",[d(7),d(14),d(19)],false],
  ["방문 제외일은 12월 7일, 14일, 19일 이틀로 정해주세요.",[],true],
  ["방문 제외일은 12월 7일, 14일, 19일 사흘로 정할까요?",[],true],
  ["방문 제외일은 12월 7일, 14일, 19일 사흘로 지정하지 마세요.",[],true],
  ["방문 제외일은 12월 7일, 14일, 19일 사흘로 정해주세요. 제외일 변경은 철회합니다.",[],true],
] as [string,string[],boolean][]){
  for(const offline of [true,false])test("counted exclusion continuation "+text+" offline="+offline,async()=>{
    const h=fixture("period",period);
    try{
      const i=requestIntent("period",text);
      assert.deepEqual([...i.requested].sort(),["excluded"]);
      assert.equal(scopedFieldParts("period",text,"start").length,0);
      assert.equal(scopedFieldParts("period",text,"end").length,0);
      const r=h.start(text);
      assert.deepEqual(Object.keys(h.holds),["excluded"]);
      const candidate=r.rule_fields.find(f=>f.field==="excluded")!;
      assert.equal(candidate.action,blocked?"keep":"set");
      if(!blocked)assert.deepEqual(candidate.value,expected);
      if(offline)await h.reply([],true);
      else await h.reply([set(r,"excluded",[d(7),d(14),d(19)])]);
      assert.equal(offered(h.field("excluded")),!blocked);
      h.apply(blocked?{}:{excluded:expected});
      assert.equal(h.holds.start,undefined);
      assert.equal(h.holds.end,undefined);
      if(blocked)assert.ok(h.holds.excluded);
    }finally{h.session.dispose();}
  });
}


for(const [field,label,value,otherClause,withdrawal,keep] of [
  ["reason","사유는","바탕면 변색","추가금은 73만 원으로 지정해 주세요.","추가금 변경 요청은 취소합니다.",true],
  ["title","추가 작업은","창틀 하부 보강","추가금액은 73만 원으로 정합니다.","추가금액 후보는 철회합니다.",true],
  ["reason","추가 사유는","접합부 이격","공사비는 73만 원으로 정합니다.","공사비 제안은 보류합니다.",true],
  ["reason","사유는","바탕면 변색","부가세는 별도로 정합니다.","세금 변경 요청은 취소합니다.",true],
  ["title","추가 작업은","창틀 하부 보강","사유는 접합부 이격으로 정합니다.","추가 사유 변경은 철회합니다.",true],
  ["reason","사유는","바탕면 변색","","추가 사유 변경 요청은 취소합니다.",false],
  ["title","추가 작업은","창틀 하부 보강","","추가 작업 후보는 철회합니다.",false],
  ["reason","사유는","접합부 이격","","그 요청은 취소합니다.",false],
  ["title","항목명은","창틀 하부 보강","","아직 보류합니다.",false],
  ["reason","사유는","접합부 이격","","추가금과 사유 변경 요청은 취소합니다.",false],
] as const){
  test("canonical withdrawal ownership "+field+" "+withdrawal,async()=>{
    const h=fixture("extra",extra),clause=label+" "+value+"으로 확정합니다.",text=[clause,otherClause,withdrawal].filter(Boolean).join(" ");
    try{
      const r=h.start(text);
      assert.equal(r.rule_fields.find(f=>f.field===field)?.action,keep?"set":"keep");
      if(keep)assert.equal(r.rule_fields.find(f=>f.field===field)?.value,value);
      await h.reply([],true);
      assert.equal(offered(h.field(field)),keep);
      h.apply(keep?{[field]:value}:{});
      if(keep)assert.equal(h.holds[field],undefined);
      else assert.ok(h.holds[field]);
    }finally{h.session.dispose();}
  });
}


for(const lead of ["조건이 맞으면","확인되면","상태가 괜찮다면","공사 진행이 가능하면"]){
  test("qualified title conditional ownership "+lead,async()=>{
    const h=fixture("extra",extra),value="도어 테두리 정리",clause=lead+" 추가 작업은 "+value+"로 지정해 주세요.";
    const amountText="추가금은 73만 원으로 확정합니다.";
    try{
      const r=h.start(amountText+" "+clause);
      assert.equal(r.rule_fields.find(f=>f.field==="amount")?.value,730000);
      assert.equal(r.rule_fields.find(f=>f.field==="title")?.action,"keep");
      await h.reply([set(r,"amount",730000,amountText),set(r,"title",value,clause)]);
      assert(!offered(h.field("title")));h.apply({amount:730000});assert.ok(h.holds.title);
    }finally{h.session.dispose();}
  });
  test("postposed excluded conditional ownership "+lead,async()=>{
    const h=fixture("period",period),clause=lead+" 12월 10일, 17일은 방문 제외 날짜로 지정해 주세요.";
    try{
      const text="종료일은 12월 26일로 확정합니다. "+clause;
      const i=requestIntent("period",text);
      assert.deepEqual([...i.requested].sort(),["end","excluded"]);
      assert.deepEqual([...i.blocked].sort(),["excluded"]);
      const r=h.start(text);await h.reply([set(r,"end",d(26),"종료일은 12월 26일로 확정합니다"),set(r,"excluded",[d(10),d(17)],clause)]);
      assert(!offered(h.field("excluded")));h.apply({end:d(26)});assert.ok(h.holds.excluded);
    }finally{h.session.dispose();}
  });
}

for(const text of ["2026년 11월 31일, 12월 15일은 제외일로 정합니다.","12월 8일, 8일은 제외일로 정합니다.","12월 8일, 15일은 제외일로 지정할 수 있나요?"]){
  test("invalid list "+text,async()=>{
    const h=fixture("period",period);try{
      const r=h.start(text);assert.notEqual(r.rule_fields.find(f=>f.field==="excluded")?.action,"set");
      await h.reply([],true);h.apply({});assert.ok(h.holds.excluded);
    }finally{h.session.dispose();}
  });
}
const old=[d(5),d(18)],next=[d(5),d(21)];
const specs=[
  {form:"dates" as const,current:dates,field:"days",label:"작업 날짜",other:"crew",otherValue:4,otherText:"작업 인원은 네 명으로 정합니다.",seed:"작업 날짜는 12월 5일과 18일로 정합니다."},
  {form:"period" as const,current:period,field:"excluded",label:"방문 제외일",other:"end",otherValue:d(27),otherText:"종료일은 12월 27일로 정합니다.",seed:"방문 제외일은 12월 5일과 18일로 정합니다."},
];
function corrected(r:AssistRequest,field:string,value:AssistValue=next,rejected:AssistValue=old,oldEvidence="18일",newEvidence="21일",evidence=r.text):CandidateField{
  return {...set(r,field,value,evidence),source:"rule",value_source:"derived",correction:{kind:"replace",rejected_value:rejected,rejected_evidence:oldEvidence,accepted_evidence:newEvidence}};
}
for(const c of specs){
  for(const ending of ["옮겨 주세요.","옮겨 주실래요?","옮겨 주시겠어요?","변경해 주시겠습니까?","바꿔 주실 수 있을까요?","옮길까요?","옮겨도 되나요?","옮겨 주실 수 있나요?","안 옮겨 주실래요?","못 옮겨 주시겠어요?","옮기지 말아 주세요.","옮길 수 있는지 알려 주세요."]){
    const good=["옮겨 주세요.","옮겨 주실래요?","옮겨 주시겠어요?","변경해 주시겠습니까?","바꿔 주실 수 있을까요?"].includes(ending);
    test(c.form+" polite move "+ending,async()=>{
      const h=fixture(c.form,c.current);try{
        await h.send(c.seed,r=>[set(r,c.field,old)]);
        const text="앞서 제안한 "+c.label+"에서 18일을 21일로 "+ending;
        const r=h.start(text);assert.equal(r.rule_fields.find(f=>f.field===c.field)?.action,"keep");
        await h.reply([corrected(r,c.field)]);
        assert.equal(offered(h.field(c.field)),good);
        h.apply(good?{[c.field]:next}:{});
        if(!good)assert.ok(h.holds[c.field]);
      }finally{h.session.dispose();}
    });
  }
  for(const mode of ["missing-prior","stale-prior","manual-during","wrong-base","wrong-origin","wrong-source","swapped-evidence","outside-evidence","wrong-delta","extra-member","wrong-field-evidence","missing-proof","offline","current","carry"] as const)test(c.form+" scoped replacement proof "+mode,async()=>{
    const h=fixture(c.form,c.current);
    try{
      if(mode!=="missing-prior"&&mode!=="current")await h.send(c.seed,r=>[set(r,c.field,old)]);
      if(mode==="stale-prior")h.manual(c.field,c.current[c.field]);
      const fromCurrent=mode==="current";
      const text=(fromCurrent?"현재 달력에 입력된 ":"앞서 제안한 ")+c.label+"에서 "+(fromCurrent?"16일을 21일":"18일을 21일")+"로 옮겨 주실래요? "+c.otherText;
      const r=h.start(text);
      if(mode==="manual-during")h.manual(c.field,c.current[c.field]);
      if(mode==="stale-prior"||mode==="missing-prior")assert(!r.previous_candidate?.fields.some(f=>f.field===c.field));
      let f=corrected(r,c.field,fromCurrent?[d(9),d(21)]:next,fromCurrent?c.current[c.field]:old,fromCurrent?"16일":"18일");
      if(mode==="wrong-base")f.correction!.rejected_value=[d(7),d(18)];
      if(mode==="wrong-origin")f.origin_request_id=r.previous_candidate!.request_id;
      if(mode==="wrong-source")f.value_source="utterance";
      if(mode==="swapped-evidence"){f.correction!.rejected_evidence="21일";f.correction!.accepted_evidence="18일";}
      if(mode==="outside-evidence")f.correction!.accepted_evidence="12월 21일";
      if(mode==="wrong-delta")f.value=[d(6),d(21)];
      if(mode==="extra-member")f.value=[d(5),d(22)];
      if(mode==="wrong-field-evidence")f.evidence=c.otherText;
      if(mode==="missing-proof")delete f.correction;
      if(mode==="offline")await h.reply([],true);else await h.reply([f,set(r,c.other,c.otherValue,c.otherText)]);
      if(mode==="current"){h.apply({[c.field]:[d(9),d(21)],[c.other]:c.otherValue});return;}
      if(mode==="carry"){
        assert.equal(h.field(c.field).value_source,"derived");
        const r3=await h.send(c.otherText,q=>[carry(q,c.field),set(q,c.other,c.otherValue)]);
        assert.equal(r3.previous_candidate?.origins?.[c.field],r.request_id);
        h.apply({[c.field]:next,[c.other]:c.otherValue});return;
      }
      if(mode==="manual-during"){h.apply({},[c.field,c.other]);h.apply({[c.other]:c.otherValue},[c.other]);assert.deepEqual(h.live().current[c.field],c.current[c.field]);return;}
      assert(!offered(h.field(c.field)));
      if(mode==="offline"){assert.ok(h.holds[c.field]);h.apply({[c.other]:c.otherValue});}
      else{h.apply({[c.other]:c.otherValue});assert.ok(h.holds[c.field]);}
    }finally{h.session.dispose();}
  });
}
for(const [form,current,field,prefix,value,other,otherValue,otherText] of [
  ["quote",quote,"valid","견적 유효기간은 12월 26일까지","2026-12-26","amount",6300000,"견적 금액은 630만 원으로 확정합니다."],
  ["period",period,"end","종료일은 12월 26일로","2026-12-26","excluded",[d(12)],"12월 12일은 제외일로 지정합니다."],
  ["dates",dates,"days","작업일은 12월 6일과 20일로",[d(6),d(20)],"crew",4,"작업 인원은 네 명으로 정합니다."],
  ["extra",extra,"reason","추가 사유는 12월 점검 후 누수 확인으로","12월 점검 후 누수 확인","amount",830000,"추가 비용은 83만 원으로 정합니다."],
] as [AssistForm,AssistCurrent,string,string,AssistValue,string,AssistValue,string][]){
  for(const suffix of ["변경해 주실래요?","정해 주시겠습니까?","안 바꿔 주실래요?","변경해도 되나요?","변경해 주실 수 있나요?"]){
    const good=suffix==="변경해 주실래요?"||suffix==="정해 주시겠습니까?";
    test(form+" independent polite scalar "+suffix,async()=>{
      const h=fixture(form,current),clause=prefix+" "+suffix,text=clause+" "+otherText;
      try{
        await h.send(text,r=>[set(r,field,value,clause),set(r,other,otherValue,otherText)]);
        assert.equal(offered(h.field(field)),good);
        h.apply({...good?{[field]:value}:{},[other]:otherValue});
        if(!good)assert.ok(h.holds[field]);
      }finally{h.session.dispose();}
    });
  }
}

for(const [text,partial] of [
  ["12월 8일, 123일, 15일은 제외일로 정합니다.",[d(8),d(15)]],
  ["12월 123일, 15일은 제외일로 정합니다.",[d(15)]],
  ["12월 8일, 8일, 15일은 제외일로 정합니다.",[d(8),d(15)]],
  ["12월 8일, 15일, 23일은 제외일로 정합니다.",[d(8),d(15)]],
] as [string,string[]][])test("whole owned list rejects partial "+text,async()=>{
  const h=fixture("period",period);
  try{
    await seedPeriod(h);
    const r=h.start(text);
    await h.reply([carry(r,"start"),carry(r,"end"),set(r,"excluded",partial)]);
    assert(!offered(h.field("excluded")));assert.ok(h.holds.excluded);
    h.apply(priorPeriod);assert.ok(h.holds.excluded);
  }finally{h.session.dispose();}
});
