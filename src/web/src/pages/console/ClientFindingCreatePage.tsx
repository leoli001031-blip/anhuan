// 运营台 · 客户问题录入（/console/clients/:clientId/rectification/new）。
// 在客户工作区内选择该客户的服务事项并录入问题。
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { Alert, Button, DatePicker, Form, Input, Select, Spin, Typography, message } from "antd";
import { useAuth } from "../../auth/OidcProvider";
import ErrorState from "../../components/ErrorState";
import { listClientServiceCases, type ServiceCase } from "../../p2Api";
import { createFinding } from "../../p2FindingsApi";
import { useAsyncContext } from "../../components/useAsyncContext";
import ClientShell from "./ClientShell";

const SEVERITY_OPTIONS = [
  { value: "critical", label: "严重" },
  { value: "high", label: "高" },
  { value: "medium", label: "中" },
  { value: "low", label: "低" },
];

export default function ClientFindingCreatePage() {
  const { clientId = "" } = useParams();
  const { getAccessToken } = useAuth();
  const navigate = useNavigate();
  const isCurrent = useAsyncContext(clientId);
  const loadSeq = useRef(0);
  const loadedContext = useRef<(() => boolean) | null>(null);
  const submitPending = useRef(false);
  const [cases, setCases] = useState<ServiceCase[] | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [submitting, setSubmitting] = useState(false);
  const [form] = Form.useForm();

  useEffect(() => {
    loadedContext.current = null;
    setCases(null);
    setError(null);
    setSubmitting(false);
    submitPending.current = false;
    form.resetFields();
  }, [clientId, form]);

  const load = useCallback(async () => {
    if (!clientId || !isCurrent()) return;
    const seq = ++loadSeq.current;
    setCases(null);
    setError(null);
    try {
      const token = getAccessToken();
      if (!token) return;
      const collection = await listClientServiceCases(token, clientId);
      if (!isCurrent() || seq !== loadSeq.current) return;
      const active = collection.items.filter(
        (c) => !["cancelled", "closed"].includes(c.status),
      );
      loadedContext.current = isCurrent;
      setCases(active);
    } catch (e) {
      if (!isCurrent() || seq !== loadSeq.current) return;
      setError(e);
      setCases(null);
    }
  }, [clientId, getAccessToken, isCurrent]);

  useEffect(() => {
    void load();
  }, [load]);

  const submit = async (values: {
    service_case_id: string;
    title: string;
    severity: string;
    description: string;
    due_at: { toISOString: () => string };
  }) => {
    if (!isCurrent() || submitPending.current) return;
    if (loadedContext.current !== isCurrent || !cases?.some((item) => item.id === values.service_case_id)) {
      message.warning("请选择当前客户的有效服务事项");
      return;
    }
    submitPending.current = true;
    setSubmitting(true);
    try {
      const finding = await createFinding(getAccessToken() ?? "", {
        service_case_id: values.service_case_id,
        title: values.title,
        severity: values.severity,
        description: values.description,
        responsible_user_id: null,
        due_at: values.due_at.toISOString(),
      });
      if (!isCurrent()) return;
      message.success("问题已创建");
      navigate(`/console/clients/${clientId}/rectification/${finding.id}`);
    } catch (reason) {
      if (isCurrent()) message.error(String(reason));
    } finally {
      if (isCurrent()) {
        submitPending.current = false;
        setSubmitting(false);
      }
    }
  };

  return (
    <ClientShell clientId={clientId}>
      <div style={{ display: "flex", alignItems: "center", gap: 12, marginBottom: 16 }}>
        <Button size="small" onClick={() => navigate(`/console/clients/${clientId}/rectification`)}>
          ← 返回整改列表
        </Button>
        <Typography.Title level={4} style={{ margin: 0 }}>
          录入问题
        </Typography.Title>
      </div>

      {error ? (
        <ErrorState error={error} onRetry={() => void load()} />
      ) : cases === null ? (
        <Spin style={{ display: "block", margin: "96px auto" }} />
      ) : cases.length === 0 ? (
        <Alert
          type="warning"
          message="该客户暂无可用的服务事项"
          description="请先在「服务事项」页签创建服务事项后再录入问题。"
          showIcon
        />
      ) : (
        <Form key={clientId} disabled={submitting} form={form} layout="vertical" onFinish={submit} style={{ maxWidth: 560 }}>
          <Form.Item
            name="service_case_id"
            label="所属服务事项"
            rules={[{ required: true, message: "请选择服务事项" }]}
          >
            <Select placeholder="选择该客户的服务事项">
              {cases.map((c) => (
                <Select.Option key={c.id} value={c.id}>
                  {c.title}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item name="title" label="问题标题" rules={[{ required: true, message: "请输入标题" }]}>
            <Input placeholder="简要描述问题" />
          </Form.Item>
          <Form.Item name="severity" label="严重度" rules={[{ required: true, message: "请选择严重度" }]}>
            <Select placeholder="选择严重度">
              {SEVERITY_OPTIONS.map((opt) => (
                <Select.Option key={opt.value} value={opt.value}>
                  {opt.label}
                </Select.Option>
              ))}
            </Select>
          </Form.Item>
          <Form.Item
            name="description"
            label="详细描述"
            rules={[{ required: true, message: "请输入问题描述" }]}
          >
            <Input.TextArea rows={4} placeholder="问题的详细说明" />
          </Form.Item>
          <Form.Item
            name="due_at"
            label="整改期限"
            rules={[{ required: true, message: "请选择整改期限" }]}
          >
            <DatePicker showTime style={{ width: "100%" }} placeholder="选择整改截止时间" />
          </Form.Item>
          <Button type="primary" htmlType="submit" loading={submitting}>
            创建问题
          </Button>
        </Form>
      )}
    </ClientShell>
  );
}
