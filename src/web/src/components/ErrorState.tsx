// 统一错误态：401/403/404/409/422/503/网络 → 互不相同的页面状态。
// 不展示原始错误码、堆栈或技术标识。
import { Button, Typography } from "antd";
import { ERROR_COPY, errorKind } from "../adapters/errors";
import { useAuth } from "../auth/OidcProvider";

export default function ErrorState({
  error,
  onRetry,
}: {
  error: unknown;
  onRetry?: () => void;
}) {
  const { login } = useAuth();
  const kind = errorKind(error);
  const copy = ERROR_COPY[kind];

  return (
    <div style={{ padding: "48px 0", textAlign: "center" }}>
      <Typography.Title level={5} style={{ marginBottom: 8 }}>
        {copy.title}
      </Typography.Title>
      <Typography.Paragraph type="secondary" style={{ marginBottom: 16 }}>
        {copy.description}
      </Typography.Paragraph>
      {kind === "unauthenticated" ? (
        <Button type="primary" onClick={() => void login()}>
          重新登录
        </Button>
      ) : onRetry ? (
        <Button onClick={onRetry}>重试</Button>
      ) : null}
    </div>
  );
}
