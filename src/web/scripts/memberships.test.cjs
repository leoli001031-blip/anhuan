// Actual page handlers and adapter code; controlled transport/UI ports, no DB/browser.
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const ts=require('../node_modules/typescript');
const {harness,deferred,walk,textContent}=require('./lib/offline-tsx-harness.cjs');
class ApiError extends Error { constructor(status,code,retryable){super(code);Object.assign(this,{status,code,retryable});} }
function load(file,ports={}) {
  const mod={exports:{}};
  const source=fs.readFileSync(path.resolve(__dirname,'../src',file),'utf8');
  const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
  vm.runInNewContext(code,{module:mod,exports:mod.exports,require:name=>ports[name]??{ApiError},console});
  return mod.exports;
}
const memberModule=load('adapters/MembershipApi.ts');
const access=load('adapters/SessionAccess.ts');
const A='aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa', B='bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
const user=n=>`10000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
const id=n=>`20000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
function receipt(eid=A) {return {enterprise_id:eid,current_user_id:user(1),can_manage:true,members:[
  {id:id(1),user_id:user(1),email:'admin@example.invalid',role:'enterprise_admin',status:'active'},
  {id:id(2),user_id:user(2),email:'colleague@example.invalid',role:'plant_admin',status:'active'},
  {id:id(3),user_id:user(3),email:'reviewer@example.invalid',role:'auditor',status:'revoked'},
  {id:id(4),user_id:user(4),email:'technical@example.invalid',role:'super_admin',status:'active'},
]};}
function screen(){
  const reads=[],writes=[],reloads=[];
  const state={session:{enterprise_id:A,product_role:'provider_admin',membership_role:'enterprise_admin'},loading:false,error:null,reload:()=>reloads.push(true)};
  const auth={user:{profile:{sub:'userA'}}};
  const api={listMemberships:()=>{const d=deferred();reads.push(d);return d.promise;},changeMembership:(member,command)=>{const d=deferred();writes.push({member,command,...d});return d.promise;}};
  const h=harness('pages/MembersPage.tsx',{imports:{
    '../adapters':{useApi:()=>api,useSessionAccess:()=>state},
    '../auth/OidcProvider':{useAuth:()=>auth},
    '../adapters/MembershipApi':memberModule,
    '../adapters/SessionAccess':access,
  }});
  h.render();return {h,state,auth,reads,writes,reloads};
}
const section=(h,email)=>walk(h.tree()).find(node=>node.type==='section'&&node.props['aria-label']===email);
const button=(tree,label)=>walk(tree).find(node=>node.type==='Button'&&textContent(node)===label);
function click(p,label,email){const node=button(email?section(p.h,email):p.h.tree(),label);assert(node,`missing ${label}`);assert(!node.props.disabled,`${label} disabled`);node.props.onClick();p.h.render();}
function choose(p,email,role){const node=walk(section(p.h,email)).find(n=>n.props?.['aria-label']===`${email}的职责`);assert(node);node.props.onChange(role);p.h.render();}
async function ready(p){p.reads[0].resolve(receipt());await p.h.settle();}
const errors=p=>walk(p.h.tree()).filter(n=>n.type==='Alert'&&n.props.type==='error').map(n=>n.props.message);

test('member actions issue exact role/revoke/restore commands and render returned list',async()=>{
  const p=screen();await ready(p);assert(!button(section(p.h,'technical@example.invalid'),'停用成员'));
  choose(p,'colleague@example.invalid','auditor');click(p,'保存职责','colleague@example.invalid');
  assert.equal(p.writes[0].member,id(2));assert.equal(p.writes[0].command.action,'role');assert.equal(p.writes[0].command.role,'auditor');
  assert.match(p.writes[0].command.request_id,/^[0-9a-f-]{36}$/);
  const changed=receipt();changed.members[1].role='auditor';p.writes[0].resolve(changed);await p.h.settle();
  assert(button(section(p.h,'colleague@example.invalid'),'保存职责').props.disabled);
  click(p,'停用成员','colleague@example.invalid');assert.equal(p.writes[1].command.action,'revoke');assert(!('role' in p.writes[1].command));
  changed.members[1].status='revoked';p.writes[1].resolve(changed);await p.h.settle();
  click(p,'恢复成员','colleague@example.invalid');assert.equal(p.writes[2].command.action,'restore');assert(!('role' in p.writes[2].command));
  changed.members[1].status='active';p.writes[2].resolve(changed);await p.h.settle();assert(button(section(p.h,'colleague@example.invalid'),'停用成员'));p.h.unmount();
});
for(const outcome of ['success','failure'])test(`older same-tenant GET ${outcome} cannot overwrite revoke receipt`,async()=>{
  const p=screen();await ready(p);click(p,'刷新成员列表');click(p,'停用成员','colleague@example.invalid');
  const changed=receipt();changed.members[1].status='revoked';p.writes[0].resolve(changed);await p.h.settle();
  if(outcome==='success')p.reads[1].resolve(receipt());else p.reads[1].reject({code:'MEMBERSHIP_UNAVAILABLE'});
  await p.h.settle();assert(button(section(p.h,'colleague@example.invalid'),'恢复成员'));assert.equal(errors(p).length,0);p.h.unmount();
});
for(const operation of ['GET','POST'])for(const outcome of ['success','failure'])test(`old tenant ${operation} ${outcome} is inert after tenant change`,async()=>{
  const p=screen();await ready(p);click(p,'刷新成员列表');
  if(operation==='POST')click(p,'停用成员','colleague@example.invalid');
  const oldHandler=button(section(p.h,'reviewer@example.invalid'),'恢复成员').props.onClick;
  p.state.session={...p.state.session,enterprise_id:B};p.h.render();
  p.reads[2].resolve(receipt(B));await p.h.settle();click(p,'恢复成员','reviewer@example.invalid');const newWrite=p.writes.at(-1);
  const old=operation==='GET'?p.reads[1]:p.writes[0];
  if(outcome==='success')old.resolve(receipt());else old.reject({code:'MEMBERSHIP_LAST_ADMIN'});
  await p.h.settle();assert.equal(errors(p).length,0);assert(button(section(p.h,'reviewer@example.invalid'),'恢复成员').props.disabled);
  const count=p.writes.length;oldHandler();assert.equal(p.writes.length,count);
  const next=receipt(B);next.members[2].status='active';newWrite.resolve(next);await p.h.settle();assert(button(section(p.h,'reviewer@example.invalid'),'停用成员'));p.h.unmount();
});
test('A to B to A does not revive old requests, and unmount prevents late writes',async()=>{
  const p=screen();await ready(p);click(p,'停用成员','colleague@example.invalid');
  p.state.session={...p.state.session,enterprise_id:B};p.h.render();p.state.session={...p.state.session,enterprise_id:A};p.h.render();
  p.reads[2].resolve(receipt());await p.h.settle();click(p,'恢复成员','reviewer@example.invalid');
  p.writes[0].resolve({...receipt(),can_manage:false,members:[]});await p.h.settle();assert.equal(p.reloads.length,0);
  p.h.unmount();p.writes[1].reject(new Error('late'));await p.h.settle();assert.equal(p.h.lateWrites(),0);
});
for(const change of ['identity','role','session-refresh'])test(`${change} invalidates an earlier same-enterprise command`,async()=>{
  const p=screen();await ready(p);click(p,'停用成员','colleague@example.invalid');
  if(change==='identity')p.auth.user.profile.sub='userB';
  if(change==='role')p.state.session.membership_role='auditor';
  if(change==='session-refresh')p.state.loading=true;
  p.h.render();p.writes[0].resolve(receipt());await p.h.settle();
  assert(!section(p.h,'colleague@example.invalid'));assert.equal(p.reloads.length,0);p.h.unmount();
});
test('unknown outcomes keep request id on retry and changed role gets a new id',async()=>{
  const p=screen();await ready(p);choose(p,'colleague@example.invalid','auditor');click(p,'保存职责','colleague@example.invalid');
  const request=p.writes[0].command.request_id;button(section(p.h,'colleague@example.invalid'),'保存职责').props.onClick();assert.equal(p.writes.length,1);
  p.writes[0].reject(new Error('response lost'));await p.h.settle();click(p,'保存职责','colleague@example.invalid');assert.equal(p.writes[1].command.request_id,request);
  p.writes[1].reject(new Error('response lost'));await p.h.settle();choose(p,'colleague@example.invalid','partner');click(p,'保存职责','colleague@example.invalid');assert.notEqual(p.writes[2].command.request_id,request);p.h.unmount();
});
for(const [code,expected] of [
  ['MEMBERSHIP_NOT_FOUND','没有本企业'],['MEMBERSHIP_LAST_ADMIN','另一位'],['MEMBERSHIP_REQUEST_CONFLICT','先前请求'],
  ['MEMBERSHIP_ROLE_INVALID','不可设置'],['MEMBERSHIP_TECHNICAL_ADMIN_PROTECTED','专用管理入口'],['MEMBERSHIP_UNAVAILABLE','暂时不可用'],
])test(`${code} shows actionable copy without raw error`,async()=>{
  const p=screen();await ready(p);click(p,'停用成员','colleague@example.invalid');p.writes[0].reject({code});await p.h.settle();
  assert(errors(p).some(message=>message.includes(expected)));assert(!textContent(p.h.tree()).includes(code));p.h.unmount();
});
test('initial denied GET has retry and no mutation controls',async()=>{
  const p=screen();p.reads[0].reject({code:'MEMBERSHIP_NOT_FOUND'});await p.h.settle();assert(!button(p.h.tree(),'停用成员'));click(p,'刷新成员列表');assert.equal(p.reads.length,2);p.h.unmount();
});
test('self demotion clears controls and refreshes the session',async()=>{
  const p=screen();await ready(p);choose(p,'admin@example.invalid','plant_admin');click(p,'保存职责','admin@example.invalid');
  p.writes[0].resolve({...receipt(),can_manage:false,members:[]});await p.h.settle();assert.equal(p.reloads.length,1);assert(!button(p.h.tree(),'停用成员'));
  assert(walk(p.h.tree()).some(n=>n.type==='Alert'&&n.props.message.includes('没有成员管理权限')));p.h.unmount();
});
test('wrong-tenant receipt never renders its members',async()=>{
  const p=screen();p.reads[0].resolve(receipt(B));await p.h.settle();assert(!section(p.h,'admin@example.invalid'));assert(errors(p).some(m=>m.includes('企业上下文')));p.h.unmount();
});
test('HTTP membership adapter uses tenant transport and binds every response to its tenant',async()=>{
  const calls=[];let enterprise=A;
  const {HttpMembershipApi}=load('adapters/MembershipApi.ts',{'../api':{ApiError,tenantFetch:async(path,options)=>{calls.push({path,options});return {payload:receipt(),enterpriseId:enterprise};}}});
  const api=new HttpMembershipApi(()=> 'token');await api.listMemberships();
  await api.changeMembership(id(2),{action:'role',request_id:id(20),role:'auditor'});
  await api.changeMembership(id(2),{action:'revoke',request_id:id(21)});await api.changeMembership(id(2),{action:'restore',request_id:id(22)});
  assert.deepEqual(calls.map(c=>c.path),['/v1/memberships',`/v1/memberships/${id(2)}/role`,`/v1/memberships/${id(2)}/revoke`,`/v1/memberships/${id(2)}/restore`]);
  assert.equal(calls[0].options.method,'GET');assert.equal(calls[1].options.method,'POST');assert.equal(calls[1].options.token,'token');
  assert.deepEqual(JSON.parse(JSON.stringify(calls[1].options.body)),{request_id:id(20),role:'auditor'});assert(!('role' in calls[2].options.body));assert(!('enterprise_id' in calls[1].options.body));
  enterprise=B;await assert.rejects(api.listMemberships(),error=>error.code==='MEMBERSHIP_ENTERPRISE_MISMATCH');
  const count=calls.length;assert.throws(()=>api.changeMembership(id(2),{action:'role',request_id:id(23),role:'super_admin'}));assert.equal(calls.length,count);
});
test('membership wire rejects unsafe receipts and accepts self-removal receipt',()=>{
  for(const change of [value=>value.members.push({...value.members[0]}),value=>value.members[1].role='owner',value=>value.members[0].status='revoked',value=>value.current_user_id='wrong',value=>{value.can_manage=false;}]){
    const value=receipt();change(value);assert.throws(()=>memberModule.parseMembershipList(value));
  }
  const value={...receipt(),can_manage:false,members:[]};assert.equal(memberModule.parseMembershipList(value).can_manage,false);
});
test('mock adapter supports idempotent role/revoke/restore and last-admin protection',async()=>{
  const {MockMembershipApi}=load('adapters/MockMembershipApi.ts');const api=new MockMembershipApi(A);
  await assert.rejects(api.changeMembership(id(1),{action:'revoke',request_id:id(20)}),error=>error.code==='MEMBERSHIP_LAST_ADMIN');
  const revoke={action:'revoke',request_id:id(21)};await api.changeMembership(id(2),revoke);await api.changeMembership(id(2),{action:'restore',request_id:id(22)});
  assert.equal((await api.changeMembership(id(2),revoke)).members[1].status,'active');
  await assert.rejects(api.changeMembership(id(4),{action:'revoke',request_id:id(23)}),error=>error.code==='MEMBERSHIP_TECHNICAL_ADMIN_PROTECTED');
  await api.changeMembership(id(2),{action:'role',request_id:id(24),role:'enterprise_admin'});
  const self=await api.changeMembership(id(1),{action:'role',request_id:id(25),role:'partner'});assert.equal(self.can_manage,false);assert.equal(self.members.length,0);
});
test('member navigation only appears for the current enterprise administrator',()=>{
  for(const role of ['provider_admin','client_user'])assert(access.canManageMembers({product_role:role,membership_role:'enterprise_admin'}));
  for(const membership_role of [undefined,null,'super_admin','plant_admin','auditor','partner'])assert(!access.canManageMembers({product_role:'client_user',membership_role}));
  assert(!access.canManageMembers({product_role:'technical_admin',membership_role:'enterprise_admin'}));
});
test('console and portal render the membership entry only for their enterprise admin',()=>{
  for(const [file,product_role] of [['ConsoleLayout.tsx','provider_admin'],['PortalLayout.tsx','client_user']])for(const membership_role of ['enterprise_admin','plant_admin',undefined]){
    const h=harness('shells/'+file,{imports:{
      '../adapters':{useSessionAccess:()=>({session:{product_role,membership_role},loading:false}),isMockData:false},
      '../adapters/SessionAccess':access,
      antd:{Button:'Button',Drawer:'Drawer',Layout:Object.assign(function Layout(){},{Header:'Header',Sider:'Sider',Content:'Content'}),Menu:'Menu',Spin:'Spin',Typography:{Text:'Text'}},
      'react-router-dom':{Navigate:'Navigate',Outlet:'Outlet',NavLink:'NavLink',useLocation:()=>({pathname:'/console/clients'}),useNavigate:()=>()=>{}},
    }});h.render();
    const links=walk(h.tree()).filter(n=>n.props?.to==='/members'||n.props?.items?.some(item=>item.key==='/members'));
    assert.equal(links.length>0,membership_role==='enterprise_admin');h.unmount();
  }
});
test('session wire preserves verified membership role and never invents one for older payloads',()=>{
  const types=load('adapters/types.ts');const {parseSessionAccess}=load('adapters/wire.ts',{'./types':types});
  const raw={schema:types.SESSION_SCHEMA,product_role:'client_user',enterprise_id:A,template_id:'enterprise-ehs-material-analysis-v1',template_title:types.TEMPLATE_TITLE,capabilities:['list_published']};
  assert.equal(parseSessionAccess(raw).membership_role,undefined);
  assert.equal(parseSessionAccess({...raw,membership_role:'enterprise_admin'}).membership_role,'enterprise_admin');
  assert.throws(()=>parseSessionAccess({...raw,membership_role:'owner'}));
});
