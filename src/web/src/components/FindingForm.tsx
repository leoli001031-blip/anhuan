import { useEffect } from "react";
import { Button, Form, Input, Select, Space } from "antd";
import type { FindingCreateInput } from "../p2FindingsApi";

interface FindingFormValues {
  service_case_id: string;
  title: string;
  description?: string;
  severity: string;
  responsible_user_id?: string;
  due_at?: string;
}

interface FindingFormProps {
  caseOptions: Array<{ value: string; label: string }>;
  initialValues?: Partial<FindingCreateInput>;
  lockedCaseId?: string | null;
  submitting?: boolean;
  submitLabel: string;
  onSubmit: (values: FindingCreateInput) => Promise<void> | void;
  onCancel?: () => void;
}

function toInputDateTime(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 16);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function toApiDateTime(value: string | undefined): string {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toISOString();
}

export default function FindingForm({
  caseOptions,
  initialValues,
  lockedCaseId,
  submitting = false,
  submitLabel,
  onSubmit,
  onCancel,
}: FindingFormProps) {
  const [form] = Form.useForm<FindingFormValues>();

  useEffect(() => {
    form.setFieldsValue({
      service_case_id: lockedCaseId ?? initialValues?.service_case_id,
      title: initialValues?.title ?? "",
      description: initialValues?.description ?? undefined,
      severity: initialValues?.severity ?? "medium",
      responsible_user_id: initialValues?.responsible_user_id ?? undefined,
      due_at: toInputDateTime(initialValues?.due_at),
    });
  }, [form, initialValues, lockedCaseId]);

  const finish = async (values: FindingFormValues) => {
    await onSubmit({
      service_case_id: values.service_case_id,
      title: values.title.trim(),
      description: values.description?.trim() || "",
      severity: values.severity,
      responsible_user_id: values.responsible_user_id?.trim() || null,
      due_at: toApiDateTime(values.due_at),
    });
  };

  return (
    <Form<FindingFormValues> form={form} layout="vertical" onFinish={finish}>
      <Form.Item
        name="service_case_id"
        label="所属服务任务"
        rules={[{ required: true, message: "请选择所属服务任务" }]}
      >
        <Select
          showSearch
          optionFilterProp="label"
          disabled={Boolean(lockedCaseId)}
          placeholder="选择服务任务"
          options={caseOptions}
        />
      </Form.Item>
      <Form.Item
        name="title"
        label="问题标题"
        rules={[
          { required: true, whitespace: true, message: "请输入问题标题" },
          { max: 200, message: "标题不能超过200个字符" },
        ]}
      >
        <Input placeholder="简要描述现场发现的问题" />
      </Form.Item>
      <Form.Item
        name="description"
        label="问题说明"
        rules={[
          { required: true, whitespace: true, message: "请输入问题说明" },
          { max: 8000, message: "问题说明不能超过8000个字符" },
        ]}
      >
        <Input.TextArea rows={4} placeholder="记录问题现状、位置和整改要求" />
      </Form.Item>
      <Form.Item
        name="severity"
        label="严重程度"
        rules={[{ required: true, message: "请选择严重程度" }]}
      >
        <Select
          options={[
            { value: "low", label: "低" },
            { value: "medium", label: "中" },
            { value: "high", label: "高" },
            { value: "critical", label: "紧急" },
          ]}
        />
      </Form.Item>
      <Form.Item
        name="responsible_user_id"
        label="责任人"
        extra="可填写当前企业内的用户标识；暂未明确时可留空"
      >
        <Input placeholder="责任人用户标识（可选）" />
      </Form.Item>
      <Form.Item
        name="due_at"
        label="整改截止时间"
        rules={[{ required: true, message: "请选择整改截止时间" }]}
      >
        <Input type="datetime-local" />
      </Form.Item>
      <Space wrap>
        <Button type="primary" htmlType="submit" loading={submitting}>
          {submitLabel}
        </Button>
        {onCancel && <Button onClick={onCancel}>取消</Button>}
      </Space>
    </Form>
  );
}
