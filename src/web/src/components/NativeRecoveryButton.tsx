import { useEffect, useRef, useState } from 'react';
import { Alert, Button, Space } from 'antd';
import { useAuth } from '../auth/OidcProvider';
import { useSessionAccess } from '../adapters';
import { pendingWrite, completePendingWrite } from '../adapters/pendingWrites';
import { recoverNativeExtraction } from '../features/p3/ingestionApi';
import { useAsyncContext } from './useAsyncContext';

export default function NativeRecoveryButton({versionId,jobId,onDone}: {versionId: string;jobId: string | null;onDone:()=>void}) {
  const {getAccessToken,user}=useAuth();const {session}=useSessionAccess();
  const context=`${session?.enterprise_id ?? ''}:${user?.profile.sub ?? ''}:${versionId}:${jobId ?? ''}`;
  const current=useAsyncContext(context);const flight=useRef(false);const controller=useRef<AbortController | null>(null);
  const [busy,setBusy]=useState(false);const [error,setError]=useState('');
  useEffect(()=>{flight.current=false;setBusy(false);setError('');return()=>controller.current?.abort()},[context]);
  const recover=async()=>{
    if(!current() || flight.current || !session?.enterprise_id || !user?.profile.sub)return;
    flight.current=true;setBusy(true);setError('');
    let identity: Awaited<ReturnType<typeof pendingWrite>> | null=null;
    try {
      identity=await pendingWrite('native-recovery',session.enterprise_id,user.profile.sub,JSON.stringify({versionId,jobId}));
      if(!current())return;
      const abort=new AbortController();controller.current=abort;
      await recoverNativeExtraction(getAccessToken(),versionId,{request_id:identity.requestId,expected_job_id:jobId},abort.signal);
      completePendingWrite(identity);if(current())onDone();
    } catch(err){
      if(!current())return;
      const status=err && typeof err==='object' && 'status' in err ? Number(err.status) : 0;
      const code=err && typeof err==='object' && 'code' in err ? String(err.code) : '';
      if(status>=400 && status<500 && identity){completePendingWrite(identity)}
      setError(code==='NATIVE_RECOVERY_JOB_CHANGED' ? '任务已有变化，请刷新状态后重试。'
        : status>=400 && status<500 ? '当前状态或权限不允许恢复，请刷新状态。'
        : '尚未确认恢复结果。请重试，系统会沿用本次请求，不重复创建任务。');
    } finally {if(current()){flight.current=false;setBusy(false)}}
  };
  return <Space direction="vertical">
    <Button loading={busy} onClick={()=>void recover()}>{jobId ? '恢复提取任务' : '开始提取'}</Button>
    {error && <Alert type="error" message={error} action={<Button onClick={onDone}>刷新状态</Button>} />}
  </Space>;
}
