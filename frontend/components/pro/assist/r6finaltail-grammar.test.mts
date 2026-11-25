// Frozen independent offline regression controls. The source override is only for isolated snapshot comparison.
import assert from "node:assert/strict";
import { test } from "node:test";
import { pathToFileURL } from "node:url";
import { resolve } from "node:path";
import type { AssistForm, AssistCurrent, AssistRequest, AssistValue, CandidateField, RuleOptions } from "./protocol.ts";
const source = process.env.QA179_R6FINALTAIL_FE_SOURCE;
const local = (name:string) => source ? pathToFileURL(resolve(source,name+".ts")).href : new URL(name+".ts",import.meta.url).href;
const { AssistSession } = await import(local("session"));
const { parseExtra } = await import(local("parse"));
const { formKeys } = await import(local("protocol"));
for (const name of ["session","parse","requestIntent","protocol"]) console.log("SOURCE_IMPORT",local(name));

const today="2026-11-25";
const opts:RuleOptions={today,baseMonth:"2026-12-01",daysMax:120,crewMax:6,requiredDays:2,vatModes:["included","excluded","exempt"]};
const extra:AssistCurrent={title:"보강",reason:"현장 추가",amount:1000000,vat:"included"};
const fresh=(r:AssistRequest,field:string,value:AssistValue,evidence=r.text):CandidateField=>({
  field,action:"set",value,evidence,status:"valid",source:"llm",alternatives:[],
  intent_kind:"explicit_change",value_source:"utterance",origin_request_id:r.request_id});
const noMention=(r:AssistRequest,field:string):CandidateField=>({
  field,action:"keep",value:null,evidence:"",status:"valid",source:"rule",alternatives:[],
  intent_kind:"no_mention",origin_request_id:r.request_id});
const keep=(r:AssistRequest,field:string):CandidateField=>({...noMention(r,field),evidence:r.text,intent_kind:"retain_current",value_source:"current"});
function carried(r:AssistRequest,field:string):CandidateField {
  const prior=r.previous_candidate,old=prior?.fields.find(f=>f.field===field);
  assert(prior&&old);
  return {...old,status:"valid",source:prior.sources![field],origin_request_id:prior.origins![field],
    alternatives:[],intent_kind:"retain_previous",value_source:"previous"};
}
function conflict(field:string,alts:{value:string;evidence:string;source:"rule"|"llm"}[]):CandidateField {
  return {field,action:"uncertain",value:null,evidence:"",status:"conflict",source:"llm",alternatives:alts.map(x=>({...x,action:"set"}))};
}
const offered=(f:CandidateField)=>f.status==="conflict"||f.status==="valid"&&["set","clear"].includes(f.action);
function harness(form:AssistForm,initial:AssistCurrent) {
  let current=structuredClone(initial),writes=0,pending=false;
  const versions:Record<string,number>={},holds:Record<string,string>={};
  let job:{r:AssistRequest;resolve:(v:unknown)=>void;reject:(e:Error)=>void};
  const session=new AssistSession(form,"offline-r6finaltail-grammar",(r:AssistRequest)=>new Promise((resolve,reject)=>{job={r:structuredClone(r),resolve,reject};}));
  const live=()=>({current:structuredClone(current),manualVersions:{...versions}});
  function start(text:string) {
    const before=structuredClone(current),count=writes;
    assert(session.send(text,current,opts,(issues:{pending?:boolean;fieldHolds?:Record<string,string|null>})=>{
      if(typeof issues.pending==="boolean")pending=issues.pending;
      for(const [k,v] of Object.entries(issues.fieldHolds??{})) if(v===null)delete holds[k];else holds[k]=v;
    },live));
    assert(pending);assert.equal(session.apply(current,()=>assert.fail("early Apply")),false);
    assert.deepEqual(current,before);assert.equal(writes,count);return job.r;
  }
  async function reply(fields:CandidateField[]=[],offline=false) {
    const before=structuredClone(current),count=writes;
    if(offline)job.reject(new Error("offline"));else job.resolve({form,request_id:job.r.request_id,source:"llm",warnings:[],
      fields:formKeys(form).map((k:string)=>fields.find(f=>f.field===k)??noMention(job.r,k))});
    await Promise.resolve();await Promise.resolve();
    assert.equal(session.view.phase,"ready");assert.equal(pending,false);
    assert.deepEqual(current,before);assert.equal(writes,count);
  }
  async function send(text:string,make:(r:AssistRequest)=>CandidateField[]) {const r=start(text);await reply(make(r));return r;}
  function field(name:string):CandidateField {return session.view.response!.fields.find((f:CandidateField)=>f.field===name)!;}
  function apply(expected:AssistCurrent) {
    const count=writes;let patch:AssistCurrent={};
    const changed=Object.keys(expected).length>0;
    assert.equal(session.apply(current,(p:AssistCurrent)=>{writes++;patch=structuredClone(p);Object.assign(current,p);}),changed);
    assert.deepEqual(patch,expected);assert.equal(writes,count+Number(changed));
    if(changed)assert.equal(session.apply(current,()=>assert.fail("duplicate Apply")),false);
  }
  function manual(field:string,value:AssistValue){current[field]=value;versions[field]=(versions[field]??0)+1;delete holds[field];}
  return {session,start,reply,send,field,apply,manual,holds,live};
}
test("called extra-10 T1/T2/T3 actual evolving origin and two-field Apply",async()=>{
  const h=harness("extra",extra);
  try{
    const r1=await h.send("항목명은 도어 주변 실링, 금액은 53만 2천 원으로 먼저 제시합니다.",r=>[
      fresh(r,"title","도어 주변 실링","항목명은 도어 주변 실링"),fresh(r,"amount",532000,"금액은 53만 2천 원")]);
    await h.send("추가금은 55만원일지 59만원일지 아직 못 정했어요.",r=>[carried(r,"title"),{
      ...noMention(r,"amount"),action:"uncertain",status:"uncertain",evidence:r.text}]);
    assert.equal(h.field("title").origin_request_id,r1.request_id);assert(!offered(h.field("amount")));
    const r3=await h.send("추가 비용 57만 6천 원으로 결정했습니다. 항목명은 처음 후보 그대로입니다.",r=>[
      carried(r,"title"),fresh(r,"amount",576000,"추가 비용 57만 6천 원으로 결정했습니다")]);
    assert.equal(r3.previous_candidate?.origins?.title,r1.request_id);
    assert.equal(h.field("title").origin_request_id,r1.request_id);
    h.apply({title:"도어 주변 실링",amount:576000});assert.deepEqual(h.holds,{});
  }finally{h.session.dispose();}
});
for(const mode of ["correct","contaminated","stored-conflict","offline"] as const)test("called extra-11 T1 "+mode,async()=>{
  const h=harness("extra",extra),text="추가 사유는 모서리 균열 확인으로 등록할 후보 부탁합니다.",value="모서리 균열 확인";
  try{
    const r=h.start(text),wrong=value+"으로 등록할 후보 부탁합니다";
    assert.equal(r.rule_fields.find(f=>f.field==="reason")?.value,value);
    if(mode==="offline")await h.reply([],true);
    else if(mode==="correct")await h.reply([fresh(r,"reason",value,"추가 사유는 "+value)]);
    else if(mode==="contaminated")await h.reply([fresh(r,"reason",wrong)]);
    else await h.reply([conflict("reason",[{value:wrong,evidence:text,source:"rule"},{value,evidence:"추가 사유는 "+value,source:"llm"}])]);
    if(mode==="correct"||mode==="offline")h.apply({reason:value});
    else {assert(!offered(h.field("reason")));assert.ok(h.holds.reason);h.apply({});}
  }finally{h.session.dispose();}
});
const textCases=[["title","항목명은 ","창틀 하단 보완"],["reason","추가 사유는 ","실리콘 접합부 들뜸"]] as const;
const endings=["으로 등록할 후보 부탁합니다","으로 기재하는 초안을 요청드립니다","으로 다시 작성할 제안안 주세요",
  "으로 지정하기 위한 후보를 제시해 주세요","이라고 명확하게 기록할 초안을 만들어 주세요",
  "으로 정식으로 설정할 후보안을 보여 주십시오","으로 등록할 후보를 주시겠어요?"];
for(const [field,label,value] of textCases){
  for(const ending of endings){
    const phrase=label+value+ending;
    test(field+" full grammar "+ending,async()=>{
      const h=harness("extra",extra);try{
        assert.equal(parseExtra(phrase,today)[field],value);
        const r=h.start(phrase);assert.equal(r.rule_fields.find(f=>f.field===field)?.value,value);
        await h.reply([fresh(r,field,value,phrase)]);h.apply({[field]:value});
      }finally{h.session.dispose();}
    });
  }
  for(const mode of ["whole-command","clipped-command","clipped-value","foreign-evidence","missing-terminal","conditional","negative","question","cancel","offline","quoted","nominal","mixed","conflict","valid-conflict","manual"] as const){
    test(field+" independent "+mode,async()=>{
      const h=harness("extra",extra);
      const phrase=label+value+"으로 등록할 후보를 부탁드립니다";
      const other=field==="title"?"reason":"title",otherLabel=other==="reason"?"추가 사유는 ":"항목명은 ",otherValue="내부 접합 확인";
      let text=phrase+". 추가 비용 81만 원으로 확정합니다.";
      if(mode==="missing-terminal")text=label+value+"으로 등록할 후보. 추가 비용 81만 원으로 확정합니다.";
      if(mode==="conditional")text=label+value+"으로 등록할 후보를 부탁드려도 된다면";
      if(mode==="negative")text=label+value+"으로 등록할 후보는 부탁하지 마세요.";
      if(mode==="question")text=label+value+"으로 등록할 후보가 맞나요?";
      if(mode==="cancel")text=phrase+". "+label+"후보 취소합니다.";
      if(mode==="quoted")text=label+"“"+value+"으로 등록할 후보를 부탁드립니다”로 정합니다.";
      if(mode==="nominal")text=label+value+" 등록 후보 목록.";
      if(mode==="mixed")text=phrase+". "+otherLabel+otherValue+"으로 기재할 초안을 요청합니다.";
      if(mode==="valid-conflict")text=phrase+". "+label+"힌지 주변 조정으로 작성할 초안 부탁합니다.";
      if(mode==="manual")text=phrase+".";
      try{
        const r=h.start(text);
        if(mode==="manual")h.manual(field,"수동 값");
        if(mode==="offline")await h.reply([],true);
        else if(mode==="conflict")await h.reply([conflict(field,[{value,evidence:phrase,source:"llm"},{value:value+"으로 등록할 후보를 부탁드립니다",evidence:phrase,source:"rule"}]),fresh(r,"amount",810000,"81만 원")]);
        else if(mode==="valid-conflict")await h.reply([conflict(field,[{value,evidence:phrase,source:"llm"},{value:"힌지 주변 조정",evidence:label+"힌지 주변 조정으로 작성할 초안 부탁합니다",source:"rule"}])]);
        else {
          const v=mode==="whole-command"||mode==="missing-terminal"?text.split(".")[0].slice(label.length):
            mode==="clipped-command"?value+"으로 등록할":mode==="clipped-value"?value.split(" ")[0]:
            mode==="quoted"?value+"으로 등록할 후보를 부탁드립니다":mode==="nominal"?value+" 등록 후보 목록":value;
          const evidence=mode==="foreign-evidence"?"81만 원":mode==="clipped-command"?v:text;
          const fields=[fresh(r,field,v,evidence)];
          if(text.includes("81만 원"))fields.push(fresh(r,"amount",810000,"81만 원"));
          if(mode==="mixed")fields.push(fresh(r,other,otherValue,otherLabel+otherValue));
          await h.reply(fields);
        }
        if(mode==="offline")h.apply({[field]:value,amount:810000});
        else if(mode==="quoted")h.apply({[field]:value+"으로 등록할 후보를 부탁드립니다"});
        else if(mode==="nominal")h.apply({[field]:value+" 등록 후보 목록"});
        else if(mode==="mixed")h.apply({[field]:value,[other]:otherValue});
        else if(mode==="valid-conflict"){
          assert.equal(h.field(field).status,"conflict");h.session.chooseAlternative(field,1);h.apply({[field]:"힌지 주변 조정"});
        }else if(mode==="manual"){h.apply({});assert.equal(h.live().current[field],"수동 값");}
        else{assert(!offered(h.field(field)));assert.ok(h.holds[field]);h.apply(text.includes("81만 원")?{amount:810000}:{});}
      }finally{h.session.dispose();}
    });
  }
}
const forms:[
  AssistForm,AssistCurrent,string,string,AssistValue,AssistValue,string
][]=[
  ["quote",{amount:4550000,days:3,vat:"included",valid:"2026-12-17"},"견적 금액","amount",5260000,4810000,"526만원"],
  ["dates",{days:["2026-12-09","2026-12-16"],crew:2},"인원","crew",3,4,"3명"],
  ["period",{start:"2026-12-02",end:"2026-12-24",excluded:[]},"종료일","end","2026-12-22","2026-12-26","12월 22일"],
  ["extra",extra,"항목명","title","걸쇠 보완","몰딩 보완","걸쇠 보완"],
];
for(const [form,initial,label,field,value,newValue,token] of forms){
  const seed=label+"은 "+token+(form==="extra"?"으로 정합니다.":"으로 제안합니다.");
  for(const [marker,ending] of [["처음 후보","그대로입니다"],["최초 제안","그대로예요"],["직전 후보","그대로죠"],["아까 제안한 값","그대로입니다"],["이전 후보","대로입니다"]]){
    test(form+" explicit identity "+marker+" "+ending,async()=>{
      const h=harness(form,initial);try{
        const r1=await h.send(seed,r=>[fresh(r,field,value,seed)]);
        const text=label+"은 "+marker+(ending==="대로입니다"?"":" ")+ending+".";
        const r2=await h.send(text,r=>[carried(r,field)]);
        assert.equal(r2.previous_candidate?.origins?.[field],r1.request_id);
        assert.equal(h.field(field).origin_request_id,r1.request_id);h.apply({[field]:value});
      }finally{h.session.dispose();}
    });
  }
  for(const mode of ["wrong-first","recent-new","forged-fresh","forged-source","forged-value","current","current-forged","question","negative","condition","cancel","missing","manual","offline"] as const){
    test(form+" historical safety "+mode,async()=>{
      const h=harness(form,initial);try{
        let saved:CandidateField|undefined;
        if(mode!=="missing")await h.send(seed,r=>{saved=fresh(r,field,value,seed);return [saved];});
        if(mode==="manual")h.manual(field,newValue);
        if(mode==="wrong-first"||mode==="recent-new"){
          const nextToken=form==="quote"?"481만원":form==="dates"?"4명":form==="period"?"12월 26일":"몰딩 보완";
          const text=label+"은 "+nextToken+"으로 수정합니다.";
          await h.send(text,r=>[fresh(r,field,newValue,text)]);
        }
        let text=label+"은 처음 후보 그대로입니다.";
        if(mode==="recent-new"||mode.startsWith("forged"))text=label+"은 직전 후보 그대로입니다.";
        if(mode.startsWith("current"))text=label+"은 현재 값 그대로입니다.";
        if(mode==="question")text=label+"은 직전 후보 그대로인가요?";
        if(mode==="negative")text=label+"은 처음 후보 그대로가 아닙니다.";
        if(mode==="condition")text="가능하면 "+label+"은 처음 후보 그대로입니다.";
        if(mode==="cancel")text+=" "+label+" 변경은 취소합니다.";
        const r=h.start(text);
        if(mode==="offline")await h.reply([],true);
        else if(mode==="current")await h.reply([keep(r,field)]);
        else if(mode==="forged-fresh"||mode==="missing")await h.reply([fresh(r,field,value,text)]);
        else if(mode==="manual")await h.reply([{...saved!,intent_kind:"retain_previous",value_source:"previous"}]);
        else {const f=carried(r,field);if(mode==="forged-source")f.source="rule";if(mode==="forged-value")f.value=newValue;await h.reply([f]);}
        if(mode==="recent-new")h.apply({[field]:newValue});
        else if(mode==="current"){assert(!offered(h.field(field)));assert.equal(h.holds[field],undefined);h.apply({});}
        else {assert(!offered(h.field(field)));h.apply({});}
      }finally{h.session.dispose();}
    });
  }
}
test("shared finite declarative identity across distinct typed fields",async()=>{
  const h=harness("extra",extra);try{
    const seed="항목명은 문틀 보완, 사유는 실측 오차 확인.";
    await h.send(seed,r=>[fresh(r,"title","문틀 보완","항목명은 문틀 보완"),fresh(r,"reason","실측 오차 확인","사유는 실측 오차 확인")]);
    await h.send("항목명은 처음 후보, 사유는 직전 후보 그대로입니다.",r=>[carried(r,"title"),carried(r,"reason")]);
    h.apply({title:"문틀 보완",reason:"실측 오차 확인"});
  }finally{h.session.dispose();}
});

for(const [field,label,value] of textCases) {
  for(const suffix of ["으로 후보 등록을 부탁합니다","으로 초안 기재 요청드립니다","으로 제안안 작성을 요청해 주세요","으로 후보 등록을 제시해 줘"]){
    test("expanded "+field+" nominal object-first "+suffix,async()=>{
      const h=harness("extra",extra),text=label+value+suffix+".";
      try {
        assert.equal(parseExtra(text,today)[field],value);
        const r=h.start(text);assert.equal(r.rule_fields.find(f=>f.field===field)?.value,value);
        await h.reply([fresh(r,field,value,text)]);h.apply({[field]:value});
      }finally{h.session.dispose();}
    });
  }
  for(const suffix of ["으로 후보 등록을","으로 등록할","으로 등록할 후보가 맞나요","으로 후보 등록을 요청할지"]){
    test("expanded "+field+" incomplete "+suffix,async()=>{
      const h=harness("extra",extra),text=label+value+suffix+". 추가 비용 82만 원으로 확정합니다.";
      try {
        const r=h.start(text);await h.reply([fresh(r,field,value,text),fresh(r,"amount",820000,"82만 원")]);
        assert(!offered(h.field(field)));assert.equal(r.rule_fields.find(f=>f.field===field)?.action,"keep");
        h.apply({amount:820000});assert.ok(h.holds[field]);
      }finally{h.session.dispose();}
    });
  }
}
for(const ending of ["그대로가 아닙니다","그대로인가요","그대로입니다. 그 요청은 취소합니다"]){
  test("expanded shared unsafe identity "+ending,async()=>{
    const h=harness("extra",extra);try{
      await h.send("항목명은 창호 보강, 사유는 수평 오차 확인.",r=>[
        fresh(r,"title","창호 보강","항목명은 창호 보강"),fresh(r,"reason","수평 오차 확인","사유는 수평 오차 확인")]);
      await h.send("항목명은 처음 후보, 사유는 직전 후보 "+ending+".",r=>[carried(r,"title"),carried(r,"reason")]);
      assert(!offered(h.field("title")));assert(!offered(h.field("reason")));h.apply({});
    }finally{h.session.dispose();}
  });
}
test("expanded shared identity before independent amount",async()=>{
  const h=harness("extra",extra);try{
    await h.send("항목명은 창호 보강, 사유는 수평 오차 확인.",r=>[
      fresh(r,"title","창호 보강","항목명은 창호 보강"),fresh(r,"reason","수평 오차 확인","사유는 수평 오차 확인")]);
    await h.send("항목명은 처음 후보, 사유는 직전 후보 그대로입니다. 추가 금액 82만 원으로 확정합니다.",r=>[
      carried(r,"title"),carried(r,"reason"),fresh(r,"amount",820000,"82만 원")]);
    h.apply({title:"창호 보강",reason:"수평 오차 확인",amount:820000});
  }finally{h.session.dispose();}
});
for(const [form,initial,label,field,value,,token] of forms){
  for(const ref of ["직전 후보","현재 값"]){
    test("expanded "+form+" negated "+ref,async()=>{
      const h=harness(form,initial);try{
        const seed=label+"은 "+token+"으로 제안합니다.";
        await h.send(seed,r=>[fresh(r,field,value,seed)]);
        await h.send(label+"은 "+ref+" 그대로가 아닙니다.",r=>[carried(r,field)]);
        assert(!offered(h.field(field)));h.apply({});
      }finally{h.session.dispose();}
    });
  }
}

for(const [field,label,value] of textCases)for(const suffix of ["으로 후보로 반영해 주세요","으로 후보 입력을 해 주세요","으로 작성할 안을 요청합니다"]){
  test("release "+field+" proposal composition "+suffix,async()=>{
    const h=harness("extra",extra),text=label+value+suffix+".";
    try{const r=h.start(text);assert.equal(r.rule_fields.find(f=>f.field===field)?.value,value);
      await h.reply([],true);h.apply({[field]:value});}
    finally{h.session.dispose();}
  });
}
const periodCurrent={start:"2026-12-02",end:"2026-12-24",excluded:[]};
for(const [name,text,expected] of [
  ["separate","시작일은 12월 6일로 제안합니다. 종료일은 12월 22일로 제안합니다.",{start:"2026-12-06",end:"2026-12-22"}],
  ["reverse","종료일은 12월 22일로 확정합니다. 시작일은 12월 6일로 정해 주세요.",{start:"2026-12-06",end:"2026-12-22"}],
  ["negative","시작일은 12월 6일로 제안하지 마세요. 종료일은 12월 22일로 제안합니다.",{}],
  ["question","시작일은 12월 6일로 할까요? 종료일은 12월 22일로 제안합니다.",{}],
  ["condition","가능하면 시작일은 12월 6일로 제안합니다. 종료일은 12월 22일로 제안합니다.",{}],
  ["duplicate","시작일은 12월 6일로 제안합니다. 시작일은 12월 22일로 제안합니다.",{}],
  ["reversed values","시작일은 12월 22일로 제안합니다. 종료일은 12월 6일로 제안합니다.",{}],
  ["too long","시작일은 12월 6일로 제안합니다. 종료일은 2027년 3월 22일로 제안합니다.",{}],
  ["invalid day","시작일은 2026년 11월 31일로 제안합니다. 종료일은 12월 22일로 제안합니다.",{}],
] as [string,string,AssistCurrent][]){
  test("release period independent boundaries "+name,async()=>{
    const h=harness("period",periodCurrent);try{
      const r=h.start(text);
      assert.deepEqual(Object.fromEntries(r.rule_fields.filter(f=>f.action==="set").map(f=>[f.field,f.value])),expected);
      await h.reply([],true);h.apply(expected);
    }finally{h.session.dispose();}
  });
}
const independentByForm={
  quote:["days",8,"공사 기간은 8일로 확정합니다."],
  dates:["days",["2026-12-10","2026-12-17"],"작업일은 12월 10일과 17일로 정합니다."],
  period:["start","2026-12-06","시작일은 12월 6일로 확정합니다."],
  extra:["amount",820000,"추가 금액은 82만 원으로 확정합니다."],
} as const;
for(const [form,initial,label,field,value,,token] of forms)for(const proof of ["scoped","whole"] as const){
  test("release "+form+" current keep proof "+proof,async()=>{
    const h=harness(form,initial),[other,otherValue,otherText]=independentByForm[form];
    try{
      const seed=label+"은 "+token+"으로 제안합니다.";
      await h.send(seed,r=>[fresh(r,field,value,seed)]);
      const scope=label+"은 현재 입력값 그대로입니다",text=otherText+" "+scope+".";
      await h.send(text,r=>[{...keep(r,field),evidence:proof==="scoped"?scope:text},fresh(r,other,otherValue as AssistValue,otherText)]);
      assert.equal(Boolean(h.holds[field]),proof==="whole");assert(!offered(h.field(field)));
      h.apply({[other]:otherValue as AssistValue});
      assert.equal(Boolean(h.holds[field]),proof==="whole");
    }finally{h.session.dispose();}
  });
}

for(const [field,label] of textCases)for(const value of ["구조적으로 안정","고정 부위의 안정","문틀 보완 후보 목록","보수 초안 검토"]){
  for(const quoted of [false,true])test("taillex "+field+" "+value+" quoted="+quoted,async()=>{
    const h=harness("extra",extra),text=label+(quoted?"“"+value+"”":value)+".";
    try{
      const r=h.start(text);assert.equal(r.rule_fields.find(f=>f.field===field)?.value,value);
      await h.reply([fresh(r,field,value,text)]);h.apply({[field]:value});
    }finally{h.session.dispose();}
  });
}
const end28="종료일은 12월 28일로 확정합니다.";
const startFirst="시작일은 처음 제안한 날짜 그대로 유지해 주세요.";
const start9="시작일은 12월 9일로 변경합니다.";
for(const [name,text,expected] of [
  ["independent end",end28+" "+startFirst+" "+start9,{end:"2026-12-28"}],
  ["reordered",startFirst+" "+start9+" "+end28,{end:"2026-12-28"}],
  ["symmetric","시작일은 12월 8일로 확정합니다. 종료일은 처음 후보 그대로입니다. 종료일은 12월 26일로 변경합니다.",{start:"2026-12-08"}],
  ["other question","종료일은 12월 28일로 할까요? "+startFirst+" "+start9,{}],
  ["conditional","가능하면 "+end28+" "+startFirst+" "+start9,{}],
  ["same-field negative",end28+" 시작일은 처음 제안한 날짜 그대로 유지하지 마세요. "+start9,{}],
  ["unbound cancellation",end28+" "+startFirst+" "+start9+" 그 요청은 취소합니다.",{}],
  ["untyped extra",end28+" "+startFirst+" "+start9+" 12월 12일도요.",{}],
  ["excluded",end28+" "+startFirst+" "+start9+" 제외일은 12월 13일.",{}],
  ["two end dates",end28+" "+startFirst+" "+start9+" 종료일은 12월 29일로 확정합니다.",{}],
  ["invalid target","종료일은 2026년 11월 31일로 확정합니다. "+startFirst+" "+start9,{}],
] as [string,string,AssistCurrent][]){
  test("tailperiod "+name,async()=>{
    const h=harness("period",periodCurrent);try{
      const seed="시작일은 12월 6일로 제안합니다. 종료일은 12월 22일로 제안합니다.";
      await h.send(seed,r=>[fresh(r,"start","2026-12-06","시작일은 12월 6일로 제안합니다"),fresh(r,"end","2026-12-22","종료일은 12월 22일로 제안합니다")]);
      const r=h.start(text);
      assert.deepEqual(Object.fromEntries(r.rule_fields.filter(f=>f.action==="set").map(f=>[f.field,f.value])),expected);
      await h.reply([],true);
      h.apply(expected);
      if("end" in expected)assert.ok(h.holds.start);
      if("start" in expected)assert.ok(h.holds.end);
    }finally{h.session.dispose();}
  });
}
test("tailperiod partial candidate cannot bypass combined period",async()=>{
  const h=harness("period",{start:"2026-12-20",end:"2026-12-24",excluded:[]});
  try{
    const r=h.start("종료일은 12월 18일로 확정합니다. "+startFirst+" "+start9);
    assert.equal(r.rule_fields.find(f=>f.field==="end")?.value,"2026-12-18");
    await h.reply([],true);h.apply({});assert.ok(h.holds.end);
  }finally{h.session.dispose();}
});
