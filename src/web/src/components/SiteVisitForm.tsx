import { useEffect } from "react";
import { Button, Form, Input, Space } from "antd";
import type { SiteVisitInput } from "../p2Api";

interface SiteVisitFormValues {
  planned_start_at?: string;
  planned_end_at?: string;
}

interface SiteVisitFormProps {
  initialValues?: Partial<SiteVisitInput>;
  submitting?: boolean;
  submitLabel: string;
  onSubmit: (values: SiteVisitInput) => Promise<void> | void;
  onCancel: () => void;
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

export default function SiteVisitForm({
  initialValues,
  submitting = false,
  submitLabel,
  onSubmit,
  onCancel,
}: SiteVisitFormProps) {
  const [form] = Form.useForm<SiteVisitFormValues>();

  useEffect(() => {
    form.setFieldsValue({
      planned_start_at: toInputDateTime(initialValues?.planned_start_at),
      planned_end_at: toInputDateTime(initialValues?.planned_end_at),
    });
  }, [form, initialValues]);

  const finish = async (values: SiteVisitFormValues) => {
    await onSubmit({
      planned_start_at: toApiDateTime(values.planned_start_at),
      planned_end_at: toApiDateTime(values.planned_end_at),
    });
  };

  return (
    <Form<SiteVisitFormValues> form={form} layout="vertical" onFinish={finish}>
      <Form.Item
        name="planned_start_at"
        label="计划开始"
        rules={[{ required: true, message: "请选择计划开始时间" }]}
      >
        <Input type="datetime-local" />
      </Form.Item>
      <Form.Item
        name="planned_end_at"
        label="计划结束"
        dependencies={["planned_start_at"]}
        rules={[
          { required: true, message: "请选择计划结束时间" },
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
      <Space wrap>
        <Button type="primary" htmlType="submit" loading={submitting}>
          {submitLabel}
        </Button>
        <Button onClick={onCancel}>取消</Button>
      </Space>
    </Form>
  );
}
