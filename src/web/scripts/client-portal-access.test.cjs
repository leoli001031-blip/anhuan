const {test}=require('node:test');
const assert=require('node:assert/strict');
const {harness,deferred,walk,textContent}=require('./lib/offline-tsx-harness.cjs');
function screen() {
  const calls=[];
  const h=harness('pages/console/ClientPortalAccessPanel.tsx',{props:{clientId:'A'},imports:{
    '../../api':{api:(...args)=>{const d=deferred();calls.push({args,...d});return d.promise;}},
    '../../features/invitations/invitationFlow':{invitationLink:token=>'https://app.example/join#invite='+token},
  },globals:{Error,window:{location:{origin:'https://app.example'}}}});
  h.render();return {h,calls};
}
const receipt=(id='A',status='active')=>({client_id:id,status,members:[],invitations:[]});
const button=(h,label)=>walk(h.tree()).find(x=>x.type==='Button'&&textContent(x)===label);
function click(h,label){button(h,label).props.onClick();h.render();}
function enter(h,email='a@example.invalid'){walk(h.tree()).find(x=>x.props?.['aria-label']==='客户负责人邮箱').props.onChange({target:{value:email}});h.render();}
async function ready(p){p.calls[0].resolve(receipt());await p.h.settle();enter(p.h);}

test('failed initial status exposes retry without access mutation controls',async()=>{
  const p=screen();p.calls[0].reject(new Error('offline'));await p.h.settle();
  assert(!button(p.h,'生成负责人邀请'));assert(!button(p.h,'开通客户门户'));assert(button(p.h,'刷新访问状态'));p.h.unmount();
});
test('invitation retry keeps the same request id for an unknown outcome',async()=>{
  const p=screen();await ready(p);click(p.h,'生成负责人邀请');const id=p.calls[1].args[1].body.request_id;
  click(p.h,'生成负责人邀请');assert.equal(p.calls.length,2);
  p.calls[1].reject(new Error('response lost'));await p.h.settle();click(p.h,'生成负责人邀请');
  assert.equal(p.calls[2].args[1].body.request_id,id);assert.equal(p.calls[2].args[0],'/v1/clients/A/portal-invitations');
  p.calls[2].resolve({token:'credential',email:'a@example.invalid'});await p.h.settle();
  assert.equal(walk(p.h.tree()).find(x=>x.type==='Alert'&&x.props.type==='success').props.message,'已为 a@example.invalid 生成邀请');p.h.unmount();
});
for(const outcome of ['success','failure'])test(`old client invitation ${outcome} cannot appear after switching clients`,async()=>{
  const p=screen();await ready(p);click(p.h,'生成负责人邀请');p.h.setProps({clientId:'B'});
  if(outcome==='success')p.calls[1].resolve({token:'private-A',email:'a@example.invalid'});else p.calls[1].reject(new Error('old failure'));
  await p.h.settle();assert(!walk(p.h.tree()).some(x=>x.type==='Alert'));assert(!textContent(p.h.tree()).includes('private-A'));
  p.calls[2].resolve(receipt('B'));await p.h.settle();assert(button(p.h,'生成负责人邀请'));p.h.unmount();
});
test('unmount prevents invitation completion from updating state',async()=>{
  const p=screen();await ready(p);click(p.h,'生成负责人邀请');p.h.unmount();p.calls[1].resolve({token:'private-A',email:'a@example.invalid'});await p.h.settle();assert.equal(p.h.lateWrites(),0);
});
test('wrong-client status response cannot render mutation controls',async()=>{
  const p=screen();p.calls[0].resolve(receipt('B'));await p.h.settle();assert(!button(p.h,'生成负责人邀请'));assert(walk(p.h.tree()).some(x=>x.type==='Alert'));p.h.unmount();
});
for(const outcome of ['success','failure'])test(`older same-client refresh ${outcome} cannot overwrite a completed revoke`,async()=>{
  const p=screen();await ready(p);click(p.h,'刷新访问状态');assert.equal(p.calls.length,2);
  click(p.h,'暂停门户访问');p.calls[2].resolve(receipt('A','revoked'));await p.h.settle();
  assert(button(p.h,'恢复访问'));assert(!button(p.h,'生成负责人邀请'));
  if(outcome==='success')p.calls[1].resolve(receipt('A','active'));else p.calls[1].reject(new Error('old read failed'));
  await p.h.settle();assert(button(p.h,'恢复访问'));assert(!button(p.h,'生成负责人邀请'));
  assert(!walk(p.h.tree()).some(x=>x.type==='Alert'&&x.props.type==='error'));p.h.unmount();
});
