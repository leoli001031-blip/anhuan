// Unified API client: relative /api, tenant header, bearer token.
// The vite dev proxy forwards /api -> 8001 and /realms -> 8080, so the
// frontend never hardcodes a host/port.

export const API = "/api";

export interface Membership {
  enterprise_id: string;
  name: string;
  role: string;
}

const ENTERPRISE_KEY = "f1-selected-enterprise";

export function getSelectedEnterprise(): string | null {
  return localStorage.getItem(ENTERPRISE_KEY);
}

export function setSelectedEnterprise(id: string | null): void {
  if (id) {
    localStorage.setItem(ENTERPRISE_KEY, id);
  } else {
    localStorage.removeItem(ENTERPRISE_KEY);
  }
}

export async function api<T = any>(
  path: string,
  options: { method?: string; body?: unknown; token?: string | null; enterpriseId?: string | null } = {
    method: "GET",
    token: "",
  },
): Promise<T> {
  const headers: Record<string, string> = {};
  if (options.token) {
    headers.Authorization = `Bearer ${options.token}`;
  }
  const enterpriseId = options.enterpriseId !== undefined ? options.enterpriseId : getSelectedEnterprise();
  if (enterpriseId) {
    headers["X-Enterprise-Id"] = enterpriseId;
  }
  if (options.body !== undefined) {
    headers["Content-Type"] = "application/json";
  }
  const resp = await fetch(`${API}${path}`, {
    method: options.method ?? "GET",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });
  if (!resp.ok) {
    const detail = await resp.text();
    throw new Error(`HTTP ${resp.status}: ${detail.slice(0, 160)}`);
  }
  if (resp.status === 204) {
    return undefined as T;
  }
  return (await resp.json()) as T;
}
