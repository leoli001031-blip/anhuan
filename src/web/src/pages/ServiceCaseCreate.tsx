import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Space, Spin, Typography, message } from "antd";
import { useNavigate } from "react-router-dom";
import { getSelectedEnterprise } from "../api";
import { useAuth } from "../auth/OidcProvider";
import ServiceCaseForm from "../components/ServiceCaseForm";
import { createServiceCase, listServiceCases } from "../p2Api";
import type { ServiceCaseInput } from "../p2Api";

export default function ServiceCaseCreate() {
  const { getAccessToken } = useAuth();
  const navigate = useNavigate();
  const [checking, setChecking] = useState(true);
  const [canCreate, setCanCreate] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const checkPermission = useCallback(async () => {
    if (!getSelectedEnterprise()) {
      setCanCreate(false);
      setError("请先在顶部选择企业");
      setChecking(false);
      return;
    }
    setChecking(true);
    setError(null);
    try {
      const collection = await listServiceCases(getAccessToken());
      setCanCreate(collection.allowed_actions.includes("create"));
    } catch (reason) {
      setCanCreate(false);
      setError(String(reason));
    } finally {
      setChecking(false);
    }
  }, [getAccessToken]);

  useEffect(() => {
    void checkPermission();
    const handleTenantChange = () => void checkPermission();
    window.addEventListener("f1-enterprise-changed", handleTenantChange);
    return () => window.removeEventListener("f1-enterprise-changed", handleTenantChange);
  }, [checkPermission]);

  const create = async (values: ServiceCaseInput) => {
    setSubmitting(true);
    setError(null);
    try {
      const created = await createServiceCase(getAccessToken(), values);
      message.success("服务任务已创建");
      navigate(`/service-cases/${created.id}`, { replace: true });
    } catch (reason) {
      setError(String(reason));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div style={{ maxWidth: 760, textAlign: "left" }}>
      <Space align="center" style={{ marginBottom: 16 }}>
        <Button onClick={() => navigate("/service-cases")}>返回任务列表</Button>
        <Typography.Title level={3} style={{ margin: 0 }}>
          创建服务任务
        </Typography.Title>
      </Space>

      {error && (
        <Alert
          type="error"
          showIcon
          message="当前操作不可用"
          description={error}
          style={{ marginBottom: 16 }}
        />
      )}

      {checking ? (
        <div style={{ padding: 48, textAlign: "center" }}>
          <Spin tip="正在确认操作权限" />
        </div>
      ) : canCreate ? (
        <Card>
          <ServiceCaseForm
            submitLabel="创建任务"
            submitting={submitting}
            onSubmit={create}
            onCancel={() => navigate("/service-cases")}
          />
        </Card>
      ) : (
        !error && (
          <Alert
            type="warning"
            showIcon
            message="你没有创建服务任务的权限"
            action={<Button onClick={() => navigate("/service-cases")}>返回列表</Button>}
          />
        )
      )}
    </div>
  );
}
