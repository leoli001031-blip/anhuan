const CACHE_PREFIX = "anhuan-internal-pwa-";
const SHELL_CACHE = CACHE_PREFIX + "shell-v1";
const STATIC_CACHE = CACHE_PREFIX + "static-v1";
const CURRENT_CACHES = new Set([SHELL_CACHE, STATIC_CACHE]);
const SHELL_KEY = "/";
const FIXED_STATIC_ASSETS = ["/manifest.webmanifest", "/pwa-icon.svg"];
const STATIC_EXTENSION = /\.(?:js|css|svg|png|webp|ico|woff2)$/i;
const AUTH_QUERY_KEYS = ["code", "state", "session_state", "iss", "error"];

self.addEventListener("install", (event) => {
  event.waitUntil(installOfflineShell());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(
        keys
          .filter((key) => key.startsWith(CACHE_PREFIX) && !CURRENT_CACHES.has(key))
          .map((key) => caches.delete(key)),
      ),
    ).then(() => self.clients.claim()),
  );
});

self.addEventListener("message", (event) => {
  if (event.data && event.data.type === "SKIP_WAITING") self.skipWaiting();
});

function pathIsOrStartsWith(pathname, prefix) {
  return pathname === prefix || pathname.startsWith(prefix + "/");
}

function isSensitiveRequest(request, url) {
  return (
    request.method !== "GET" ||
    url.origin !== self.location.origin ||
    request.headers.has("Authorization") ||
    request.headers.has("Range") ||
    request.cache === "no-store" ||
    pathIsOrStartsWith(url.pathname, "/api") ||
    pathIsOrStartsWith(url.pathname, "/realms") ||
    pathIsOrStartsWith(url.pathname, "/callback") ||
    AUTH_QUERY_KEYS.some((key) => url.searchParams.has(key))
  );
}

function responseIsCacheable(response, expectedType) {
  const cacheControl = response.headers.get("Cache-Control") || "";
  const vary = response.headers.get("Vary") || "";
  const contentType = response.headers.get("Content-Type") || "";
  return (
    response.status === 200 &&
    response.type === "basic" &&
    !/(?:^|,)\s*(?:no-store|private)\b/i.test(cacheControl) &&
    !response.headers.has("Set-Cookie") &&
    vary.trim() !== "*" &&
    contentType.toLowerCase().includes(expectedType)
  );
}

function expectedResponseType(pathname) {
  if (pathname === "/manifest.webmanifest") return "application/manifest";
  if (/\.css$/i.test(pathname)) return "text/css";
  if (/\.js$/i.test(pathname)) return "javascript";
  if (/\.woff2$/i.test(pathname)) return "font/";
  return "image/";
}

async function installOfflineShell() {
  const rootRequest = new Request(SHELL_KEY, {
    cache: "reload",
    credentials: "omit",
  });
  const rootResponse = await fetch(rootRequest);
  if (!responseIsCacheable(rootResponse, "text/html")) {
    throw new Error("P8_SHELL_RESPONSE_REJECTED");
  }
  const html = await rootResponse.clone().text();
  const assetPaths = new Set(FIXED_STATIC_ASSETS);
  const references = html.matchAll(/\b(?:src|href)=["']([^"']+)["']/g);
  for (const reference of references) {
    const url = new URL(reference[1], self.location.origin);
    if (
      url.origin === self.location.origin &&
      url.pathname.startsWith("/assets/") &&
      !url.pathname.includes("..") &&
      url.search === "" &&
      STATIC_EXTENSION.test(url.pathname)
    ) {
      assetPaths.add(url.pathname);
    }
  }
  const shellCache = await caches.open(SHELL_CACHE);
  await shellCache.put(SHELL_KEY, rootResponse);
  const staticCache = await caches.open(STATIC_CACHE);
  for (const path of assetPaths) {
    const request = new Request(path, { cache: "reload", credentials: "omit" });
    const response = await fetch(request);
    if (!responseIsCacheable(response, expectedResponseType(path))) {
      throw new Error("P8_STATIC_RESPONSE_REJECTED");
    }
    await staticCache.put(request, response);
  }
}

async function navigationResponse(request) {
  try {
    const response = await fetch(request);
    if (responseIsCacheable(response, "text/html")) {
      const cache = await caches.open(SHELL_CACHE);
      await cache.put(SHELL_KEY, response.clone());
    }
    return response;
  } catch {
    const fallback = await caches.match(SHELL_KEY, { cacheName: SHELL_CACHE });
    return fallback || Response.error();
  }
}

function isStaticAsset(url) {
  return (
    url.pathname.startsWith("/assets/") ||
    url.pathname === "/manifest.webmanifest" ||
    url.pathname === "/pwa-icon.svg" ||
    url.pathname === "/favicon.svg" ||
    STATIC_EXTENSION.test(url.pathname)
  );
}

async function staticResponse(request) {
  const cached = await caches.match(request, { cacheName: STATIC_CACHE });
  if (cached) return cached;
  const response = await fetch(request);
  const pathname = new URL(request.url).pathname;
  const type = expectedResponseType(pathname);
  if (responseIsCacheable(response, type)) {
    const cache = await caches.open(STATIC_CACHE);
    await cache.put(request, response.clone());
  }
  return response;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  const url = new URL(request.url);
  if (isSensitiveRequest(request, url)) return;
  if (request.mode === "navigate") {
    event.respondWith(navigationResponse(request));
    return;
  }
  if (isStaticAsset(url)) event.respondWith(staticResponse(request));
});
