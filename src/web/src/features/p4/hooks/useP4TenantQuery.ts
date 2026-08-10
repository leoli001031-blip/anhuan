import { useCallback, useEffect, useRef, useState } from "react";
import { getSelectedEnterprise } from "../../../api";
import { useAuth } from "../../../auth/OidcProvider";
import { userFacingViewsReportsError } from "../viewsReportsApi";

type QueryLoader<T> = (token: string | null, signal: AbortSignal) => Promise<T>;
type MutationLoader<T> = (token: string | null, signal: AbortSignal) => Promise<T>;

export function useP4TenantQuery<T>(initialData: T, loader: QueryLoader<T>) {
  const { getAccessToken } = useAuth();
  const [data, setData] = useState<T>(initialData);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tenantEpoch, setTenantEpoch] = useState(0);
  const activeQuery = useRef<AbortController | null>(null);
  const activeMutation = useRef<AbortController | null>(null);

  const reload = useCallback(async () => {
    activeQuery.current?.abort();
    setData(initialData);
    setError(null);
    if (!getSelectedEnterprise()) {
      setLoading(false);
      setError("请先在顶部选择企业");
      return;
    }
    const controller = new AbortController();
    activeQuery.current = controller;
    setLoading(true);
    try {
      const next = await loader(getAccessToken(), controller.signal);
      if (!controller.signal.aborted) setData(next);
    } catch (reason) {
      if (!controller.signal.aborted) setError(userFacingViewsReportsError(reason));
    } finally {
      if (activeQuery.current === controller) activeQuery.current = null;
      if (!controller.signal.aborted) setLoading(false);
    }
  }, [getAccessToken, initialData, loader]);

  useEffect(() => {
    void reload();
    const handleTenantChange = () => {
      activeQuery.current?.abort();
      activeMutation.current?.abort();
      setData(initialData);
      setError(null);
      setTenantEpoch((current) => current + 1);
      void reload();
    };
    window.addEventListener("f1-enterprise-changed", handleTenantChange);
    return () => {
      window.removeEventListener("f1-enterprise-changed", handleTenantChange);
      activeQuery.current?.abort();
      activeMutation.current?.abort();
    };
  }, [initialData, reload]);

  const runMutation = useCallback(
    async <R,>(operation: MutationLoader<R>): Promise<R> => {
      activeMutation.current?.abort();
      const controller = new AbortController();
      activeMutation.current = controller;
      try {
        return await operation(getAccessToken(), controller.signal);
      } finally {
        if (activeMutation.current === controller) activeMutation.current = null;
      }
    },
    [getAccessToken],
  );

  return {
    data,
    setData,
    loading,
    error,
    setError,
    reload,
    runMutation,
    tenantEpoch,
  };
}
