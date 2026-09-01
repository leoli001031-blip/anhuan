// 客户上下文：唯一来源是路由 :clientId。不存在切换器，不读 localStorage。
import { useEffect, useState } from "react";
import { useApi } from "../../adapters";
import type { ClientAccount } from "../../adapters/types";

export function useClient(clientId: string) {
  const api = useApi();
  const [client, setClient] = useState<ClientAccount | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let active = true;
    setError(null);
    api
      .getClient(clientId)
      .then((c) => {
        if (active) setClient(c);
      })
      .catch((e) => {
        if (active) setError(e);
      });
    return () => {
      active = false;
    };
  }, [api, clientId, nonce]);

  return { client, error, reload: () => setNonce((n) => n + 1) };
}
