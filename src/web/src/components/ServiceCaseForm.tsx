import { useEffect } from "react";
import { Button, Form, Input, Space } from "antd";
import type { ServiceCaseInput } from "../p2Api";

interface FormValues {
  title: string;
  description?: string;
  service_type: string;
  planned_start_at?: string;
  planned_end_at?: string;
}

interface ServiceCaseFormProps {
  initialValues?: Partial<ServiceCaseInput>;
  submitting?: boolean;
  submitLabel: string;
  onSubmit: (values: ServiceCaseInput) => Promise<void> | void;
  onCancel?: () => void;
}

function toInputDateTime(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value.slice(0, 16);
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
}

function toApiDateTime(value: string | undefined): string | null {
  if (!value) return null;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toISOString();
}

export default function ServiceCaseForm({
  initialValues,
  submitting = false,
  submitLabel,
  onSubmit,
  onCancel,
}: ServiceCaseFormProps) {
  const [form] = Form.useForm<FormValues>();

  useEffect(() => {
    form.setFieldsValue({
      title: initialValues?.title ?? "",
      description: initialValues?.description ?? undefined,
      service_type: initialValues?.service_type ?? "",
      planned_start_at: toInputDateTime(initialValues?.planned_start_at),
      planned_end_at: toInputDateTime(initialValues?.planned_end_at),
    });
  }, [form, initialValues]);

  const finish = async (values: FormValues) => {
    await onSubmit({
      title: values.title.trim(),
      description: values.description?.trim() || null,
      service_type: values.service_type.trim(),
      planned_start_at: toApiDateTime(values.planned_start_at),
      planned_end_at: toApiDateTime(values.planned_end_at),
    });
  };

  return (
    <Form<FormValues> form={form} layout="vertical" onFinish={finish}>
      <Form.Item
        name="title"
        label="服务任务名称"
        rules={[
          { required: true, whitespace: true, message: "请输入服务任务名称" },
          { max: 200, message: "名称不能超过200个字符" },
        ]}
      >
        <Input placeholder="例如：季度现场安全检查" />
      </Form.Item>
      <Form.Item
        name="service_type"
        label="服务类型"
        rules={[{ required: true, whitespace: true, message: "请输入服务类型" }]}
      >
        <Input placeholder="例如：现场检查、咨询服务、专项培训" />
      </Form.Item>
      <Form.Item name="description" label="任务说明">
        <Input.TextArea rows={4} placeholder="填写服务范围、目标和注意事项" />
      </Form.Item>
      <Space size="large" wrap style={{ width: "100%" }}>
        <Form.Item name="planned_start_at" label="计划开始">
          <Input type="datetime-local" />
        </Form.Item>
        <Form.Item
          name="planned_end_at"
          label="计划结束"
          dependencies={["planned_start_at"]}
          rules={[
            ({ getFieldValue }) => ({
              validator(_, value?: string) {
                const start = getFieldValue("planned_start_at") as string | undefined;
                if (!start || !value || value >= start) return Promise.resolve();
                return Promise.reject(new Error("计划结束不能早于计划开始"));
              },
            }),
          ]}
        >
          <Input type="datetime-local" />
        </Form.Item>
      </Space>
      <Space>
        <Button type="primary" htmlType="submit" loading={submitting}>
          {submitLabel}
        </Button>
        {onCancel && <Button onClick={onCancel}>取消</Button>}
      </Space>
    </Form>
  );
}
