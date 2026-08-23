// Adapter 装配：唯一决定 mock/HTTP 边界的地方。
// mock 只在 DEV 且 VITE_MATERIAL_RAG_REPORT_MOCK=1 时启用；其余一律 HTTP。
import { createContext, useContext, useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { useAuth } from "../auth/OidcProvider";
import {
  ENTERPRISE_CHANGED_EVENT,
  api,
  getSelectedEnterprise,
  getTenantGeneration,
  getTenantSnapshot,
  commitTenantSnapshot,
  isTenantAbortError,
  type Membership,
} from "../api";
import type { AnalysisReportApi } from "./AnalysisReportApi";
import type { SessionAccess } from "./SessionAccess";
import { HttpAnalysisReportApi } from "./HttpAnalysisReportApi";
import { MockAnalysisReportApi } from "./MockAnalysisReportApi";
import { ApiError } from "./errors";
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
  const [membershipEpoch, setMembershipEpoch] = useState(0);
  const [membershipError, setMembershipError] = useState<unknown>(null);

  useEffect(() => {
    const onInvalidate = () => {
      setTenantReady(false);
      setMembershipError(null);
      setMembershipEpoch((value) => value + 1);
    };
    window.addEventListener(ENTERPRISE_CHANGED_EVENT, onInvalidate);
    return () => window.removeEventListener(ENTERPRISE_CHANGED_EVENT, onInvalidate);
  }, []);

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
    const born = getTenantGeneration();
    setTenantReady(false);
    setMembershipError(null);
    api<Membership[]>("/v1/users/me/enterprises", {
      token: getAccessToken(),
      enterpriseId: null,
    })
      .then((data) => {
        if (!active || born !== getTenantGeneration()) return;
        const stored = getSelectedEnterprise();
        const next = data.some((item) => item.enterprise_id === stored)
          ? stored
          : (data[0]?.enterprise_id ?? null);
        // 空 membership 是可恢复错误状态，不是无限加载。
        if (!next) {
          setMembershipError(new ApiError(403, "NO_MEMBERSHIP", false));
          return;
        }
        commitTenantSnapshot(next, born);
        setTenantReady(true);
      })
      .catch((caught) => {
        if (!active || born !== getTenantGeneration()) return;
        // membership 失败可恢复：交会员话层呈现错误与重试。
        setMembershipError(caught);
      });
    return () => {
      active = false;
    };
  }, [getAccessToken, isAuthenticated, isInitializing, membershipEpoch]);

  const membershipReady =
    isMockData || (tenantReady && getTenantSnapshot().ready);
  return (
    <ApiContext.Provider value={apiClient}>
      <SessionAccessProvider
        tenantReady={membershipReady}
        membershipError={membershipError}
        onMembershipRetry={() => setMembershipEpoch((value) => value + 1)}
      >
        {children}
      </SessionAccessProvider>
    </ApiContext.Provider>
  );
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

const SessionContext = createContext<SessionState | null>(null);

function SessionAccessProvider({
  children,
  tenantReady,
  membershipError,
  onMembershipRetry,
}: {
  children: ReactNode;
  tenantReady: boolean;
  membershipError: unknown;
  onMembershipRetry: () => void;
}) {
  const apiClient = useApi();
  const { isAuthenticated, isInitializing } = useAuth();
  const [session, setSession] = useState<SessionAccessV1 | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    const onEnterpriseChanged = () => {
      setSession(null);
      setError(null);
      setLoading(true);
      setNonce((n) => n + 1);
    };
    window.addEventListener(ENTERPRISE_CHANGED_EVENT, onEnterpriseChanged);
    return () => window.removeEventListener(ENTERPRISE_CHANGED_EVENT, onEnterpriseChanged);
  }, []);

  useEffect(() => {
    if (isInitializing) return;
    if (!isAuthenticated) {
      setSession(null);
      setError(null);
      setLoading(false);
      return;
    }
    // membership 失败/为空：呈现可恢复错误，重试走 membership 重载。
    if (membershipError) {
      setSession(null);
      setError(membershipError);
      setLoading(false);
      return;
    }
    if (!tenantReady) {
      setSession(null);
      setError(null);
      setLoading(true);
      return;
    }
    let active = true;
    let settled = false;
    const born = getTenantGeneration();
    setLoading(true);
    setError(null);
    apiClient
      .getSessionAccess()
      .then((next) => {
        if (!active || born !== getTenantGeneration()) return;
        setSession(next);
        settled = true;
      })
      .catch((caught) => {
        if (!active || born !== getTenantGeneration()) return;
        if (isTenantAbortError(caught) || (caught instanceof ApiError && caught.code === "REQUEST_ABORTED")) {
          return;
        }
        setError(caught);
        settled = true;
      })
      .finally(() => {
        if (!active || born !== getTenantGeneration()) return;
        if (settled) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [apiClient, nonce, isAuthenticated, isInitializing, tenantReady, membershipError]);

  const value = useMemo<SessionState>(
    () => ({
      session,
      loading,
      error,
      reload: membershipError
        ? onMembershipRetry
        : () => setNonce((n) => n + 1),
    }),
    [session, loading, error, membershipError, onMembershipRetry],
  );

  return <SessionContext.Provider value={value}>{children}</SessionContext.Provider>;
}

// 共享会话：企业变化立即丢弃旧 session，不把 localStorage 当权威。
export function useSessionAccess(): SessionState {
  const state = useContext(SessionContext);
  if (!state) throw new Error("SessionAccessProvider missing");
  return state;
}
