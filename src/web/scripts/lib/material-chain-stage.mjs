import { createHash, randomUUID } from 'node:crypto';
import { appendFile, lstat, readFile, writeFile } from 'node:fs/promises';
import path from 'node:path';

const FORMATS = Object.freeze({
  pdf: ['chain.pdf', 'application/pdf', 'pdf_page'],
  docx: ['chain.docx', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'docx_block'],
  xlsx: ['chain.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'xlsx_cells'],
  jpeg: ['chain.jpg', 'image/jpeg', 'image'],
});
const PROCESS_TIMEOUT = 300_000;
const ACTIVE_MODAL = '.ant-modal-wrap:not([style*="display: none"]) .ant-modal';
const normalize = value => String(value ?? '').replace(/\s+/g, ' ').trim();
const canonical = value => JSON.stringify(value && typeof value==='object' && !Array.isArray(value)
  ? Object.fromEntries(Object.entries(value).sort(([a],[b])=>a.localeCompare(b)).map(([key,item])=>[key,JSON.parse(canonical(item))])) : value);
const sha256 = value => createHash('sha256').update(value).digest('hex');

// The only direct write below is an ordinary retrieval request. Upload, review,
// report creation and generation must originate from actual product UI controls.
async function sessionHttp(page, route, body) {
  return page.evaluate(`(async () => {
    const key = Object.keys(sessionStorage).find(name => name.startsWith('oidc.user:'));
    const session = JSON.parse(sessionStorage.getItem(key) || 'null');
    const enterprise = localStorage.getItem('f1-selected-enterprise');
    if (!session?.access_token || !enterprise) return {status:0,payload:{detail:'SESSION_MISSING'}};
    const response = await fetch(${JSON.stringify(route)}, {
      method:${JSON.stringify(body === undefined ? 'GET' : 'POST')},
      headers:{Authorization:'Bearer '+session.access_token,'X-Enterprise-Id':enterprise,
        ${body === undefined ? '' : "'Content-Type':'application/json',"}},
      ${body === undefined ? '' : `body:JSON.stringify(${JSON.stringify(body)}),`}
    });
    let payload = null; try {payload = await response.json()} catch {}
    return {status:response.status,payload};
  })()`);
}

export async function executeMaterialChain({cdp, origin, secretDirectory, controlDirectory,
  runIdentityToPath, identity, crmAccountId, enterpriseId, bindSessionAccess,
  navigateLoggedInPath, bindLastAnalysisRequest, VerifyError, delay}) {
  const checks = [];
  const documents = [];
  let frozenReport = null;
  let activePage = null;
  const diagnose = async id => {
    if (!activePage) return null;
    const page = activePage;
    const snapshot = await page.evaluate(`(() => {
      const describe = element => {
        const style = getComputedStyle(element), box = element.getBoundingClientRect();
        const ancestors = []; let parent = element.parentElement;
        for (let n=0; parent && n<5; n++,parent=parent.parentElement) {
          const computed = getComputedStyle(parent);
          ancestors.push({tag:parent.tagName,class:parent.className,display:computed.display,visibility:computed.visibility});
        }
        return {tag:element.tagName,class:element.className,style:element.getAttribute('style'),
          display:style.display,visibility:style.visibility,opacity:style.opacity,
          rect:{x:box.x,y:box.y,width:box.width,height:box.height},ancestors};
      };
      return {path:location.pathname,interactions:window.__materialChainInteractions??[],observed_messages:window.__materialChainMessages??[],body_text:(document.body?.innerText??'').slice(0,6000),
        inputs:Array.from(document.querySelectorAll('input')).filter(input=>input.type!=='password').slice(0,30)
          .map(input=>({...describe(input),type:input.type,multiple:input.multiple})),
        modals:Array.from(document.querySelectorAll('[role="dialog"],.ant-modal-root,.ant-modal-wrap,.ant-modal')).slice(0,20).map(describe),
        buttons:Array.from(document.querySelectorAll('button')).filter(button=>getComputedStyle(button).visibility!=='hidden')
          .slice(0,40).map(button=>({...describe(button),text:(button.textContent??'').slice(0,100),disabled:button.disabled}))};
    })()`).catch(()=>({diagnostic_error:'PAGE_SNAPSHOT_UNAVAILABLE'}));
    const filename = `${id}-failure.png`;
    try {
      const result = await page.cdp.call('Page.captureScreenshot',{format:'png',captureBeyondViewport:false},page.sessionId);
      if (typeof result?.data==='string') {
        await writeFile(path.join(controlDirectory,filename),Buffer.from(result.data,'base64'),{mode:0o600});
        snapshot.screenshot=filename;
      }
    } catch {snapshot.screenshot_error='SCREENSHOT_UNAVAILABLE'}
    return snapshot;
  };
  const planned = ['fixture_manifest','provider_identity','batch_upload_four_formats',
    ...Object.keys(FORMATS).flatMap(format=>[`${format}_processed`,`${format}_original_ui`,...(format==='docx'?['docx_human_revision_ui']:[])]),
    ...Object.keys(FORMATS).map(format=>`${format}_retrieval_http`),'shared_material_upload_ui','shared_material_processed','report_generation_four_format_citations_ui',
    'docx_new_version_upload_ui','docx_new_version_processed_retrieval','frozen_report_preserved_after_new_version',
    'partial_xlsx_upload_ui','partial_xlsx_excluded_ui_http','partial_xlsx_report_blocked_ui',
    'resume_upload_receipt_loss_ui','mixed_batch_invalid_file_independent_ui',
    'resume_upload_refresh_retry_ui','resume_upload_single_document_version_http'];
  const journal = path.join(controlDirectory,'material-checks.jsonl');
  const record = event => appendFile(journal,`${JSON.stringify(event)}\n`,{mode:0o600});
  await record({event:'collected',schema:'anhuan-material-browser-checks-v1',checks:planned});
  const reject = (code, evidence = {}) => {throw new VerifyError(code, evidence)};
  // A measured hit target must survive asynchronous React layout and scrolling.
  // Capture listeners only observe trusted input; no click is retried or simulated
  // through HTMLElement.click(), and no business data is written by the observer.
  const clickButtonText = async (page,textValue,code,timeout=30_000,{root=null,selector='button,.ant-btn'}={}) => {
    await page.waitForApiIdle();
    await page.evaluate(`(() => {
      if (window.__materialChainObserver) return;
      window.__materialChainObserver=true; window.__materialChainInteractions=[];
      for (const type of ['pointerdown','mousedown','mouseup','click']) {
        document.addEventListener(type,event=>{
          const target=event.target instanceof Element?event.target:null;
          const button=target?.closest('button');
          window.__materialChainInteractions.push({type,tag:target?.tagName??null,
            text:(target?.textContent??'').slice(0,120),button_text:(button?.textContent??'').slice(0,120),
            x:event.clientX,y:event.clientY,isTrusted:event.isTrusted});
          if(window.__materialChainInteractions.length>40)window.__materialChainInteractions.shift();
        },{capture:true,passive:true});
      }
    })()`);
    const locate = scroll => `(() => {
      const wanted=${JSON.stringify(textValue)};
      const scopes=${JSON.stringify(root)}===null?[document]:Array.from(document.querySelectorAll(${JSON.stringify(root)}));
      if(scopes.length!==1)return null;
      for(const button of scopes[0].querySelectorAll(${JSON.stringify(selector)})) {
        if (!(button instanceof HTMLElement) || button.disabled || button.querySelector('input:disabled')
          || wanted!==null && (button.textContent??'').replace(/\\s+/g,'').trim()!==wanted.replace(/\\s+/g,''))continue;
        const style=getComputedStyle(button);
        if(style.display==='none'||style.visibility==='hidden'||style.pointerEvents==='none')continue;
        if(${scroll?'true':'false'})button.scrollIntoView({block:'center',inline:'center',behavior:'instant'});
        const box=button.getBoundingClientRect(),x=box.left+box.width/2,y=box.top+box.height/2,hit=document.elementFromPoint(x,y);
        if(box.width>0&&box.height>0&&x>=0&&y>=0&&x<innerWidth&&y<innerHeight&&hit&&(button===hit||button.contains(hit))) {
          return {x,y,rect:{x:box.x,y:box.y,width:box.width,height:box.height},text:(button.textContent??'').trim(),tag:button.tagName};
        }
      }
      return null;
    })()`;
    const deadline=Date.now()+timeout;
    let point=null,previous=null,stable=0,scrolled=false;
    while(Date.now()<deadline) {
      const current=await page.evaluate(locate(!scrolled));
      if(current)scrolled=true;
      if(current&&previous&&['x','y','width','height'].every(key=>Math.abs(current.rect[key]-previous.rect[key])<0.25))stable++;
      else stable=0;
      previous=current;
      if(current&&stable>=3){point=current;break}
      await delay(100);
    }
    if(!point)reject(code,{interaction:'NO_STABLE_BUTTON',button:textValue});
    await record({event:'interaction',phase:'before_click',button:textValue??selector,root,target:point});
    await page.cdp.call('Input.dispatchMouseEvent',{type:'mouseMoved',x:point.x,y:point.y},page.sessionId);
    const final=await page.evaluate(locate(false));
    if(!final||Math.abs(final.x-point.x)>0.25||Math.abs(final.y-point.y)>0.25)reject(code,{interaction:'TARGET_MOVED_BEFORE_PRESS',button:textValue,before:point,after:final});
    await page.cdp.call('Input.dispatchMouseEvent',{type:'mousePressed',x:point.x,y:point.y,button:'left',clickCount:1},page.sessionId);
    await page.cdp.call('Input.dispatchMouseEvent',{type:'mouseReleased',x:point.x,y:point.y,button:'left',clickCount:1},page.sessionId);
    await record({event:'interaction',phase:'after_click',button:textValue??selector,root,
      events:await page.evaluate('window.__materialChainInteractions??[]')});
  };

  const clickElementStable = (page,selector,code) => clickButtonText(page,null,code,30_000,{selector});

  const check = async (id, operation) => {
    try {
      const details = await operation();
      checks.push({id,outcome:'passed',...(id==='fixture_manifest'?{formats:details.files.map(file=>file.id)}:details)});
      await record({event:'check',...checks.at(-1)});
      process.stdout.write(`${JSON.stringify({material_chain_check:checks.at(-1)})}\n`);
      return details;
    } catch (error) {
      const reason = error instanceof VerifyError ? error.code : 'MATERIAL_CHAIN_UNEXPECTED';
      const diagnostic = await diagnose(id);
      checks.push({id,outcome:'failed',reason,...(error.evidence ?? {}),...(diagnostic?{diagnostic}:{})});
      await record({event:'check',...checks.at(-1)});
      for (const pending of planned.filter(value=>!checks.some(item=>item.id===value))) {
        await record({event:'check',id:pending,outcome:'not_run',reason:'PREVIOUS_CHECK_FAILED'});
      }
      throw new VerifyError(reason,{stage:'material-chain',checks,documents});
    }
  };
  const fixtures = await check('fixture_manifest', async () => {
    const directory = path.join(controlDirectory,'material-fixtures');
    const stat = await lstat(directory);
    if (!stat.isDirectory() || stat.isSymbolicLink()) reject('MATERIAL_CHAIN_FIXTURE_DIRECTORY_INVALID');
    const manifest = JSON.parse(await readFile(path.join(directory,'manifest.json'),'utf8'));
    if (!Array.isArray(manifest.files) || manifest.files.length !== 4) reject('MATERIAL_CHAIN_MANIFEST_INVALID');
    const files = [];
    for (const [id,[filename,mime,locator_kind]] of Object.entries(FORMATS)) {
      const matches = manifest.files.filter(file => file.id === id);
      if (matches.length !== 1) reject('MATERIAL_CHAIN_FORMAT_MISSING',{format:id});
      const item = matches[0];
      const filePath = path.resolve(directory,item.path);
      const info = await lstat(filePath);
      if (path.dirname(filePath) !== directory || path.basename(filePath) !== filename
        || item.filename !== filename || item.mime !== mime || item.locator_kind !== locator_kind
        || !info.isFile() || info.isSymbolicLink() || !info.size || info.size > 20 * 1024 * 1024
        || typeof item.expected_text !== 'string' || !item.expected_text.trim()) reject('MATERIAL_CHAIN_FIXTURE_INVALID',{format:id});
      const actualSha = sha256(await readFile(filePath));
      if (typeof item.sha256!=='string' || !/^[0-9a-f]{64}$/.test(item.sha256) || item.sha256!==actualSha) {
        reject('MATERIAL_CHAIN_FIXTURE_SHA_MISMATCH',{format:id});
      }
      files.push({...item,path:filePath,source_sha256:actualSha});
    }
    const shared=manifest.shared_file;
    if (!shared || shared.id!=='shared_docx' || shared.filename!=='shared-method.docx'
      || shared.mime!==FORMATS.docx[1] || shared.locator_kind!=='docx_block' || typeof shared.path!=='string'
      || typeof shared.expected_text!=='string' || !shared.expected_text.trim()) reject('MATERIAL_CHAIN_SHARED_FIXTURE_INVALID');
    const sharedPath=path.resolve(directory,shared.path);
    const sharedInfo=await lstat(sharedPath);
    if(path.dirname(sharedPath)!==directory || path.basename(sharedPath)!==shared.filename || !sharedInfo.isFile()
      || sharedInfo.isSymbolicLink() || !sharedInfo.size || sharedInfo.size>20*1024*1024) reject('MATERIAL_CHAIN_SHARED_FIXTURE_INVALID');
    const sharedSha=sha256(await readFile(sharedPath));
    if(typeof shared.sha256!=='string' || !/^[0-9a-f]{64}$/.test(shared.sha256) || shared.sha256!==sharedSha) reject('MATERIAL_CHAIN_FIXTURE_SHA_MISMATCH',{format:shared.id});
    const extras={};
    for(const [id,filename,mime,expectedText,locatorKind] of [
      ['new_docx','chain-new.docx',FORMATS.docx[1],'ZXNEW 46 mg/L','docx_block'],
      ['partial_xlsx','formula-unresolved.xlsx',FORMATS.xlsx[1],'XLSX_FORMULA_UNRESOLVED','xlsx_cells'],
      ['resume_docx','resume-note.docx',FORMATS.docx[1],'Synthetic resumable upload identity remains unchanged after a lost receipt.','docx_block'],
      ['invalid_file','unsupported.bin','application/octet-stream','UNSUPPORTED_EXTENSION',null],
    ]) {
      const item=manifest.extra_files?.[id];
      if(!item || item.id!==id || item.filename!==filename || item.mime!==mime || item.expected_text!==expectedText
        || item.locator_kind!==locatorKind || typeof item.path!=='string') reject('MATERIAL_CHAIN_EXTRA_FIXTURE_INVALID',{format:id});
      const filenamePath=path.resolve(directory,item.path),info=await lstat(filenamePath);
      if(path.dirname(filenamePath)!==directory || path.basename(filenamePath)!==filename || !info.isFile() || info.isSymbolicLink()
        || !info.size || info.size>20*1024*1024) reject('MATERIAL_CHAIN_EXTRA_FIXTURE_INVALID',{format:id});
      const digest=sha256(await readFile(filenamePath));
      if(typeof item.sha256!=='string' || !/^[0-9a-f]{64}$/.test(item.sha256) || item.sha256!==digest) reject('MATERIAL_CHAIN_FIXTURE_SHA_MISMATCH',{format:id});
      extras[id]={...item,path:filenamePath,source_sha256:digest};
    }
    return {files,sharedFile:{...shared,path:sharedPath,source_sha256:sharedSha},extras};
  });
  // Keep absolute temporary paths out of the emitted final check evidence.
  checks[0] = {id:'fixture_manifest',outcome:'passed',formats:fixtures.files.map(file=>file.id)};
  return runIdentityToPath(cdp,origin,secretDirectory,identity,'/console/clients',async page => {
    activePage=page;
    await check('provider_identity',async () => {
      await bindSessionAccess(page,enterpriseId,'provider_admin','MATERIAL_CHAIN_SESSION_MISMATCH');
      await navigateLoggedInPath(page,`/console/clients/${crmAccountId}`,'MATERIAL_CHAIN_CLIENT_NAV_FAILED');
      await clickElementStable(page,'.ant-tabs-tab[data-node-key="materials"] [role="tab"]','MATERIAL_CHAIN_MATERIAL_TAB_MISSING');
      await page.waitForExpression(`location.pathname===${JSON.stringify(`/console/clients/${crmAccountId}/materials`)}`,'MATERIAL_CHAIN_MATERIAL_ROUTE_MISSING');
      return {identity:'tenant-a',product_role:'provider_admin',client_account_id:crmAccountId};
    });
    await check('batch_upload_four_formats',async () => {
      await clickButtonText(page,'批量上传','MATERIAL_CHAIN_BATCH_BUTTON_MISSING');
      await page.waitForExpression(`!!document.querySelector('.ant-modal-wrap:not([style*="display: none"]) .ant-modal input[type="file"][multiple]')`,'MATERIAL_CHAIN_BATCH_INPUT_MISSING');
      const before = page.apiResponseEvents.length;
      await page.setFileInputFiles('.ant-modal-wrap:not([style*="display: none"]) .ant-modal input[type="file"][multiple]',fixtures.files.map(file=>file.path),'MATERIAL_CHAIN_BATCH_FILE_SELECTION_FAILED');
      await page.waitForExpression(`document.querySelectorAll('.ant-modal-wrap:not([style*="display: none"]) .ant-modal section').length===4`,'MATERIAL_CHAIN_BATCH_ROWS_MISSING');
      await clickButtonText(page,'上传未完成文件','MATERIAL_CHAIN_BATCH_SUBMIT_MISSING',30_000,{root:ACTIVE_MODAL});
      await page.waitForExpression(`Array.from(document.querySelectorAll('.ant-modal-wrap:not([style*="display: none"]) .ant-modal section')).filter(row=>(row.textContent??'').includes('已接收')).length===4`,'MATERIAL_CHAIN_BATCH_RECEIPTS_MISSING',120_000);
      await page.waitForApiIdle();
      const events = page.apiResponseEvents.slice(before).filter(event=>event.method==='POST' && event.path==='/api/v1/ingestion/documents');
      if (events.length!==4 || events.some(event=>event.status!==202)) reject('MATERIAL_CHAIN_UPLOAD_HTTP_MISMATCH',{statuses:events.map(event=>event.status)});
      for (const event of events) {
        const body = JSON.parse(await page.getResponseBody(event.requestId,'MATERIAL_CHAIN_UPLOAD_BODY_MISSING'));
        const fixture = fixtures.files.find(file=>file.filename===body.display_name);
        const version = body.latest_version;
        if (!fixture || !version?.id || !body.id || body.knowledge_scope?.kind!=='client'
          || body.knowledge_scope?.client_account_id!==crmAccountId || version.content_type!==fixture.mime) reject('MATERIAL_CHAIN_UPLOAD_RECEIPT_INVALID');
        documents.push({format:fixture.id,document_id:body.id,version_id:version.id,filename:fixture.filename});
      }
      if (new Set(documents.map(item=>item.format)).size!==4 || new Set(documents.map(item=>item.version_id)).size!==4) reject('MATERIAL_CHAIN_UPLOAD_ID_COLLISION');
      await clickButtonText(page,'关闭','MATERIAL_CHAIN_BATCH_CLOSE_MISSING',30_000,{root:ACTIVE_MODAL});
      await page.waitForExpression(`!document.querySelector('.ant-modal-wrap:not([style*="display: none"]) .ant-modal input[type="file"][multiple]')`,'MATERIAL_CHAIN_BATCH_CLOSE_FAILED');
      return {received:4,http_statuses:events.map(event=>event.status)};
    });
    for (const fixture of fixtures.files) {
      const document = documents.find(item=>item.format===fixture.id);
      let review;
      await check(`${fixture.id}_processed`,async () => {
        const deadline = Date.now()+PROCESS_TIMEOUT;
        let last;
        while (Date.now()<deadline) {
          const version = await sessionHttp(page,`/api/v1/ingestion/versions/${document.version_id}`);
          const candidate = await sessionHttp(page,`/api/v1/ingestion/versions/${document.version_id}/review`);
          last={http_status:version.status,version_status:version.payload?.workflow_status,quarantine_status:version.payload?.quarantine_status,
            scan_status:version.payload?.scan_status,preview_status:version.payload?.preview_status,retryable:version.payload?.retryable,
            reason:version.payload?.reason_code,review_http_status:candidate.status,editable:candidate.payload?.editable};
          if (version.status!==200) reject('MATERIAL_CHAIN_VERSION_HTTP_FAILED',{format:fixture.id,...last});
          if (version.payload?.id!==document.version_id || !['received','processing','ready','blocked','failed'].includes(version.payload?.workflow_status)
            || typeof version.payload?.retryable!=='boolean') reject('MATERIAL_CHAIN_VERSION_DTO_INVALID',{format:fixture.id,...last});
          if (version.status===200 && version.payload?.quarantine_status==='released' && version.payload?.scan_status==='clean' && version.payload?.preview_status==='ready' && candidate.status===200 && candidate.payload?.editable) {
            review=candidate.payload;break;
          }
          // VersionOut maps processing rejection and terminal delivery failure to
          // blocked, not rejected; only retryable processing is worth polling.
          if (version.payload.workflow_status==='failed' || version.payload.workflow_status==='blocked' && !version.payload.retryable
            || version.payload.scan_status==='infected') reject('MATERIAL_CHAIN_PROCESSING_FAILED',{format:fixture.id,...last});
          await delay(1500);
        }
        if (!review) reject('MATERIAL_CHAIN_PROCESSING_TIMEOUT',{format:fixture.id,...last});
        if (review.source_format!==fixture.id || review.source_sha256!==fixture.source_sha256 || !review.base_items?.length
          || review.base_items.some(item=>item.locator?.kind!==fixture.locator_kind)
          || !normalize(review.base_items.map(item=>item.text).join(' ')).includes(normalize(fixture.expected_text))) reject('MATERIAL_CHAIN_EXTRACTION_MISMATCH',{format:fixture.id});
        return {version_id:document.version_id,source_sha256:review.source_sha256,base_revision_id:review.base_revision_id,
          locator_kind:fixture.locator_kind,fragment_count:review.base_items.length};
      });
      await check(`${fixture.id}_original_ui`,async () => {
        await clickButtonText(page,fixture.filename,'MATERIAL_CHAIN_DOCUMENT_BUTTON_MISSING');
        await clickButtonText(page,'校对提取内容','MATERIAL_CHAIN_REVIEW_BUTTON_MISSING');
        await page.waitForExpression(`!!document.querySelector('.ant-modal-wrap:not([style*="display: none"]) .ant-modal textarea[aria-label^="修订 "]')`,'MATERIAL_CHAIN_REVIEW_CONTENT_MISSING');
        const item=review.base_items.find(item=>normalize(item.text).includes(normalize(fixture.expected_text)));
        if (!item) reject('MATERIAL_CHAIN_EXPECTED_FRAGMENT_MISSING',{format:fixture.id});
        // Every fixture deliberately fits on the first review page.
        const selector=`.ant-modal-wrap:not([style*="display: none"]) .ant-modal section:has(textarea[aria-label=${JSON.stringify(`修订 ${item.location}`)}])`;
        await clickButtonText(page,'查看原件位置','MATERIAL_CHAIN_ORIGINAL_BUTTON_MISSING',30_000,{root:selector});
        const route=`/api/v1/evidence/versions/${document.version_id}/review-fragments/${item.id}/original`;
        const response=await bindLastAnalysisRequest(page,'GET',route,[200],'MATERIAL_CHAIN_ORIGINAL_HTTP_MISSING');
        const original=JSON.parse(response.body);
        if (original.source_sha256!==fixture.source_sha256 || original.document_version_id!==document.version_id
          || canonical(original.locator)!==canonical(item.locator)) reject('MATERIAL_CHAIN_ORIGINAL_IDENTITY_MISMATCH',{format:fixture.id});
        if (['pdf','jpeg'].includes(fixture.id)) {
          if (!original.image?.startsWith('data:image/jpeg;base64,')) reject('MATERIAL_CHAIN_ORIGINAL_IMAGE_MISSING');
          await page.waitForExpression(`Array.from(document.querySelectorAll('.ant-modal-wrap:not([style*="display: none"]) .ant-modal img')).some(image=>image.complete && image.naturalWidth>0)`,'MATERIAL_CHAIN_ORIGINAL_IMAGE_NOT_RENDERED');
        } else if (!normalize(original.original_text).includes(normalize(fixture.expected_text))) reject('MATERIAL_CHAIN_ORIGINAL_TEXT_MISMATCH',{format:fixture.id});
        await page.waitForExpression(`(document.querySelector('.ant-modal-wrap:not([style*="display: none"]) .ant-modal')?.innerText??'').includes(${JSON.stringify(`原件 · ${item.location}`)})`,'MATERIAL_CHAIN_ORIGINAL_UI_MISSING');
        return {locator_kind:fixture.locator_kind,original_http_status:200,rendered:true};
      });
      if (fixture.id==='docx') await check('docx_human_revision_ui',async () => {
        const item=review.base_items.find(item=>normalize(item.text).includes(normalize(fixture.expected_text)));
        const revised=`${item.text}\nReviewed source value: ${fixture.expected_text}`;
        const selector=`.ant-modal-wrap:not([style*="display: none"]) .ant-modal textarea[aria-label=${JSON.stringify(`修订 ${item.location}`)}]`;
        await clickElementStable(page,selector,'MATERIAL_CHAIN_REVISION_TEXTAREA_MISSING');
        await page.evaluate(`(() => {const input=document.querySelector(${JSON.stringify(selector)}); input.select()})()`);
        await page.cdp.call('Input.insertText',{text:revised},page.sessionId);
        await page.waitForExpression(`document.querySelector(${JSON.stringify(selector)})?.value===${JSON.stringify(revised)}`,'MATERIAL_CHAIN_REVISION_INPUT_NOT_UPDATED');
        await clickElementStable(page,`${ACTIVE_MODAL} .ant-checkbox-wrapper`,'MATERIAL_CHAIN_REVIEW_CHECKBOX_MISSING');
        await page.waitForExpression(`document.querySelector(${JSON.stringify(`${ACTIVE_MODAL} input[type=checkbox]`)})?.checked===true`,'MATERIAL_CHAIN_REVIEW_NOT_CHECKED');
        await clickButtonText(page,'确认并保存修订','MATERIAL_CHAIN_REVIEW_CONFIRM_MISSING',30_000,{root:ACTIVE_MODAL});
        const response=await bindLastAnalysisRequest(page,'POST',`/api/v1/ingestion/versions/${document.version_id}/review`,[200],'MATERIAL_CHAIN_REVIEW_HTTP_MISSING');
        const receipt=JSON.parse(response.body);
        const after=await sessionHttp(page,`/api/v1/ingestion/versions/${document.version_id}/review`);
        if (receipt.action!=='confirm' || after.status!==200 || after.payload?.review_head?.id!==receipt.id
          || after.payload?.review_head?.base_revision_id!==review.base_revision_id
          || !after.payload.review_items.some(entry=>entry.text===revised)
          || after.payload.base_items.find(entry=>entry.id===item.id)?.text!==item.text) reject('MATERIAL_CHAIN_REVIEW_NOT_PRESERVED');
        document.review_revision_id=receipt.id;
        document.reviewed_text=revised;
        return {revision_id:receipt.id,revision_no:receipt.revision_no,original_preserved:true};
      });
      // Reload through the existing route; review completion may remount the drawer.
      await navigateLoggedInPath(page,`/console/clients/${crmAccountId}/materials`,'MATERIAL_CHAIN_MATERIAL_RETURN_FAILED');
    }
    for (const fixture of fixtures.files) await check(`${fixture.id}_retrieval_http`,async () => {
      const document=documents.find(item=>item.format===fixture.id);
      const body={question:fixture.expected_text,request_id:randomUUID(),client_account_id:crmAccountId};
      const answer=await sessionHttp(page,'/api/v1/material-qa',body);
      const matching=answer.payload?.citations?.find(item=>item.document_version_id===document.version_id
        && item.locator?.kind===fixture.locator_kind && normalize(item.snippet).includes(normalize(fixture.expected_text)));
      if (answer.status!==200 || !answer.payload?.answer || !matching || matching.source_sha256!==fixture.source_sha256
        || document.review_revision_id && matching.evidence_revision_id!==document.review_revision_id) reject('MATERIAL_CHAIN_RETRIEVAL_MISMATCH',{format:fixture.id,http_status:answer.status,refusal_reason:answer.payload?.refusal_reason});
      return {version_id:document.version_id,locator_kind:fixture.locator_kind,evidence_revision_id:matching.evidence_revision_id ?? null};
    });
    let sharedDocument;
    await check('shared_material_upload_ui',async () => {
      const fixture=fixtures.sharedFile;
      await navigateLoggedInPath(page,'/console/shared-materials','MATERIAL_CHAIN_SHARED_NAV_FAILED');
      await clickButtonText(page,'批量上传','MATERIAL_CHAIN_SHARED_BATCH_BUTTON_MISSING');
      await page.waitForExpression(`!!document.querySelector(${JSON.stringify(`${ACTIVE_MODAL} input[type="file"][multiple]`)})`,'MATERIAL_CHAIN_SHARED_INPUT_MISSING');
      const before=page.apiResponseEvents.length;
      await page.setFileInputFiles(`${ACTIVE_MODAL} input[type="file"][multiple]`,[fixture.path],'MATERIAL_CHAIN_SHARED_FILE_SELECTION_FAILED');
      await page.waitForExpression(`document.querySelectorAll(${JSON.stringify(`${ACTIVE_MODAL} section`)}).length===1`,'MATERIAL_CHAIN_SHARED_ROW_MISSING');
      await clickButtonText(page,'上传未完成文件','MATERIAL_CHAIN_SHARED_SUBMIT_MISSING',30_000,{root:ACTIVE_MODAL});
      await page.waitForExpression(`Array.from(document.querySelectorAll(${JSON.stringify(`${ACTIVE_MODAL} section`)})).filter(row=>(row.textContent??'').includes('已接收')).length===1`,'MATERIAL_CHAIN_SHARED_RECEIPT_MISSING',120_000);
      await page.waitForApiIdle();
      const events=page.apiResponseEvents.slice(before).filter(event=>event.method==='POST' && event.path==='/api/v1/ingestion/documents');
      if(events.length!==1 || events[0].status!==202) reject('MATERIAL_CHAIN_SHARED_UPLOAD_HTTP_MISMATCH',{statuses:events.map(event=>event.status)});
      const body=JSON.parse(await page.getResponseBody(events[0].requestId,'MATERIAL_CHAIN_SHARED_UPLOAD_BODY_MISSING'));
      if(body.display_name!==fixture.filename || !body.id || !body.latest_version?.id || body.latest_version.content_type!==fixture.mime
        || body.knowledge_scope?.kind!=='service_provider' || body.knowledge_scope?.client_account_id!==null) reject('MATERIAL_CHAIN_SHARED_SCOPE_MISMATCH');
      sharedDocument={format:'shared_docx',document_id:body.id,version_id:body.latest_version.id,filename:fixture.filename,scope_kind:'service_provider'};
      documents.push(sharedDocument);
      await clickButtonText(page,'关闭','MATERIAL_CHAIN_SHARED_BATCH_CLOSE_MISSING',30_000,{root:ACTIVE_MODAL});
      await page.waitForExpression(`!document.querySelector(${JSON.stringify(`${ACTIVE_MODAL} input[type="file"][multiple]`)})`,'MATERIAL_CHAIN_SHARED_BATCH_CLOSE_FAILED');
      return {document_id:sharedDocument.document_id,version_id:sharedDocument.version_id,scope_kind:'service_provider',http_status:202};
    });
    await check('shared_material_processed',async () => {
      const fixture=fixtures.sharedFile,deadline=Date.now()+PROCESS_TIMEOUT;
      let last,review;
      while(Date.now()<deadline) {
        const version=await sessionHttp(page,`/api/v1/ingestion/versions/${sharedDocument.version_id}`);
        const candidate=await sessionHttp(page,`/api/v1/ingestion/versions/${sharedDocument.version_id}/review`);
        last={http_status:version.status,version_status:version.payload?.workflow_status,quarantine_status:version.payload?.quarantine_status,
          scan_status:version.payload?.scan_status,preview_status:version.payload?.preview_status,retryable:version.payload?.retryable,
          reason:version.payload?.reason_code,review_http_status:candidate.status,editable:candidate.payload?.editable};
        if(version.status!==200) reject('MATERIAL_CHAIN_SHARED_VERSION_HTTP_FAILED',last);
        if(version.payload?.id!==sharedDocument.version_id || !['received','processing','ready','blocked','failed'].includes(version.payload?.workflow_status)
          || typeof version.payload?.retryable!=='boolean') reject('MATERIAL_CHAIN_SHARED_VERSION_DTO_INVALID',last);
        if(version.payload.quarantine_status==='released' && version.payload.scan_status==='clean' && version.payload.preview_status==='ready'
          && candidate.status===200 && candidate.payload?.editable) {review=candidate.payload;break}
        if(version.payload.workflow_status==='failed' || version.payload.workflow_status==='blocked' && !version.payload.retryable
          || version.payload.scan_status==='infected') reject('MATERIAL_CHAIN_SHARED_PROCESSING_FAILED',last);
        await delay(1500);
      }
      if(!review) reject('MATERIAL_CHAIN_SHARED_PROCESSING_TIMEOUT',last);
      if(review.version_id!==sharedDocument.version_id || review.source_format!=='docx' || review.source_sha256!==fixture.source_sha256
        || !review.base_items?.length || review.base_items.some(item=>item.locator?.kind!=='docx_block')
        || !normalize(review.base_items.map(item=>item.text).join(' ')).includes(normalize(fixture.expected_text))) reject('MATERIAL_CHAIN_SHARED_EXTRACTION_MISMATCH');
      sharedDocument.base_revision_id=review.base_revision_id;
      return {version_id:sharedDocument.version_id,source_sha256:review.source_sha256,base_revision_id:review.base_revision_id,
        locator_kind:'docx_block',fragment_count:review.base_items.length,review_http_status:200};
    });
    await check('report_generation_four_format_citations_ui',async () => {
      await navigateLoggedInPath(page,`/console/clients/${crmAccountId}/reports`,'MATERIAL_CHAIN_REPORT_NAV_FAILED');
      await clickButtonText(page,'新建报告','MATERIAL_CHAIN_CREATE_REPORT_BUTTON_MISSING');
      await page.waitForExpression(`(document.body?.innerText??'').includes('生成首个版本')`,'MATERIAL_CHAIN_REPORT_WORKBENCH_MISSING');
      const created=await bindLastAnalysisRequest(page,'POST',`/api/v1/analysis-reports/clients/${crmAccountId}/reports`,[200],'MATERIAL_CHAIN_REPORT_CREATE_HTTP_MISSING');
      const report=JSON.parse(created.body);
      await clickButtonText(page,'生成首个版本','MATERIAL_CHAIN_GENERATE_BUTTON_MISSING');
      const generated=await bindLastAnalysisRequest(page,'POST','/generations',[200],'MATERIAL_CHAIN_GENERATION_HTTP_MISSING');
      const generation=JSON.parse(generated.body);
      await page.waitForExpression(`Array.from(document.querySelectorAll('button')).some(button=>(button.textContent??'').replace(/\\s+/g,'')==='提交审核')`,'MATERIAL_CHAIN_REPORT_DRAFT_TIMEOUT',PROCESS_TIMEOUT);
      const detail=await sessionHttp(page,`/api/v1/analysis-reports/versions/${generation.version_id}`);
      if (detail.status!==200 || detail.payload?.status!=='draft' || detail.payload?.report_id!==report.report_id) reject('MATERIAL_CHAIN_REPORT_DRAFT_MISMATCH');
      for (const fixture of fixtures.files) {
        const document=documents.find(item=>item.format===fixture.id);
        const citation=detail.payload.citations?.find(item=>item.document_version_id===document.version_id
          && item.locator?.kind===fixture.locator_kind && normalize(item.excerpt).includes(normalize(fixture.expected_text)));
        if (!citation || document.review_revision_id && citation.evidence_revision_id!==document.review_revision_id) reject('MATERIAL_CHAIN_REPORT_CITATION_MISMATCH',{format:fixture.id});
      }
      const sharedCitation=detail.payload.citations?.find(item=>item.document_version_id===sharedDocument.version_id
        && item.locator?.kind==='docx_block' && item.evidence_revision_id===sharedDocument.base_revision_id
        && normalize(item.excerpt).includes(normalize(fixtures.sharedFile.expected_text)));
      if(!sharedCitation) reject('MATERIAL_CHAIN_REPORT_SHARED_CITATION_MISMATCH');
      frozenReport={report_id:report.report_id,version_id:generation.version_id,
        sections:detail.payload.sections,citations:detail.payload.citations,
        body_sha256:sha256(canonical({sections:detail.payload.sections,citations:detail.payload.citations}))};
      return {report_id:report.report_id,version_id:generation.version_id,formats:fixtures.files.map(file=>file.id),
        shared_version_id:sharedDocument.version_id,shared_citation_id:sharedCitation.citation_id,status:'draft'};
    });
    const originalDocx=documents.find(item=>item.format==='docx');
    let successor,oldOriginal;
    const oldCitation=frozenReport.citations.find(item=>item.document_version_id===originalDocx.version_id);
    await check('docx_new_version_upload_ui',async () => {
      const fixture=fixtures.extras.new_docx;
      const previous=await sessionHttp(page,`/api/v1/ingestion/versions/${originalDocx.version_id}`);
      if(previous.status!==200 || previous.payload?.document_id!==originalDocx.document_id || !Number.isInteger(previous.payload.version_number)) reject('MATERIAL_CHAIN_OLD_VERSION_IDENTITY_INVALID');
      const original=await sessionHttp(page,`/api/v1/evidence/citations/${oldCitation.citation_id}/original`);
      const oldFixture=fixtures.files.find(item=>item.id==='docx');
      if(original.status!==200 || original.payload?.source_sha256!==oldFixture.source_sha256
        || original.payload?.document_version_id!==originalDocx.version_id
        || !normalize(original.payload?.original_text).includes(normalize(oldFixture.expected_text))) reject('MATERIAL_CHAIN_FROZEN_ORIGINAL_BASE_INVALID');
      oldOriginal=original.payload;
      await navigateLoggedInPath(page,`/console/clients/${crmAccountId}/materials`,'MATERIAL_CHAIN_NEW_VERSION_NAV_FAILED');
      await clickButtonText(page,originalDocx.filename,'MATERIAL_CHAIN_NEW_VERSION_DOCUMENT_MISSING');
      await clickButtonText(page,'上传新版本','MATERIAL_CHAIN_NEW_VERSION_BUTTON_MISSING');
      await page.waitForExpression(`!!document.querySelector(${JSON.stringify(`${ACTIVE_MODAL} input[type="file"]`)})`,'MATERIAL_CHAIN_NEW_VERSION_INPUT_MISSING');
      const before=page.apiResponseEvents.length;
      await page.setFileInputFiles(`${ACTIVE_MODAL} input[type="file"]`,[fixture.path],'MATERIAL_CHAIN_NEW_VERSION_FILE_SELECTION_FAILED');
      await page.waitForExpression(`(document.querySelector(${JSON.stringify(ACTIVE_MODAL)})?.innerText??'').includes(${JSON.stringify(fixture.filename)})`,'MATERIAL_CHAIN_NEW_VERSION_FILE_NOT_VISIBLE');
      await clickButtonText(page,'上传到隔离区','MATERIAL_CHAIN_NEW_VERSION_SUBMIT_MISSING',30_000,{root:ACTIVE_MODAL});
      const route=`/api/v1/ingestion/documents/${originalDocx.document_id}/versions`;
      const response=await bindLastAnalysisRequest(page,'POST',route,[202],'MATERIAL_CHAIN_NEW_VERSION_RECEIPT_MISSING');
      const events=page.apiResponseEvents.slice(before).filter(item=>item.method==='POST' && item.path===route);
      const version=JSON.parse(response.body);
      if(events.length!==1 || version.document_id!==originalDocx.document_id || !version.id || version.id===originalDocx.version_id
        || version.version_number!==previous.payload.version_number+1 || version.content_type!==fixture.mime || version.original_filename!==fixture.filename) reject('MATERIAL_CHAIN_NEW_VERSION_RECEIPT_INVALID');
      successor={format:'new_docx',document_id:version.document_id,version_id:version.id,filename:fixture.filename,
        version_number:version.version_number,previous_version_id:originalDocx.version_id};
      documents.push(successor);
      return {document_id:version.document_id,old_version_id:originalDocx.version_id,new_version_id:version.id,
        old_version_number:previous.payload.version_number,new_version_number:version.version_number,http_status:202};
    });
    await check('docx_new_version_processed_retrieval',async () => {
      const fixture=fixtures.extras.new_docx,deadline=Date.now()+PROCESS_TIMEOUT;
      let review,last;
      while(Date.now()<deadline) {
        const version=await sessionHttp(page,`/api/v1/ingestion/versions/${successor.version_id}`);
        const candidate=await sessionHttp(page,`/api/v1/ingestion/versions/${successor.version_id}/review`);
        const native=await sessionHttp(page,`/api/v1/ingestion/versions/${successor.version_id}/native-extraction`);
        last={http_status:version.status,workflow_status:version.payload?.workflow_status,quarantine_status:version.payload?.quarantine_status,
          reason:version.payload?.reason_code,native_http_status:native.status,native_state:native.payload?.state,effective_state:native.payload?.effective?.state};
        if(version.status!==200 || version.payload?.id!==successor.version_id || version.payload?.document_id!==originalDocx.document_id) reject('MATERIAL_CHAIN_NEW_VERSION_STATUS_INVALID',last);
        if(version.payload.workflow_status==='failed' || version.payload.workflow_status==='blocked' && !version.payload.retryable
          || native.payload?.state==='blocked') reject('MATERIAL_CHAIN_NEW_VERSION_PROCESSING_FAILED',last);
        if(version.payload.quarantine_status==='released' && version.payload.scan_status==='clean' && version.payload.preview_status==='ready'
          && candidate.status===200 && candidate.payload?.editable && native.status===200 && native.payload?.state==='done'
          && native.payload?.coverage_state==='complete' && native.payload?.effective?.state==='ready') {review=candidate.payload;break}
        await delay(1500);
      }
      if(!review) reject('MATERIAL_CHAIN_NEW_VERSION_PROCESSING_TIMEOUT',last);
      if(review.version_id!==successor.version_id || review.source_format!=='docx' || review.source_sha256!==fixture.source_sha256
        || review.source_sha256===oldOriginal.source_sha256 || !review.base_items?.length
        || review.base_items.some(item=>item.locator?.kind!=='docx_block')
        || !normalize(review.base_items.map(item=>item.text).join(' ')).includes(normalize(fixture.expected_text))) reject('MATERIAL_CHAIN_NEW_VERSION_EXTRACTION_MISMATCH');
      const detail=await sessionHttp(page,`/api/v1/ingestion/documents/${originalDocx.document_id}`);
      if(detail.status!==200 || detail.payload?.id!==originalDocx.document_id || detail.payload?.latest_version?.id!==successor.version_id
        || detail.payload?.latest_version?.version_number!==successor.version_number
        || !detail.payload?.versions?.some(item=>item.id===originalDocx.version_id)) reject('MATERIAL_CHAIN_VERSION_HISTORY_MISMATCH');
      const answer=await sessionHttp(page,'/api/v1/material-qa',{question:fixture.expected_text,request_id:randomUUID(),client_account_id:crmAccountId});
      const citation=answer.payload?.citations?.find(item=>item.document_version_id===successor.version_id && item.source_sha256===fixture.source_sha256
        && item.evidence_revision_id===review.base_revision_id && item.locator?.kind==='docx_block' && normalize(item.snippet).includes(normalize(fixture.expected_text)));
      if(answer.status!==200 || !answer.payload?.answer || !citation || answer.payload.citations.some(item=>item.document_version_id===originalDocx.version_id)) reject('MATERIAL_CHAIN_NEW_VERSION_RETRIEVAL_MISMATCH');
      const stale=await sessionHttp(page,'/api/v1/material-qa',{question:fixtures.files.find(item=>item.id==='docx').expected_text,request_id:randomUUID(),client_account_id:crmAccountId});
      if(stale.status!==200 || !Array.isArray(stale.payload?.citations) || stale.payload.citations.some(item=>item.document_version_id===originalDocx.version_id)) reject('MATERIAL_CHAIN_OLD_VERSION_STILL_RETRIEVABLE');
      successor.base_revision_id=review.base_revision_id;
      return {version_id:successor.version_id,source_sha256:review.source_sha256,base_revision_id:review.base_revision_id,
        version_number:successor.version_number,old_version_excluded:true};
    });
    await check('frozen_report_preserved_after_new_version',async () => {
      const current=await sessionHttp(page,`/api/v1/analysis-reports/versions/${frozenReport.version_id}`);
      if(current.status!==200 || current.payload?.version_id!==frozenReport.version_id || current.payload?.report_id!==frozenReport.report_id
        || canonical(current.payload.sections)!==canonical(frozenReport.sections) || canonical(current.payload.citations)!==canonical(frozenReport.citations)) reject('MATERIAL_CHAIN_FROZEN_REPORT_CHANGED');
      const citation=current.payload.citations.find(item=>item.citation_id===oldCitation.citation_id);
      if(citation.document_version_id!==originalDocx.version_id || citation.evidence_revision_id!==originalDocx.review_revision_id
        || citation.excerpt!==oldCitation.excerpt || canonical(citation.locator)!==canonical(oldCitation.locator)) reject('MATERIAL_CHAIN_FROZEN_CITATION_CHANGED');
      const original=await sessionHttp(page,`/api/v1/evidence/citations/${citation.citation_id}/original`);
      if(original.status!==200 || canonical(original.payload)!==canonical(oldOriginal)
        || original.payload.source_sha256===fixtures.extras.new_docx.source_sha256) reject('MATERIAL_CHAIN_FROZEN_ORIGINAL_CHANGED');
      return {report_version_id:frozenReport.version_id,citation_id:citation.citation_id,old_document_version_id:originalDocx.version_id,
        old_evidence_revision_id:citation.evidence_revision_id,old_source_sha256:original.payload.source_sha256,
        frozen_content_sha256:frozenReport.body_sha256};
    });
    let partialDocument;
    await check('partial_xlsx_upload_ui',async () => {
      const fixture=fixtures.extras.partial_xlsx;
      await navigateLoggedInPath(page,`/console/clients/${crmAccountId}/materials`,'MATERIAL_CHAIN_PARTIAL_NAV_FAILED');
      await clickButtonText(page,'批量上传','MATERIAL_CHAIN_PARTIAL_BATCH_BUTTON_MISSING');
      await page.waitForExpression(`!!document.querySelector(${JSON.stringify(`${ACTIVE_MODAL} input[type="file"][multiple]`)})`,'MATERIAL_CHAIN_PARTIAL_INPUT_MISSING');
      const before=page.apiResponseEvents.length;
      await page.setFileInputFiles(`${ACTIVE_MODAL} input[type="file"][multiple]`,[fixture.path],'MATERIAL_CHAIN_PARTIAL_FILE_SELECTION_FAILED');
      await page.waitForExpression(`document.querySelectorAll(${JSON.stringify(`${ACTIVE_MODAL} section`)}).length===1`,'MATERIAL_CHAIN_PARTIAL_ROW_MISSING');
      await clickButtonText(page,'上传未完成文件','MATERIAL_CHAIN_PARTIAL_SUBMIT_MISSING',30_000,{root:ACTIVE_MODAL});
      await page.waitForExpression(`Array.from(document.querySelectorAll(${JSON.stringify(`${ACTIVE_MODAL} section`)})).filter(row=>(row.textContent??'').includes('已接收')).length===1`,'MATERIAL_CHAIN_PARTIAL_RECEIPT_MISSING',120_000);
      await page.waitForApiIdle();
      const events=page.apiResponseEvents.slice(before).filter(event=>event.method==='POST' && event.path==='/api/v1/ingestion/documents');
      if(events.length!==1 || events[0].status!==202) reject('MATERIAL_CHAIN_PARTIAL_UPLOAD_HTTP_MISMATCH');
      const body=JSON.parse(await page.getResponseBody(events[0].requestId,'MATERIAL_CHAIN_PARTIAL_UPLOAD_BODY_MISSING'));
      if(body.display_name!==fixture.filename || !body.id || !body.latest_version?.id || body.latest_version.content_type!==fixture.mime
        || body.knowledge_scope?.kind!=='client' || body.knowledge_scope?.client_account_id!==crmAccountId) reject('MATERIAL_CHAIN_PARTIAL_SCOPE_MISMATCH');
      partialDocument={format:'partial_xlsx',document_id:body.id,version_id:body.latest_version.id,filename:fixture.filename};
      documents.push(partialDocument);
      await clickButtonText(page,'关闭','MATERIAL_CHAIN_PARTIAL_BATCH_CLOSE_MISSING',30_000,{root:ACTIVE_MODAL});
      await page.waitForExpression(`!document.querySelector(${JSON.stringify(`${ACTIVE_MODAL} input[type="file"][multiple]`)})`,'MATERIAL_CHAIN_PARTIAL_BATCH_CLOSE_FAILED');
      return {document_id:body.id,version_id:body.latest_version.id,http_status:202};
    });
    await check('partial_xlsx_excluded_ui_http',async () => {
      const fixture=fixtures.extras.partial_xlsx,deadline=Date.now()+PROCESS_TIMEOUT;
      let native,last;
      while(Date.now()<deadline) {
        const version=await sessionHttp(page,`/api/v1/ingestion/versions/${partialDocument.version_id}`);
        const result=await sessionHttp(page,`/api/v1/ingestion/versions/${partialDocument.version_id}/native-extraction`);
        last={http_status:version.status,workflow_status:version.payload?.workflow_status,quarantine_status:version.payload?.quarantine_status,
          reason:version.payload?.reason_code,native_http_status:result.status,native_state:result.payload?.state};
        if(version.status!==200 || version.payload?.id!==partialDocument.version_id) reject('MATERIAL_CHAIN_PARTIAL_VERSION_INVALID',last);
        if(version.payload.workflow_status==='failed' || version.payload.workflow_status==='blocked' && !version.payload.retryable
          || result.payload?.state==='blocked') reject('MATERIAL_CHAIN_PARTIAL_PROCESSING_FAILED',last);
        if(version.payload.quarantine_status==='released' && version.payload.scan_status==='clean' && version.payload.preview_status==='ready'
          && result.status===200 && result.payload?.state==='done') {native=result.payload;break}
        await delay(1500);
      }
      if(!native) reject('MATERIAL_CHAIN_PARTIAL_PROCESSING_TIMEOUT',last);
      if(native.version_id!==partialDocument.version_id || native.source_format!=='xlsx' || native.coverage_state!=='partial'
        || native.fragment_count!==0 || native.report_source_eligible!==false || native.effective?.state!=='partial' || native.effective?.fragment_count!==0
        || !native.debts?.some(item=>item.reason_code===fixture.expected_text && item.part && item.path)) reject('MATERIAL_CHAIN_PARTIAL_COVERAGE_MISMATCH');
      const fragments=await sessionHttp(page,`/api/v1/ingestion/versions/${partialDocument.version_id}/native-extraction/${native.revision_id}/fragments`);
      const review=await sessionHttp(page,`/api/v1/ingestion/versions/${partialDocument.version_id}/review`);
      if(fragments.status!==200 || fragments.payload?.version_id!==partialDocument.version_id || fragments.payload?.revision_id!==native.revision_id
        || !Array.isArray(fragments.payload?.items) || fragments.payload.items.length!==0
        || review.status!==200 || review.payload?.source_sha256!==fixture.source_sha256 || review.payload?.editable!==false
        || !Array.isArray(review.payload?.base_items) || review.payload.base_items.length!==0) reject('MATERIAL_CHAIN_PARTIAL_EVIDENCE_LEAK');
      const answer=await sessionHttp(page,'/api/v1/material-qa',{question:fixture.filename+' 42+4',request_id:randomUUID(),client_account_id:crmAccountId});
      if(answer.status!==200 || !Array.isArray(answer.payload?.citations) || answer.payload.citations.some(item=>item.document_version_id===partialDocument.version_id)) reject('MATERIAL_CHAIN_PARTIAL_QA_LEAK');
      await navigateLoggedInPath(page,`/console/clients/${crmAccountId}/materials`,'MATERIAL_CHAIN_PARTIAL_STATUS_NAV_FAILED');
      await clickButtonText(page,fixture.filename,'MATERIAL_CHAIN_PARTIAL_DOCUMENT_BUTTON_MISSING');
      await page.waitForExpression(`(document.querySelector('.ant-drawer-open')?.innerText??'').includes('Excel 提取不完整')
        && (document.querySelector('.ant-drawer-open')?.innerText??'').includes('此材料不可用于问答或报告。')`,'MATERIAL_CHAIN_PARTIAL_UI_NOT_VISIBLE');
      return {version_id:partialDocument.version_id,revision_id:native.revision_id,source_sha256:review.payload.source_sha256,
        coverage_state:'partial',fragment_count:0,report_source_eligible:false,debts:native.debts,ui_incomplete_visible:true,qa_excluded:true};
    });
    await check('partial_xlsx_report_blocked_ui',async () => {
      await navigateLoggedInPath(page,`/console/clients/${crmAccountId}/reports`,'MATERIAL_CHAIN_PARTIAL_REPORT_NAV_FAILED');
      await clickButtonText(page,'新建报告','MATERIAL_CHAIN_PARTIAL_CREATE_REPORT_MISSING');
      await page.waitForExpression(`(document.body?.innerText??'').includes('生成首个版本')`,'MATERIAL_CHAIN_PARTIAL_EMPTY_REPORT_MISSING');
      const created=await bindLastAnalysisRequest(page,'POST',`/api/v1/analysis-reports/clients/${crmAccountId}/reports`,[200],'MATERIAL_CHAIN_PARTIAL_REPORT_CREATE_RECEIPT_MISSING');
      const report=JSON.parse(created.body);
      if(!report.report_id || report.current_version_id!==null || report.current_status!=='empty') reject('MATERIAL_CHAIN_PARTIAL_REPORT_NOT_EMPTY');
      // Observe actual rendered notices before the action. A short-lived toast
      // may disappear during network-idle binding; retaining its observed text
      // and time is evidence, while injecting or overriding a message is not.
      await page.evaluate(`(() => {
        window.__materialChainMessageObserver?.disconnect();
        clearInterval(window.__materialChainMessageTimer);
        window.__materialChainMessages=[];
        const started=Date.now(),seen=new Set();
        const collect=() => {
          for(const element of document.querySelectorAll('.ant-message-notice-content,.ant-notification-notice-message,.ant-alert-message,[role=alert]')) {
            const style=getComputedStyle(element),box=element.getBoundingClientRect(),text=(element.innerText??'').trim();
            if(!text || text.length>2000 || seen.has(text) || box.width<=0 || box.height<=0 || style.display==='none' || style.visibility==='hidden')continue;
            let visible=true;
            for(let parent=element;parent;parent=parent.parentElement) {
              const value=getComputedStyle(parent);
              if(value.display==='none'||value.visibility==='hidden'||Number(value.opacity)===0){visible=false;break}
            }
            if(!visible)continue;
            seen.add(text); window.__materialChainMessages.push({observed_at:new Date().toISOString(),elapsed_ms:Date.now()-started,
              text,class:element.className,rect:{x:box.x,y:box.y,width:box.width,height:box.height}});
          }
        };
        window.__materialChainMessageObserver=new MutationObserver(collect);
        window.__materialChainMessageObserver.observe(document.body,{subtree:true,childList:true,characterData:true,attributes:true,attributeFilter:['class','style']});
        window.__materialChainMessageTimer=setInterval(collect,50);
        collect();
      })()`);
      await clickButtonText(page,'生成首个版本','MATERIAL_CHAIN_PARTIAL_GENERATE_BUTTON_MISSING');
      const response=await bindLastAnalysisRequest(page,'POST',`/reports/${report.report_id}/generations`,[409],'MATERIAL_CHAIN_PARTIAL_GENERATION_NOT_BLOCKED');
      if(JSON.parse(response.body)?.detail!=='REPORT_SOURCE_INDEX_OUTDATED') reject('MATERIAL_CHAIN_PARTIAL_GENERATION_WRONG_REASON');
      await page.waitForExpression(`(window.__materialChainMessages??[]).some(item=>item.text.includes('资料索引需更新，请重新处理相关材料后再生成'))`,'MATERIAL_CHAIN_PARTIAL_GENERATION_WARNING_MISSING');
      const warning=await page.evaluate(`(window.__materialChainMessages??[]).find(item=>item.text.includes('资料索引需更新，请重新处理相关材料后再生成'))`);
      await page.evaluate(`(() => {window.__materialChainMessageObserver?.disconnect();clearInterval(window.__materialChainMessageTimer)})()`);
      const collection=await sessionHttp(page,`/api/v1/analysis-reports/clients/${crmAccountId}/reports`);
      const after=collection.payload?.reports?.find(item=>item.report_id===report.report_id);
      if(collection.status!==200 || !after || after.current_version_id!==null || after.current_status!=='empty' || after.version_number!==0) reject('MATERIAL_CHAIN_PARTIAL_GENERATION_CREATED_VERSION');
      const frozen=await sessionHttp(page,`/api/v1/analysis-reports/versions/${frozenReport.version_id}`);
      if(frozen.status!==200 || canonical(frozen.payload?.sections)!==canonical(frozenReport.sections)
        || canonical(frozen.payload?.citations)!==canonical(frozenReport.citations)) reject('MATERIAL_CHAIN_PARTIAL_CHANGED_FROZEN_REPORT');
      return {report_id:report.report_id,generation_http_status:409,reason:'REPORT_SOURCE_INDEX_OUTDATED',current_version_id:null,warning,
        frozen_report_version_id:frozenReport.version_id,frozen_content_sha256:frozenReport.body_sha256};
    });
    const uploadRoute='/api/v1/ingestion/documents';
    const readPendingUploads=() => page.evaluate(`Object.entries(sessionStorage)
      .filter(([key])=>/^anhuan\\.pending-write\\.v1\\.[0-9a-f]{64}$/.test(key))
      .map(([key,request_id])=>({payload_digest:key.slice('anhuan.pending-write.v1.'.length),request_id}))`);
    // Upload replaces its input immediately after change. Observe the selected
    // File objects during the real event, before that DOM reset, without retaining bytes.
    const observeFileSelection=() => page.evaluate(`(() => {
      if(window.__materialChainSelectionObserver)return;
      window.__materialChainSelectionObserver=true;
      document.addEventListener('change',event=>{
        if(!(event.target instanceof HTMLInputElement)||event.target.type!=='file')return;
        const files=Array.from(event.target.files??[]);
        window.__materialChainSelectionObserved=Promise.all(files.map(async file=>{
          const digest=await crypto.subtle.digest('SHA-256',await file.arrayBuffer());
          return {filename:file.name,size:file.size,mime:file.type,source_sha256:Array.from(new Uint8Array(digest),byte=>byte.toString(16).padStart(2,'0')).join('')};
        }));
      },{capture:true,passive:true});
    })()`);
    const selectedFileDigest=async fixture => {
      const selected=await page.evaluate(`(async()=>{
        const matches=(await window.__materialChainSelectionObserved??[]).filter(file=>file.filename===${JSON.stringify(fixture.filename)});
        return matches.length===1?matches[0]:null;
      })()`);
      if(!selected || selected.source_sha256!==fixture.source_sha256 || selected.mime!==fixture.mime) reject('MATERIAL_CHAIN_RESUME_SELECTED_FILE_MISMATCH');
      return selected;
    };
    const listClientDocuments=async () => {
      const items=[],seen=new Set();let cursor=null;
      for(let count=0;count<20;count++) {
        const query=new URLSearchParams({scope_kind:'client',client_account_id:crmAccountId,limit:'100',...(cursor?{cursor}:{})});
        const response=await sessionHttp(page,`${uploadRoute}?${query}`);
        if(response.status!==200 || !Array.isArray(response.payload?.items)) reject('MATERIAL_CHAIN_RESUME_LIST_INVALID');
        items.push(...response.payload.items);
        cursor=response.payload.next_cursor;
        if(!cursor)return items;
        if(seen.has(cursor))reject('MATERIAL_CHAIN_RESUME_LIST_CURSOR_LOOP');
        seen.add(cursor);
      }
      reject('MATERIAL_CHAIN_RESUME_LIST_INCOMPLETE');
    };
    let lostReceipt,firstSelection,pendingIdentity,mixedRows,initialUploadCount,resumeDocument;
    await check('resume_upload_receipt_loss_ui',async () => {
      const fixture=fixtures.extras.resume_docx,invalid=fixtures.extras.invalid_file;
      const existing=await listClientDocuments();
      if(existing.some(item=>[fixture.filename,invalid.filename].includes(item.display_name))) reject('MATERIAL_CHAIN_RESUME_FIXTURE_ALREADY_PRESENT');
      await navigateLoggedInPath(page,`/console/clients/${crmAccountId}/materials`,'MATERIAL_CHAIN_RESUME_NAV_FAILED');
      await clickButtonText(page,'批量上传','MATERIAL_CHAIN_RESUME_BATCH_BUTTON_MISSING');
      await page.waitForExpression(`!!document.querySelector(${JSON.stringify(`${ACTIVE_MODAL} input[type="file"][multiple]`)})`,'MATERIAL_CHAIN_RESUME_INPUT_MISSING');
      await observeFileSelection();
      // Invalid first ensures its validation result cannot stop the following valid row.
      await page.setFileInputFiles(`${ACTIVE_MODAL} input[type="file"][multiple]`,[invalid.path,fixture.path],'MATERIAL_CHAIN_RESUME_FILE_SELECTION_FAILED');
      await page.waitForExpression(`document.querySelectorAll(${JSON.stringify(`${ACTIVE_MODAL} section`)}).length===2`,'MATERIAL_CHAIN_RESUME_ROWS_MISSING');
      firstSelection=await selectedFileDigest(fixture);
      const before=page.ingestionUploadRequests.length;
      let interceptionError=null,interceptionTask=Promise.resolve();
      const unsubscribe=page.cdp.on(page.sessionId,'Fetch.requestPaused',event=>{
        interceptionTask=(async()=>{
          let settled=false;
          try {
            const route=new URL(event.request?.url??'').pathname;
            if(lostReceipt || event.request?.method!=='POST' || route!==uploadRoute || event.responseStatusCode!==202) {
              await page.cdp.call('Fetch.continueRequest',{requestId:event.requestId},page.sessionId);settled=true;return;
            }
            // Read the real server response, identify only our synthetic upload,
            // then drop that response. Never fulfill or replace a response body.
            const actual=await page.cdp.call('Fetch.getResponseBody',{requestId:event.requestId},page.sessionId);
            const receipt=JSON.parse(actual.base64Encoded?Buffer.from(actual.body,'base64').toString('utf8'):actual.body);
            if(receipt.display_name!==fixture.filename || receipt.latest_version?.original_filename!==fixture.filename
              || receipt.knowledge_scope?.kind!=='client' || receipt.knowledge_scope?.client_account_id!==crmAccountId) {
              await page.cdp.call('Fetch.continueRequest',{requestId:event.requestId},page.sessionId);settled=true;
              interceptionError='MATERIAL_CHAIN_RESUME_INTERCEPT_WRONG_RECEIPT';return;
            }
            const header=Object.entries(event.request.headers??{}).find(([name])=>name.toLowerCase()==='idempotency-key');
            lostReceipt={receipt,http_status:event.responseStatusCode,request_id:header?.[1]??null};
            await page.cdp.call('Fetch.failRequest',{requestId:event.requestId,errorReason:'ConnectionReset'},page.sessionId);settled=true;
          } catch {interceptionError='MATERIAL_CHAIN_RESUME_RESPONSE_LOSS_FAILED'}
          finally {
            if(!settled)await page.cdp.call('Fetch.continueRequest',{requestId:event.requestId},page.sessionId).catch(()=>undefined);
          }
        })();
      });
      try {
        await page.cdp.call('Fetch.enable',{patterns:[{urlPattern:`${origin}${uploadRoute}`,requestStage:'Response'}]},page.sessionId);
        await clickButtonText(page,'上传未完成文件','MATERIAL_CHAIN_RESUME_SUBMIT_MISSING',30_000,{root:ACTIVE_MODAL});
        await page.waitForExpression(`Array.from(document.querySelectorAll(${JSON.stringify(`${ACTIVE_MODAL} section`)}))
          .some(row=>(row.innerText??'').includes(${JSON.stringify(fixture.filename)})&&(row.innerText??'').includes('结果未确认'))`,'MATERIAL_CHAIN_RESUME_UNKNOWN_UI_MISSING',120_000);
        await interceptionTask;
        if(interceptionError)reject(interceptionError);
      } finally {
        await page.cdp.call('Fetch.disable',{},page.sessionId).catch(()=>undefined);
        unsubscribe();
      }
      await page.waitForApiIdle();
      const requests=page.ingestionUploadRequests.slice(before);
      initialUploadCount=requests.length;
      if(!lostReceipt || initialUploadCount!==1 || !/^[0-9a-f-]{36}$/i.test(lostReceipt.request_id??'')
        || requests[0].idempotencyKey!==lostReceipt.request_id || !lostReceipt.receipt.id || !lostReceipt.receipt.latest_version?.id) reject('MATERIAL_CHAIN_RESUME_LOST_RECEIPT_INVALID');
      const pending=await readPendingUploads();
      const matching=pending.filter(item=>item.request_id===lostReceipt.request_id);
      if(matching.length!==1)reject('MATERIAL_CHAIN_RESUME_PENDING_IDENTITY_MISSING');
      pendingIdentity=matching[0];
      mixedRows=await page.evaluate(`Array.from(document.querySelectorAll(${JSON.stringify(`${ACTIVE_MODAL} section`)})).map(row=>({
        text:row.innerText,visible:row.getBoundingClientRect().width>0&&row.getBoundingClientRect().height>0,
        name:row.querySelector('input')?.value??null,name_disabled:row.querySelector('input')?.disabled??null}))`);
      const valid=mixedRows.find(row=>row.name===fixture.filename);
      if(!valid?.visible || !valid.name_disabled || !valid.text.includes('结果未确认') || !valid.text.includes('重试会沿用本次请求')) reject('MATERIAL_CHAIN_RESUME_UNKNOWN_STATE_INVALID');
      return {document_id:lostReceipt.receipt.id,version_id:lostReceipt.receipt.latest_version.id,server_http_status:202,
        response_loss:'CDP_FETCH_RESPONSE_CONNECTION_RESET',request_id:lostReceipt.request_id,payload_digest:pendingIdentity.payload_digest,
        source_sha256:firstSelection.source_sha256,ui_unknown:true};
    });
    await check('mixed_batch_invalid_file_independent_ui',async () => {
      const invalid=fixtures.extras.invalid_file,row=mixedRows.find(item=>item.name===invalid.filename);
      if(!row?.visible || !row.text.includes('请修改') || !row.text.includes('当前环境未开放此文件格式')
        || row.text.includes('已接收') || initialUploadCount!==1)reject('MATERIAL_CHAIN_MIXED_INVALID_NOT_INDEPENDENT');
      const listed=await listClientDocuments();
      const valid=listed.filter(item=>item.display_name===fixtures.extras.resume_docx.filename);
      if(listed.some(item=>item.display_name===invalid.filename) || valid.length!==1 || valid[0].id!==lostReceipt.receipt.id
        || valid[0].version_count!==1 || valid[0].latest_version?.id!==lostReceipt.receipt.latest_version.id) reject('MATERIAL_CHAIN_MIXED_BATCH_DOCUMENTS_MISMATCH');
      return {invalid_filename:invalid.filename,invalid_ui_text:row.text,invalid_document_count:0,
        valid_document_id:valid[0].id,valid_version_id:valid[0].latest_version.id,real_upload_request_count:1,valid_server_http_status:202};
    });
    await check('resume_upload_refresh_retry_ui',async () => {
      const fixture=fixtures.extras.resume_docx;
      const beforeRefresh=await page.evaluate('performance.timeOrigin');
      await page.cdp.call('Page.reload',{ignoreCache:true},page.sessionId);
      await page.waitForExpression(`performance.timeOrigin>${JSON.stringify(beforeRefresh)}&&document.readyState==='complete'
        &&location.pathname===${JSON.stringify(`/console/clients/${crmAccountId}/materials`)}`,'MATERIAL_CHAIN_RESUME_REFRESH_FAILED');
      await page.waitForApiIdle();
      await bindSessionAccess(page,enterpriseId,'provider_admin','MATERIAL_CHAIN_RESUME_REFRESH_SESSION_MISMATCH');
      const afterRefresh=await readPendingUploads();
      if(!afterRefresh.some(item=>canonical(item)===canonical(pendingIdentity)))reject('MATERIAL_CHAIN_RESUME_REFRESH_IDENTITY_CHANGED');
      await clickButtonText(page,'批量上传','MATERIAL_CHAIN_RESUME_REOPEN_MISSING');
      await page.waitForExpression(`!!document.querySelector(${JSON.stringify(`${ACTIVE_MODAL} input[type="file"][multiple]`)})`,'MATERIAL_CHAIN_RESUME_RESELECT_INPUT_MISSING');
      await observeFileSelection();
      await page.setFileInputFiles(`${ACTIVE_MODAL} input[type="file"][multiple]`,[fixture.path],'MATERIAL_CHAIN_RESUME_RESELECT_FAILED');
      await page.waitForExpression(`document.querySelectorAll(${JSON.stringify(`${ACTIVE_MODAL} section`)}).length===1`,'MATERIAL_CHAIN_RESUME_RESELECT_ROW_MISSING');
      const secondSelection=await selectedFileDigest(fixture);
      if(canonical(firstSelection)!==canonical(secondSelection))reject('MATERIAL_CHAIN_RESUME_FILE_CHANGED');
      const requestBoundary=page.ingestionUploadRequests.length,responseBoundary=page.apiResponseEvents.length;
      await clickButtonText(page,'上传未完成文件','MATERIAL_CHAIN_RESUME_RETRY_SUBMIT_MISSING',30_000,{root:ACTIVE_MODAL});
      await page.waitForExpression(`Array.from(document.querySelectorAll(${JSON.stringify(`${ACTIVE_MODAL} section`)}))
        .some(row=>(row.innerText??'').includes(${JSON.stringify(fixture.filename)})&&(row.innerText??'').includes('已接收'))`,'MATERIAL_CHAIN_RESUME_RETRY_RECEIPT_UI_MISSING',120_000);
      await page.waitForApiIdle();
      const requests=page.ingestionUploadRequests.slice(requestBoundary),responses=page.apiResponseEvents.slice(responseBoundary)
        .filter(event=>event.method==='POST'&&event.path===uploadRoute);
      if(requests.length!==1 || requests[0].idempotencyKey!==lostReceipt.request_id || responses.length!==1 || responses[0].status!==202) reject('MATERIAL_CHAIN_RESUME_RETRY_IDEMPOTENCY_MISMATCH');
      const receipt=JSON.parse(await page.getResponseBody(responses[0].requestId,'MATERIAL_CHAIN_RESUME_RETRY_BODY_MISSING'));
      if(receipt.id!==lostReceipt.receipt.id || receipt.latest_version?.id!==lostReceipt.receipt.latest_version.id
        || receipt.version_count!==1 || receipt.versions?.length!==1 || receipt.versions[0].version_number!==1
        || receipt.latest_version.original_filename!==fixture.filename || receipt.knowledge_scope?.client_account_id!==crmAccountId) reject('MATERIAL_CHAIN_RESUME_RETRY_CREATED_DUPLICATE');
      if((await readPendingUploads()).some(item=>item.request_id===lostReceipt.request_id || item.payload_digest===pendingIdentity.payload_digest)) reject('MATERIAL_CHAIN_RESUME_CONFIRMED_IDENTITY_NOT_CLEARED');
      resumeDocument={format:'resume_docx',filename:fixture.filename,document_id:receipt.id,version_id:receipt.latest_version.id};
      documents.push(resumeDocument);
      await clickButtonText(page,'关闭','MATERIAL_CHAIN_RESUME_RETRY_CLOSE_MISSING',30_000,{root:ACTIVE_MODAL});
      return {document_id:receipt.id,version_id:receipt.latest_version.id,request_id:lostReceipt.request_id,
        payload_digest:pendingIdentity.payload_digest,source_sha256:secondSelection.source_sha256,
        refreshed:true,reselected:true,identity_preserved:true,retry_http_status:202,receipt_confirmed:true,pending_identity_cleared:true};
    });
    await check('resume_upload_single_document_version_http',async () => {
      const fixture=fixtures.extras.resume_docx,listed=await listClientDocuments();
      const matches=listed.filter(item=>item.display_name===fixture.filename);
      const detail=await sessionHttp(page,`${uploadRoute}/${resumeDocument.document_id}`);
      if(matches.length!==1 || matches[0].id!==resumeDocument.document_id || matches[0].version_count!==1
        || detail.status!==200 || detail.payload?.version_count!==1 || detail.payload?.versions?.length!==1
        || detail.payload?.latest_version?.id!==resumeDocument.version_id || detail.payload.versions[0].id!==resumeDocument.version_id
        || listed.some(item=>item.display_name===fixtures.extras.invalid_file.filename))reject('MATERIAL_CHAIN_RESUME_DUPLICATE_DOCUMENT_OR_VERSION');
      let review,last;const deadline=Date.now()+PROCESS_TIMEOUT;
      while(Date.now()<deadline) {
        const version=await sessionHttp(page,`/api/v1/ingestion/versions/${resumeDocument.version_id}`);
        const candidate=await sessionHttp(page,`/api/v1/ingestion/versions/${resumeDocument.version_id}/review`);
        last={http_status:version.status,workflow_status:version.payload?.workflow_status,reason:version.payload?.reason_code};
        if(version.status!==200 || version.payload?.document_id!==resumeDocument.document_id
          || version.payload.workflow_status==='failed' || version.payload.workflow_status==='blocked'&&!version.payload.retryable) reject('MATERIAL_CHAIN_RESUME_PROCESSING_FAILED',last);
        if(version.payload.scan_status==='clean'&&version.payload.quarantine_status==='released'&&version.payload.preview_status==='ready'
          &&candidate.status===200&&candidate.payload?.editable) {review=candidate.payload;break}
        await delay(1500);
      }
      if(!review)reject('MATERIAL_CHAIN_RESUME_PROCESSING_TIMEOUT',last);
      if(review.source_sha256!==fixture.source_sha256 || review.version_id!==resumeDocument.version_id || review.source_format!=='docx'
        || !review.base_items?.length || review.base_items.some(item=>item.locator?.kind!=='docx_block')
        || !normalize(review.base_items.map(item=>item.text).join(' ')).includes(normalize(fixture.expected_text)))reject('MATERIAL_CHAIN_RESUME_CONTENT_MISMATCH');
      return {document_id:resumeDocument.document_id,version_id:resumeDocument.version_id,document_count:1,version_count:1,
        source_sha256:review.source_sha256,base_revision_id:review.base_revision_id,invalid_document_count:0};
    });
    return {stage:'material-chain',status:'TARGETED_TEST_PASSED',checks,documents,
      verified_scope:['four_client_formats','shared_report_context','source_review','new_version_current_retrieval','frozen_original_preservation','partial_zero_evidence','response_loss_refresh_resume','mixed_batch_validation_isolation'],
      boundary:{identities:'synthetic',fixtures:'synthetic',ocr_quality:'NOT_EVALUATED',human_acceptance:'NOT_TESTED',production:'NOT_TESTED'}};
  }).catch(async error => {
    if (error instanceof VerifyError && error.evidence?.stage==='material-chain') throw error;
    const reason=error instanceof VerifyError?error.code:'MATERIAL_CHAIN_UNEXPECTED';
    if (!checks.some(item=>item.id==='provider_identity')) {
      checks.push({id:'provider_identity',outcome:'failed',reason});
      await record({event:'check',...checks.at(-1)});
    }
    for (const id of planned.filter(value=>!checks.some(item=>item.id===value))) {
      await record({event:'check',id,outcome:'not_run',reason:'PREVIOUS_CHECK_FAILED'});
    }
    throw new VerifyError(reason,{stage:'material-chain',checks,documents});
  });
}
