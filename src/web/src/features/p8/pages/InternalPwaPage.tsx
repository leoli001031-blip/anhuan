import { useState } from "react";
import { Alert, Button, Descriptions, Divider, Modal, Space, Spin, Tag, Typography } from "antd";
import OnlineOfflineBadge from "../components/OnlineOfflineBadge";
import P8BoundaryBanner from "../components/P8BoundaryBanner";
import { useInternalPwaStatus } from "../hooks/useInternalPwaStatus";

const ERROR_COPY: Record<string, string> = {
  INSTALL_NOT_ALLOWED: "浏览器未允许本次安装，请使用浏览器菜单手动安装",
  SW_REGISTRATION_FAILED: "Service Worker 注册失败；当前仍可按普通在线网页使用",
  PWA_OPERATION_FAILED: "PWA 操作未完成，请稍后重试",
};

function stateTag(active: boolean, yes: string, no: string) {
  return <Tag color={active ? "green" : "default"}>{active ? yes : no}</Tag>;
}

export default function InternalPwaPage() {
  const pwa = useInternalPwaStatus();
  const [clearOpen, setClearOpen] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);

  const install = async () => {
    setNotice(null);
    try {
      const choice = await pwa.install();
      setNotice(choice?.outcome === "accepted" ? "浏览器已接受安装请求" : "安装未执行；可稍后从浏览器菜单重试");
    } catch { setNotice(null); }
  };

  const checkUpdate = async () => {
    setNotice(null);
    try {
      const supported = await pwa.checkForUpdate();
      setNotice(supported ? "已检查静态应用壳更新" : "当前浏览器不支持 Service Worker 更新检查");
    } catch { setNotice(null); }
  };

  const applyUpdate = async () => {
    setNotice(null);
    try {
      const applied = await pwa.applyUpdate();
      if (!applied) setNotice("当前没有 waiting update");
    } catch { setNotice(null); }
  };

  const clearCaches = async () => {
    setNotice(null);
    try {
      await pwa.clearShellCaches();
      window.location.reload();
    } catch { setClearOpen(false); }
  };

  return (
    <div style={{ textAlign: "left", maxWidth: 980 }}>
      <Space wrap align="center" style={{ width: "100%", justifyContent: "space-between", marginBottom: 16 }}>
        <div>
          <Typography.Title level={3} style={{ marginBottom: 4 }}>内部 PWA 状态</Typography.Title>
          <Typography.Text type="secondary">安装、联网、静态应用壳与更新生命周期</Typography.Text>
        </div>
        <OnlineOfflineBadge />
      </Space>

      <P8BoundaryBanner />
      {!pwa.online && <Alert type="error" showIcon message="当前离线：业务数据不可用" description="静态应用壳可以打开，但不会展示缓存的租户、任务、通知、报告或用户数据。恢复联网后再访问业务页面。" style={{ marginBottom: 16 }} />}
      {pwa.waiting && <Alert type="info" showIcon message="检测到 waiting update" description="更新不会后台强制刷新；请在保存当前表单后主动点击“应用更新”。" action={<Button type="primary" onClick={() => void applyUpdate()} loading={pwa.busy}>应用更新</Button>} style={{ marginBottom: 16 }} />}
      {pwa.errorCode && <Alert type="error" showIcon message="PWA 操作未完成" description={ERROR_COPY[pwa.errorCode] ?? ERROR_COPY.PWA_OPERATION_FAILED} style={{ marginBottom: 16 }} />}
      {notice && <Alert type="success" showIcon message={notice} closable onClose={() => setNotice(null)} style={{ marginBottom: 16 }} />}

      <section aria-labelledby="p8-status-heading">
        <Typography.Title id="p8-status-heading" level={4}>当前能力</Typography.Title>
        <Descriptions bordered size="small" column={{ xs: 1, sm: 1, md: 2 }}>
          <Descriptions.Item label="网络">{stateTag(pwa.online, "在线", "离线")}</Descriptions.Item>
          <Descriptions.Item label="安装状态">{stateTag(pwa.installed, "已安装或刚接受安装", "未确认安装")}</Descriptions.Item>
          <Descriptions.Item label="Standalone">{stateTag(pwa.standalone, "独立窗口", "浏览器标签页")}</Descriptions.Item>
          <Descriptions.Item label="Service Worker 支持">{stateTag(pwa.serviceWorkerSupported, "支持", "不支持")}</Descriptions.Item>
          <Descriptions.Item label="Worker 控制">{stateTag(pwa.controlled, "已控制当前页面", "尚未控制")}</Descriptions.Item>
          <Descriptions.Item label="Waiting update">{stateTag(pwa.waiting, "等待用户应用", "无等待更新")}</Descriptions.Item>
          <Descriptions.Item label="浏览器安装提示">{stateTag(pwa.installable, "可由用户安装", "当前无安装提示")}</Descriptions.Item>
          <Descriptions.Item label="业务数据策略"><Tag color="blue">ONLINE DATA ONLY</Tag></Descriptions.Item>
        </Descriptions>
      </section>

      <Divider />
      <section aria-labelledby="p8-actions-heading">
        <Typography.Title id="p8-actions-heading" level={4}>用户操作</Typography.Title>
        <Space wrap>
          {pwa.installable && !pwa.installed && <Button type="primary" loading={pwa.busy} onClick={() => void install()}>安装内部应用</Button>}
          <Button disabled={!pwa.serviceWorkerSupported || pwa.busy} onClick={() => void checkUpdate()}>检查静态壳更新</Button>
          {pwa.waiting && <Button type="primary" loading={pwa.busy} onClick={() => void applyUpdate()}>应用更新</Button>}
          <Button danger disabled={!pwa.serviceWorkerSupported || pwa.busy} onClick={() => setClearOpen(true)}>清除本应用静态缓存</Button>
          <Button disabled={pwa.busy} onClick={() => void pwa.refresh()}>刷新状态</Button>
          {pwa.busy && <Spin size="small" />}
        </Space>
        <Typography.Paragraph type="secondary" style={{ marginTop: 12 }}>
          清理只删除名称以本应用固定前缀开头的 Cache Storage；不会清除 Cookie、OIDC、localStorage 或其他站点数据。
        </Typography.Paragraph>
      </section>

      <Divider />
      <section aria-labelledby="p8-manual-heading">
        <Typography.Title id="p8-manual-heading" level={4}>没有安装按钮时</Typography.Title>
        <Typography.Paragraph>
          支持安装的桌面浏览器可从地址栏或浏览器菜单选择“安装应用”；移动端浏览器可使用“添加到主屏幕”。若浏览器不支持，继续以在线网页使用即可。
        </Typography.Paragraph>
        <Typography.Text type="secondary">该入口不是正式小程序，也不会生成商店包、推送通知或后台同步任务。</Typography.Text>
      </section>

      <Modal open={clearOpen} title="清除本应用静态缓存" okText="清除并重新加载" cancelText="取消" okButtonProps={{ danger: true }} confirmLoading={pwa.busy} onCancel={() => setClearOpen(false)} onOk={() => void clearCaches()}>
        <Typography.Paragraph>只删除本应用固定 cache 前缀的静态应用壳，并重新加载当前页面。</Typography.Paragraph>
        <Typography.Text type="secondary">不会删除业务数据、登录状态、Cookie、localStorage 或其他网站缓存。</Typography.Text>
      </Modal>
    </div>
  );
}
