const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('fs'),path=require('path'),vm=require('vm'),ts=require('typescript');
const {harness,textContent,repo,walk,deferred}=require('./lib/offline-tsx-harness.cjs');
const code=ts.transpileModule(fs.readFileSync(path.join(repo,'src/web/src/adapters/evidenceLocation.ts'),'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS}}).outputText;
const mod={exports:{}};vm.runInNewContext(code,{module:mod,exports:mod.exports});const parse=mod.exports.citationPosition;
const id='00000000-0000-4000-8000-000000000001';
const docx={schema_version:2,kind:'docx_block',part:'word/document.xml',body_index:4,row_index:2,cell_index:1,grid_column:1,grid_span:2,paragraph_index:1};
const xlsx={schema_version:2,kind:'xlsx_cells',sheet_id:2,sheet_name:'监测记录',part:'xl/worksheets/sheet2.xml',cell_range:'C88'};
const jpeg={schema_version:2,kind:'image',source_width:200,source_height:100,rendered_width:200,rendered_height:100,rendered_sha256:'a'.repeat(64),exif_orientation:1};
for(const [locator,expected] of [[docx,'表格第 2 行第 1 列'],[xlsx,'监测记录 · C88'],[jpeg,'图像全文']])test(`typed citation renders ${locator.kind} without fabricated page`,()=>{
 const position=parse({page_number:null,locator,evidence_revision_id:id});assert.equal(position.pageNumber,null);assert.ok(position.location.includes(expected));
 const h=harness('components/ReportDocument.tsx',{props:{sections:[{key:"citations",title:"引用",body:"引用列表"}],citations:[{citation_id:id,document_version_id:id,documentName:'核对材料',versionNumber:3,excerpt:'COD 17 mg/L',...position}]},imports:{'../adapters/types':{SECTION_ORDER:[{key:"citations",title:"引用"}]}}});
 h.render();const text=textContent(h.tree());assert.ok(text.includes(expected));assert.ok(!text.includes('第 null 页'));assert.ok(text.includes('第 3 版'));h.unmount();
});
test('typed citation rejects fake page, malformed cell, location mismatch and missing revision',()=>{
 for(const row of [
 {page_number:1,locator:docx,evidence_revision_id:id},
 {page_number:null,locator:{...xlsx,cell_range:'XFE1048577'},evidence_revision_id:id},
 {page_number:null,locator:jpeg,evidence_revision_id:id,location:'第 1 页'},
 {page_number:null,locator:docx},
 ])assert.throws(()=>parse(row));
 assert.equal(parse({page_number:3}).pageNumber,3);
 assert.equal(parse({page_number:7,locator:{schema_version:2,kind:'pdf_page',page_number:7},evidence_revision_id:id}).location,'第 7 页');
});

function originalScreen(){
 const reads=[];const target={citationId:id,documentVersionId:id,locator:docx};
 const h=harness('components/OriginalEvidenceView.tsx',{props:{target},imports:{'../api':{tenantFetch:(path,options)=>{const d=deferred();reads.push({...d,path,options});return d.promise}}}});
 h.render();return {h,reads,target};
}
const openOriginal=h=>{walk(h.tree()).find(n=>n.type==='Button'&&n.props.children==='查看原件位置').props.onClick();h.render()};
const originalPayload=(target,extra={})=>({payload:{document_version_id:target.documentVersionId,source_sha256:'a'.repeat(64),locator:target.locator,location:'正文块 4 · 表格第 2 行第 1 列',original_text:'原件 COD 42 mg/L',image:null,...extra}});
test('original viewer displays authenticated source position and rejects substituted location',async()=>{
 const {h,reads,target}=originalScreen();assert.equal(reads.length,0);openOriginal(h);
 reads[0].resolve(originalPayload(target));await h.settle();assert.match(textContent(h.tree()),/原件 COD 42 mg\/L/);
 h.unmount();const other=originalScreen();openOriginal(other.h);
 other.reads[0].resolve(originalPayload(other.target,{locator:xlsx}));await other.h.settle();assert.equal(walk(other.h.tree()).find(n=>n.type==='Alert').props.message,'暂时无法打开原件位置');other.h.unmount();
});
for(const outcome of ['success','failure'])test(`old citation original ${outcome} cannot overwrite next source`,async()=>{
 const {h,reads,target}=originalScreen();openOriginal(h);h.setProps({target:{...target,citationId:'another',documentVersionId:'another'}});
 assert.equal(reads[0].options.signal.aborted,true);openOriginal(h);
 const second={...target,documentVersionId:'another'};reads.at(-1).resolve(originalPayload(second,{original_text:'当前原件'}));await h.settle();
 if(outcome==='success')reads[0].resolve(originalPayload(target));else reads[0].reject(new Error('old'));
 await h.settle();assert.match(textContent(h.tree()),/当前原件/);assert.doesNotMatch(textContent(h.tree()),/42 mg/);h.unmount();
});
test('review viewer uses base-only route and switching to QA identity aborts old source read',async()=>{
 const {h,reads,target}=originalScreen();const base={documentVersionId:id,fragmentId:id,revisionId:id,sourceSha256:'a'.repeat(64),locator:docx,reviewBase:true};
 h.setProps({target:base});openOriginal(h);assert.match(reads[0].path,/\/review-fragments\//);
 h.setProps({target:{...base,reviewBase:false}});assert(reads[0].options.signal.aborted);openOriginal(h);assert.match(reads.at(-1).path,/\/fragments\//);
 reads[0].resolve(originalPayload(target));reads.at(-1).resolve(originalPayload(base,{source_sha256:'b'.repeat(64)}));await h.settle();
 assert.equal(walk(h.tree()).find(n=>n.type==='Alert').props.message,'暂时无法打开原件位置');h.unmount();
});
