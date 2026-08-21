// Adapter 装配：唯一决定 mock/HTTP 边界的地方。
// mock 只在 DEV 且 VITE_MATERIAL_RAG_REPORT_MOCK=1 时启用；其余一律 HTTP。
import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { Spin } from "antd";
import { useAuth } from "../auth/OidcProvider";
import {
  api,
  getSelectedEnterprise,
  setSelectedEnterprise,
  type Membership,
} from "../api";
import type { AnalysisReportApi } from "./AnalysisReportApi";
import type { SessionAccess } from "./SessionAccess";
import { HttpAnalysisReportApi } from "./HttpAnalysisReportApi";
import { MockAnalysisReportApi } from "./MockAnalysisReportApi";
import type { SessionAccessV1 } from "./types";

export const isMockData =
  import.meta.env.DEV && import.meta.env.VITE_MATERIAL_RAG_REPORT_MOCK === "1";

export type AppApi = AnalysisReportApi & SessionAccess;

function createApi(getToken: () => string | null): AppApi {
  if (isMockData) {
    return new MockAnalysisReportApi();
  }
  return new HttpAnalysisReportApi(getToken);
}

const ApiContext = createContext<AppApi | null>(null);

export function ApiProvider({ children }: { children: ReactNode }) {
  const { getAccessToken, isAuthenticated, isInitializing } = useAuth();
  const tokenRef = useRef(getAccessToken);
  tokenRef.current = getAccessToken;
  const apiClient = useMemo(() => createApi(() => tokenRef.current()), []);
  const [tenantReady, setTenantReady] = useState(isMockData);

  useEffect(() => {
    if (isMockData) {
      setTenantReady(true);
      return;
    }
    if (isInitializing) return;
    if (!isAuthenticated) {
      setTenantReady(true);
      return;
    }
    let active = true;
    setTenantReady(false);
    api<Membership[]>("/v1/users/me/enterprises", { token: getAccessToken() })
      .then((data) => {
        if (!active) return;
        const stored = getSelectedEnterprise();
        const next = data.some((item) => item.enterprise_id === stored)
          ? stored
          : (data[0]?.enterprise_id ?? null);
        if (next !== stored) setSelectedEnterprise(next);
      })
      .catch(() => undefined)
      .finally(() => {
        if (active) setTenantReady(true);
      });
    return () => {
      active = false;
    };
  }, [getAccessToken, isAuthenticated, isInitializing]);

  if (!isMockData && isAuthenticated && !tenantReady) {
    return <Spin fullscreen tip="正在确认企业身份" />;
  }
  return <ApiContext.Provider value={apiClient}>{children}</ApiContext.Provider>;
}

export function useApi(): AppApi {
  const api = useContext(ApiContext);
  if (!api) throw new Error("ApiProvider missing");
  return api;
}

export interface SessionState {
  session: SessionAccessV1 | null;
  loading: boolean;
  error: unknown;
  reload: () => void;
}

// 会话身份加载：每个壳加载一次，角色门只读这里。
export function useSessionAccess(): SessionState {
  const api = useApi();
  const [session, setSession] = useState<SessionAccessV1 | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    let active = true;
    setLoading(true);
    setError(null);
    api
      .getSessionAccess()
      .then((s) => {
        if (active) setSession(s);
      })
      .catch((e) => {
        if (active) setError(e);
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [api, nonce]);

  return {
    session,
    loading,
    error,
    reload: () => setNonce((n) => n + 1),
  };
}
