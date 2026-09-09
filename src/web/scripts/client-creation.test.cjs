// Actual TSX handlers/adapters with controlled UI and transport ports. No browser/DB.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const ts=require('../node_modules/typescript');
const {harness,deferred,walk,textContent}=require('./lib/offline-tsx-harness.cjs');
class ApiError extends Error {constructor(status,code,retryable){super(code);Object.assign(this,{status,code,retryable});}}
function load(file,ports={},env={},globals={}) {
 const mod={exports:{}};
 const source=fs.readFileSync(path.resolve(__dirname,'../src',file),'utf8').replaceAll('import.meta.env','__env');
 const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
 const requirePort=name=>ports[name]??(name.endsWith('/errors')||name==='./errors'?{ApiError}:
   name.startsWith('.')?load(path.posix.normalize(path.posix.join(path.posix.dirname(file),name))+'.ts',ports,env,globals):{});
 vm.runInNewContext(code,{module:mod,exports:mod.exports,require:requirePort,console,crypto,__env:env,setTimeout:fn=>fn(),Blob,Map,URL,...globals});
 return mod.exports;
}
const helper=load('adapters/clientCreation.ts');
const types=load('adapters/types.ts');
const A='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',B='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const id=n=>`20000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
const raw=(eid=A)=>({id:id(1),enterprise_id:eid,display_name:'后来编辑的名称',stage:'active',industry_note:null,region_note:null,updated_at:'2026-09-09T01:00:00Z',next_follow_up_at:null});
const client=()=>({id:id(1),name:'客户 A',stage:'lead',updatedAt:'2026-09-09T01:00:00Z'});
const button=(h,label)=>walk(h.tree()).find(n=>n.type==='Button'&&textContent(n)===label);
function screen(legacy=false,storage=new Map()){
 const storageApi={getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)};
 const pending=load('adapters/pendingWrites.ts',{}, {}, {sessionStorage:storageApi,TextEncoder,
 crypto:{randomUUID:crypto.randomUUID,subtle:{digest:async(_alg,bytes)=>crypto.createHash('sha256').update(bytes).digest()}}});
 const reads=[],writes=[],validations=[];
 const state={session:{enterprise_id:A,product_role:'provider_admin',membership_role:'enterprise_admin'},loading:false,error:null};
 const auth={user:{profile:{sub:'subject-A'}},getAccessToken:()=> 'token',isInitializing:false};
 const formState={name:' 客户 A ',stage:'lead',defer:false};
 const api={listClients:()=>{const d=deferred();reads.push(d);return d.promise;},createClient:input=>{const d=deferred();writes.push({input,...d});return d.promise;}};
 const imports=legacy?{
  '../../../adapters':{useSessionAccess:()=>state},'../../../auth/OidcProvider':{useAuth:()=>auth},
  '../../../adapters/clientCreation':helper,'../../../adapters/pendingWrites':pending,
  '../viewsReportsApi':{listCrmAccounts:()=>api.listClients(),createCrmAccount:(_token,input)=>api.createClient(input),userFacingViewsReportsError:()=> '读取失败'},
  '../reasonCopy':{crmStageCopy:s=>s,formatP4DateTime:s=>s,stageColor:()=> 'blue'},
  antd:{Grid:{useBreakpoint:()=>({md:true})},List:Object.assign(function List(){},{Item:'ListItem'}),Button:'Button',Alert:'Alert',Empty:'Empty',Space:'Space',Spin:'Spin',Table:'Table',Tag:'Tag',Typography:{Title:'Title',Text:'Text',Paragraph:'Paragraph'}},
 }:{'../../adapters':{useApi:()=>api,useSessionAccess:()=>state,isMockData:false},'../../auth/OidcProvider':{useAuth:()=>auth},'../../adapters/types':types,'../../adapters/clientCreation':helper,'../../adapters/pendingWrites':pending};
 const h=harness(legacy?'features/p4/pages/CrmAccountListPage.tsx':'pages/console/ClientsPage.tsx',{imports,form:{
  validateFields:()=>{const d=deferred();validations.push(d);if(!formState.defer)d.resolve({name:formState.name,stage:formState.stage});return d.promise;},
 }});
 h.render();return {h,state,auth,reads,writes,validations,formState,legacy,storage,pending};
}
async function ready(p){p.reads[0].resolve(p.legacy?{items:[],allowed_actions:['create']}:[]);await p.h.settle();}
const modal=p=>walk(p.h.tree()).find(n=>p.legacy?String(n.type).endsWith('/CrmAccountModal'):n.type===p.h.Modal);
function open(p){button(p.h,p.legacy?'新建客户档案':'新建客户').props.onClick();p.h.render();}
function submit(p,input={display_name:' 客户 A ',stage:'lead'}){const m=modal(p);if(p.legacy)m.props.onSubmit(input);else m.props.onOk();p.h.render();}
const key=p=>p.writes.at(-1).input[p.legacy?'request_id':'requestId'];
for(const legacy of [false,true]){
 const label=legacy?'legacy CRM':'console';
 test(`${label}: cleanup fault after deleting identity cannot turn confirmed success into retry`,async()=>{
  class DeleteThenFail extends Map { delete(key){super.delete(key);throw new Error('storage changed during cleanup')} }
  const storage=new DeleteThenFail();const p=screen(legacy,storage);await ready(p);open(p);submit(p);await p.h.settle();
  p.writes[0].resolve(legacy?raw():client());await p.h.settle();assert.deepEqual(p.h.navigation,[legacy?'/crm/'+id(1):'/console/clients/'+id(1)]);
  assert.equal(storage.size,0);assert(!walk(p.h.tree()).some(n=>JSON.stringify(n.props).includes('浏览器未能保存')));p.h.unmount();
 });

 test(`${label}: reload retains unknown request, confirmed success clears only that command`,async()=>{
  const storage=new Map();const p=screen(legacy,storage);await ready(p);open(p);submit(p);await p.h.settle();const original=key(p);
  p.writes[0].reject(new Error('lost response'));await p.h.settle();p.h.unmount();
  const q=screen(legacy,storage);await ready(q);open(q);submit(q);await q.h.settle();assert.equal(key(q),original);
  q.writes[0].resolve(legacy?raw():client());await q.h.settle();assert.equal(storage.size,0);q.h.unmount();
  const r=screen(legacy,storage);await ready(r);open(r);submit(r);await r.h.settle();assert.notEqual(key(r),original);r.h.unmount();
 });
 test(`${label}: storage failure prevents POST`,async()=>{
  const broken={get(){throw new Error('storage denied')},set(){throw new Error('storage denied')}};
  const p=screen(legacy,broken);await ready(p);open(p);submit(p);await p.h.settle();assert.equal(p.writes.length,0);assert(walk(p.h.tree()).some(n=>JSON.stringify(n.props).includes('浏览器未能保存')));p.h.unmount();
 });

 test(`${label}: real create is visible and issues one canonical request then navigates`,async()=>{
  const p=screen(legacy);await ready(p);open(p);submit(p);submit(p);await p.h.settle();assert.equal(p.writes.length,1);assert.match(key(p),/^[0-9a-f-]{36}$/);
  assert.equal(p.writes[0].input[legacy?'display_name':'name'],'客户 A');assert.equal(p.writes[0].input.stage,'lead');
  p.writes[0].resolve(legacy?raw():client());await p.h.settle();assert.deepEqual(p.h.navigation,[legacy?'/crm/'+id(1):'/console/clients/'+id(1)]);p.h.unmount();
 });
 test(`${label}: unknown outcome, close/reopen and A-B-A drafts preserve original request id`,async()=>{
  const p=screen(legacy);await ready(p);open(p);submit(p);await p.h.settle();const original=key(p);
  p.writes[0].reject(new Error('response lost'));await p.h.settle();modal(p).props.onCancel();p.h.render();open(p);submit(p);await p.h.settle();assert.equal(key(p),original);
  p.writes[1].reject({code:'CRM_ACCOUNT_UNAVAILABLE'});await p.h.settle();p.formState.name='客户 B';submit(p,{display_name:'客户 B',stage:'lead'});await p.h.settle();assert.notEqual(key(p),original);
  p.writes[2].reject(new Error('response lost'));await p.h.settle();p.formState.name='客户 A';submit(p);await p.h.settle();assert.equal(key(p),original);p.h.unmount();
 });
 for(const change of ['enterprise','subject','role','loading'])for(const result of ['success','failure'])test(`${label}: ${change} fences old POST ${result}`,async()=>{
  const p=screen(legacy);await ready(p);open(p);const oldSubmit=legacy?modal(p).props.onSubmit:modal(p).props.onOk;submit(p);await p.h.settle();
  if(change==='enterprise')p.state.session={...p.state.session,enterprise_id:B};
  if(change==='subject')p.auth.user={profile:{sub:'subject-B'}};
  if(change==='role')p.state.session={...p.state.session,product_role:'provider_consultant',membership_role:'plant_admin'};
  if(change==='loading')p.state.loading=true;
  p.h.render();if(result==='success')p.writes[0].resolve(legacy?raw():client());else p.writes[0].reject(new Error('old error'));
  await p.h.settle();oldSubmit({display_name:'old',stage:'lead'});await p.h.settle();assert.equal(p.writes.length,1);assert.equal(p.h.navigation.length,0);assert.equal(p.h.messages.length,0);assert(!textContent(p.h.tree()).includes('创建结果尚未确认'));p.h.unmount();
 });
 test(`${label}: A-B-A does not revive old commands and unmount has no late state writes`,async()=>{
  const p=screen(legacy);await ready(p);open(p);submit(p);await p.h.settle();const old=key(p);
  p.state.session={...p.state.session,enterprise_id:B};p.h.render();p.state.session={...p.state.session,enterprise_id:A};p.h.render();
  p.reads.at(-1).resolve(legacy?{items:[],allowed_actions:['create']}:[]);await p.h.settle();open(p);submit(p);await p.h.settle();assert.equal(key(p),old);
  p.writes[0].resolve(legacy?raw():client());await p.h.settle();assert.equal(p.h.navigation.length,0);
  p.h.unmount();p.writes[1].reject(new Error('late'));await p.h.settle();assert.equal(p.h.lateWrites(),0);
 });
 for(const role of ['technical_admin','client_user','provider_consultant','unconfigured'])test(`${label}: ${role} has no create entry`,async()=>{
  const p=screen(legacy);p.state.session.product_role=role;p.h.render();p.reads.at(-1).resolve(legacy?{items:[],allowed_actions:['create']}:[]);await p.h.settle();assert(!button(p.h,legacy?'新建客户档案':'新建客户'));p.h.unmount();
 });
 test(`${label}: older subject list cannot appear under a new subject`,async()=>{
  const p=screen(legacy);p.auth.user={profile:{sub:'subject-B'}};p.h.render();
  p.reads[0].resolve(legacy?{items:[raw()],allowed_actions:['create']}:[client()]);await p.h.settle();
  assert(!walk(p.h.tree()).some(n=>n.props?.dataSource?.length));p.h.unmount();
 });
}
test('console: validateFields is single-flight and cannot send after identity changes',async()=>{
 const p=screen();await ready(p);open(p);p.formState.defer=true;submit(p);submit(p);assert.equal(p.validations.length,1);
 p.auth.user={profile:{sub:'subject-B'}};p.h.render();p.validations[0].resolve({name:'old client',stage:'lead'});await p.h.settle();assert.equal(p.writes.length,0);assert.equal(p.h.navigation.length,0);p.h.unmount();
});
test('console: failed validation does not send and permits a corrected submit',async()=>{
 const p=screen();await ready(p);open(p);p.formState.defer=true;submit(p);p.validations[0].reject({errorFields:['name']});await p.h.settle();assert.equal(p.writes.length,0);
 p.formState.defer=false;submit(p);await p.h.settle();assert.equal(p.writes.length,1);p.h.unmount();
});
test('actual legacy modal: validation double click, context switch and unmount are fenced',async()=>{
 const validations=[],writes=[];let props={open:true,contextKey:'A',onCancel(){},onSubmit:async value=>writes.push(value)};
 const h=harness('features/p4/components/CrmAccountModal.tsx',{props,form:{setFieldsValue(){},validateFields:()=>{const d=deferred();validations.push(d);return d.promise;}}});
 h.render();h.tree().props.onOk();h.tree().props.onOk();h.render();assert.equal(validations.length,1);
 props={...props,contextKey:'B'};h.setProps(props);validations[0].resolve({display_name:'old',stage:'lead'});await h.settle();assert.equal(writes.length,0);
 h.tree().props.onOk();h.render();h.unmount();validations[1].resolve({display_name:'late',stage:'lead'});await h.settle();assert.equal(writes.length,0);assert.equal(h.lateWrites(),0);
});
test('actual legacy modal: unknown-result draft survives close and reopen',async()=>{
 const sets=[];const props={open:true,contextKey:'A',onCancel(){},onSubmit:async()=>{}};
 const h=harness('features/p4/components/CrmAccountModal.tsx',{props,form:{setFieldsValue:value=>sets.push(value)}});h.render();assert.equal(sets.length,1);
 h.setProps({...props,open:false});h.tree().props.afterOpenChange(false);h.setProps(props);assert.equal(sets.length,1);h.unmount();
});
test('canonical signature includes all create fields and normalized defaults',()=>{
 const base=helper.normalizeClientCreate({display_name:' A ',stage:'lead'});
 assert.deepEqual(JSON.parse(JSON.stringify(base)),{display_name:'A',stage:'lead',owner_user_id:null,industry_note:null,region_note:null,next_follow_up_at:null});
 for(const extra of [{owner_user_id:id(2)},{industry_note:'行业'},{region_note:'区域'},{next_follow_up_at:'2026-09-09T09:00:00+08:00'}])assert.notEqual(JSON.stringify(helper.normalizeClientCreate({display_name:'A',stage:'lead',...extra})),JSON.stringify(base));
 assert.throws(()=>helper.normalizeClientCreate({display_name:'  ',stage:'lead'}));
});
test('actual HTTP facade sends required request id and binds current-field receipt to frozen tenant',async()=>{
 const calls=[];let response={payload:raw(),enterpriseId:A,status:201};
 const {HttpAnalysisReportApi}=load('adapters/HttpAnalysisReportApi.ts',{'../api':{tenantFetch:async(path,options)=>{calls.push({path,options});return response;}},'./MembershipApi':{HttpMembershipApi:class{}},'../features/managementHealth':{},'./wire':{},'./AnalysisReportApi':{}});
 const api=new HttpAnalysisReportApi(()=> 'token');const input={name:' A ',stage:'lead',requestId:id(10)};
 const result=await api.createClient(input);assert.equal(result.name,'后来编辑的名称');assert.equal(result.stage,'active');assert.equal(calls[0].path,'/v1/views-reports/crm/accounts');assert.equal(calls[0].options.body.request_id,id(10));assert.equal(calls[0].options.body.display_name,'A');assert(!('enterprise_id' in calls[0].options.body));
 response={...response,enterpriseId:B};await assert.rejects(api.createClient(input),error=>error.code==='CRM_ACCOUNT_ENTERPRISE_MISMATCH');
 const count=calls.length;await assert.rejects(api.createClient({...input,requestId:''}));assert.equal(calls.length,count);
});
test('actual legacy transport always sends request_id and checks the tenant snapshot',async()=>{
 const calls=[];let response={payload:raw(),enterpriseId:A,status:201};
 const api=load('features/p4/viewsReportsApi.ts',{'../../api':{ApiError,tenantFetch:async(path,options)=>{calls.push({path,options});return response;}},'./reasonCopy':{p4ReasonCopy:code=>code},'../../adapters/clientCreation':helper});
 await api.createCrmAccount('token',{display_name:' A ',stage:'lead',request_id:id(10)});assert.equal(calls[0].options.body.request_id,id(10));
 await api.createCrmAccount('token',{display_name:' A ',stage:'lead'});assert.match(calls[1].options.body.request_id,/^[0-9a-f-]{36}$/);
 response={...response,enterpriseId:B};await assert.rejects(api.createCrmAccount('token',{display_name:'A',stage:'lead',request_id:id(10)}),error=>error.code==='CRM_ACCOUNT_ENTERPRISE_MISMATCH');
});
test('create receipt rejects malformed/current-tenant-unsafe responses',()=>{
 for(const change of [r=>r.id='wrong',r=>r.stage='invented',r=>r.updated_at='yesterday',r=>r.display_name='',r=>delete r.region_note]){const r=raw();change(r);assert.throws(()=>helper.assertClientCreateReceipt(r,A,201));}
 assert.throws(()=>helper.assertClientCreateReceipt(raw(),A,200));assert.throws(()=>helper.assertClientCreateReceipt(raw(),null,201));
 helper.assertClientCreateReceipt(raw(),A,201);
});
test('actual mock: parallel retry creates once, changed payload conflicts, current values replay and revoked role rejects',async()=>{
 const {MockAnalysisReportApi}=load('adapters/MockAnalysisReportApi.ts',{'../api':{ApiError},'../features/managementHealth':{}},{});
 const api=new MockAnalysisReportApi();const input={name:' A ',stage:'lead',requestId:id(10)};const before=(await api.listClients()).length;
 const [first,second]=await Promise.all([api.createClient(input),api.createClient({...input,name:'A'})]);assert.equal(first.id,second.id);assert.equal((await api.listClients()).length,before+1);
 await assert.rejects(api.createClient({...input,name:'B'}),error=>error.code==='CRM_ACCOUNT_REQUEST_CONFLICT');
 api.clients.find(row=>row.id===first.id).name='已编辑';assert.equal((await api.createClient(input)).name,'已编辑');
 api.role='client_user';await assert.rejects(api.createClient(input),error=>error.code==='CRM_MANAGER_REQUIRED');
});

test('pending identity stores no raw business data and partitions actor, enterprise, payload and operation',async()=>{
 const storage=new Map();const p=screen(false,storage);const operation='crm.account.create';
 const first=await p.pending.pendingWrite(operation,A,'subject-A','private customer note');
 const repeated=await p.pending.pendingWrite(operation,A,'subject-A','private customer note');assert.equal(first.requestId,repeated.requestId);
 for(const args of [[operation,B,'subject-A','private customer note'],[operation,A,'subject-B','private customer note'],[operation,A,'subject-A','different'],['other',A,'subject-A','private customer note']]){
  assert.notEqual((await p.pending.pendingWrite(...args)).requestId,first.requestId);
 }
 assert(!JSON.stringify([...storage]).includes('private'));assert(!JSON.stringify([...storage]).includes('subject-A'));
 p.pending.completePendingWrite(first);assert.equal(storage.size,4);p.h.unmount();
});
test('unreadable pending identity is retained and refuses a new identity',async()=>{
 const storage=new Map();const p=screen(false,storage);const args=['crm.account.create',A,'subject-A','input'];
 const ticket=await p.pending.pendingWrite(...args);storage.set(ticket.key,'corrupted');
 await assert.rejects(p.pending.pendingWrite(...args),e=>e.code==='PENDING_WRITE_STORAGE_UNAVAILABLE');assert.equal(storage.get(ticket.key),'corrupted');p.h.unmount();
});
test('console and legacy CRM recover the same unresolved command in one browser tab',async()=>{
 const storage=new Map();const p=screen(false,storage);await ready(p);open(p);submit(p);await p.h.settle();const original=key(p);
 p.writes[0].reject(new Error('lost'));await p.h.settle();p.h.unmount();const q=screen(true,storage);await ready(q);open(q);submit(q);await q.h.settle();assert.equal(key(q),original);q.h.unmount();
});
