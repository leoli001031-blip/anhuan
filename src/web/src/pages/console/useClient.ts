// 客户上下文：唯一来源是路由 :clientId。不存在切换器，不读 localStorage。
import { useEffect, useState } from "react";
import { useApi } from "../../adapters";
import type { ClientAccount } from "../../adapters/types";

export function useClient(clientId: string) {
  const api = useApi();
  const [state, setState] = useState<{
    contextId: string;
    client: ClientAccount | null;
    error: unknown;
  }>(() => ({ contextId: clientId, client: null, error: null }));
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let active = true;
    const requestClientId = clientId;
    setState({ contextId: requestClientId, client: null, error: null });
    api
      .getClient(requestClientId)
      .then((c) => {
        if (active) {
          setState({ contextId: requestClientId, client: c, error: null });
        }
      })
      .catch((e) => {
        if (active) {
          setState({ contextId: requestClientId, client: null, error: e });
        }
      });
    return () => {
      active = false;
    };
  }, [api, clientId, nonce]);

  const inCurrentContext = state.contextId === clientId;
  return {
    contextId: state.contextId,
    client: inCurrentContext ? state.client : null,
    error: inCurrentContext ? state.error : null,
    reload: () => setNonce((n) => n + 1),
  };
}
