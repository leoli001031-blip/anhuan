import { useCallback, useEffect, useState } from "react";
import { BellOutlined } from "@ant-design/icons";
import { Badge, Button, Tooltip } from "antd";
import { useNavigate } from "react-router-dom";
import { getSelectedEnterprise } from "../api";
import { useAuth } from "../auth/OidcProvider";
import {
  getUnreadNotificationCount,
  NOTIFICATIONS_CHANGED_EVENT,
} from "../p2WorkbenchApi";

export default function NotificationBell() {
  const { getAccessToken } = useAuth();
  const navigate = useNavigate();
  const [count, setCount] = useState(0);
  const [error, setError] = useState(false);

  const refresh = useCallback(async () => {
    if (!getSelectedEnterprise()) {
      setCount(0);
      setError(false);
      return;
    }
    try {
      setCount(await getUnreadNotificationCount(getAccessToken()));
      setError(false);
    } catch {
      setError(true);
    }
  }, [getAccessToken]);

  useEffect(() => {
    void refresh();
    const handleRefresh = () => void refresh();
    window.addEventListener("f1-enterprise-changed", handleRefresh);
    window.addEventListener(NOTIFICATIONS_CHANGED_EVENT, handleRefresh);
    const timer = window.setInterval(handleRefresh, 60_000);
    return () => {
      window.removeEventListener("f1-enterprise-changed", handleRefresh);
      window.removeEventListener(NOTIFICATIONS_CHANGED_EVENT, handleRefresh);
      window.clearInterval(timer);
    };
  }, [refresh]);

  return (
    <Tooltip title={error ? "未读提醒暂时无法刷新" : "站内提醒"}>
      <Badge count={count} overflowCount={99} size="small">
        <Button
          aria-label={`站内提醒，${count}条未读`}
          icon={<BellOutlined />}
          onClick={() => navigate("/notifications")}
        />
      </Badge>
    </Tooltip>
  );
}
