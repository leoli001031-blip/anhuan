import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Input, Modal, Space, Tag, Typography, Upload } from 'antd';
import { useAuth } from '../auth/OidcProvider';
import { useSessionAccess } from '../adapters';
import { completePendingWrite } from '../adapters/pendingWrites';
import { createIngestionDocument } from '../features/p3/ingestionApi';
import { MATERIAL_CONTENT_TYPES, pendingUpload, uploadFileError, validateDocumentReceipt } from '../features/p3/uploadWrite';
import type { IngestionCapabilities, KnowledgeScopeTarget } from '../features/p3/types';
import { useAsyncContext } from './useAsyncContext';

type Row = {id: string; file: File; name: string; state: 'pending' | 'sending' | 'received' | 'unknown' | 'invalid'; error?: string; documentId?: string};
const LABEL = {pending:'待上传',sending:'正在上传',received:'已接收',unknown:'结果未确认',invalid:'请修改'};
export default function BatchMaterialUploadModal({open,scope,capabilities,onCancel,onReceived,onInspect}: {
  open: boolean; scope: KnowledgeScopeTarget; capabilities: IngestionCapabilities | null;
  onCancel: () => void; onReceived: () => void; onInspect: (documentId: string) => void;
}) {
  const {getAccessToken,user} = useAuth();
  const {session} = useSessionAccess();
  const context = `${session?.enterprise_id ?? ''}:${user?.profile.sub ?? ''}:${scope.kind}:${scope.client_account_id ?? ''}:${open}`;
  const current = useAsyncContext(context);
  const [rows,setRows] = useState<Row[]>([]);
  const [busy,setBusy] = useState(false);
  const [error,setError] = useState('');
  const flight = useRef(false);
  const abort = useRef<AbortController | null>(null);
  useEffect(() => {
    setRows([]);setBusy(false);setError('');flight.current=false;
    return () => abort.current?.abort();
  }, [context]);
  const patch = (id: string, update: Partial<Row>) => {if(current())setRows(xs=>xs.map(x=>x.id===id ? {...x,...update} : x))};
  const send = async () => {
    if (!current() || flight.current || !session?.enterprise_id || !user?.profile.sub) return;
    flight.current=true;setBusy(true);setError('');
    const controller = new AbortController();abort.current=controller;
    try {
      // Sequential uploads bound memory and keep per-file results independent.
      for (const row of rows) {
        if (!current() || controller.signal.aborted) return;
        if (row.state === 'received') continue;
        const validation = uploadFileError(row.file, capabilities) || (!row.name.trim() || row.name.trim().length > 160 ? '名称须为 1–160 个字符' : null);
        if (validation) {patch(row.id,{state:'invalid',error:validation});continue}
        patch(row.id,{state:'sending',error:undefined});
        try {
          const identity = await pendingUpload(session.enterprise_id,user.profile.sub,row.file,{displayName:row.name.trim(),scope});
          if (!current() || controller.signal.aborted) return;
          const result = await createIngestionDocument(getAccessToken(),row.name.trim(),row.file,identity.requestId,controller.signal,'unknown',scope);
          validateDocumentReceipt(result,row.file,scope);
          completePendingWrite(identity);
          if (!current() || controller.signal.aborted) return;
          patch(row.id,{state:'received',documentId:result.id});onReceived();
        } catch (reason) {
          if (!current() || controller.signal.aborted) return;
          const code = reason && typeof reason==='object' && 'code' in reason ? String(reason.code) : '';
          patch(row.id,{state:'unknown',error:code==='P3_IDEMPOTENCY_KEY_CONFLICT'
            ? '上传请求与已有记录冲突，请检查材料列表。'
            : '未确认上传结果；重试会沿用本次请求。'});
        }
      }
    } finally {if(current()){flight.current=false;setBusy(false)}}
  };
  const accepted = capabilities?.allowed_types.filter(x=>MATERIAL_CONTENT_TYPES.includes(x.content_type)) ?? [];
  return <Modal title="批量上传材料" open={open} width={800} footer={null} maskClosable={false} keyboard={!busy} closable={!busy}
    onCancel={()=>{if(!flight.current)onCancel()}}>
    <Space direction="vertical" style={{width:'100%'}}>
      <Typography.Paragraph>每批最多 20 份。已接收的材料继续在后台检查与提取，可分别查看处理详情。刷新后重新选择相同文件和名称，可接续未确认的上传。</Typography.Paragraph>
      {error && <Alert type="error" message={error} />}
      <Upload multiple showUploadList={false} disabled={busy || rows.length>=20} accept={accepted.flatMap(x=>[x.content_type,...x.extensions]).join(',')}
        beforeUpload={file=>{
          if(flight.current)return Upload.LIST_IGNORE;
          setRows(xs=>xs.length>=20 ? xs : [...xs,{id:crypto.randomUUID(),file,name:file.name.slice(0,160),state:'pending'}]);
          return false;
        }}><Button disabled={busy || rows.length>=20}>选择文件</Button></Upload>
      {rows.map(row=><section key={row.id} style={{borderBottom:'1px solid #eee',paddingBottom:12,width:'100%'}}>
        <Space wrap><Typography.Text>{row.file.name}</Typography.Text><Tag color={row.state==='received'?'green':row.state==='unknown'?'orange':undefined}>{LABEL[row.state]}</Tag></Space>
        <Input aria-label={`材料名称 ${row.file.name}`} value={row.name} maxLength={160} disabled={busy || row.state==='received' || row.state==='unknown'}
          onChange={event=>patch(row.id,{name:event.target.value,state:'pending',error:undefined})} />
        {row.error && <Typography.Text type="danger">{row.error}</Typography.Text>}
        {row.documentId ? <Button onClick={()=>{if(!flight.current){onCancel();onInspect(row.documentId!)}}} disabled={busy}>查看处理详情</Button>
          : <Button disabled={busy} onClick={()=>setRows(xs=>xs.filter(x=>x.id!==row.id))}>移除</Button>}
      </section>)}
      <Space><Button type="primary" loading={busy} disabled={busy || !rows.some(x=>x.state!=='received') || !capabilities?.upload_enabled}
        onClick={()=>void send()}>上传未完成文件</Button><Button disabled={busy} onClick={onCancel}>关闭</Button></Space>
    </Space>
  </Modal>;
}
