// 纯内存租户状态：localStorage 只是选择候选，不是请求头权威。
export const ENTERPRISE_KEY = "f1-selected-enterprise";
export const ENTERPRISE_CHANGED_EVENT = "f1-enterprise-changed";

let tenantRequestController = new AbortController();
let tenantRequestGeneration = 0;
let validatedEnterpriseId: string | null = null;
let tenantSnapshotReady = false;

export class TenantTransportError extends Error {
  readonly status: number;
  readonly code: string;
  readonly retryable: boolean;

  constructor(code: string, status = 0, retryable = false) {
    super(code);
    this.name = "TenantTransportError";
    this.status = status;
    this.code = code;
    this.retryable = retryable;
  }
}

interface MergedAbortSignal {
  signal: AbortSignal;
  dispose: () => void;
}

function mergeAbortSignals(...signals: Array<AbortSignal | undefined>): MergedAbortSignal {
  const activeSignals = signals.filter((signal): signal is AbortSignal => Boolean(signal));
  if (activeSignals.length === 1) {
    return { signal: activeSignals[0], dispose: () => undefined };
  }
  const controller = new AbortController();
  const abort = () => controller.abort();
  for (const signal of activeSignals) {
    if (signal.aborted) {
      controller.abort();
      break;
    }
    signal.addEventListener("abort", abort, { once: true });
  }
  return {
    signal: controller.signal,
    dispose: () => {
      for (const signal of activeSignals) signal.removeEventListener("abort", abort);
    },
  };
}

export function getTenantGeneration(): number {
  return tenantRequestGeneration;
}

export function getTenantSnapshot(): {
  enterpriseId: string | null;
  generation: number;
  ready: boolean;
} {
  return {
    enterpriseId: validatedEnterpriseId,
    generation: tenantRequestGeneration,
    ready: tenantSnapshotReady,
  };
}

export function getSelectedEnterprise(): string | null {
  return localStorage.getItem(ENTERPRISE_KEY);
}

export function invalidateTenantContext(): void {
  tenantRequestGeneration += 1;
  validatedEnterpriseId = null;
  tenantSnapshotReady = false;
  const previousController = tenantRequestController;
  tenantRequestController = new AbortController();
  previousController.abort();
  window.dispatchEvent(new Event(ENTERPRISE_CHANGED_EVENT));
}

function handleNativeStorage(event: StorageEvent): void {
  if (event.key !== ENTERPRISE_KEY && event.key !== null) return;
  if (event.key === ENTERPRISE_KEY && event.oldValue === event.newValue) return;
  invalidateTenantContext();
}

if (typeof window !== "undefined") {
  window.addEventListener("storage", handleNativeStorage);
}

export function setSelectedEnterprise(id: string | null): void {
  if (tenantSnapshotReady && validatedEnterpriseId === id) return;
  if (id) {
    localStorage.setItem(ENTERPRISE_KEY, id);
  } else {
    localStorage.removeItem(ENTERPRISE_KEY);
  }
  invalidateTenantContext();
}

export function commitTenantSnapshot(enterpriseId: string, generation: number): void {
  if (generation !== tenantRequestGeneration) return;
  validatedEnterpriseId = enterpriseId;
  tenantSnapshotReady = true;
  localStorage.setItem(ENTERPRISE_KEY, enterpriseId);
}

export interface TenantBoundRequest {
  enterpriseId: string | null;
  generation: number;
  signal: AbortSignal;
  dispose: () => void;
}

export function bindTenantRequest(options?: {
  enterpriseId?: null;
  signal?: AbortSignal;
}): TenantBoundRequest {
  const generation = tenantRequestGeneration;
  const membershipDiscovery =
    options !== undefined && "enterpriseId" in options && options.enterpriseId === null;
  if (!membershipDiscovery && (!tenantSnapshotReady || validatedEnterpriseId === null)) {
    throw new TenantTransportError("TENANT_SNAPSHOT_UNREADY");
  }
  const merged = mergeAbortSignals(options?.signal, tenantRequestController.signal);
  return {
    enterpriseId: membershipDiscovery ? null : validatedEnterpriseId,
    generation,
    signal: merged.signal,
    dispose: merged.dispose,
  };
}

export function assertTenantBound(bound: TenantBoundRequest): void {
  if (bound.signal.aborted || bound.generation !== tenantRequestGeneration) {
    throw new TenantTransportError("REQUEST_ABORTED");
  }
}

export function isTenantAbortError(error: unknown): boolean {
  return (
    (error instanceof TenantTransportError &&
      (error.code === "REQUEST_ABORTED" || error.code === "TENANT_SNAPSHOT_UNREADY")) ||
    (error instanceof DOMException && error.name === "AbortError")
  );
}
