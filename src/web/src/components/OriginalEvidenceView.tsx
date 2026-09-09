import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Space, Spin, Typography } from 'antd';
import { tenantFetch } from '../api';
import { useAuth } from '../auth/OidcProvider';
import { isMockData } from '../adapters';
import { useAsyncContext } from './useAsyncContext';

export type OriginalTarget = {documentVersionId: string; locator: Record<string, unknown>; citationId?: string; fragmentId?: string; revisionId?: string; sourceSha256?: string; reviewBase?: boolean};
type Original = {document_version_id: string; source_sha256: string; location: string; locator: Record<string, unknown>; original_text: string | null; image: string | null};
const canonical = (value: unknown): string => value && typeof value === 'object' && !Array.isArray(value)
  ? JSON.stringify(Object.fromEntries(Object.entries(value).filter(([,v])=>v!==undefined).sort(([a],[b])=>a.localeCompare(b)).map(([k,v])=>[k,JSON.parse(canonical(v))]))) : JSON.stringify(value);

export default function OriginalEvidenceView({target}: {target: OriginalTarget}) {
  const {getAccessToken} = useAuth();
  const identity = canonical(target);
  const current = useAsyncContext(identity);
  const downloadController = useRef<AbortController | null>(null);
  const [opened,setOpened] = useState(false);
  const [value,setValue] = useState<{identity: string; data: Original} | null>(null);
  const [error,setError] = useState(false);
  const [retry,setRetry] = useState(0);
  const [downloading,setDownloading] = useState(false);
  const [downloadError,setDownloadError] = useState(false);
  const path = target.citationId ? `/v1/evidence/citations/${encodeURIComponent(target.citationId)}/original`
    : target.fragmentId && target.revisionId ? `/v1/evidence/versions/${encodeURIComponent(target.documentVersionId)}/${target.reviewBase?'review-fragments':'fragments'}/${encodeURIComponent(target.fragmentId)}/original?revision_id=${encodeURIComponent(target.revisionId)}` : null;
  useEffect(()=>{setOpened(false);setValue(null);setError(false);setDownloadError(false);setDownloading(false)},[identity]);
  useEffect(()=>()=>downloadController.current?.abort(),[identity]);
  useEffect(()=>{
    if (!opened || !path) return;
    const controller=new AbortController();setValue(null);setError(false);
    tenantFetch(path,{token:getAccessToken(),signal:controller.signal}).then(result=>{
      if(controller.signal.aborted)return;
      const data=result.payload as Original;
      if (!data || data.document_version_id!==target.documentVersionId || canonical(data.locator)!==canonical(target.locator)
        || !/^[0-9a-f]{64}$/.test(data.source_sha256) || target.sourceSha256 && data.source_sha256!==target.sourceSha256
        || typeof data.location!=='string' || (data.original_text!==null && typeof data.original_text!=='string')
        || (data.image!==null && (typeof data.image!=='string' || !/^data:image\/jpeg;base64,[A-Za-z0-9+/]+=*$/.test(data.image)))
        || (data.image===null)===(data.original_text===null)) throw new Error('CITATION_ORIGINAL_INVALID');
      setValue({identity,data});
    }).catch(()=>{if(!controller.signal.aborted)setError(true)});
    return()=>controller.abort();
  // identity freezes every target field; a changed citation aborts its read.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  },[opened,path,identity,retry,getAccessToken]);
  if(isMockData || !path)return null;
  const download=async()=>{
    if(downloading)return;
    setDownloading(true);setDownloadError(false);
    const controller = new AbortController();downloadController.current=controller;
    try {
      const result=await tenantFetch(path+(path.includes('?')?'&':'?')+'download=true',{token:getAccessToken(),parse:'response',signal:controller.signal});
      if(!result.response?.ok)throw new Error('CITATION_ORIGINAL_UNAVAILABLE');
      const blob=await result.response.blob();if(!current() || controller.signal.aborted)return;
      const url=URL.createObjectURL(blob);const anchor=document.createElement('a');
      anchor.href=url;anchor.download=`原件.${target.locator.kind==='pdf_page'?'pdf':target.locator.kind==='docx_block'?'docx':target.locator.kind==='xlsx_cells'?'xlsx':'jpg'}`;
      anchor.click();window.setTimeout(()=>URL.revokeObjectURL(url),1000);
    } catch {if(current() && !controller.signal.aborted)setDownloadError(true)} finally {if(current())setDownloading(false)}
  };
  const data=value?.identity===identity ? value.data : null;
  return <Space direction="vertical" style={{width:'100%'}}>
    <Space wrap><Button onClick={()=>setOpened(true)}>查看原件位置</Button><Button loading={downloading} onClick={()=>void download()}>下载原件</Button></Space>
    {downloadError && <Alert type="error" message="原件下载失败，请重试。" />}
    {opened && (error ? <Alert type="warning" message="暂时无法打开原件位置" description="来源权限、版本状态或原件可能已变化。此处保留的引用摘录仍是生成时保存的内容。" action={<Button onClick={()=>setRetry(x=>x+1)}>重试</Button>} />
      : !data ? <Spin /> : <section>
        <Typography.Paragraph strong>原件 · {data.location}</Typography.Paragraph>
        {data.image ? <img src={data.image} alt={`原件 ${data.location}`} style={{width:'100%',height:'auto'}} />
          : <Typography.Paragraph style={{whiteSpace:'pre-wrap',overflowWrap:'anywhere'}}>{data.original_text || '（空白）'}</Typography.Paragraph>}
      </section>)}
  </Space>;
}
