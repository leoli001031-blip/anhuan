export const INTERNAL_PWA_SW_URL = "/pwa-sw.js";
export const INTERNAL_PWA_CACHE_PREFIX = "anhuan-internal-pwa-";
export const INTERNAL_PWA_STATE_EVENT = "anhuan-internal-pwa-state";
export const INTERNAL_PWA_SKIP_WAITING_MESSAGE = { type: "SKIP_WAITING" } as const;

export const INTERNAL_PWA_BOUNDARIES = [
  "INTERNAL_PWA_ONLY",
  "NO_FORMAL_MINI_PROGRAM",
  "NO_PRODUCTION_PUBLISH",
  "ONLINE_DATA_ONLY",
  "NOT_PRODUCTION",
] as const;
