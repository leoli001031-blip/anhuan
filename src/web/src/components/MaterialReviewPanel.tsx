import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Checkbox, Input, Modal, Pagination, Select, Space, Spin, Typography } from 'antd';
import { useAuth } from '../auth/OidcProvider';
import { useSessionAccess } from '../adapters';
import { completePendingWrite, pendingWrite } from '../adapters/pendingWrites';
import { getMaterialReview, saveMaterialReview } from '../features/p3/ingestionApi';
import { REVIEW_FIELDS, type MaterialReview, type ReviewWrite } from '../features/p3/materialReview';
import { useAsyncContext } from './useAsyncContext';
import OriginalEvidenceView from './OriginalEvidenceView';

export default function MaterialReviewPanel({versionId,onChanged}: {versionId: string; onChanged?: () => void}) {
  const {getAccessToken, user} = useAuth();
  const {session} = useSessionAccess();
  const context = `${session?.enterprise_id ?? ''}:${user?.profile.sub ?? ''}:${versionId}`;
  const current = useAsyncContext(context);
  const [open,setOpen] = useState(false);
  const [loaded,setLoaded] = useState<{context: string; data: MaterialReview} | null>(null);
  const value = loaded?.context === context ? loaded.data : null;
  const [texts,setTexts] = useState<string[]>([]);
  const [fields,setFields] = useState<ReviewWrite['fields']>([]);
  const [checked,setChecked] = useState(false);
  const [error,setError] = useState('');
  const [busy,setBusy] = useState(false);
  const [page,setPage] = useState(1);
  const [refresh,setRefresh] = useState(0);
  const flight = useRef(false);
  const pending = useRef<ReviewWrite | null>(null);
  useEffect(() => {setOpen(false);setLoaded(null);setError('');pending.current=null;flight.current=false;setBusy(false)}, [context]);
  useEffect(() => {
    if (!open || pending.current) return;
    const controller = new AbortController();
    setLoaded(null);setChecked(false);setPage(1);setError('');
    getMaterialReview(getAccessToken(), versionId, controller.signal).then(result => {
      if (!current() || controller.signal.aborted) return;
      setLoaded({context,data:result});
      const compatible = result.review_head?.action === 'confirm' && result.review_head.base_revision_id === result.base_revision_id
        && result.review_head.base_manifest_sha256 === result.base_manifest_sha256;
      const prior = new Map(compatible ? result.review_items.filter(x=>x.entry_kind==='text').map(x=>[x.base_fragment_id,x.text]) : []);
      setTexts(result.base_items.map(x=>prior.get(x.id) ?? x.text));
      setFields(compatible ? result.review_items.filter(x=>x.entry_kind==='field').map(x=>({base_fragment_id:x.base_fragment_id,field_name:x.field_name!,text:x.text})) : []);
    }).catch(() => {if(current() && !controller.signal.aborted) setError('暂时无法读取校对内容，请重新加载。')});
    return ()=>controller.abort();
  }, [open,refresh,versionId,getAccessToken,current,context]);
  const submit = async(action: 'confirm' | 'revoke') => {
    if (!current() || !value || flight.current || !session?.enterprise_id || !user?.profile.sub) return;
    if (!pending.current && action==='confirm' && !checked) return;
    flight.current=true;setBusy(true);setError('');
    try {
      const body: ReviewWrite = pending.current ?? {
        request_id:'',expected_review_revision_id:value.review_head?.id ?? null,action,
        base_revision_id:action==='confirm' ? value.base_revision_id : null,
        base_manifest_sha256:action==='confirm' ? value.base_manifest_sha256 : null,
        checked_against_source:action==='confirm',texts:action==='confirm' ? value.base_items.map((x,n)=>({base_fragment_id:x.id,text:texts[n]})) : [],
        fields:action==='confirm' ? [...fields].sort((a,b)=>a.field_name.localeCompare(b.field_name)) : [],
      };
      const identity = await pendingWrite('material-review',session.enterprise_id,user.profile.sub,JSON.stringify({versionId,...body,request_id:undefined}));
      if (!current()) return;
      body.request_id=identity.requestId;pending.current=body;
      await saveMaterialReview(getAccessToken(),versionId,body);
      completePendingWrite(identity);
      if (!current()) return;
      pending.current=null;setRefresh(x=>x+1);onChanged?.();
    } catch (err) {
      if (!current()) return;
      const code = err && typeof err==='object' && 'code' in err ? String(err.code) : '';
      const status = err && typeof err==='object' && 'status' in err ? Number(err.status) : 0;
      if (status>=400 && status<500 && code!=='INVALID_RESPONSE') pending.current=null;
      setError(code==='REVIEW_REVISION_CONFLICT' || code==='REVIEW_BASE_CHANGED' ? '内容已有新版本，请重新加载并核对后提交。'
        : pending.current ? '尚未取得提交结果。请重试本次提交；内容已暂时锁定，避免重复或覆盖。' : '未能提交，请检查内容或重新加载。');
    } finally {if(current()){flight.current=false;setBusy(false)}}
  };
  const locked=busy || !!pending.current;
  return <>
    <Button onClick={()=>setOpen(true)}>校对提取内容</Button>
    <Modal title="校对提取内容" open={open} width={980} footer={null} maskClosable={false} onCancel={()=>{if(!busy)setOpen(false)}}>
      <Space direction="vertical" style={{width:'100%'}}>
        {error && <Alert type="error" message={error} action={pending.current
          ? <Button disabled={busy} onClick={()=>void submit(pending.current!.action)}>重试本次提交</Button>
          : <Button onClick={()=>setRefresh(x=>x+1)}>重新加载</Button>} />}
        {!value && !error && <Spin />}
        {value && <>
          <Alert type="info" message={value.review_head ? `人工修订 ${value.review_head.revision_no} · ${value.review_head.action==='confirm' ? '已确认' : '已撤销'}` : '尚无人工修订'}
            description="请对照原文件核对。原始提取会保留，每次确认都会保存新修订。" />
          {!value.editable && <Alert type="warning" message="当前没有完整、可校对的提取结果，请先完成材料处理。" />}
          {value.editable && <>
            {value.base_items.slice((page-1)*10,page*10).map((item,index)=>{
              const n=(page-1)*10+index;
              return <section key={item.id}>
                <Typography.Text strong>{item.location}</Typography.Text>
                <OriginalEvidenceView target={{documentVersionId:versionId,fragmentId:item.id,revisionId:value.base_revision_id!,
                  locator:item.locator,sourceSha256:value.source_sha256,reviewBase:true}} />
                <div style={{display:'grid',gridTemplateColumns:'1fr 1fr',gap:12}}>
                  <div><Typography.Text type="secondary">原始提取</Typography.Text><div style={{whiteSpace:'pre-wrap',maxHeight:180,overflow:'auto'}}>{item.text || '（空白）'}</div></div>
                  <Input.TextArea aria-label={`修订 ${item.location}`} value={texts[n]} disabled={locked} autoSize={{minRows:3,maxRows:8}}
                    onChange={event=>{setTexts(prior=>prior.map((x,i)=>i===n ? event.target.value : x));setChecked(false)}} />
                </div>
              </section>;
            })}
            <Pagination current={page} pageSize={10} total={value.base_items.length} showSizeChanger={false} onChange={setPage} />
            <Typography.Text strong>字段校对</Typography.Text>
            {fields.map((field,n)=><Space key={field.field_name} align="start">
              <Typography.Text>{REVIEW_FIELDS.find(x=>x[0]===field.field_name)?.[1] ?? field.field_name}</Typography.Text>
              <Select aria-label={`字段来源 ${field.field_name}`} style={{width:220}} disabled={locked} value={field.base_fragment_id}
                options={value.base_items.map(x=>({value:x.id,label:x.location}))}
                onChange={id=>{setFields(xs=>xs.map((x,i)=>i===n ? {...x,base_fragment_id:id} : x));setChecked(false)}} />
              <Input.TextArea aria-label={`字段值 ${field.field_name}`} maxLength={4096} disabled={locked} value={field.text}
                onChange={event=>{setFields(xs=>xs.map((x,i)=>i===n ? {...x,text:event.target.value} : x));setChecked(false)}} />
              <Button disabled={locked} onClick={()=>{setFields(xs=>xs.filter((_,i)=>i!==n));setChecked(false)}}>移除</Button>
            </Space>)}
            <Select<string> aria-label="添加校对字段" placeholder="添加校对字段" value={undefined} disabled={locked || fields.length>=15} style={{width:220}}
              options={REVIEW_FIELDS.filter(([name])=>!fields.some(x=>x.field_name===name)).map(([value,label])=>({value,label}))}
              onChange={name=>{setFields(xs=>[...xs,{field_name:name,base_fragment_id:value.base_items[0].id,text:''}]);setChecked(false)}} />
            <Checkbox checked={checked} disabled={locked} onChange={event=>setChecked(event.target.checked)}>我已对照原文件核对全部文字、数值、单位和字段来源</Checkbox>
            <Button type="primary" loading={busy} disabled={!checked || locked} onClick={()=>void submit('confirm')}>确认并保存修订</Button>
          </>}
          {value.review_head?.action==='confirm' && <Button danger disabled={locked} onClick={()=>void submit('revoke')}>撤销当前人工确认</Button>}
        </>}
      </Space>
    </Modal>
  </>;
}
