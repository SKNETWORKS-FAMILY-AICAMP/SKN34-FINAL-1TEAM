// Frozen independent controls; source override is for isolated comparison with the prior snapshot.
import assert from "node:assert/strict";
import {test} from "node:test";
import {resolve} from "node:path";
import {pathToFileURL} from "node:url";
import type {AssistCurrent,AssistRequest,AssistValue,CandidateField} from "./protocol.ts";
const source=process.env.QA179_R6INTEGRATED_FE_SOURCE;
const local=(name:string)=>source?pathToFileURL(resolve(source,name+".ts")).href:new URL(name+".ts",import.meta.url).href;
const {AssistSession}=await import(local("session"));
const {parseExtra}=await import(local("parse"));
const {formKeys}=await import(local("protocol"));
for(const name of ["session","parse","requestIntent","protocol"])console.log("SOURCE_IMPORT",local(name));
const today="2026-11-25",opts={today,vatModes:["included","excluded","exempt"]};
const initial:AssistCurrent={title:"보강",reason:"현장 추가",amount:1000000,vat:"included"};
const set=(r:AssistRequest,field:string,value:AssistValue,evidence=r.text):CandidateField=>({
  field,action:"set",value,evidence,status:"valid",source:"llm",alternatives:[],intent_kind:"explicit_change",value_source:"utterance",origin_request_id:r.request_id});
const noMention=(r:AssistRequest,field:string):CandidateField=>({
  field,action:"keep",value:null,evidence:"",status:"valid",source:"rule",alternatives:[],intent_kind:"no_mention",origin_request_id:r.request_id});
function carry(r:AssistRequest,field:string):CandidateField{
  const p=r.previous_candidate,f=p?.fields.find(f=>f.field===field);assert(p&&f);
  return {...f,status:"valid",source:p.sources![field],origin_request_id:p.origins![field],alternatives:[],intent_kind:"retain_previous",value_source:"previous"};
}
function conflict(field:string,alts:{value:string;evidence:string;source:"rule"|"llm"}[]):CandidateField{
  return {field,action:"uncertain",value:null,evidence:"",status:"conflict",source:"llm",alternatives:alts.map(a=>({...a,action:"set"}))};
}
const offered=(f:CandidateField)=>f.status==="conflict"||f.status==="valid"&&["set","clear"].includes(f.action);
function fixture(){
  let current=structuredClone(initial),writes=0,pending=false;
  const holds:Record<string,string>={},versions:Record<string,number>={};
  let job:{r:AssistRequest;resolve:(v:unknown)=>void;reject:(e:Error)=>void};
  const session=new AssistSession("extra","offline-value-span",(r:AssistRequest)=>new Promise((resolve,reject)=>job={r:structuredClone(r),resolve,reject}));
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
    if(offline)job.reject(new Error("offline"));else job.resolve({form:"extra",request_id:job.r.request_id,source:"llm",warnings:[],
      fields:formKeys("extra").map((f:string)=>fields.find(x=>x.field===f)??noMention(job.r,f))});
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
const cases=[
  ["title","추가 작업은 ","걸레받이 모서리 보수"],
  ["title","추가 공사는 ","창틀 하부 보완"],
  ["title","추가 시공: ","문짝 하부 실링"],
  ["title","추가 작업 항목명은 ","경첩 주변 판재 교체"],
  ["title","추가 공사 항목 제목은 ","문틀 하부 마감"],
  ["title","추가 시공 항목 이름은 ","주방 몰딩 보완"],
  ["title","추가 작업명은 ","걸쇠 받침 보수"],
  ["title","공사명은 ","판재 이음부 보강"],
  ["reason","추가 작업 사유는 ","고정 부위의 흔들림"],
  ["reason","추가 시공 이유는 ","벽면 단차 확인"],
  ["reason","추가 비용 사유는 ","기존 실측 오차"],
  ["reason","추가 공사 이유: ","목재 끝부분 들뜸"],
] as const;
for(const [field,label,value] of cases)for(const suffix of ["","으로 지정해 주세요","으로 등록할 후보를 부탁합니다"]){
  test("exact "+label+suffix,async()=>{
    const h=fixture(),clause=label+value+suffix,text=clause+". 추가 비용 83만 원으로 확정합니다.";
    try{
      assert.equal(parseExtra(text,today)[field],value);
      const r=h.start(text);assert.equal(r.rule_fields.find(f=>f.field===field)?.value,value);
      await h.reply([set(r,field,value,clause),set(r,"amount",830000,"83만 원")]);h.apply({[field]:value,amount:830000});
      assert.deepEqual(h.holds,{});
    }finally{h.session.dispose();}
  });
}
for(const mode of ["correct","stored-conflict","whole","clip","offline"] as const)test("called extra-12 "+mode,async()=>{
  const h=fixture(),text="추가 작업은 천장 가장자리 보강, 추가 사유는 이음부 들뜸으로 먼저 적어주세요.";
  const value="천장 가장자리 보강",bad="추가 작업은 "+value;
  try{
    const r=h.start(text);assert.equal(r.rule_fields.find(f=>f.field==="title")?.value,value);
    if(mode==="offline")await h.reply([],true);
    else await h.reply([mode==="stored-conflict"?conflict("title",[{value:bad,evidence:text,source:"rule"},{value,evidence:bad,source:"llm"}]):
      set(r,"title",mode==="whole"?bad:mode==="clip"?"가장자리 보강":value,bad),set(r,"reason","이음부 들뜸","추가 사유는 이음부 들뜸으로")]);
    if(mode==="correct"||mode==="offline")h.apply({title:value,reason:"이음부 들뜸"});
    else{assert(!offered(h.field("title")));assert.ok(h.holds.title);h.session.chooseAlternative("title",0);h.apply({reason:"이음부 들뜸"});assert.ok(h.holds.title);}
  }finally{h.session.dispose();}
});
for(const [field,label,value] of [cases[0],cases[8]]){
  for(const mode of ["whole","clip-start","clip-end","bad-evidence","bad-suffix","question","condition","negation","withdrawal","incomplete","competing","conflict","bad-conflict","literal","prior","stale","manual"] as const)test(field+" proof "+mode,async()=>{
    const h=fixture(),clause=label+value,alt="몰딩 아래쪽 단차 보완";
    let text=clause+". 추가 비용 83만 원으로 확정합니다.",v=value;
    if(mode==="whole")v=(label+value) as typeof value;
    if(mode==="clip-start")v=value.split(" ").slice(1).join(" ") as typeof value;
    if(mode==="clip-end")v=value.split(" ").slice(0,-1).join(" ") as typeof value;
    if(mode==="bad-suffix"){text=clause+"으로 지정해 주세요. 추가 비용 83만 원으로 확정합니다.";v=(value+"으로 지정해 주세요") as typeof value;}
    if(mode==="question")text=clause+"으로 할까요? 추가 비용 83만 원으로 확정합니다.";
    if(mode==="condition")text="가능하면 "+clause+"으로 정합니다. 추가 비용 83만 원으로 확정합니다.";
    if(mode==="negation")text=clause+"으로 정하지 마세요. 추가 비용 83만 원으로 확정합니다.";
    if(mode==="withdrawal")text=clause+"으로 정합니다. "+label+"취소합니다. 추가 비용 83만 원으로 확정합니다.";
    if(mode==="incomplete")text=clause+"으로 등록할 후보. 추가 비용 83만 원으로 확정합니다.";
    if(mode==="competing"||mode==="conflict"||mode==="bad-conflict")text=clause+". "+label+alt+". 추가 비용 83만 원으로 확정합니다.";
    if(mode==="literal"){v=(label+value) as typeof value;text=label+'“'+v+'”로 정합니다. 추가 비용 83만 원으로 확정합니다.';}
    try{
      const r=h.start(text);
      if(mode==="manual")h.manual(field,"수동 값");
      let f=set(r,field,v,mode==="bad-evidence"?"83만 원":text);
      if(mode==="conflict"||mode==="bad-conflict")f=conflict(field,[{value,evidence:clause,source:"llm"},
        {value:mode==="bad-conflict"?label+alt:alt,evidence:label+alt,source:"rule"}]);
      await h.reply([f,set(r,"amount",830000,"83만 원")]);
      if(mode==="literal"){h.apply({[field]:v,amount:830000});return;}
      if(mode==="conflict"){assert.equal(h.field(field).status,"conflict");assert.equal(h.session.view.choices[field].selected,false);h.session.chooseAlternative(field,1);h.apply({[field]:alt,amount:830000});return;}
      if(mode==="manual"){h.apply({},[field,"amount"]);h.apply({amount:830000},["amount"]);assert.equal(h.live().current[field],"수동 값");return;}
      if(mode==="prior"||mode==="stale"){
        assert.equal(h.field(field).value,value);
        const saved=h.field(field);
        if(mode==="stale")h.manual(field,"수동 값");
        const r2=h.start(label+"처음 후보 그대로입니다.");
        if(mode==="prior"){assert.equal(r2.previous_candidate?.origins?.[field],r.request_id);await h.reply([carry(r2,field),carry(r2,"amount")]);h.apply({[field]:value,amount:830000});}
        else {assert(!r2.previous_candidate?.fields.some(f=>f.field===field));await h.reply([{...saved,intent_kind:"retain_previous",value_source:"previous"},carry(r2,"amount")]);assert(!offered(h.field(field)));h.apply({amount:830000});}
        return;
      }
      assert(!offered(h.field(field)));assert.ok(h.holds[field]);h.apply({amount:830000});assert.ok(h.holds[field]);
    }finally{h.session.dispose();}
  });
}
for(const [field,label,literal] of [
  ["title","추가 작업은 ","추가 작업은 모서리 보완, 사유는 미정, 비용은 21만 원"],
  ["reason","추가 작업 사유는 ","추가 시공은 목재 마감, 금액은 21만 원"],
] as const)test(field+" quote scope and independent scalar",async()=>{
  const h=fixture(),clause=label+'“'+literal+'”로 확정합니다.',text=clause+" 추가 비용 83만 원으로 확정합니다.";
  try{
    assert.deepEqual(parseExtra(text,today),{[field]:literal,amount:830000});
    const r=h.start(text);await h.reply([set(r,field,literal,clause),set(r,"amount",830000,"83만 원"),
      set(r,field==="title"?"reason":"title","모서리 보완","추가 작업은 모서리 보완")]);
    assert.deepEqual(Object.keys(h.holds).sort(),[field,"amount"].sort());h.apply({[field]:literal,amount:830000});
  }finally{h.session.dispose();}
});
for(const [field,label,value] of [
  ["title","추가 작업은 ","이름표 부착판 보강"],
  ["title","추가 공사는 ","사유지 경계 마감"],
  ["reason","추가 작업 이유는 ","작업명판 파손"],
  ["reason","추가 작업 사유는 ","구조적으로 안정"],
  ["title","추가 작업은 ","추가 작업대 보수"],
  ["title","추가 작업은 ","추가 공사비 산출표 설치"],
] as const)test("nominal "+value,async()=>{
  const h=fixture(),text=label+value+".";
  try{
    assert.equal(parseExtra(text,today)[field],value);
    const r=h.start(text);await h.reply([set(r,field,value,text)]);h.apply({[field]:value});
  }finally{h.session.dispose();}
});
for(const value of ["추가 작업대 보강","작업 장비 받침 보완","추가 작업 위치 점검"]){
  test("ordinary free nominal "+value,async()=>{
    const h=fixture(),text=value+", 83만 원";
    try{
      const r=h.start(text);assert.equal(r.rule_fields.find(f=>f.field==="title")?.value,value);
      await h.reply([set(r,"title",value,value),set(r,"amount",830000,"83만 원")]);h.apply({title:value,amount:830000});
    }finally{h.session.dispose();}
  });
}

for(const [prefix,expected] of [
  ["추가 작업명은 문틀 보수로","문틀 보수"],
  ["추가 작업은 몰딩 교체로","몰딩 교체"],
  ["추가 시공은 창틀 보강으로","창틀 보강"],
  ["추가 공사는 “통로”로","통로"],
  ["추가 작업은 비상 통로","비상 통로"],
  ["추가 작업은 제어 회로","제어 회로"],
  ["추가 작업은 보강 통로","보강 통로"],
] as const)test("shared terminal "+prefix,async()=>{
  const h=fixture(),text=prefix+", 추가 작업 사유는 접합부 이격으로 제안합니다.";
  try{
    assert.equal(parseExtra(text,today).title,expected);
    const r=h.start(text);assert.equal(r.rule_fields.find(f=>f.field==="title")?.value,expected);
    await h.reply([set(r,"title",expected,prefix),set(r,"reason","접합부 이격","추가 작업 사유는 접합부 이격으로 제안합니다")]);
    h.apply({title:expected,reason:"접합부 이격"});
  }finally{h.session.dispose();}
});
for(const [prefix,expected] of [
  ["추가 작업은 보수로","보수로"],
  ["추가 작업은 창틀 보강으로","창틀 보강으로"],
  ["추가 작업은 비상 통로","비상 통로"],
] as const)test("no shared terminal "+prefix,async()=>{
  const h=fixture(),text=prefix+". 추가 비용 83만 원으로 정합니다.";
  try{
    assert.equal(parseExtra(text,today).title,expected);
    await h.send(text,r=>[set(r,"title",expected,prefix),set(r,"amount",830000,"83만 원")]);h.apply({title:expected,amount:830000});
  }finally{h.session.dispose();}
});
for(const [field,first,second,value] of [
  ["title","추가 작업은 선반 고정","추가 작업 변경은 철회해 주세요.","선반 고정"],
  ["title","추가 공사는 선반 고정","추가 공사 요청은 취소합니다.","선반 고정"],
  ["reason","추가 작업 사유는 실측 오차","추가 작업 사유는 취소합니다.","실측 오차"],
] as const)test("compound withdrawal "+field+" "+second,async()=>{
  const h=fixture(),text=first+". "+second+" 추가 비용 83만 원으로 정합니다.";
  try{
    const r=h.start(text);assert.notEqual(r.rule_fields.find(f=>f.field===field)?.action,"set");
    await h.reply([set(r,field,value,first),set(r,"amount",830000,"83만 원")]);assert(!offered(h.field(field)));h.apply({amount:830000});
  }finally{h.session.dispose();}
});
test("nominal replacement competes with first candidate",async()=>{
  const h=fixture();try{
    await h.send("추가 작업은 선반 보강.",r=>[set(r,"title","선반 보강")]);
    await h.send("추가 작업은 처음 후보 그대로입니다. 추가 작업은 하부 몰딩 보수. 추가 비용 83만 원으로 정합니다.",r=>[
      carry(r,"title"),set(r,"amount",830000,"83만 원")]);
    assert(!offered(h.field("title")));assert.ok(h.holds.title);h.apply({amount:830000});
  }finally{h.session.dispose();}
});
test("qualified conditional cannot leak old or new title",async()=>{
  const h=fixture(),text="만약 추가 작업은 문짝 보강으로 정합니다. 추가 비용 83만 원으로 정합니다.";
  try{
    const r=h.start(text);assert.notEqual(r.rule_fields.find(f=>f.field==="title")?.action,"set");
    await h.reply([set(r,"title","문짝 보강"),set(r,"amount",830000,"83만 원")]);assert(!offered(h.field("title")));h.apply({amount:830000});
  }finally{h.session.dispose();}
});

for(const [field,label] of [cases[0],cases[8]])for(const value of ["항목 이름표 손상","사유 이음부 확인","작업명판 파손"]){
  test("marker boundary "+field+" "+value,async()=>{
    const h=fixture(),text=label+value+". 추가 비용 83만 원으로 확정합니다.";
    try{
      assert.equal(parseExtra(text,today)[field],value);
      const r=h.start(text);assert.equal(r.rule_fields.find(f=>f.field===field)?.value,value);
      await h.reply([set(r,field,value,label+value),set(r,"amount",830000,"83만 원")]);h.apply({[field]:value,amount:830000});
    }finally{h.session.dispose();}
  });
}
for(const [field,label,value] of [cases[0],cases[8]])for(const report of ["으로 전에 입력했습니다","이라고 아까 말했어요","으로 과거에 기록했습니다"]){
  test("past report "+field+" "+report,async()=>{
    const h=fixture(),text="추가 비용 83만 원으로 확정합니다. "+label+value+report+".";
    try{
      const r=h.start(text);assert.notEqual(r.rule_fields.find(f=>f.field===field)?.action,"set");
      await h.reply([set(r,field,value,label+value+report),set(r,"amount",830000,"83만 원")]);assert(!offered(h.field(field)));h.apply({amount:830000});assert.ok(h.holds[field]);
    }finally{h.session.dispose();}
  });
}

for(const label of ["사유 칸에","추가 작업 이유는"])for(const suffix of ["써 놓은 건 전부 지워서 빈칸으로 두세요","비워서 빈칸으로 두세요"]){
  test("master clear request is not a nominal value "+label+" "+suffix,async()=>{
    const h=fixture(),clause=label+" "+suffix,text=clause+". 추가 비용 83만 원으로 확정합니다.";
    try{
      const r=h.start(text);assert.notEqual(r.rule_fields.find(f=>f.field==="reason")?.action,"set");
      await h.reply([{...set(r,"reason",null,clause),action:"clear"},set(r,"amount",830000,"83만 원")]);
      assert.equal(h.field("reason").action,"clear");
      h.apply({reason:null,amount:830000},["reason","amount"]);
    }finally{h.session.dispose();}
  });
  test("master forged clear SET blocked "+label+" "+suffix,async()=>{
    const h=fixture(),clause=label+" "+suffix,text=clause+". 추가 비용 83만 원으로 확정합니다.";
    try{
      await h.send(text,r=>[set(r,"reason",suffix,clause),set(r,"amount",830000,"83만 원")]);
      assert(!offered(h.field("reason")));h.apply({amount:830000});assert.ok(h.holds.reason);
    }finally{h.session.dispose();}
  });
  test("master clipped clear SET blocked "+label+" "+suffix,async()=>{
    const h=fixture(),clause=label+" "+suffix;
    try{
      await h.send(clause,r=>[set(r,"reason","빈칸",clause)]);assert(!offered(h.field("reason")));h.apply({});
    }finally{h.session.dispose();}
  });
}
