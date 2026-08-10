import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Space, Spin, Typography, message } from "antd";
import { useNavigate, useSearchParams } from "react-router-dom";
import { getSelectedEnterprise } from "../api";
import { useAuth } from "../auth/OidcProvider";
import FindingForm from "../components/FindingForm";
import { createFinding, listFindings } from "../p2FindingsApi";
import type { FindingCreateInput } from "../p2FindingsApi";
import { listServiceCases } from "../p2Api";

export default function FindingCreate() {
  const { getAccessToken } = useAuth();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const lockedCaseId = searchParams.get("caseId");
  const [checking, setChecking] = useState(true);
  const [canCreate, setCanCreate] = useState(false);
  const [caseOptions, setCaseOptions] = useState<Array<{ value: string; label: string }>>([]);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!getSelectedEnterprise()) {
      setCanCreate(false);
      setError("请先在顶部选择企业");
      setChecking(false);
      return;
    }
    setChecking(true);
    setError(null);
    try {
      const [findings, serviceCases] = await Promise.all([
        listFindings(getAccessToken(), "all", lockedCaseId),
        listServiceCases(getAccessToken()),
      ]);
      setCanCreate(findings.allowed_actions.includes("create"));
      const options = serviceCases.items.map((item) => ({
        value: item.id,
        label: item.title,
      }));
      if (lockedCaseId && !options.some((item) => item.value === lockedCaseId)) {
        options.unshift({ value: lockedCaseId, label: "当前服务任务" });
      }
      setCaseOptions(options);
    } catch (reason) {
      setCanCreate(false);
      setCaseOptions([]);
      setError(String(reason));
    } finally {
      setChecking(false);
    }
  }, [getAccessToken, lockedCaseId]);

  useEffect(() => {
    void load();
    const handleTenantChange = () => void load();
    window.addEventListener("f1-enterprise-changed", handleTenantChange);
    return () => window.removeEventListener("f1-enterprise-changed", handleTenantChange);
  }, [load]);

  const create = async (values: FindingCreateInput) => {
    setSubmitting(true);
    setError(null);
    try {
      const finding = await createFinding(getAccessToken(), values);
      message.success("问题已登记");
      navigate(`/findings/${finding.id}`, { replace: true });
    } catch (reason) {
      setError(String(reason));
    } finally {
      setSubmitting(false);
    }
  };

  const goBack = () => {
    navigate(lockedCaseId ? `/service-cases/${lockedCaseId}` : "/findings");
  };

  return (
    <div style={{ maxWidth: 760, textAlign: "left" }}>
      <Space align="center" wrap style={{ marginBottom: 16 }}>
        <Button onClick={goBack}>返回</Button>
        <Typography.Title level={3} style={{ margin: 0 }}>
          登记现场问题
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
          <Spin tip="正在加载登记表单" />
        </div>
      ) : canCreate ? (
        <Card>
          <FindingForm
            caseOptions={caseOptions}
            lockedCaseId={lockedCaseId}
            submitLabel="登记问题"
            submitting={submitting}
            onSubmit={create}
            onCancel={goBack}
          />
        </Card>
      ) : (
        !error && (
          <Alert
            type="warning"
            showIcon
            message="你没有登记问题的权限"
            action={<Button onClick={goBack}>返回</Button>}
          />
        )
      )}
    </div>
  );
}
