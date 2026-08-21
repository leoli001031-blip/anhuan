import { useCallback, useEffect, useState } from "react";
import { INTERNAL_PWA_STATE_EVENT } from "../constants";
import {
  applyWaitingInternalPwaUpdate,
  checkForInternalPwaUpdate,
  clearInternalPwaShellCaches,
  getInternalPwaSnapshot,
  promptInternalPwaInstall,
  refreshInternalPwaReachability,
  refreshInternalPwaRegistration,
  registerInternalPwaServiceWorker,
  type InternalPwaInstallChoice,
  type InternalPwaSnapshot,
} from "../serviceWorkerRegistration";

export interface InternalPwaStatus extends InternalPwaSnapshot {
  busy: boolean;
  errorCode: string | null;
  install: () => Promise<InternalPwaInstallChoice | null>;
  checkForUpdate: () => Promise<boolean>;
  applyUpdate: () => Promise<boolean>;
  clearShellCaches: () => Promise<number>;
  refresh: () => Promise<void>;
}

function safeCode(reason: unknown): string {
  if (reason instanceof DOMException && reason.name === "NotAllowedError") return "INSTALL_NOT_ALLOWED";
  return "PWA_OPERATION_FAILED";
}

export function useInternalPwaStatus(): InternalPwaStatus {
  const [snapshot, setSnapshot] = useState<InternalPwaSnapshot>(() => getInternalPwaSnapshot());
  const [busy, setBusy] = useState(false);
  const [errorCode, setErrorCode] = useState<string | null>(null);

  const sync = useCallback(() => setSnapshot(getInternalPwaSnapshot()), []);
  useEffect(() => {
    window.addEventListener(INTERNAL_PWA_STATE_EVENT, sync);
    void registerInternalPwaServiceWorker().then(sync).catch(() => setErrorCode("SW_REGISTRATION_FAILED"));
    void refreshInternalPwaRegistration().then(sync).catch(() => undefined);
    void refreshInternalPwaReachability().then(sync).catch(() => undefined);
    return () => window.removeEventListener(INTERNAL_PWA_STATE_EVENT, sync);
  }, [sync]);

  const run = useCallback(async <T,>(operation: () => Promise<T>): Promise<T> => {
    setBusy(true);
    setErrorCode(null);
    try { return await operation(); }
    catch (reason) { setErrorCode(safeCode(reason)); throw reason; }
    finally { setBusy(false); sync(); }
  }, [sync]);

  return {
    ...snapshot,
    busy,
    errorCode,
    install: () => run(promptInternalPwaInstall),
    checkForUpdate: () => run(checkForInternalPwaUpdate),
    applyUpdate: () => run(applyWaitingInternalPwaUpdate),
    clearShellCaches: () => run(clearInternalPwaShellCaches),
    refresh: async () => {
      await run(async () => {
        await Promise.all([
          refreshInternalPwaRegistration(),
          refreshInternalPwaReachability(),
        ]);
      });
    },
  };
}
