import { useEffect, useState } from "react";
import { Table, Typography, Upload, Button, message } from "antd";
import { UploadOutlined } from "@ant-design/icons";
import { useAuth } from "../auth/OidcProvider";
import { api, getSelectedEnterprise } from "../api";

interface Doc {
  id: string;
  filename: string;
  size: number;
  content_type: string;
  status: string;
}

export default function DocumentList() {
  const { getAccessToken } = useAuth();
  const [rows, setRows] = useState<Doc[]>([]);

  const refresh = () => {
    api<Doc[]>("/v1/documents", { token: getAccessToken() })
      .then(setRows)
      .catch(() => setRows([]));
  };

  useEffect(refresh, [getAccessToken]);

  const uploadProps = {
    name: "file",
    action: "/api/v1/documents/upload",
    headers: {
      Authorization: `Bearer ${getAccessToken()}`,
      "X-Enterprise-Id": getSelectedEnterprise() ?? "",
    },
    onChange(info: any) {
      if (info.file.status === "done") {
        message.success("上传成功");
        refresh();
      } else if (info.file.status === "error") {
        message.error("上传失败");
      }
    },
  };

  return (
    <div>
      <Typography.Title level={4}>文档</Typography.Title>
      <Upload {...uploadProps}>
        <Button icon={<UploadOutlined />}>上传文件</Button>
      </Upload>
      <Table<Doc>
        rowKey="id"
        dataSource={rows}
        style={{ marginTop: 16 }}
        columns={[
          { title: "文件名", dataIndex: "filename" },
          { title: "类型", dataIndex: "content_type" },
          { title: "大小", dataIndex: "size" },
          { title: "状态", dataIndex: "status" },
        ]}
      />
    </div>
  );
}
