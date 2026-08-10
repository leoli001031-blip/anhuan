import {
  INTERNAL_PWA_CACHE_PREFIX,
  INTERNAL_PWA_SKIP_WAITING_MESSAGE,
  INTERNAL_PWA_STATE_EVENT,
  INTERNAL_PWA_SW_URL,
} from "./constants";

export interface InternalPwaInstallChoice {
  outcome: "accepted" | "dismissed";
  platform: string;
}

interface InternalBeforeInstallPromptEvent extends Event {
  prompt: () => Promise<void>;
  userChoice: Promise<InternalPwaInstallChoice>;
}

export interface InternalPwaSnapshot {
  online: boolean;
  standalone: boolean;
  serviceWorkerSupported: boolean;
  controlled: boolean;
  waiting: boolean;
  installable: boolean;
  installed: boolean;
}

let registration: ServiceWorkerRegistration | null = null;
let registrationPromise: Promise<ServiceWorkerRegistration | null> | null = null;
let installPrompt: InternalBeforeInstallPromptEvent | null = null;
let installedInSession = false;
let globalListenersReady = false;
let reloadOnNextController = false;
const wiredRegistrations = new WeakSet<ServiceWorkerRegistration>();

function emitState(): void {
  if (typeof window !== "undefined") window.dispatchEvent(new Event(INTERNAL_PWA_STATE_EVENT));
}

export function isInternalPwaStandalone(): boolean {
  if (typeof window === "undefined") return false;
  const mediaStandalone = window.matchMedia?.("(display-mode: standalone)").matches ?? false;
  const iosStandalone = Boolean((navigator as Navigator & { standalone?: boolean }).standalone);
  return mediaStandalone || iosStandalone;
}

function ensureGlobalListeners(): void {
  if (globalListenersReady || typeof window === "undefined") return;
  globalListenersReady = true;
  window.addEventListener("beforeinstallprompt", (event) => {
    event.preventDefault();
    installPrompt = event as InternalBeforeInstallPromptEvent;
    emitState();
  });
  window.addEventListener("appinstalled", () => {
    installPrompt = null;
    installedInSession = true;
    emitState();
  });
  window.addEventListener("online", emitState);
  window.addEventListener("offline", emitState);
  const displayMode = window.matchMedia?.("(display-mode: standalone)");
  displayMode?.addEventListener?.("change", emitState);
  if (typeof navigator !== "undefined" && "serviceWorker" in navigator) {
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      emitState();
      if (reloadOnNextController) {
        reloadOnNextController = false;
        window.location.reload();
      }
    });
  }
}

function wireRegistration(next: ServiceWorkerRegistration): void {
  registration = next;
  if (wiredRegistrations.has(next)) return;
  wiredRegistrations.add(next);
  next.addEventListener("updatefound", () => {
    const worker = next.installing;
    if (!worker) return;
    worker.addEventListener("statechange", () => {
      if (worker.state === "installed" || worker.state === "activated" || worker.state === "redundant") emitState();
    });
  });
  emitState();
}

function isOwnedRegistration(next: ServiceWorkerRegistration): boolean {
  const worker = next.waiting ?? next.installing ?? next.active;
  if (!worker) return false;
  try {
    const url = new URL(worker.scriptURL);
    return url.origin === window.location.origin && url.pathname === INTERNAL_PWA_SW_URL;
  } catch {
    return false;
  }
}

function registrationAllowed(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof navigator !== "undefined" &&
    import.meta.env.PROD &&
    window.isSecureContext &&
    "serviceWorker" in navigator
  );
}

export async function registerInternalPwaServiceWorker(): Promise<ServiceWorkerRegistration | null> {
  ensureGlobalListeners();
  if (!registrationAllowed()) return null;
  if (registration) return registration;
  if (registrationPromise) return registrationPromise;
  registrationPromise = navigator.serviceWorker.register(INTERNAL_PWA_SW_URL, {
    scope: "/",
    updateViaCache: "none",
  })
    .then((next) => {
      wireRegistration(next);
      return next;
    })
    .finally(() => { registrationPromise = null; });
  return registrationPromise;
}

export async function refreshInternalPwaRegistration(): Promise<ServiceWorkerRegistration | null> {
  ensureGlobalListeners();
  if (!registrationAllowed()) return null;
  const next = registration ?? (await navigator.serviceWorker.getRegistrations()).find(isOwnedRegistration) ?? null;
  if (next) wireRegistration(next);
  emitState();
  return next ?? null;
}

export function getInternalPwaSnapshot(): InternalPwaSnapshot {
  ensureGlobalListeners();
  const standalone = isInternalPwaStandalone();
  return {
    online: typeof navigator === "undefined" ? true : navigator.onLine,
    standalone,
    serviceWorkerSupported: typeof navigator !== "undefined" && "serviceWorker" in navigator,
    controlled: typeof navigator !== "undefined" && "serviceWorker" in navigator && Boolean(navigator.serviceWorker.controller),
    waiting: Boolean(registration?.waiting),
    installable: Boolean(installPrompt),
    installed: standalone || installedInSession,
  };
}

export async function promptInternalPwaInstall(): Promise<InternalPwaInstallChoice | null> {
  ensureGlobalListeners();
  const prompt = installPrompt;
  if (!prompt) return null;
  await prompt.prompt();
  const choice = await prompt.userChoice;
  installPrompt = null;
  installedInSession = choice.outcome === "accepted";
  emitState();
  return choice;
}

export async function checkForInternalPwaUpdate(): Promise<boolean> {
  const next = await refreshInternalPwaRegistration();
  if (!next) return false;
  await next.update();
  emitState();
  return true;
}

export async function applyWaitingInternalPwaUpdate(): Promise<boolean> {
  const next = await refreshInternalPwaRegistration();
  if (!next?.waiting) return false;
  reloadOnNextController = true;
  next.waiting.postMessage(INTERNAL_PWA_SKIP_WAITING_MESSAGE);
  return true;
}

export async function clearInternalPwaShellCaches(): Promise<number> {
  if (typeof window === "undefined" || !("caches" in window)) return 0;
  const keys = await caches.keys();
  const ownedKeys = keys.filter((key) => key.startsWith(INTERNAL_PWA_CACHE_PREFIX));
  const deleted = await Promise.all(ownedKeys.map((key) => caches.delete(key)));
  return deleted.filter(Boolean).length;
}

ensureGlobalListeners();
