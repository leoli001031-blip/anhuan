const {test}=require('node:test');
const assert=require('node:assert/strict');
const {harness,deferred,walk,textContent}=require('./lib/offline-tsx-harness.cjs');
const {uploadFixture,file,receipt,version,auth,uuid}=require('./lib/upload-fixture.cjs');
const caps={upload_enabled:true,scanner:{state:'ready'},limits:{max_file_bytes:50*1024*1024},allowed_types:[
 ['application/pdf',['.pdf']],['application/vnd.openxmlformats-officedocument.wordprocessingml.document',['.docx']],
 ['application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',['.xlsx']],['image/jpeg',['.jpg','.jpeg']],
].map(([content_type,extensions])=>({content_type,extensions,max_file_bytes:50*1024*1024}))};
const button=(h,label)=>walk(h.tree()).find(x=>x.type==='Button'&&textContent(x)===label);
function screen(storage=new Map()){
 const fixture=uploadFixture(storage),writes=[],received=[],inspected=[];
 const props={open:true,scope:{kind:'client',client_account_id:'A'},capabilities:caps,onCancel(){},onReceived:()=>received.push(1),onInspect:id=>inspected.push(id)};
 const h=harness('components/BatchMaterialUploadModal.tsx',{props,imports:{
  '../auth/OidcProvider':{useAuth:()=>auth},'../features/p3/uploadWrite':fixture.helper,'../adapters/pendingWrites':fixture.pending,
  '../features/p3/ingestionApi':{createIngestionDocument:(...args)=>{const d=deferred();writes.push({...d,args});return d.promise}}
 }});h.render();return {h,props,writes,received,inspected,storage};
}
function select(s,files){const picker=walk(s.h.tree()).find(x=>x.props?.multiple);for(const f of files)picker.props.beforeUpload(f);s.h.render()}
async function send(s){button(s.h,'上传未完成文件').props.onClick();await s.h.settle()}
async function accept(s,n,client='A'){s.writes[n].resolve(receipt(s.writes[n].args[2],client,uuid(n+10)));await s.h.settle()}
test('batch sends four allowed formats, preserves partial outcomes, skips successes on retry and fences double clicks',async()=>{
 const s=screen();select(s,[file('a.pdf'),file('b.docx'),file('c.xlsx'),file('d.jpg'),file('bad.exe')]);
 await send(s);await send(s);assert.equal(s.writes.length,1);await accept(s,0);
 s.writes[1].reject(new Error('response lost'));await s.h.settle();assert.equal(s.writes.length,3);
 await accept(s,2);await accept(s,3);assert.equal(s.received.length,3);assert.equal(s.writes.length,4);
 assert.match(textContent(s.h.tree()),/当前环境未开放此文件格式/);const original=s.writes[1].args[3];
 await send(s);assert.equal(s.writes.length,5);assert.equal(s.writes[4].args[3],original);assert.equal(s.writes[4].args[2].name,'b.docx');
 await accept(s,4);assert.equal(s.received.length,4);assert.equal(s.storage.size,0);
 assert.equal(walk(s.h.tree()).filter(x=>x.type==='Button'&&textContent(x)==='查看处理详情').length,4);s.h.unmount();
});
test('batch reload reselects same bytes and reuses unknown request without storing business data',async()=>{
 const storage=new Map(),s=screen(storage);select(s,[file('private.docx')]);await send(s);const key=s.writes[0].args[3];
 s.writes[0].reject(new Error('lost'));await s.h.settle();s.h.unmount();
 assert(!JSON.stringify([...storage]).includes('private'));assert.equal(storage.size,1);
 const next=screen(storage);select(next,[file('private.docx')]);await send(next);assert.equal(next.writes[0].args[3],key);
 await accept(next,0);assert.equal(storage.size,0);next.h.unmount();
});
for(const outcome of ['success','failure'])test(`batch customer switch aborts flight and old ${outcome} cannot continue queue or alter current rows`,async()=>{
 const s=screen();select(s,[file('A.pdf'),file('A.docx')]);await send(s);
 s.h.setProps({...s.props,scope:{kind:'client',client_account_id:'B'}});assert(s.writes[0].args[4].aborted);
 select(s,[file('B.xlsx')]);await send(s);
 if(outcome==='success')s.writes[0].resolve(receipt(s.writes[0].args[2]));else s.writes[0].reject(new Error('old'));
 await s.h.settle();assert.equal(s.writes.length,2);assert.equal(s.received.length,0);assert(!textContent(s.h.tree()).includes('A.pdf'));
 await accept(s,1,'B');assert.equal(s.received.length,1);s.h.unmount();
});
test('malformed or wrong-scope upload receipt retains original command identity',async()=>{
 const s=screen();select(s,[file()]);await send(s);const key=s.writes[0].args[3];s.writes[0].resolve({id:uuid(1)});await s.h.settle();
 assert.equal(s.received.length,0);await send(s);assert.equal(s.writes[1].args[3],key);
 await accept(s,1,'B');assert.equal(s.received.length,0);assert.equal(s.storage.size,1);await send(s);assert.equal(s.writes[2].args[3],key);
 await accept(s,2);assert.equal(s.received.length,1);s.h.unmount();
});
test('storage failure blocks POST and file/tenant/subject/target changes separate pending identities',async()=>{
 const broken={get(){throw new Error('denied')},set(){throw new Error('denied')}};
 const s=screen(broken);select(s,[file()]);await send(s);assert.equal(s.writes.length,0);s.h.unmount();
 const f=uploadFixture(),target={documentId:uuid(1)},original=await f.helper.pendingUpload('tenant','actor',file(),target);
 for(const args of [['tenant2','actor',file(),target],['tenant','actor2',file(),target],['tenant','actor',file('资料.docx',100,'changed'),target],['tenant','actor',file(),{documentId:uuid(2)}]]) {
  assert.notEqual((await f.helper.pendingUpload(...args)).requestId,original.requestId);
 }
 assert.equal((await f.helper.pendingUpload('tenant','actor',file(),target)).requestId,original.requestId);
});
test('same file replay validates its historical version even after a newer version becomes latest',()=>{
 const f=uploadFixture(),source=file(),value=receipt(source);value.latest_version=version(file('new.pdf'),value.id,uuid(5));
 value.versions.unshift(value.latest_version);f.helper.validateDocumentReceipt(value,source,{kind:'client',client_account_id:'A'});
 assert.throws(()=>f.helper.validateVersionReceipt(version(source,'wrong'),source,value.id),/INVALID_RESPONSE/);
});
test('batch max size and capability removal block posting without discarding rows',async()=>{
 const s=screen();select(s,[file('large.docx',26*1024*1024),file('large.jpg',21*1024*1024)]);await send(s);assert.equal(s.writes.length,0);
 assert.match(textContent(s.h.tree()),/大小上限/);s.h.unmount();
 const q=screen();select(q,[file('a.xlsx')]);q.h.setProps({...q.props,capabilities:{...caps,allowed_types:caps.allowed_types.slice(0,1)}});await send(q);
 assert.equal(q.writes.length,0);assert.match(textContent(q.h.tree()),/未开放此文件格式/);q.h.unmount();
});
test('batch bounded to 20 selected files',()=>{const s=screen();select(s,Array.from({length:23},(_,i)=>file(`${i}.pdf`)));assert.equal(walk(s.h.tree()).filter(x=>x.type==='section').length,20);s.h.unmount()});
