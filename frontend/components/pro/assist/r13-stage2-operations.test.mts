import assert from "node:assert/strict";
import {test} from "node:test";
import {AssistSession} from "./session.ts";
import {formKeys,type AssistCurrent,type AssistForm,type AssistRequest,type CandidateField,type RuleOptions,type AssistValue} from "./protocol.ts";
const opts:RuleOptions={today:"2026-11-25",baseMonth:"2026-12-01",crewMax:6,daysMax:120,requiredDays:2,vatModes:["included","excluded","exempt"]};
const currents:Record<AssistForm,AssistCurrent>={
  quote:{amount:3400000,vat:"included",days:4,valid:"2026-12-28"},
  period:{start:"2026-12-02",end:"2026-12-24",excluded:[]},
  dates:{days:["2026-12-08","2026-12-17"],crew:2},
  extra:{title:"기존 공사",reason:"기존 사유",amount:250000,vat:"included"},
};
type Example={name:string;form:AssistForm;text:string;field:string;value:AssistValue;evidence:string;pass:boolean;detail?:Partial<CandidateField>;immutable?:string[]};
const cases:Example[]=[
  {name:"same-value independent duration observation",form:"quote",text:"현장을 보니 공사에 9일 걸릴 것 같습니다. 소요일은 9일로 수정 부탁드립니다.",field:"days",value:9,evidence:"소요일은 9일로 수정",pass:true},
  {name:"different-value earlier estimate",form:"quote",text:"현장을 보니 공사에 7일 걸릴 것 같습니다. 소요일은 9일로 수정 부탁드립니다.",field:"days",value:9,evidence:"소요일은 9일로 수정",pass:false},
  {name:"causal background before owned command",form:"dates",text:"기존 인력으로는 공사가 늦을 것 같아서 작업자 다섯 명으로 올려 주세요.",field:"crew",value:5,evidence:"작업자 다섯 명으로 올려 주세요",pass:true},
  {name:"attached condition cannot be cut away",form:"dates",text:"승인이 나면 작업자 다섯 명으로 올려 주세요.",field:"crew",value:5,evidence:"작업자 다섯 명으로 올려 주세요",pass:false},
  {name:"closed independent inquiry before preview",form:"dates",text:"현장에 사람이 오면 어떨지 보고 싶어요. 작업 인원 5명으로 변경하는 후보를 보여 주세요.",field:"crew",value:5,evidence:"작업 인원 5명으로 변경하는 후보",pass:true},
  {name:"open independent condition before command",form:"dates",text:"승인이 나면. 작업 인원 5명으로 변경하는 후보를 보여 주세요.",field:"crew",value:5,evidence:"작업 인원 5명으로 변경하는 후보",pass:false},
  {name:"same-count sufficiency before independent change",form:"dates",text:"현장 정리가 끝나서 작업자는 세 명이면 됩니다. 인원 3명으로 줄여 주세요.",field:"crew",value:3,evidence:"인원 3명으로 줄여 주세요",pass:true},
  {name:"exact crew value must be checked",form:"dates",text:"현장 정리가 끝나서 작업자는 세 명이면 됩니다. 인원 3명으로 줄여 주세요.",field:"crew",value:4,evidence:"인원 3명으로 줄여 주세요",pass:false},
  {name:"explicit endpoint owns literal despite unrelated start ambiguity",form:"period",text:"마감은 12월 27일로 확정해 주세요. 착수일은 12월 4일과 6일 중에서 아직 미정이에요.",field:"end",value:"2026-12-27",evidence:"마감은 12월 27일로 확정",pass:true},
  {name:"wrong-field endpoint must not be copied",form:"period",text:"마감은 12월 27일로 확정해 주세요. 착수일은 12월 4일과 6일 중에서 아직 미정이에요.",field:"start",value:"2026-12-27",evidence:"마감은 12월 27일로 확정",pass:false},
  {name:"shared scalar predicate exact nominal particle",form:"extra",text:"공사명은 주방 선반 보강으로, 추가 금액은 42만 원으로 수정하겠습니다.",field:"title",value:"주방 선반 보강",evidence:"공사명은 주방 선반 보강으로",pass:true},
  {name:"shared predicate cannot authorize shortened noun",form:"extra",text:"공사명은 주방 선반 보강으로, 추가 금액은 42만 원으로 수정하겠습니다.",field:"title",value:"선반 보강",evidence:"공사명은 주방 선반 보강으로",pass:false},
  {name:"typed VAT billing mode without duplicate positive grammar",form:"quote",text:"부가세도 별도로 청구하는 방식으로 변경 부탁드립니다.",field:"vat",value:"excluded",evidence:"부가세도 별도로 청구하는 방식으로 변경",pass:true},
  {name:"wrong VAT mode",form:"quote",text:"부가세도 별도로 청구하는 방식으로 변경 부탁드립니다.",field:"vat",value:"included",evidence:"부가세도 별도로 청구하는 방식으로 변경",pass:false},
  {name:"VAT check-only is not mode assignment",form:"quote",text:"부가세는 따로 확인하고 철거비는 별도로 청구합니다.",field:"vat",value:"excluded",evidence:"부가세는 따로 확인하고 철거비는 별도로 청구합니다.",pass:false},
  {name:"immutable field remains locked",form:"dates",text:"작업 인원을 5명으로 변경해 주세요.",field:"crew",value:5,evidence:"작업 인원을 5명으로 변경해 주세요.",pass:false,immutable:["crew"]},
  {name:"malformed canonical origin never enters legacy",form:"dates",text:"작업 인원을 5명으로 변경해 주세요.",field:"crew",value:5,evidence:"작업 인원을 5명으로 변경해 주세요.",pass:false,detail:{origin_request_id:"stale"}},
  {name:"wrong numeric fragment is not complete evidence",form:"dates",text:"작업 인원을 15명으로 변경해 주세요.",field:"crew",value:5,evidence:"5명으로 변경해 주세요.",pass:false},
  {name:"negative after exact evidence still vetoes",form:"dates",text:"작업 인원을 5명으로 변경하지 마세요.",field:"crew",value:5,evidence:"작업 인원을 5명으로 변경",pass:false},
];
for(const c of cases)test("canonical operation: "+c.name,async()=>{
  const current=structuredClone(currents[c.form]);let pending=false;
  const holds:Record<string,string>={};
  const s=new AssistSession(c.form,"00000000-0000-4000-8000-000000000179",async(r:AssistRequest)=>({
    form:c.form,source:"llm",request_id:r.request_id,warnings:[],
    fields:formKeys(c.form).map(field=>field===c.field?{
      field,action:"set",value:c.value,evidence:c.evidence,status:"valid",source:"llm",alternatives:[],
      intent_kind:"explicit_change",value_source:"utterance",origin_request_id:r.request_id,...c.detail
    }:{field,action:"keep",value:null,evidence:"",status:"valid",source:"rule",alternatives:[],
      intent_kind:"no_mention",value_source:null,origin_request_id:r.request_id})
  }));
  try{
    assert(s.send(c.text,current,{...opts,immutableFields:c.immutable},d=>{
      pending=d.pending??pending;
      for(const [k,v] of Object.entries(d.fieldHolds??{})){if(v===null)delete holds[k];else if(typeof v==="string")holds[k]=v;}
    },()=>({current:structuredClone(current),manualVersions:{}})));
    assert(pending);assert(!s.apply(current,()=>assert.fail("before response")));
    await new Promise<void>(resolve=>setImmediate(resolve));
    assert.equal(s.view.phase,"ready");assert(!pending);
    const f=s.view.response!.fields.find(f=>f.field===c.field)!;
    assert.equal(f.status==="valid"&&f.action==="set",c.pass);
    assert.deepEqual(current,currents[c.form]);
    for(const k of formKeys(c.form))s.select(k,k===c.field);
    let patch:AssistCurrent={};
    assert.equal(s.apply(current,p=>{patch=structuredClone(p);}),c.pass);
    assert.deepEqual(patch,c.pass?{[c.field]:c.value}:{});
  }finally{s.dispose();}
});
