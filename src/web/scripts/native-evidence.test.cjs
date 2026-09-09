const {test}=require('node:test');
const assert=require('node:assert/strict');
const {harness,deferred,walk}=require('./lib/offline-tsx-harness.cjs');
const {uploadFixture,file:uploadFile,version:uploadVersion,receipt:uploadReceipt,auth:uploadAuth}=require('./lib/upload-fixture.cjs');
const version=id=>({id,updated_at:'2026-09-08T00:00:00Z',quarantine_status:'released'});
const receipt=(id,extra={})=>({schema:'anhuan-native-extraction-v1',version_id:id,state:'done',coverage_state:'partial',expected_block_count:5,processed_block_count:5,fragment_count:0,report_source_eligible:false,...extra});
function screen(){
  const reads=[];
  const h=harness('components/NativeEvidenceStatus.tsx',{props:{version:version('A')},imports:{
    '../features/p3/ingestionApi':{getNativeExtractionStatus:(_token,id,signal)=>{const d=deferred();reads.push({...d,id,signal});return d.promise;}}
  }});
  h.render();return {h,reads};
}
const alert=h=>walk(h.tree()).find(n=>n.type==='Alert');

function recoveryScreen(){
 const writes=[],completed=[],identities=new Map(),done=[];const getAccessToken=()=> 'token';
 const h=harness('components/NativeRecoveryButton.tsx',{props:{versionId:'A',jobId:'jobA',onDone:()=>done.push('A')},imports:{
  '../auth/OidcProvider':{useAuth:()=>({getAccessToken,user:{profile:{sub:'actor'}}})},
  '../adapters/pendingWrites':{pendingWrite:async(_op,_eid,_sub,key)=>{if(!identities.has(key))identities.set(key,'request-'+identities.size);return {key,requestId:identities.get(key)}},completePendingWrite:x=>completed.push(x)},
  '../features/p3/ingestionApi':{recoverNativeExtraction:(_token,version,body,signal)=>{const d=deferred();writes.push({...d,version,body,signal});return d.promise}}
 }});h.render();return {h,writes,completed,done};
}
async function triggerRecovery(s){walk(s.h.tree()).find(x=>x.type==='Button'&&x.props.children==='恢复提取任务').props.onClick();await s.h.settle()}
test('native recovery unknown outcome retains request and double click starts once',async()=>{
 const s=recoveryScreen();await triggerRecovery(s);await triggerRecovery(s);assert.equal(s.writes.length,1);
 s.writes[0].reject(new Error('unknown'));await s.h.settle();await triggerRecovery(s);
 assert.equal(s.writes[1].body.request_id,s.writes[0].body.request_id);assert.equal(s.writes[1].body.expected_job_id,'jobA');
 s.writes[1].resolve();await s.h.settle();assert.deepEqual(s.done,['A']);assert.equal(s.completed.length,1);s.h.unmount();
});
for(const outcome of ['success','failure'])test(`old recovery ${outcome} cannot refresh next material`,async()=>{
 const s=recoveryScreen();await triggerRecovery(s);s.h.setProps({versionId:'B',jobId:'jobB',onDone:()=>s.done.push('B')});assert(s.writes[0].signal.aborted);
 await triggerRecovery(s);if(outcome==='success')s.writes[0].resolve();else s.writes[0].reject(new Error('old'));
 await s.h.settle();assert.equal(s.done.length,0);s.writes[1].resolve();await s.h.settle();assert.deepEqual(s.done,['B']);s.h.unmount();
});

const DOCX='application/vnd.openxmlformats-officedocument.wordprocessingml.document';
const caps=(types=['application/pdf',DOCX])=>({upload_enabled:true,scanner:{state:'ready'},limits:{max_file_bytes:50*1024*1024},allowed_types:types.map(content_type=>({content_type,extensions:[content_type===DOCX?'.docx':'.pdf'],max_file_bytes:50*1024*1024}))});
const modal=h=>walk(h.tree()).find(n=>n.type===h.Modal);
const picker=h=>walk(h.tree()).find(n=>n.props?.maxCount===1);
const docx=(name='资料.docx',size=100)=>uploadFile(name,size);
async function uploadScreen(capabilities=caps()){
  const writes=[];const fixture=uploadFixture();
  const h=harness('components/MaterialPanel.tsx',{props:{scope:'client',clientId:'A'},form:{validateFields:async()=>({name:'材料'})},imports:{
    '../auth/OidcProvider':{useAuth:()=>uploadAuth},'../features/p3/uploadWrite':fixture.helper,'../adapters/pendingWrites':fixture.pending,
    '../features/p3/ingestionApi':{getIngestionCapabilities:async()=>capabilities,listIngestionDocuments:async()=>({items:[],next_cursor:null}),createIngestionDocument:(...args)=>{const d=deferred();writes.push({...d,args});return d.promise},userFacingIngestionError:()=> '上传失败，请重试'}
  }});
  h.render();await h.settle();return {h,writes};
}
function openUpload(h){walk(h.tree()).find(n=>n.type==='Button'&&n.props.children==='上传客户材料').props.onClick();h.render();}
function select(h,file=docx()){const result=picker(h).props.beforeUpload(file);h.render();return result;}
async function send(h){modal(h).props.onOk();await h.settle();}

test('material upload honors DOCX capability and 25 MiB parser limit',async()=>{
  const {h}=await uploadScreen();openUpload(h);assert.match(picker(h).props.accept,/\.docx/);
  assert.equal(select(h,docx('大文件.docx',26*1024*1024)),'LIST_IGNORE');
  assert.equal(select(h,docx('边界.docx',25*1024*1024)),false);
  assert.equal(select(h,docx('表格.xlsx')),'LIST_IGNORE');h.unmount();
  const onlyPdf=await uploadScreen(caps(['application/pdf']));openUpload(onlyPdf.h);
  assert.equal(select(onlyPdf.h),'LIST_IGNORE');onlyPdf.h.unmount();
});
test('DOCX upload preserves file scope and unknown-result request identity',async()=>{
  const {h,writes}=await uploadScreen();openUpload(h);const file=docx();select(h,file);await send(h);
  assert.equal(writes.length,1);assert.equal(writes[0].args[2],file);assert.equal(writes[0].args[6].client_account_id,'A');
  writes[0].reject(new Error('unknown'));await h.settle();await send(h);
  assert.equal(writes[1].args[3],writes[0].args[3]);writes[1].resolve(uploadReceipt(file));await h.settle();
  assert.equal(modal(h).props.open,false);assert.equal(modal(h).props.confirmLoading,false);h.unmount();
});
for(const outcome of ['success','failure'])test(`old customer upload ${outcome} cannot clear next customer upload`,async()=>{
  const {h,writes}=await uploadScreen();openUpload(h);select(h);await send(h);
  h.setProps({scope:'client',clientId:'B'});await h.settle();assert.equal(modal(h).props.open,false);
  openUpload(h);select(h,docx('B.docx'));await send(h);assert.equal(writes[1].args[6].client_account_id,'B');
  const count=h.messages.length;
  if(outcome==='success')writes[0].resolve({id:'docA'});else writes[0].reject(new Error('old'));
  await h.settle();assert.equal(h.messages.length,count);assert.equal(modal(h).props.open,true);assert.equal(modal(h).props.confirmLoading,true);
  writes[1].reject(new Error('unknown'));await h.settle();await send(h);assert.equal(writes[2].args[3],writes[1].args[3]);assert.notEqual(writes[2].args[3],writes[0].args[3]);
  writes[2].resolve(uploadReceipt(docx('B.docx'),'B'));await h.settle();assert.equal(modal(h).props.open,false);h.unmount();
});
test('new version target change aborts old DOCX request and ignores late success',async()=>{
  const writes=[],successes=[];const fixture=uploadFixture();
  const props={mode:'version',open:true,documentId:'A',capabilities:caps(),acceptedContentTypes:[DOCX],token:'offline-token',onCancel(){},onSuccess:r=>successes.push(r)};
  const h=harness('features/p3/components/DocumentUploadModal.tsx',{props,imports:{'../../../auth/OidcProvider':{useAuth:()=>uploadAuth},'../uploadWrite':fixture.helper,'../../../adapters/pendingWrites':fixture.pending,'../ingestionApi':{uploadDocumentVersion:(...args)=>{const d=deferred();writes.push({...d,args});return d.promise},userFacingIngestionError:()=> '失败'}}});
  const choose=()=>{picker(h).props.onChange({fileList:[{originFileObj:docx('新版本.docx')}]});h.render()};
  h.render();choose();await send(h);h.setProps({...props,documentId:'B'});
  assert.equal(writes[0].args[4].aborted,true);assert.equal(modal(h).props.confirmLoading,false);choose();await send(h);
  writes[0].resolve({document_id:'A',id:'versionA'});await h.settle();assert.equal(successes.length,0);assert.equal(modal(h).props.confirmLoading,true);
  writes[1].resolve(uploadVersion(docx('新版本.docx'),'B'));await h.settle();assert.equal(successes.length,1);assert.equal(successes[0].documentId,'B');h.unmount();
});

test('partial DOCX shows incomplete and never claims report readiness',async()=>{
  const {h,reads}=screen();reads[0].resolve(receipt('A'));await h.settle();
  assert.equal(alert(h).props.message,'DOCX 提取不完整');
  assert.match(alert(h).props.description,/5 \/ 5/);assert.match(alert(h).props.description,/不可用于问答或报告/);
  assert.equal(h.timers.size,0);h.unmount();
});
test('legacy extraction receipt without effective state does not imply downstream integration',async()=>{
  const {h,reads}=screen();reads[0].resolve(receipt('A',{coverage_state:'complete',fragment_count:5,report_source_eligible:true}));await h.settle();
  assert.equal(alert(h).props.message,'DOCX 原生提取完成');assert.match(alert(h).props.description,/暂不可用于问答或报告/);h.unmount();
});
for(const kind of ['review','extraction'])test(`effective ${kind} is available for citations`,async()=>{
  const {h,reads}=screen();reads[0].resolve(receipt('A',{effective:{state:'ready',evidence_kind:kind,fragment_count:5}}));await h.settle();
  assert.equal(alert(h).props.type,'success');assert.match(alert(h).props.description,/可用于新问答和报告引用/);h.unmount();
});
test('revoked review supersedes complete extraction readiness',async()=>{
  const {h,reads}=screen();reads[0].resolve(receipt('A',{coverage_state:'complete',report_source_eligible:true,effective:{state:'revoked'}}));await h.settle();
  assert.equal(alert(h).props.message,'当前人工确认已撤销');assert.match(alert(h).props.description,/退出新问答和新报告/);h.unmount();
});
for(const outcome of ['success','failure'])test(`old version ${outcome} cannot overwrite new partial result`,async()=>{
  const {h,reads}=screen();h.setProps({version:version('B')});assert(reads[0].signal.aborted);
  reads[1].resolve(receipt('B'));await h.settle();
  if(outcome==='success')reads[0].resolve(receipt('A',{coverage_state:'complete',report_source_eligible:true}));else reads[0].reject(new Error('old'));
  await h.settle();assert.equal(alert(h).props.message,'DOCX 提取不完整');h.unmount();
});
test('active job polls until terminal partial and unmount aborts outstanding request',async()=>{
  const {h,reads}=screen();reads[0].resolve(receipt('A',{state:'running',coverage_state:null}));await h.settle();
  assert.equal(h.timers.size,1);h.fireTimers();h.render();assert.equal(reads.length,2);
  reads[1].resolve(receipt('A'));await h.settle();assert.equal(h.timers.size,0);
  h.setProps({version:version('B')});h.unmount();assert(reads[2].signal.aborted);
  reads[2].resolve(receipt('B'));await h.settle();assert.equal(h.lateWrites(),0);
});
test('wrong version receipt becomes retryable failure',async()=>{
  const {h,reads}=screen();reads[0].resolve(receipt('B'));await h.settle();
  assert.equal(alert(h).props.type,'error');alert(h).props.action.props.onClick();h.render();
  assert.equal(reads.length,2);reads[1].resolve(receipt('A'));await h.settle();assert.equal(alert(h).props.message,'DOCX 提取不完整');h.unmount();
});

test('Excel and JPEG use declared capabilities and format-specific byte bounds',async()=>{
  const xlsx='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet';
  const capabilities={...caps(),allowed_types:[...caps().allowed_types,{content_type:xlsx,extensions:['.xlsx'],max_file_bytes:50*1024*1024},{content_type:'image/jpeg',extensions:['.jpg','.jpeg'],max_file_bytes:50*1024*1024}]};
  const {h}=await uploadScreen(capabilities);openUpload(h);
  assert.match(picker(h).props.accept,/\.xlsx/);assert.match(picker(h).props.accept,/\.jpeg/);
  for(const [name,type,limit] of [['表格.xlsx',xlsx,25],['现场.jpeg','image/jpeg',20]]){
    assert.equal(select(h,{name,type,size:limit*1024*1024}),false);
    assert.equal(select(h,{name,type,size:limit*1024*1024+1}),'LIST_IGNORE');
  }
  h.unmount();
});
test('Excel and JPEG status name their actual format and structural unit',async()=>{
  for(const [type,label,unit] of [['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','Excel','单元格'],['image/jpeg','图片','图像']]){
    const {h,reads}=screen();h.setProps({version:{...version('B'),content_type:type}});
    reads[1].resolve(receipt('B'));await h.settle();
    assert.equal(alert(h).props.message,`${label} 提取不完整`);assert.match(alert(h).props.description,new RegExp(unit));h.unmount();
  }
});
