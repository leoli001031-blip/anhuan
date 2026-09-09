const fs=require('node:fs'),path=require('node:path'),vm=require('node:vm'),crypto=require('node:crypto');
const ts=require('../../node_modules/typescript');
const uuid=n=>`10000000-0000-4000-8000-${String(n).padStart(12,'0')}`;
class IngestionApiError extends Error{constructor(status,code,retryable){super(code);Object.assign(this,{status,code,retryable})}}
function uploadFixture(storage=new Map()){
 const globals={crypto:{randomUUID:crypto.randomUUID,subtle:{digest:async(_alg,bytes)=>crypto.createHash('sha256').update(new Uint8Array(bytes)).digest()}},TextEncoder,
  sessionStorage:{getItem:k=>storage.get(k)??null,setItem:(k,v)=>storage.set(k,v),removeItem:k=>storage.delete(k)}};
 const cache=new Map();
 function load(file){
  if(cache.has(file))return cache.get(file);
  const mod={exports:{}};cache.set(file,mod.exports);
  const code=ts.transpileModule(fs.readFileSync(path.resolve(__dirname,'../../src',file),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
  const requirePort=name=>name.endsWith('ingestionApi')?{IngestionApiError}:name.endsWith('/errors')?{ApiError:IngestionApiError}
    :load(path.posix.normalize(path.posix.join(path.posix.dirname(file),name))+'.ts');
  vm.runInNewContext(code,{module:mod,exports:mod.exports,require:requirePort,...globals});return mod.exports;
 }
 return {helper:load('features/p3/uploadWrite.ts'),pending:load('adapters/pendingWrites.ts'),storage};
}
const file=(name='资料.docx',size=100,bytes='actual file bytes')=>({name,size,type:name.endsWith('.pdf')?'application/pdf':name.endsWith('.xlsx')?'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet':name.endsWith('.jpg')?'image/jpeg':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',arrayBuffer:async()=>new TextEncoder().encode(bytes).buffer});
const version=(f,documentId=uuid(1),id=uuid(2))=>({id,document_id:documentId,original_filename:f.name,size_bytes:f.size});
const receipt=(f,client='A',documentId=uuid(1))=>({id:documentId,knowledge_scope:{kind:client===null?'service_provider':'client',client_account_id:client},versions:[version(f,documentId)],latest_version:version(f,documentId)});
const auth={user:{profile:{sub:'actor'}},getAccessToken:()=> 'token'};
module.exports={uploadFixture,file,version,receipt,auth,uuid};
