const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('fs'),path=require('path'),vm=require('vm');
const ts=require('../node_modules/typescript');
const {harness,deferred,walk,textContent}=require('./lib/offline-tsx-harness.cjs');
const token='header.payload.signature';
const tenant='61000000-0000-4000-8000-000000000001';
function flow() {
  const values=new Map();const storage={getItem:k=>values.get(k)??null,setItem:(k,v)=>values.set(k,v),removeItem:k=>values.delete(k)};
  const module={exports:{}};const source=fs.readFileSync(path.resolve(__dirname,'../src/features/invitations/invitationFlow.ts'),'utf8');
  vm.runInNewContext(ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText,{module,exports:module.exports,sessionStorage:storage,Date,Error});
  return {flow:module.exports,storage,values};
}
function screen(authenticated=true,fragment='') {
  const state=flow(),calls=[],selected=[],login=[],history=[];
  let user=authenticated?{profile:{sub:'user-a',email:'a@example.invalid'}}:null;
  const h=harness('pages/JoinPage.tsx',{imports:{
    '../auth/OidcProvider':{useAuth:()=>({user,isAuthenticated:!!user,isInitializing:false,getAccessToken:()=>user?'bearer-a':null,login:async p=>login.push(p),logout:async()=>{}})},
    '../api':{api:(...args)=>{const d=deferred();calls.push({args,...d});return d.promise},setSelectedEnterprise:e=>selected.push(e),invalidateTenantContext:()=>{}},
    '../features/invitations/invitationFlow':state.flow,
  },globals:{Error,window:{location:{pathname:'/join',hash:fragment},history:{replaceState:(...args)=>history.push(args)}}}});
  h.render();return {h,...state,calls,selected,login,history,setUser(value){user=value;h.render()}};
}
const button=(h,label)=>walk(h.tree()).find(x=>x.type==='Button'&&textContent(x)===label);
function enter(p){walk(p.h.tree()).find(x=>x.type==='TextArea').props.onChange({target:{value:token}});p.h.render()}
function accept(p){button(p.h,p.loginOnly?'登录并继续':'接受邀请并进入').props.onClick();p.h.render()}

test('invitation bearer travels only in fragment and tab storage',()=>{
 const p=flow();const link=p.flow.invitationLink(token,'https://app.example');
 assert.equal(new URL(link).search,'');assert.equal(new URL(link).hash,'#invite='+token);
 p.flow.rememberInvitation(token);assert.equal(p.flow.readInvitation(),token);
 p.flow.forgetInvitation();assert.equal(p.flow.readInvitation(),'');
});
test('expired, future and malformed pending credentials are discarded',()=>{
 for(const savedAt of [Date.now()-86400001,Date.now()+60000,'bad']){
  const p=flow();p.values.set('anhuan.pending-invitation.v1',JSON.stringify({token,savedAt}));
  assert.equal(p.flow.readInvitation(),'');assert.equal(p.values.size,0);
 }
});
test('OIDC callback accepts only the fixed join destination',()=>{
 const p=flow();for(const state of [null,{}, {returnTo:'https://evil.invalid'}, {returnTo:'//evil.invalid'}, {returnTo:'/join#invite='+token}])assert.equal(p.flow.signinDestination(state),'/');
 assert.equal(p.flow.signinDestination({returnTo:'/join'}),'/join');
});
test('join consumes fragment and removes it from browser history',()=>{
 const p=screen(true,'#invite='+token);assert.equal(p.flow.readInvitation(),token);
 assert.equal(p.history[0][2],'/join');assert.equal(walk(p.h.tree()).find(x=>x.type==='TextArea').props.value,token);p.h.unmount();
});
test('new user can sign in without membership or business session',async()=>{
 const p=screen(false);p.loginOnly=true;enter(p);accept(p);await p.h.settle();
 assert.deepEqual(p.login,['/join']);assert.equal(p.calls.length,0);assert.equal(p.flow.readInvitation(),token);p.h.unmount();
});
test('accept posts only invite credential without a selected tenant',async()=>{
 const p=screen();enter(p);accept(p);assert.equal(p.calls.length,1);
 const [route,options]=p.calls[0].args;assert.equal(route,'/v1/invitations/consume');assert.equal(options.enterpriseId,null);
 assert.equal(JSON.stringify(options.body),JSON.stringify({token}));assert.equal(options.token,'bearer-a');
 p.calls[0].resolve({enterprise_id:tenant});await p.h.settle();assert.deepEqual(p.selected,[tenant]);assert.deepEqual(p.h.navigation,['/']);assert.equal(p.flow.readInvitation(),'');p.h.unmount();
});
test('double acceptance dispatches once while response is pending',async()=>{
 const p=screen();enter(p);accept(p);accept(p);assert.equal(p.calls.length,1);p.calls[0].resolve({enterprise_id:tenant});await p.h.settle();p.h.unmount();
});
for(const result of ['success','failure'])test(`old account ${result} cannot select enterprise after account changes`,async()=>{
 const p=screen();enter(p);accept(p);p.setUser({profile:{sub:'user-b',email:'b@example.invalid'}});
 if(result==='success')p.calls[0].resolve({enterprise_id:tenant});else p.calls[0].reject(new Error('INVITE_IDENTITY_MISMATCH'));
 await p.h.settle();assert.deepEqual(p.selected,[]);assert.deepEqual(p.h.navigation,[]);assert(!textContent(p.h.tree()).includes('当前登录邮箱与受邀邮箱不同'));p.h.unmount();
});
test('unmount discards a delayed acceptance result',async()=>{
 const p=screen();enter(p);accept(p);p.h.unmount();p.calls[0].resolve({enterprise_id:tenant});await p.h.settle();assert.deepEqual(p.selected,[]);assert.equal(p.h.lateWrites(),0);
});
test('mismatched email leaves credential available and shows recovery copy',async()=>{
 const p=screen(true,'#invite='+token);accept(p);p.calls[0].reject(new Error('INVITE_IDENTITY_MISMATCH'));await p.h.settle();
 const alert=walk(p.h.tree()).find(x=>x.type==='Alert');assert.match(alert.props.message,/受邀邮箱/);assert.equal(p.flow.readInvitation(),token);assert.deepEqual(p.selected,[]);p.h.unmount();
});
test('malformed server tenant is never selected',async()=>{
 const p=screen();enter(p);accept(p);p.calls[0].resolve({enterprise_id:'attacker'});await p.h.settle();assert.deepEqual(p.selected,[]);assert.deepEqual(p.h.navigation,[]);p.h.unmount();
});
