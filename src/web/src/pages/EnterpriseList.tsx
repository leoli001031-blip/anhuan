import { useEffect, useState } from "react";
import { Table, Typography } from "antd";
import { useAuth } from "../auth/OidcProvider";
import { api } from "../api";

interface Enterprise {
  id: string;
  name: string;
  license_no: string;
}

export default function EnterpriseList() {
  const { getAccessToken } = useAuth();
  const [rows, setRows] = useState<Enterprise[]>([]);

  useEffect(() => {
    api<Enterprise[]>("/api/v1/enterprises", { token: getAccessToken() })
      .then(setRows)
      .catch(() => setRows([]));
  }, [getAccessToken]);

  return (
    <div>
      <Typography.Title level={4}>企业列表</Typography.Title>
      <Table<Enterprise>
        rowKey="id"
        dataSource={rows}
        columns={[
          { title: "名称", dataIndex: "name" },
          { title: "许可证号", dataIndex: "license_no" },
        ]}
      />
    </div>
  );
}
