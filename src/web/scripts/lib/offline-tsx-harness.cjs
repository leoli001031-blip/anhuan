// Executes real TSX with offline UI/API ports. No DOM/browser/network/DB.
// Hook lifecycle: persistent refs/state, dependency memoization, cleanup-before-setup,
// and synchronous rerenders after effects/state changes. Child UI components are ports.
const fs=require('fs'),path=require('path'),vm=require('vm'),crypto=require('crypto');
const repo=path.resolve(__dirname,'../../../..');
const ts=require(path.join(repo,'src/web/node_modules/typescript'));
const tick=()=>new Promise(setImmediate);
const deferred=()=>{let resolve,reject;const promise=new Promise((a,b)=>{resolve=a;reject=b});return {promise,resolve,reject}};
const walk=t=>!t||typeof t!=='object'?[]:[t,...[].concat(t.props?.children??[]).flat(Infinity).flatMap(walk)];
const textContent=t=>typeof t==='string'?t:typeof t==='number'?String(t):!t||typeof t!=='object'?'':[].concat(t.props?.children??[]).flat(Infinity).map(textContent).join('');
const same=(a,b)=>a&&b&&a.length===b.length&&a.every((v,i)=>Object.is(v,b[i]));
class ApiError extends Error{constructor(status,code,retryable){super(code);Object.assign(this,{status,code,retryable})}}
function harness(relativePath,options={}){
 let slots=[],cursor=0,scheduled=[],dirty=false,mounted=true,lateWrites=0,params=options.params??{clientId:'A',reportId:'reportA'},props=options.props,root;
 const messages=[],navigation=[],confirmations=[],storage=new Map(),timers=new Map();let nextTimer=0;
 const form={resetFields(){},validateFields:async()=>({}),...options.form};const token=()=> 'offline-token';
 const hooks={
  useState(initial){const i=cursor++;if(!slots[i])slots[i]={kind:'state',value:typeof initial==='function'?initial():initial};return [slots[i].value,v=>{const next=typeof v==='function'?v(slots[i].value):v;if(!mounted)lateWrites++;if(!Object.is(next,slots[i].value)){slots[i].value=next;dirty=true}}]},
  useRef(value){const i=cursor++;if(!slots[i])slots[i]={kind:'ref',value:{current:value}};return slots[i].value},
  useMemo(f,deps){const i=cursor++;if(!slots[i]||!same(slots[i].deps,deps))slots[i]={kind:'memo',value:f(),deps};return slots[i].value},
  useCallback(f,deps){const i=cursor++;if(!slots[i]||!same(slots[i].deps,deps))slots[i]={kind:'memo',value:f,deps};return slots[i].value},
  useEffect(f,deps){const i=cursor++;if(!slots[i]||!same(slots[i].deps,deps)){scheduled.push({i,f,cleanup:slots[i]?.cleanup});slots[i]={kind:'effect',deps,cleanup:slots[i]?.cleanup}}}
 };
 hooks.useLayoutEffect=hooks.useEffect;
 const Form=Object.assign(function Form(){},{Item:'FormItem',useForm:()=>[form]});
 const Modal=Object.assign(function Modal(){},{confirm:config=>{confirmations.push(config);return {destroy(){config.destroyed=true}}}});
 const ui={Form,Modal,Typography:{Title:'Title',Text:'Text',Paragraph:'Paragraph'},Input:Object.assign(function Input(){},{TextArea:'TextArea'}),Select:Object.assign(function Select(){},{Option:'Option'}),Descriptions:Object.assign(function Descriptions(){},{Item:'DescriptionItem'}),Upload:Object.assign(function Upload(){},{LIST_IGNORE:'LIST_IGNORE'}),message:{}};
 for(const kind of ['success','error','warning','info'])ui.message[kind]=m=>messages.push({kind,m});
 for(const k of ['Button','DatePicker','Alert','Drawer','Space','Spin','Checkbox','Popconfirm','Tag','Table','Skeleton','Empty'])ui[k]=k;
 const session={enterprise_id:'tenant',capabilities:['generate','review','publish','withdraw']};
 const api=options.api??{};
 const jsx=(type,props)=>({type,props});
 const storageApi={getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)};
 const setTimer=f=>{const id=++nextTimer;timers.set(id,f);return id};const clearTimer=id=>timers.delete(id);
 const requireShim=name=>{
  if(options.imports?.[name])return options.imports[name];
  if(name.endsWith('/useAsyncContext')) {
    const hookCode=ts.transpileModule(fs.readFileSync(path.join(repo,'src/web/src/components/useAsyncContext.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
    const hookModule={exports:{}}; vm.runInNewContext(hookCode,{require:requireShim,module:hookModule,exports:hookModule.exports}); return hookModule.exports;
  }
  if(name==='react')return hooks;
  if(name==='react/jsx-runtime')return {jsx,jsxs:jsx};
  if(name==='antd')return ui;
  if(name==='react-router-dom')return {useParams:()=>params,useNavigate:()=>url=>navigation.push(url),Link:'Link'};
  if(name.endsWith('/adapters'))return {useApi:()=>api,useSessionAccess:()=>({session}),isMockData:false};
  if(name.endsWith('/adapters/errors'))return {ApiError,errorKind:e=>e.kind??'network'};
  if(name.endsWith('/adapters/types'))return {REPORT_STATUS_LABEL:{empty:'空',draft:'草稿',approved:'已批准',review_pending:'待审核',failed:'失败'},MATERIAL_STATUS_LABEL:{}};
  if(name.includes('OidcProvider'))return {useAuth:()=>({getAccessToken:token})};
  if(name.endsWith('/useNarrow'))return {useNarrow:()=>options.narrow??false};
  if(name.includes('reasonCopy')) {
    const reasonCode=ts.transpileModule(fs.readFileSync(path.join(repo,'src/web/src/features/p3/reasonCopy.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
    const reasonModule={exports:{}};vm.runInNewContext(reasonCode,{module:reasonModule,exports:reasonModule.exports});return reasonModule.exports;
  }
  if(name==='dayjs')return {default:v=>v};
  return {__esModule:true,default:name,formatDateTime:v=>v,saveHtmlReportArtifact(){}};
 };
 const source=fs.readFileSync(path.join(repo,'src/web/src',relativePath),'utf8');
 const code=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,jsx:ts.JsxEmit.ReactJSX}}).outputText;
 const module={exports:{}};vm.runInNewContext(code,{require:requireShim,module,exports:module.exports,sessionStorage:storageApi,crypto,AbortController,console,setTimeout:setTimer,clearTimeout:clearTimer,window:{setTimeout:setTimer,clearTimeout:clearTimer},URL,URLSearchParams,Buffer,...options.globals});
 function render(next){if(next)params=next;let n=0;do{dirty=false;cursor=0;scheduled=[];root=module.exports.default(props);for(const e of scheduled)e.cleanup?.();for(const e of scheduled)slots[e.i].cleanup=e.f();if(++n>30)throw new Error('render loop fixture');}while(dirty&&mounted);return root}
 return {Form,Modal,ui,messages,navigation,confirmations,storage,timers,hash:crypto.createHash('sha256').update(source).digest('hex'),render,setProps(value){props=value;render()},tree:()=>root,params:()=>params,states:()=>slots.filter(s=>s.kind==='state').map(s=>s.value),refs:()=>slots.filter(s=>s.kind==='ref').map(s=>s.value.current),lateWrites:()=>lateWrites,async settle(n=8){for(let i=0;i<n;i++){await tick();if(mounted)render()}},fireTimers(){const copy=[...timers.values()];timers.clear();copy.forEach(f=>f())},unmount(){mounted=false;slots.filter(s=>s.kind==='effect').forEach(s=>s.cleanup?.())}};
}
module.exports={harness,deferred,walk,textContent,tick,repo,ApiError};
