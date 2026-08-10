import { useEffect, useState } from "react";
import { Table, Typography } from "antd";
import { useAuth } from "../auth/OidcProvider";
import { api } from "../api";

interface AuditRow {
  id: string;
  user_sub: string;
  action: string;
  resource_type: string;
  resource_id: string | null;
  result: string;
  created_at?: string;
}

export default function AuditPage() {
  const { getAccessToken } = useAuth();
  const [rows, setRows] = useState<AuditRow[]>([]);

  useEffect(() => {
    api<AuditRow[]>("/api/v1/audit", { token: getAccessToken() })
      .then(setRows)
      .catch(() => setRows([]));
  }, [getAccessToken]);

  return (
    <div>
      <Typography.Title level={4}>审计日志</Typography.Title>
      <Table<AuditRow>
        rowKey="id"
        dataSource={rows}
        columns={[
          { title: "用户", dataIndex: "user_sub" },
          { title: "动作", dataIndex: "action" },
          { title: "资源", dataIndex: "resource_type" },
          { title: "结果", dataIndex: "result" },
          { title: "时间", dataIndex: "created_at" },
        ]}
      />
    </div>
  );
}
