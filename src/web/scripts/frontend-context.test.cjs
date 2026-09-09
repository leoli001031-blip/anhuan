// Regression of A01/A02/A05/A06/A07: actual TSX handlers, controlled API promises.
// UI ports are not browser/Ant Design, tenant RLS, or release verification.
// The transport regressions at the end additionally exercise loopback HTTP.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const {harness,deferred,walk,textContent} = require('./lib/offline-tsx-harness.cjs');
const button=(h,label)=>walk(h.tree()).find(n=>n.type==='Button' && textContent(n)===label);
const click=(h,label)=>{const b=button(h,label);assert(b,`missing button: ${label}`);assert(!b.props.disabled);b.props.onClick();h.render();};
const modal=h=>walk(h.tree()).find(n=>n.type===h.Modal);
const form=h=>walk(h.tree()).find(n=>n.type===h.Form);
const route=(h,c)=>h.render({clientId:c,reportId:'report'+c});
const version=(c,status,n=1)=>({version_id:`version${c}${n}`,version_number:n,status,created_at:'2026-09-08T00:00:00Z'});
const requestKey=c=>`ar-generate-request:tenant:${c}:report${c}`;
const jobKey=c=>`ar-job:tenant:${c}:report${c}`;
const generationButton=h=>walk(h.tree()).find(n=>n.type==='Button' && /^(生成首个版本|生成新版本|恢复生成任务)$/.test(textContent(n)));
function workbench(status='empty', options={}) {
  const generations=[],transitions=[],jobReads=[],downloads=[];
  const versions={A:status==='empty'?[]:[version('A',status)],B:status==='empty'?[]:[version('B',status)]};
  const api={
    listClientReports:async c=>[{report_id:'report'+c}],
    listVersions:async r=>versions[r.slice(-1)],
    getVersion:async v=>({version_id:v,sections:[{title:'测试',body:'正文'}],citations:[],review_events:[]}),
    generate:(c,r,key)=>{const d=deferred();generations.push({client:c,report:r,key,...d});return d.promise},
    transition:(v,action,evidence)=>{const d=deferred();transitions.push({version:v,action,evidence,...d});return d.promise},
    getJob:id=>{const d=deferred();jobReads.push({id,...d});return d.promise},
    getVersionPdfArtifact:v=>{const d=deferred();downloads.push({version:v,...d});return d.promise},
  };
  const h=harness('pages/console/ReportWorkbenchPage.tsx',{api,...options});
  return {h,api,versions,generations,transitions,jobReads,downloads};
}

const statusLabels=h=>walk(h.tree()).map(n=>n.props?.label).filter(x=>typeof x==='string');
for(const state of ['retry_wait','blocked'])test(`E05 ${state} shows delivery failure while preserving the same job`,async()=>{
  const p=workbench();route(p.h,'A');await p.h.settle();click(p.h,'生成首个版本');
  p.generations[0].resolve({status:'queued',job_id:'jobA',version_id:'versionA1'});await p.h.settle();
  p.h.fireTimers();await p.h.settle();
  p.jobReads[0].resolve({status:'queued',version_id:'versionA1',error_reason:null,delivery:{state,attempt:2,reason_code:'REPORT_QUEUE_DISPATCH_FAILED'}});await p.h.settle();
  assert(statusLabels(p.h).includes(state==='retry_wait'?'投递失败，等待自动重试':'任务已暂停，需要处理后恢复'));
  assert.match(textContent(p.h.tree()),/已尝试投递 2 次/);assert.equal(p.h.storage.get(jobKey('A')),'jobA');
  if(state==='blocked'){assert.equal(p.h.timers.size,0);click(p.h,'刷新任务状态');}
  p.h.fireTimers();await p.h.settle();assert.equal(p.jobReads[1].id,'jobA');
  p.versions.A=[version('A','draft')];p.jobReads[1].resolve({status:'draft',version_id:'versionA1',error_reason:null,delivery:{state:'done',attempt:2,reason_code:null}});await p.h.settle();
  assert.equal(p.h.storage.has(jobKey('A')),false);assert.equal(p.h.timers.size,0);
  assert(!statusLabels(p.h).some(x=>/投递失败|任务已暂停/.test(x)));p.h.unmount();
});
test('E05 late retry metadata cannot replace the next customer generation state',async()=>{
  const p=workbench();route(p.h,'A');await p.h.settle();click(p.h,'生成首个版本');
  p.generations[0].resolve({status:'queued',job_id:'jobA'});await p.h.settle();p.h.fireTimers();await p.h.settle();
  route(p.h,'B');await p.h.settle();click(p.h,'生成首个版本');p.generations[1].resolve({status:'queued',job_id:'jobB'});await p.h.settle();
  p.jobReads[0].resolve({status:'queued',delivery:{state:'blocked',attempt:99,reason_code:'REPORT_ACTOR_REVOKED'}});await p.h.settle();
  assert(!statusLabels(p.h).includes('任务已暂停，需要处理后恢复'));assert.equal(p.h.storage.get(jobKey('B')),'jobB');p.h.unmount();
});
test('E05 wire validates delivery metadata and accepts an older server response',()=>{
  const fs=require('fs'),path=require('path'),vm=require('vm'),ts=require('../node_modules/typescript');
  const cache=new Map();
  function load(file){
    if(cache.has(file))return cache.get(file);
    const module={exports:{}};cache.set(file,module.exports);
    const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
    vm.runInNewContext(code,{module,exports:module.exports,require:name=>load(path.resolve(path.dirname(file),name+'.ts'))});return module.exports;
  }
  const parse=load(path.resolve(__dirname,'../src/adapters/wire.ts')).parseJobStatus;
  const payload={schema:'anhuan-analysis-report-job-v1',job_id:'11111111-1111-4111-8111-111111111111',version_id:'22222222-2222-4222-8222-222222222222',status:'queued',error_reason:null};
  assert.equal(parse(payload).delivery,null);
  const valid={state:'retry_wait',attempt:1,reason_code:'REPORT_QUEUE_DISPATCH_FAILED'};
  assert.equal(parse({...payload,delivery:valid}).delivery.state,'retry_wait');
  for(const bad of [{...valid,state:'unknown'},{...valid,attempt:-1},{...valid,attempt:1.5},{...valid,attempt:101},{...valid,reason_code:'raw backend exception'},{state:'pending',attempt:0}]){
    assert.throws(()=>parse({...payload,delivery:bad}),e=>e.code==='CONTRACT_FIELD_MISSING');
  }
});
for (const outcome of ['queued','draft','failed','network','conflict']) {
  test(`A01 old generation ${outcome} cannot change B job, recovery key or loading`,async()=>{
    const p=workbench();route(p.h,'A');await p.h.settle();click(p.h,'生成首个版本');
    const oldKey=p.h.storage.get(requestKey('A'));
    route(p.h,'B');await p.h.settle();click(p.h,'生成首个版本');
    const newKey=p.h.storage.get(requestKey('B'));assert.notEqual(oldKey,newKey);
    const beforeMessages=p.h.messages.length;
    if(['network','conflict'].includes(outcome))p.generations[0].reject({kind:outcome});
    else p.generations[0].resolve({status:outcome,job_id:'jobA',version_id:'versionA1'});
    await p.h.settle();
    assert.equal(p.h.storage.get(requestKey('B')),newKey);
    assert.equal(p.h.storage.get(requestKey('A')),oldKey);
    assert.equal(p.h.storage.get(jobKey('B')),undefined);
    assert.equal(generationButton(p.h).props.loading,true);
    assert.equal(p.h.messages.length,beforeMessages);
    p.h.fireTimers();await p.h.settle();assert.equal(p.jobReads.length,0);
    p.generations[1].resolve({status:'queued',job_id:'jobB'});await p.h.settle();
    assert.equal(p.h.storage.get(jobKey('B')),'jobB');
    assert.equal(generationButton(p.h).props.loading,false);
    p.h.fireTimers();assert.deepEqual(p.jobReads.map(x=>x.id),['jobB']);p.h.unmount();
  });
}
for(const action of ['publish','withdraw']) {
  for(const destination of ['route','version','unmount']) {
    test(`A01 ${action} confirmation destroyed and inert after ${destination}`,async()=>{
      const p=workbench(action==='publish'?'approved':'published');
      if(destination==='version')p.versions.A.push(version('A',action==='publish'?'approved':'published',2));
      route(p.h,'A');await p.h.settle();click(p.h,action==='publish'?'发布':'撤回');
      const confirmation=p.h.confirmations[0];assert(confirmation);
      if(destination==='route'){route(p.h,'B');await p.h.settle();}
      else if(destination==='version')click(p.h,'第 1 版');
      else p.h.unmount();
      assert.equal(confirmation.destroyed,true);
      await confirmation.onOk();assert.equal(p.transitions.length,0);
      if(destination!=='unmount')p.h.unmount();
    });
  }
}
for(const outcome of ['success','conflict','network']) {
  test(`A01 old transition ${outcome}/finally cannot affect B transition`,async()=>{
    const p=workbench('approved');route(p.h,'A');await p.h.settle();click(p.h,'发布');p.h.confirmations[0].onOk();p.h.render();
    route(p.h,'B');await p.h.settle();click(p.h,'发布');p.h.confirmations[1].onOk();p.h.render();
    assert.deepEqual(p.transitions.map(x=>x.version),['versionA1','versionB1']);
    const before=p.h.messages.length;
    if(outcome==='success')p.transitions[0].resolve({});else p.transitions[0].reject({kind:outcome});
    await p.h.settle();assert.equal(button(p.h,'发布').props.loading,true);assert.equal(p.h.messages.length,before);
    p.transitions[1].resolve({});await p.h.settle();assert.equal(button(p.h,'发布').props.loading,false);assert.equal(p.h.messages.at(-1).m,'已发布');p.h.unmount();
  });
}
test('A01 failed-job follow-up cannot clear a new context recovery request',async()=>{
  const p=workbench();route(p.h,'A');await p.h.settle();click(p.h,'生成首个版本');
  p.generations[0].resolve({status:'failed',job_id:'jobA'});await p.h.settle();assert.equal(p.jobReads.length,1);
  route(p.h,'B');await p.h.settle();click(p.h,'生成首个版本');const key=p.h.storage.get(requestKey('B'));
  p.jobReads[0].resolve({status:'failed',error_reason:'REPORT_LLM_OUTPUT_INVALID'});await p.h.settle();
  assert.equal(p.h.storage.get(requestKey('B')),key);assert.equal(generationButton(p.h).props.loading,true);assert.equal(p.h.messages.length,0);p.h.unmount();
});
test('A01 A to B to A and unmount never revive old callbacks',async()=>{
  const p=workbench();route(p.h,'A');await p.h.settle();click(p.h,'生成首个版本');
  route(p.h,'B');await p.h.settle();route(p.h,'A');await p.h.settle();click(p.h,'恢复生成任务');
  assert.equal(p.generations[0].key,p.generations[1].key);
  p.generations[0].resolve({status:'queued',job_id:'oldJob'});await p.h.settle();assert.equal(p.h.storage.get(jobKey('A')),undefined);
  assert.equal(generationButton(p.h).props.loading,true);
  p.h.unmount();p.generations[1].reject(new Error('late'));await p.h.settle();assert.equal(p.h.lateWrites(),0);assert.equal(p.h.messages.length,0);
});
test('A01 draft recovery requires report membership before selecting version',async()=>{
  const p=workbench();route(p.h,'A');await p.h.settle();click(p.h,'生成首个版本');
  p.generations[0].resolve({status:'draft',version_id:'foreign-version'});await p.h.settle();
  assert(p.h.messages.some(x=>x.m.includes('版本归属校验失败')));assert(!button(p.h,'提交审核'));assert(p.h.storage.has(requestKey('A')));
  click(p.h,'恢复生成任务');p.versions.A=[version('A','draft')];p.generations[1].resolve({status:'draft',version_id:'versionA1'});await p.h.settle();
  assert(button(p.h,'提交审核'));assert.equal(p.h.storage.has(requestKey('A')),false);p.h.unmount();
});
const caseFor=c=>({id:'case'+c,title:c+'服务',status:'planned',client_account_id:c});
const values=c=>({service_case_id:'case'+c,title:c+'客户问题',description:'完整描述',severity:'high',due_at:{toISOString:()=> '2026-09-09T00:00:00Z'}});
function findingPage(){
  const lists=[],creates=[],resets=[];
  const h=harness('pages/console/ClientFindingCreatePage.tsx',{form:{resetFields(){resets.push(true)}},imports:{
    '../../p2Api':{listClientServiceCases:(token,client)=>{const d=deferred();lists.push({client,...d});return d.promise}},
    '../../p2FindingsApi':{createFinding:(token,body)=>{const d=deferred();creates.push({body,...d});return d.promise}}
  }});return {h,lists,creates,resets};
}
for(const outcome of ['success','failure'])test(`A02 out-of-order ${outcome} list preserves B options and rejects A selection`,async()=>{
  const p=findingPage();route(p.h,'A');route(p.h,'B');p.lists[1].resolve({items:[caseFor('B')]});await p.h.settle();
  if(outcome==='success')p.lists[0].resolve({items:[caseFor('A')]});else p.lists[0].reject(new Error('old list failed'));await p.h.settle();
  const choices=walk(p.h.tree()).filter(x=>x.type==='Option').map(x=>x.props.value);assert(choices.includes('caseB'));assert(!choices.includes('caseA'));
  await form(p.h).props.onFinish(values('A'));assert.equal(p.creates.length,0);
  form(p.h).props.onFinish(values('B'));assert.equal(p.creates[0].body.service_case_id,'caseB');p.creates[0].resolve({id:'findingB'});await p.h.settle();
  assert.deepEqual(p.h.navigation,['/console/clients/B/rectification/findingB']);assert.equal(p.resets.length,2);p.h.unmount();
});
for(const outcome of ['success','failure'])test(`A02 old submit ${outcome} and finally cannot navigate or clear B form`,async()=>{
  const p=findingPage();route(p.h,'A');p.lists[0].resolve({items:[caseFor('A')]});await p.h.settle();const oldForm=form(p.h);oldForm.props.onFinish(values('A'));
  route(p.h,'B');p.lists[1].resolve({items:[caseFor('B')]});await p.h.settle();form(p.h).props.onFinish(values('B'));p.h.render();
  await oldForm.props.onFinish(values('A'));assert.equal(p.creates.length,2);
  if(outcome==='success')p.creates[0].resolve({id:'findingA'});else p.creates[0].reject(new Error('old failure'));await p.h.settle();
  assert.equal(button(p.h,'创建问题').props.loading,true);assert.equal(p.h.navigation.length,0);assert.equal(p.h.messages.length,0);
  p.creates[1].resolve({id:'findingB'});await p.h.settle();assert.equal(button(p.h,'创建问题').props.loading,false);assert.deepEqual(p.h.navigation,['/console/clients/B/rectification/findingB']);p.h.unmount();
});
function reportsPage(){const creates=[],archives=[];const api={
 listClientReports:async c=>[{report_id:'report'+c,title:c+'报告',current_status:'draft',archived_at:null,updated_at:'2026-09-08T00:00:00Z'}],
 createReport:(client,key)=>{const d=deferred();creates.push({client,key,...d});return d.promise},
 archiveReport:(id)=>{const d=deferred();archives.push({id,...d});return d.promise},
};return {h:harness('pages/console/ClientReportsPage.tsx',{api,narrow:true}),creates,archives};}
for(const createOutcome of ['success','failure'])for(const archiveOutcome of ['success','failure'])test(`A06 separate loading slots: create ${createOutcome}, archive ${archiveOutcome}`,async()=>{
 const p=reportsPage();route(p.h,'A');await p.h.settle();click(p.h,'新建报告');click(p.h,'归档');modal(p.h).props.onOk();p.h.render();
 if(archiveOutcome==='success')p.archives[0].resolve({});else p.archives[0].reject(new Error('archive fail'));await p.h.settle();
 assert.equal(button(p.h,'新建报告').props.loading,true);
 if(createOutcome==='success')p.creates[0].resolve({report_id:'newA'});else p.creates[0].reject(new Error('create fail'));await p.h.settle();
 assert.equal(button(p.h,'新建报告').props.loading,false);assert.equal(button(p.h,'归档').props.loading,false);
 if(createOutcome==='failure'){click(p.h,'新建报告');assert.equal(p.creates[0].key,p.creates[1].key);p.creates[1].resolve({report_id:'retryA'});await p.h.settle();}
 p.h.unmount();
});
test('A06 previous client create finally does not release new client loading',async()=>{
 const p=reportsPage();route(p.h,'A');await p.h.settle();click(p.h,'新建报告');route(p.h,'B');await p.h.settle();assert.equal(button(p.h,'新建报告').props.loading,false);click(p.h,'新建报告');
 p.creates[0].reject(new Error('A'));await p.h.settle();assert.equal(button(p.h,'新建报告').props.loading,true);assert.equal(p.h.messages.length,0);
 p.creates[1].resolve({report_id:'newB'});await p.h.settle();assert.deepEqual(p.h.navigation,['/console/clients/B/reports/newB']);p.h.unmount();
});
function servicesPage(){const creates=[];const ports={listClientServiceCases:async()=>({items:[],allowed_actions:['create']}),createClientServiceCase:(token,client,body)=>{const d=deferred();creates.push({client,body,...d});return d.promise}};return {h:harness('pages/console/ClientServicesPage.tsx',{imports:{'../../p2Api':ports}}),creates};}
const serviceForm=h=>walk(h.tree()).find(n=>n.type==='../../components/ServiceCaseForm');
for(const outcome of ['success','failure'])test(`A07 A create ${outcome} does not close, clear, unlock or message B form`,async()=>{
 const p=servicesPage();route(p.h,'A');await p.h.settle();click(p.h,'新建服务事项');const old=serviceForm(p.h);old.props.onSubmit({title:'A服务'});
 route(p.h,'B');await p.h.settle();click(p.h,'新建服务事项');assert.equal(serviceForm(p.h).props.submitting,false);serviceForm(p.h).props.onSubmit({title:'B服务'});p.h.render();
 await old.props.onSubmit({title:'stale'});assert.equal(p.creates.length,2);
 if(outcome==='success')p.creates[0].resolve({id:'caseA'});else p.creates[0].reject(new Error('A fail'));await p.h.settle();
 assert.equal(modal(p.h).props.open,true);assert.equal(serviceForm(p.h).props.submitting,true);assert.equal(p.h.messages.length,0);
 p.creates[1].resolve({id:'caseB'});await p.h.settle();assert.equal(modal(p.h).props.open,false);assert.equal(serviceForm(p.h).props.submitting,false);assert.equal(p.h.messages.length,1);p.h.unmount();
});
const documents=(client='A',count=101,active=false)=>Array.from({length:count},(_,i)=>({id:client+'doc'+(i+1),display_name:'材料'+(i+1),declared_material_kind:'unknown',knowledge_scope:{kind:'client',client_account_id:client},status:'ready',version_count:1,latest_version:{id:client+'version'+i,workflow_status:active?'processing':'ready',quarantine_status:'released',scan_status:'clean',preview_status:active?'pending':'ready',allowed_actions:[]},created_at:'2026-09-08T00:00:00Z',updated_at:'2026-09-08T00:00:00Z',allowed_actions:[]}));
const capabilities={upload_enabled:true,allowed_types:[{content_type:'application/pdf',extensions:['.pdf'],max_file_bytes:1024}],limits:{max_file_bytes:1024},scanner:{state:'ready'}};
const renderedDocuments=(h,narrow)=>narrow?walk(h.tree()).filter(n=>n.type==='Button'&&/^材料\d+$/.test(textContent(n))).map(n=>textContent(n)):walk(h.tree()).find(n=>n.type==='Table').props.dataSource.map(d=>d.display_name);
function materialsPage(narrow,loader){const calls=[];const ports={getIngestionCapabilities:async()=>capabilities,listIngestionDocuments:(token,params)=>{calls.push(params);return loader(params,calls.length)},userFacingIngestionError:e=>String(e)};return {h:harness('components/MaterialPanel.tsx',{narrow,props:{scope:'client',clientId:'A'},imports:{'../features/p3/ingestionApi':ports}}),calls};}
for(const narrow of [false,true])test(`A05 ${narrow?'mobile':'desktop'} fetches document 101 and retains it through active refresh`,async()=>{
 const docs=documents('A',101,true);
 const p=materialsPage(narrow,async params=>params.cursor?{items:docs.slice(100),next_cursor:null}:{items:docs.slice(0,100),next_cursor:'after100'});
 p.h.render();await p.h.settle();assert.equal(renderedDocuments(p.h,narrow).length,100);assert(textContent(p.h.tree()).includes('已加载 100 份'));assert(!textContent(p.h.tree()).includes('共 100 份'));
 click(p.h,'加载更多材料');await p.h.settle();assert.equal(p.calls[1].cursor,'after100');assert.equal(renderedDocuments(p.h,narrow).length,101);assert(renderedDocuments(p.h,narrow).includes('材料101'));assert(textContent(p.h.tree()).includes('共 101 份'));assert(!button(p.h,'加载更多材料'));
 p.h.fireTimers();await p.h.settle();assert.equal(p.calls.length,4);assert.equal(p.calls[3].cursor,'after100');assert.equal(renderedDocuments(p.h,narrow).length,101);p.h.unmount();
});
test('A05 page failure retains existing rows and same cursor is retryable',async()=>{
 const docs=documents();let fail=true;const p=materialsPage(false,async params=>{if(params.cursor&&fail)throw Error('page fail');return params.cursor?{items:docs.slice(99),next_cursor:null}:{items:docs.slice(0,100),next_cursor:'after100'}});
 p.h.render();await p.h.settle();click(p.h,'加载更多材料');await p.h.settle();assert.equal(renderedDocuments(p.h,false).length,100);assert.equal(button(p.h,'重试加载更多材料').props.loading,false);
 fail=false;click(p.h,'重试加载更多材料');await p.h.settle();assert.deepEqual(p.calls.slice(1).map(x=>x.cursor),['after100','after100']);assert.equal(renderedDocuments(p.h,false).length,101);p.h.unmount();
});
for(const outcome of ['success','failure'])test(`A05 stale ${outcome} page cannot append into B or clear B loading`,async()=>{
 const pending=[];const p=materialsPage(false,params=>{const docs=documents(params.clientAccountId);if(params.cursor){const d=deferred();pending.push({client:params.clientAccountId,...d});return d.promise;}return Promise.resolve({items:docs.slice(0,100),next_cursor:'after100'});});
 p.h.render();await p.h.settle();click(p.h,'加载更多材料');p.h.setProps({scope:'client',clientId:'B'});await p.h.settle();click(p.h,'加载更多材料');
 if(outcome==='success')pending[0].resolve({items:documents('A').slice(100),next_cursor:null});else pending[0].reject(Error('old page'));await p.h.settle();
 assert.equal(button(p.h,'加载更多材料').props.loading,true);assert.equal(renderedDocuments(p.h,false).length,100);
 pending[1].resolve({items:documents('B').slice(100),next_cursor:null});await p.h.settle();assert.equal(renderedDocuments(p.h,false).length,101);const table=walk(p.h.tree()).find(n=>n.type==='Table');assert(table.props.dataSource.every(d=>d.knowledge_scope.client_account_id==='B'));p.h.unmount();
});
for(const path of ['http','accepted-failed','poll-failed'])test(`outdated source index uses actionable copy on ${path}`,async()=>{
 const {ApiError}=require('./lib/offline-tsx-harness.cjs');const p=workbench();route(p.h,'A');await p.h.settle();click(p.h,'生成首个版本');
 if(path==='http')p.generations[0].reject(new ApiError(409,'REPORT_SOURCE_INDEX_OUTDATED',false));
 else {
   p.generations[0].resolve({status:path==='poll-failed'?'queued':'failed',job_id:'jobA'});await p.h.settle();
   if(path==='poll-failed')p.h.fireTimers();
   p.jobReads[0].resolve({status:'failed',error_reason:'REPORT_SOURCE_INDEX_OUTDATED'});
 }
 await p.h.settle();assert.equal(p.h.messages.at(-1).m,'资料索引需更新，请重新处理相关材料后再生成');assert.equal(p.h.storage.has(requestKey('A')),false);p.h.unmount();
});
for(const outcome of ['draft','failed','network','permanent'])test(`A01 old poll ${outcome} cannot clean B storage or restart A reads`,async()=>{
 const p=workbench();route(p.h,'A');await p.h.settle();click(p.h,'生成首个版本');p.generations[0].resolve({status:'queued',job_id:'jobA'});await p.h.settle();p.h.fireTimers();assert.equal(p.jobReads.length,1);
 route(p.h,'B');await p.h.settle();click(p.h,'生成首个版本');const key=p.h.storage.get(requestKey('B'));const count=p.h.messages.length;
 if(outcome==='network')p.jobReads[0].reject({kind:'network'});
 else if(outcome==='permanent')p.jobReads[0].reject({kind:'notFound'});
 else p.jobReads[0].resolve({status:outcome,version_id:'versionA1',error_reason:'REPORT_LLM_OUTPUT_INVALID'});
 await p.h.settle();assert.equal(p.h.storage.get(requestKey('B')),key);assert.equal(p.h.messages.length,count);assert.equal(generationButton(p.h).props.loading,true);assert.equal(p.h.storage.get(jobKey('A')),'jobA');p.h.unmount();
});
for(const outcome of ['success','failure'])test(`A01 old download ${outcome} cannot notify B or clear its downloading state`,async()=>{
 const p=workbench('approved');route(p.h,'A');await p.h.settle();click(p.h,'下载 PDF 报告');route(p.h,'B');await p.h.settle();click(p.h,'下载 PDF 报告');
 if(outcome==='success')p.downloads[0].resolve({});else p.downloads[0].reject(Error('A failed'));await p.h.settle();assert.equal(button(p.h,'下载 PDF 报告').props.loading,true);assert.equal(p.h.messages.length,0);
 p.downloads[1].resolve({});await p.h.settle();assert.equal(button(p.h,'下载 PDF 报告').props.loading,false);assert.equal(p.h.messages.length,1);p.h.unmount();
});
for(const page of ['finding','services'])test(`unmounted ${page} submit does not write React state or navigate`,async()=>{
 const p=page==='finding'?findingPage():servicesPage();route(p.h,'A');
 if(page==='finding'){p.lists[0].resolve({items:[caseFor('A')]});await p.h.settle();form(p.h).props.onFinish(values('A'));}
 else {await p.h.settle();click(p.h,'新建服务事项');serviceForm(p.h).props.onSubmit({title:'A服务'});}
 p.h.unmount();p.creates[0].resolve({id:'resultA'});await p.h.settle();assert.equal(p.h.lateWrites(),0);assert.equal(p.h.navigation.length,0);assert.equal(p.h.messages.length,0);
});
test('A05 a cancelled refresh cannot replace the newly loaded second page',async()=>{
 const docs=documents('A',101,true),refresh=deferred();let calls=0;
 const p=materialsPage(false,async params=>{calls++;if(calls===2)return refresh.promise;return params.cursor?{items:docs.slice(100),next_cursor:null}:{items:docs.slice(0,100),next_cursor:'after100'};});
 p.h.render();await p.h.settle();p.h.fireTimers();click(p.h,'加载更多材料');await p.h.settle();assert.equal(renderedDocuments(p.h,false).length,101);
 refresh.resolve({items:docs.slice(0,100),next_cursor:'after100'});await p.h.settle();assert.equal(renderedDocuments(p.h,false).length,101);assert(!button(p.h,'加载更多材料'));p.h.unmount();
});


// Exercise production transport + adapter with real loopback HTTP responses.
// UI rendering still uses offline hook/Ant Design ports; ApiError is never stubbed.
async function reportHttpFixture(t, status, detail) {
  const http=require('node:http'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
  const ts=require('../node_modules/typescript');
  const requests=[];
  const server=http.createServer(async(req,res)=>{
    let body='';for await(const chunk of req)body+=chunk;
    requests.push({method:req.method,url:req.url,headers:req.headers,body});
    res.writeHead(status,{'Content-Type':'application/json'});
    res.end(JSON.stringify({detail}));
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  t.after(()=>new Promise((resolve,reject)=>{server.close(error=>error?reject(error):resolve());server.closeAllConnections();}));
  const base=`http://127.0.0.1:${server.address().port}`;
  const cache=new Map(),storage=new Map();
  const globals={AbortController,Event,console,URL,URLSearchParams,
    localStorage:{getItem:key=>storage.get(key)??null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)},
    window:{addEventListener(){},dispatchEvent(){}},fetch:(url,options)=>fetch(new URL(url,base),options)};
  function load(file){
    if(cache.has(file))return cache.get(file);
    const mod={exports:{}};cache.set(file,mod.exports);
    const code=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
    vm.runInNewContext(code,{...globals,module:mod,exports:mod.exports,require:name=>{
      assert(name.startsWith('.'),`unexpected external module: ${name}`);
      return load(path.resolve(path.dirname(file),name.endsWith('.ts')?name:name+'.ts'));
    }});
    return mod.exports;
  }
  const transport=load(path.resolve(__dirname,'../src/api.ts'));
  const errors=load(path.resolve(__dirname,'../src/adapters/errors.ts'));
  transport.commitTenantSnapshot('tenant',transport.getTenantGeneration());
  const {HttpAnalysisReportApi}=load(path.resolve(__dirname,'../src/adapters/HttpAnalysisReportApi.ts'));
  return {transport,errors,http:new HttpAnalysisReportApi(()=> 'transport-regression-token'),requests};
}

// Unlike deferred-only tests, loopback I/O needs the handler's request to settle.
async function settleHttp(h, done) {
  const deadline=Date.now()+3000;
  while(!done()){
    await new Promise(resolve=>setTimeout(resolve,5));await h.settle();
    assert(Date.now()<deadline,'HTTP handler did not settle');
  }
  await h.settle();
}

test('real transport and report adapters expose the same ApiError constructor',async t=>{
  const f=await reportHttpFixture(t,409,{code:'REPORT_SOURCE_INDEX_OUTDATED',retryable:false});
  let caught;
  try{await f.http.generate('A','reportA','request-id');}catch(error){caught=error;}
  assert(caught instanceof f.transport.ApiError);
  assert(caught instanceof f.errors.ApiError);
  assert.equal(f.transport.ApiError,f.errors.ApiError);
  assert.equal(caught.status,409);assert.equal(caught.code,'REPORT_SOURCE_INDEX_OUTDATED');assert.equal(caught.retryable,false);
  assert.equal(f.requests[0].headers['x-enterprise-id'],'tenant');
  assert.equal(f.requests[0].headers.authorization,'Bearer transport-regression-token');
});

for(const code of ['REPORT_SOURCE_INDEX_OUTDATED','REPORT_REQUEST_CONFLICT'])test(`real HTTP 409 ${code} reaches the matching report generation branch`,async t=>{
  const f=await reportHttpFixture(t,409,{code,retryable:false});
  const p=workbench('empty',{imports:{'../../adapters/errors':f.errors}});
  let finished=false;
  p.api.generate=async(...args)=>{try{return await f.http.generate(...args);}finally{finished=true;}};
  route(p.h,'A');await p.h.settle();click(p.h,'生成首个版本');
  const key=p.h.storage.get(requestKey('A'));assert(key);
  await settleHttp(p.h,()=>finished);
  assert.deepEqual(p.h.messages.at(-1),{kind:'warning',m:code==='REPORT_SOURCE_INDEX_OUTDATED'?'资料索引需更新，请重新处理相关材料后再生成':'请求冲突，已为你刷新'});
  assert.equal(p.h.storage.has(requestKey('A')),false);assert.equal(p.h.storage.has(jobKey('A')),false);
  assert.equal(generationButton(p.h).props.loading,false);
  assert.equal(f.requests.length,1);assert.equal(f.requests[0].method,'POST');
  assert.equal(f.requests[0].url,'/api/v1/analysis-reports/clients/A/reports/reportA/generations');
  assert.equal(JSON.parse(f.requests[0].body).request_id,key);p.h.unmount();
});

for(const [status,retryable] of [[503,false],[409,true]])test(`real HTTP ${status} polling honors explicit retryable=${retryable}`,async t=>{
  const f=await reportHttpFixture(t,status,{code:'REPORT_JOB_UNAVAILABLE',retryable});
  const p=workbench('empty',{imports:{'../../adapters/errors':f.errors}});
  let finished=false;
  p.api.getJob=async(...args)=>{try{return await f.http.getJob(...args);}finally{finished=true;}};
  route(p.h,'A');await p.h.settle();click(p.h,'生成首个版本');
  const key=p.h.storage.get(requestKey('A'));
  p.generations[0].resolve({status:'queued',job_id:'jobA'});await p.h.settle();
  p.h.fireTimers();await settleHttp(p.h,()=>finished);
  assert.equal(p.h.storage.get(requestKey('A')),key);
  assert.equal(p.h.storage.has(jobKey('A')),retryable);
  if(retryable){
    assert.equal(p.h.messages.length,0);
    assert(button(p.h,'重新查询'),'retryable job must retain the manual polling action');
  }else{
    assert.equal(p.h.messages.at(-1).m,'生成任务已失效，请恢复原请求');
    assert.equal(p.h.timers.size,0);
  }
  assert.equal(f.requests.length,1);assert.equal(f.requests[0].method,'GET');
  assert.equal(f.requests[0].url,'/api/v1/analysis-reports/jobs/jobA');p.h.unmount();
});
