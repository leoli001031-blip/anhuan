// 运营台 · 指定客户材料（/console/clients/:clientId/materials）
// 只展示该客户域材料，不与共享域或其他客户聚合。
import { useParams } from "react-router-dom";
import MaterialPanel from "../../components/MaterialPanel";
import ClientShell from "./ClientShell";

export default function ClientMaterialsPage() {
  const { clientId = "" } = useParams();
  return (
    <ClientShell clientId={clientId}>
      <MaterialPanel scope="client" clientId={clientId} />
    </ClientShell>
  );
}
