const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('fs'),path=require('path'),vm=require('vm');
const {harness,deferred,walk,textContent,repo,ApiError}=require('./lib/offline-tsx-harness.cjs');
const ts=require('typescript');
const code=ts.transpileModule(fs.readFileSync(path.join(repo,'src/web/src/features/p3/materialReview.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
const mod={exports:{}};vm.runInNewContext(code,{module:mod,exports:mod.exports,require:()=>({IngestionApiError:ApiError})});
const contracts=mod.exports;
const id=n=>`00000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
const result=(vid=id(1))=>({version_id:vid,source_format:'docx',source_sha256:'a'.repeat(64),base_revision_id:id(2),base_manifest_sha256:'b'.repeat(64),editable:true,review_head:null,
 base_items:[{id:id(3),base_fragment_id:id(3),ordinal:0,entry_kind:'text',field_name:null,locator:{schema_version:2,kind:'docx_block',body_index:1},location:'正文块 1 · 段落',text:'COD 42 mg/L',body_sha256:'c'.repeat(64)}],review_items:[]});
function screen(){
 const reads=[],writes=[],completed=[],identities=new Map();const getAccessToken=()=> 'token';
 const h=harness('components/MaterialReviewPanel.tsx',{props:{versionId:id(1)},imports:{
  '../auth/OidcProvider':{useAuth:()=>({getAccessToken,user:{profile:{sub:'actor'}}})},
  '../features/p3/materialReview':contracts,
  '../adapters/pendingWrites':{pendingWrite:async(_op,_eid,_sub,key)=>{if(!identities.has(key))identities.set(key,id(50+identities.size));return {key,requestId:identities.get(key)}},completePendingWrite:x=>completed.push(x)},
  '../features/p3/ingestionApi':{getMaterialReview:(_token,version,signal)=>{const d=deferred();reads.push({...d,version,signal});return d.promise},saveMaterialReview:(_token,version,body)=>{const d=deferred();writes.push({...d,version,body});return d.promise}},
 }});h.render();return {h,reads,writes,completed};
}
const button=(h,label)=>walk(h.tree()).find(x=>x.type==='Button'&&textContent(x)===label);
const checkbox=h=>walk(h.tree()).find(x=>x.type==='Checkbox');
async function open(s){button(s.h,'校对提取内容').props.onClick();s.h.render();s.reads.at(-1).resolve(result(s.reads.at(-1).version));await s.h.settle()}
async function submit(s){checkbox(s.h).props.onChange({target:{checked:true}});s.h.render();button(s.h,'确认并保存修订').props.onClick();await s.h.settle()}
test('review parser rejects wrong version, missing base and mismatched receipt',()=>{
 assert.equal(contracts.parseMaterialReview(result(),id(1)).base_items[0].text,'COD 42 mg/L');
 for(const raw of [{...result(),version_id:id(9)},{...result(),base_revision_id:null},{...result(),base_items:[{...result().base_items[0],ordinal:2}]}])assert.throws(()=>contracts.parseMaterialReview(raw,id(1)));
 assert.throws(()=>contracts.parseReviewReceipt({request_id:id(8),id:id(9),revision_no:1,action:'confirm',replayed:false},{request_id:id(7),action:'confirm'}));
});
test('review requires source acknowledgment and editing clears it',async()=>{
 const s=screen();await open(s);assert.equal(button(s.h,'确认并保存修订').props.disabled,true);
 checkbox(s.h).props.onChange({target:{checked:true}});s.h.render();assert.equal(button(s.h,'确认并保存修订').props.disabled,false);
 walk(s.h.tree()).find(x=>x.type==='TextArea').props.onChange({target:{value:'COD 24 mg/L'}});s.h.render();assert.equal(checkbox(s.h).props.checked,false);
 await submit(s);assert.equal(s.writes[0].body.texts[0].text,'COD 24 mg/L');assert.equal(s.writes[0].body.checked_against_source,true);s.h.unmount();
});
test('unknown review outcome locks content and retries identical request',async()=>{
 const s=screen();await open(s);await submit(s);const body=s.writes[0].body;
 s.writes[0].reject(new Error('unknown'));await s.h.settle();assert.equal(walk(s.h.tree()).find(x=>x.type==='TextArea').props.disabled,true);
 // Alert action is a React prop rather than a child in the offline renderer.
 const alert=walk(s.h.tree()).find(x=>x.type==='Alert'&&x.props.type==='error');alert.props.action.props.onClick();await s.h.settle();
 assert.equal(s.writes[1].body.request_id,body.request_id);assert.equal(s.writes[1].body,body);
 s.writes[1].resolve({id:id(80)});await s.h.settle();assert.equal(s.completed.length,1);assert.equal(s.reads.length,2);s.h.unmount();
});
for(const outcome of ['success','failure'])test(`old version review ${outcome} cannot alter new version`,async()=>{
 const s=screen();await open(s);await submit(s);s.h.setProps({versionId:id(8)});await open(s);await submit(s);
 if(outcome==='success')s.writes[0].resolve({id:id(80)});else s.writes[0].reject(new Error('old'));
 await s.h.settle();assert.equal(button(s.h,'确认并保存修订').props.loading,true);assert.equal(s.writes[1].version,id(8));s.h.unmount();
});
test('stale revision shows conflict and requires refreshed acknowledgment',async()=>{
 const s=screen();await open(s);await submit(s);s.writes[0].reject(new ApiError(409,'REVIEW_REVISION_CONFLICT',false));await s.h.settle();
 const alert=walk(s.h.tree()).find(x=>x.type==='Alert'&&x.props.type==='error');assert.match(alert.props.message,/新版本/);
 alert.props.action.props.onClick();await s.h.settle();s.reads.at(-1).resolve(result());await s.h.settle();assert.equal(checkbox(s.h).props.checked,false);s.h.unmount();
});
test('review original target binds original base fragment revision locator and source hash',async()=>{
 const s=screen();await open(s);const node=walk(s.h.tree()).find(x=>String(x.type).endsWith('/OriginalEvidenceView'));
 const expected=result();assert.equal(node.props.target.fragmentId,expected.base_items[0].id);
 assert.equal(node.props.target.revisionId,expected.base_revision_id);assert.equal(node.props.target.sourceSha256,expected.source_sha256);
 assert.equal(node.props.target.reviewBase,true);assert.deepEqual(node.props.target.locator,expected.base_items[0].locator);s.h.unmount();
});
