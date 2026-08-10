import { spawn } from "node:child_process";
import { constants } from "node:fs";
import { access, chmod, lstat, mkdtemp, open, readFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const CACHE_PREFIX = "anhuan-internal-pwa-";
const SELECTED_ENTERPRISE_KEY = "f1-selected-enterprise";
const PWA_SENTINEL_CACHE = "anhuan-engineering-pwa-update-sentinel";
const PWA_SENTINEL_PATH = "/pwa-update-sentinel";
const CONTROLLER_CHANGE_KEY = "anhuan-engineering-controller-changes";
const BROWSER_READY_SIGNAL = "browser-ready";
const IMAGE_READY_SIGNAL = "image-b-ready";
const CONTROL_SIGNAL = Buffer.from("ready\n", "ascii");
const COMMAND_TIMEOUT_MS = 15_000;
const WAIT_TIMEOUT_MS = 20_000;
const UPDATE_TIMEOUT_MS = 180_000;
let unexpectedFailureReason = "BROWSER_STAGE_BOOTSTRAP_UNEXPECTED";
const TOP_LEVEL_PAGES = Object.freeze([
  "/workbench",
  "/calendar",
  "/notifications",
  "/service-cases",
  "/my-tasks",
  "/findings",
  "/rectification",
  "/reviews",
  "/controlled-documents",
  "/dashboard",
  "/crm",
  "/reports",
  "/policies",
  "/policy-impact",
  "/quality",
  "/rehearsal",
  "/internal-app",
]);
const IDENTITIES = Object.freeze([
  {
    key: "admin",
    username: "admin@anhuan.local",
    secret: "oidc_admin_anhuan_local",
  },
  { key: "auditor", username: "auditor", secret: "oidc_auditor" },
  { key: "tenant", username: "tenant-a", secret: "oidc_tenant_a" },
]);
const ROLE_PAGE_CONTRACTS = Object.freeze({
  consultant: Object.freeze({
    identityKey: "auditor",
    pages: Object.freeze([
      Object.freeze({
        route: "/reviews",
        protectedApiPath: "/api/v1/findings",
        title: "顾问复核",
        reasonKey: "CONSULTANT_REVIEWS",
      }),
      Object.freeze({
        route: "/quality/disagreements",
        protectedApiPath: "/api/v1/automated-quality/disagreements",
        title: "合成分歧队列",
        reasonKey: "CONSULTANT_QUALITY_DISAGREEMENTS",
      }),
    ]),
  }),
  enterprise: Object.freeze({
    identityKey: "tenant",
    pages: Object.freeze([
      Object.freeze({
        route: "/rectification",
        protectedApiPath: "/api/v1/findings",
        title: "企业整改",
        reasonKey: "ENTERPRISE_RECTIFICATION",
      }),
      Object.freeze({
        route: "/service-cases",
        protectedApiPath: "/api/v1/service-cases",
        title: "服务任务",
        reasonKey: "ENTERPRISE_SERVICE_CASES",
      }),
    ]),
  }),
});
const ROUTE_REASON_KEYS = Object.freeze({
  "/workbench": "WORKBENCH",
  "/calendar": "CALENDAR",
  "/notifications": "NOTIFICATIONS",
  "/service-cases": "SERVICE_CASES",
  "/my-tasks": "MY_TASKS",
  "/findings": "FINDINGS",
  "/rectification": "RECTIFICATION",
  "/reviews": "REVIEWS",
  "/controlled-documents": "CONTROLLED_DOCUMENTS",
  "/dashboard": "DASHBOARD",
  "/crm": "CRM",
  "/reports": "REPORTS",
  "/policies": "POLICIES",
  "/policy-impact": "POLICY_IMPACT",
  "/quality": "QUALITY",
  "/rehearsal": "REHEARSAL",
  "/internal-app": "INTERNAL_PWA",
});

class VerifyError extends Error {
  constructor(code) {
    super(code);
    this.code = code;
  }
}

function fail(code) {
  throw new VerifyError(code);
}

function markUnexpectedFailure(reason) {
  unexpectedFailureReason = reason;
}

function delay(milliseconds) {
  return new Promise((resolve) => setTimeout(resolve, milliseconds));
}

function withTimeout(promise, milliseconds, code) {
  let timer;
  return Promise.race([
    promise,
    new Promise((_, reject) => {
      timer = setTimeout(() => reject(new VerifyError(code)), milliseconds);
    }),
  ]).finally(() => clearTimeout(timer));
}

function parseInputs() {
  const inputs = process.argv.slice(2);
  if (inputs.length < 2 || inputs.length > 5) fail("BROWSER_INPUT_INVALID");
  let headed = false;
  let controlDirectory = null;
  for (let index = 2; index < inputs.length; index += 1) {
    if (inputs[index] === "--headed" && !headed) {
      headed = true;
      continue;
    }
    if (
      inputs[index] === "--pwa-update-control"
      && controlDirectory === null
      && index + 1 < inputs.length
    ) {
      controlDirectory = inputs[index + 1];
      index += 1;
      continue;
    }
    fail("BROWSER_INPUT_INVALID");
  }
  let parsed;
  try {
    parsed = new URL(inputs[0]);
  } catch {
    fail("BROWSER_ORIGIN_INVALID");
  }
  const loopback = new Set(["127.0.0.1", "[::1]", "localhost"]);
  if (
    !/^[\x21-\x7e]{1,256}$/.test(inputs[0]) ||
    !["http:", "https:"].includes(parsed.protocol) ||
    !loopback.has(parsed.hostname) ||
    parsed.username ||
    parsed.password ||
    parsed.pathname !== "/" ||
    parsed.search ||
    parsed.hash ||
    parsed.origin !== inputs[0].replace(/\/$/, "")
  ) {
    fail("BROWSER_ORIGIN_INVALID");
  }
  if (!path.isAbsolute(inputs[1]) || inputs[1].includes("\0")) {
    fail("BROWSER_SECRET_DIRECTORY_INVALID");
  }
  if (
    controlDirectory !== null
    && (!path.isAbsolute(controlDirectory) || controlDirectory.includes("\0"))
  ) {
    fail("PWA_UPDATE_CONTROL_DIRECTORY_INVALID");
  }
  return {
    origin: parsed.origin,
    secretDirectory: path.resolve(inputs[1]),
    headed,
    controlDirectory: controlDirectory === null ? null : path.resolve(controlDirectory),
  };
}

function sameFile(left, right) {
  return (
    left.dev === right.dev &&
    left.ino === right.ino &&
    left.size === right.size &&
    left.mtimeMs === right.mtimeMs &&
    left.ctimeMs === right.ctimeMs
  );
}

async function validateSecretDirectory(directory) {
  let info;
  try {
    info = await lstat(directory);
  } catch {
    fail("BROWSER_SECRET_DIRECTORY_INVALID");
  }
  if (
    info.isSymbolicLink() ||
    !info.isDirectory() ||
    (info.mode & 0o777) !== 0o700 ||
    info.uid !== process.geteuid()
  ) {
    fail("BROWSER_SECRET_DIRECTORY_INVALID");
  }
}

async function validateControlDirectory(directory) {
  let info;
  try {
    info = await lstat(directory);
  } catch {
    fail("PWA_UPDATE_CONTROL_DIRECTORY_INVALID");
  }
  if (
    info.isSymbolicLink()
    || !info.isDirectory()
    || (info.mode & 0o777) !== 0o700
    || info.uid !== process.geteuid()
  ) {
    fail("PWA_UPDATE_CONTROL_DIRECTORY_INVALID");
  }
}

async function writeControlSignal(directory, name) {
  if (![BROWSER_READY_SIGNAL, IMAGE_READY_SIGNAL].includes(name)) {
    fail("PWA_UPDATE_CONTROL_SIGNAL_INVALID");
  }
  const signalPath = path.join(directory, name);
  let handle;
  try {
    handle = await open(
      signalPath,
      constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW,
      0o600,
    );
    const written = await handle.write(CONTROL_SIGNAL, 0, CONTROL_SIGNAL.length, 0);
    if (written.bytesWritten !== CONTROL_SIGNAL.length) {
      fail("PWA_UPDATE_CONTROL_SIGNAL_INVALID");
    }
    await handle.sync();
  } catch (error) {
    if (error instanceof VerifyError) throw error;
    fail("PWA_UPDATE_CONTROL_SIGNAL_INVALID");
  } finally {
    await handle?.close().catch(() => undefined);
  }
}

async function controlSignalPresent(directory, name) {
  if (![BROWSER_READY_SIGNAL, IMAGE_READY_SIGNAL].includes(name)) {
    fail("PWA_UPDATE_CONTROL_SIGNAL_INVALID");
  }
  let handle;
  try {
    handle = await open(
      path.join(directory, name),
      constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
    );
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    fail("PWA_UPDATE_CONTROL_SIGNAL_INVALID");
  }
  try {
    const before = await handle.stat();
    if (
      !before.isFile()
      || before.nlink !== 1
      || before.uid !== process.geteuid()
      || (before.mode & 0o777) !== 0o600
      || before.size !== CONTROL_SIGNAL.length
    ) {
      fail("PWA_UPDATE_CONTROL_SIGNAL_INVALID");
    }
    const bytes = await handle.readFile();
    const after = await handle.stat();
    if (
      !sameFile(before, after)
      || !bytes.equals(CONTROL_SIGNAL)
    ) {
      fail("PWA_UPDATE_CONTROL_SIGNAL_INVALID");
    }
    return true;
  } finally {
    await handle.close();
  }
}

async function waitForControlSignal(directory, name) {
  const deadline = Date.now() + UPDATE_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (await controlSignalPresent(directory, name)) return;
    await delay(75);
  }
  fail("PWA_UPDATE_CONTROL_TIMEOUT");
}

async function readSecret(directory, name) {
  if (!/^[a-z0-9_]{1,64}$/.test(name)) fail("BROWSER_SECRET_INVALID");
  const secretPath = path.join(directory, name);
  let handle;
  try {
    handle = await open(
      secretPath,
      constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
    );
  } catch {
    fail("BROWSER_SECRET_INVALID");
  }
  try {
    const before = await handle.stat();
    if (
      !before.isFile() ||
      before.nlink !== 1 ||
      before.uid !== process.geteuid() ||
      (before.mode & 0o777) !== 0o600 ||
      before.size < 1 ||
      before.size > 4096
    ) {
      fail("BROWSER_SECRET_INVALID");
    }
    const bytes = await handle.readFile();
    const after = await handle.stat();
    if (!sameFile(before, after) || bytes.length !== before.size) {
      fail("BROWSER_SECRET_CHANGED");
    }
    const value = bytes.toString("utf8").replace(/[\r\n]+$/, "");
    if (!value || value.includes("\0") || value.includes("\n") || value.includes("\r")) {
      fail("BROWSER_SECRET_INVALID");
    }
    return value;
  } finally {
    await handle.close();
  }
}

async function findChrome() {
  const candidates = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
  ];
  for (const candidate of candidates) {
    try {
      await access(candidate, constants.X_OK);
      return candidate;
    } catch {
      // Continue through the fixed system-browser allowlist.
    }
  }
  fail("SYSTEM_CHROME_MISSING");
}

async function waitForDevTools(profile, child) {
  const marker = path.join(profile, "DevToolsActivePort");
  const deadline = Date.now() + WAIT_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (child.spawnFailed || child.exitCode !== null || child.signalCode !== null) {
      fail("CHROME_EARLY_EXIT");
    }
    try {
      const lines = (await readFile(marker, "utf8")).trim().split("\n");
      const port = Number(lines[0]);
      if (
        Number.isInteger(port) &&
        port > 0 &&
        port < 65536 &&
        /^\/devtools\/browser\/[A-Za-z0-9-]+$/.test(lines[1] ?? "")
      ) {
        return `ws://127.0.0.1:${port}${lines[1]}`;
      }
    } catch {
      // Chrome creates the marker only after the debugging socket is ready.
    }
    await delay(50);
  }
  fail("CHROME_DEBUG_ENDPOINT_TIMEOUT");
}

async function launchChrome(headed) {
  const executable = await findChrome();
  const temporaryRoot = path.resolve(os.tmpdir());
  const profile = await mkdtemp(path.join(temporaryRoot, "anhuan-engineering-browser-"));
  await chmod(profile, 0o700);
  const args = [
    "--remote-debugging-address=127.0.0.1",
    "--remote-debugging-port=0",
    "--remote-allow-origins=*",
    `--user-data-dir=${profile}`,
    "--no-first-run",
    "--no-default-browser-check",
    "--no-proxy-server",
    "--disable-background-networking",
    "--disable-component-update",
    "--disable-default-apps",
    "--disable-sync",
    "--metrics-recording-only",
    "--password-store=basic",
    "--use-mock-keychain",
    "about:blank",
  ];
  if (headed) {
    args.unshift("--window-position=-10000,-10000", "--window-size=1200,900");
  } else {
    args.unshift("--headless=new");
  }
  const child = spawn(executable, args, {
    detached: process.platform !== "win32",
    stdio: "ignore",
    env: {
      HOME: profile,
      LANG: "C",
      PATH: process.env.PATH ?? "/usr/bin:/bin",
      TMPDIR: temporaryRoot,
    },
  });
  child.spawnFailed = false;
  child.on("error", () => { child.spawnFailed = true; });
  try {
    const websocketUrl = await waitForDevTools(profile, child);
    return { child, profile, temporaryRoot, websocketUrl };
  } catch (error) {
    await cleanupChrome({ child, profile, temporaryRoot, cdp: null });
    throw error;
  }
}

function terminateChrome(child, signal) {
  if (
    !child ||
    !Number.isInteger(child.pid) ||
    child.pid < 2 ||
    child.exitCode !== null ||
    child.signalCode !== null
  ) return;
  try {
    if (process.platform === "win32") child.kill(signal);
    else process.kill(-child.pid, signal);
  } catch {
    // The exact process group may already have exited after Browser.close.
  }
}

async function waitForChildExit(child, milliseconds) {
  if (!child || child.exitCode !== null || child.signalCode !== null) return true;
  return withTimeout(
    new Promise((resolve) => child.once("exit", () => resolve(true))),
    milliseconds,
    "CHROME_EXIT_TIMEOUT",
  ).catch(() => false);
}

function cleanupChrome(runtime) {
  if (!runtime) return Promise.resolve();
  if (!runtime.cleanupPromise) runtime.cleanupPromise = cleanupChromeOnce(runtime);
  return runtime.cleanupPromise;
}

async function cleanupChromeOnce(runtime) {
  if (runtime.cdp) {
    await runtime.cdp.call("Browser.close", {}, null, 2_000).catch(() => undefined);
    runtime.cdp.close();
  }
  let exited = await waitForChildExit(runtime.child, 2_000);
  if (!exited) {
    terminateChrome(runtime.child, "SIGTERM");
    exited = await waitForChildExit(runtime.child, 2_000);
  }
  if (!exited) {
    terminateChrome(runtime.child, "SIGKILL");
    exited = await waitForChildExit(runtime.child, 2_000);
  }
  if (!exited) fail("CHROME_PROCESS_CLEANUP_FAILED");
  const expectedPrefix = path.join(runtime.temporaryRoot, "anhuan-engineering-browser-");
  if (!runtime.profile.startsWith(expectedPrefix) || path.dirname(runtime.profile) !== runtime.temporaryRoot) {
    fail("CHROME_PROFILE_BOUNDARY_INVALID");
  }
  await rm(runtime.profile, { recursive: true, force: true });
}

class CdpConnection {
  constructor(socket) {
    this.socket = socket;
    this.nextId = 1;
    this.pending = new Map();
    this.listeners = new Map();
    socket.addEventListener("message", (event) => this.receive(event.data));
    socket.addEventListener("close", () => this.rejectPending());
    socket.addEventListener("error", () => this.rejectPending());
  }

  static async connect(websocketUrl) {
    const socket = new WebSocket(websocketUrl);
    await withTimeout(
      new Promise((resolve, reject) => {
        socket.addEventListener("open", resolve, { once: true });
        socket.addEventListener("error", reject, { once: true });
      }),
      COMMAND_TIMEOUT_MS,
      "CDP_CONNECT_TIMEOUT",
    ).catch(() => fail("CDP_CONNECT_FAILED"));
    return new CdpConnection(socket);
  }

  receive(raw) {
    let message;
    try {
      message = JSON.parse(typeof raw === "string" ? raw : String(raw));
    } catch {
      this.rejectPending();
      return;
    }
    if (Number.isInteger(message.id)) {
      const pending = this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      clearTimeout(pending.timer);
      if (message.error) pending.reject(new VerifyError("CDP_COMMAND_REJECTED"));
      else pending.resolve(message.result ?? {});
      return;
    }
    if (typeof message.method !== "string") return;
    const key = `${message.sessionId ?? "browser"}:${message.method}`;
    for (const listener of this.listeners.get(key) ?? []) {
      try {
        listener(message.params ?? {});
      } catch {
        // Event observers only maintain aggregate counters.
      }
    }
  }

  rejectPending() {
    for (const pending of this.pending.values()) {
      clearTimeout(pending.timer);
      pending.reject(new VerifyError("CDP_CONNECTION_CLOSED"));
    }
    this.pending.clear();
  }

  on(sessionId, method, listener) {
    const key = `${sessionId ?? "browser"}:${method}`;
    const listeners = this.listeners.get(key) ?? new Set();
    listeners.add(listener);
    this.listeners.set(key, listeners);
    return () => {
      listeners.delete(listener);
      if (listeners.size === 0) this.listeners.delete(key);
    };
  }

  call(method, params = {}, sessionId = null, timeout = COMMAND_TIMEOUT_MS) {
    if (this.socket.readyState !== 1) return Promise.reject(new VerifyError("CDP_CONNECTION_CLOSED"));
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new VerifyError("CDP_COMMAND_TIMEOUT"));
      }, timeout);
      this.pending.set(id, { resolve, reject, timer });
      const message = { id, method, params };
      if (sessionId) message.sessionId = sessionId;
      this.socket.send(JSON.stringify(message));
    });
  }

  close() {
    try {
      this.socket.close();
    } catch {
      // Browser.close may already have closed the debugging socket.
    }
    this.rejectPending();
  }
}

function isApiUrl(raw, origin) {
  try {
    const url = new URL(raw);
    return url.origin === origin && (url.pathname === "/api" || url.pathname.startsWith("/api/"));
  } catch {
    return false;
  }
}

function apiPath(raw, origin) {
  try {
    const url = new URL(raw);
    if (url.origin !== origin || (url.pathname !== "/api" && !url.pathname.startsWith("/api/"))) {
      return null;
    }
    return url.pathname;
  } catch {
    return null;
  }
}

function tenantHeader(headers) {
  for (const [name, value] of Object.entries(headers ?? {})) {
    if (name.toLowerCase() === "x-enterprise-id" && typeof value === "string") return value;
  }
  return null;
}

class BrowserPage {
  constructor(cdp, sessionId, origin) {
    this.cdp = cdp;
    this.sessionId = sessionId;
    this.origin = origin;
    this.apiInflight = new Set();
    this.apiRequestIds = new Set();
    this.apiResponses = 0;
    this.apiNon2xx = 0;
    this.apiFailureRoutes = new Set();
    this.apiFailureStatuses = new Map();
    this.apiResponseEvents = [];
    this.currentRoute = "/";
    this.tenantRequests = [];
    this.offlineProbe = false;
    this.offlineDocumentFromServiceWorker = false;
    this.unsubscribe = [];
  }

  async initialize() {
    this.unsubscribe.push(
      this.cdp.on(this.sessionId, "Network.requestWillBeSent", (event) => {
        if (!isApiUrl(event.request?.url, this.origin)) return;
        this.apiRequestIds.add(event.requestId);
        this.apiInflight.add(event.requestId);
        this.tenantRequests.push(tenantHeader(event.request?.headers));
      }),
      this.cdp.on(this.sessionId, "Network.responseReceived", (event) => {
        const responsePath = apiPath(event.response?.url, this.origin);
        if (responsePath !== null) {
          this.apiResponses += 1;
          const status = Number(event.response?.status);
          this.apiResponseEvents.push({
            path: responsePath,
            route: this.currentRoute,
            status: Number.isInteger(status) ? status : null,
          });
          if (!Number.isFinite(status) || status < 200 || status >= 300) {
            this.apiNon2xx += 1;
            this.apiFailureRoutes.add(this.currentRoute);
            const statuses = this.apiFailureStatuses.get(this.currentRoute) ?? new Set();
            if (Number.isInteger(status)) statuses.add(status);
            this.apiFailureStatuses.set(this.currentRoute, statuses);
          }
        }
        if (
          this.offlineProbe &&
          event.type === "Document" &&
          event.response?.fromServiceWorker === true
        ) {
          this.offlineDocumentFromServiceWorker = true;
        }
      }),
      this.cdp.on(this.sessionId, "Network.loadingFinished", (event) => {
        this.apiInflight.delete(event.requestId);
      }),
      this.cdp.on(this.sessionId, "Network.loadingFailed", (event) => {
        this.apiInflight.delete(event.requestId);
      }),
    );
    await this.cdp.call("Page.enable", {}, this.sessionId);
    await this.cdp.call("Runtime.enable", {}, this.sessionId);
    await this.cdp.call("Network.enable", {}, this.sessionId);
    await this.cdp.call("Network.setCacheDisabled", { cacheDisabled: true }, this.sessionId);
  }

  async evaluate(expression) {
    const result = await this.cdp.call(
      "Runtime.evaluate",
      { expression, awaitPromise: true, returnByValue: true, userGesture: true },
      this.sessionId,
    );
    if (result.exceptionDetails) fail("BROWSER_EVALUATION_FAILED");
    return result.result?.value;
  }

  async waitForExpression(expression, code, timeout = WAIT_TIMEOUT_MS) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      try {
        if (await this.evaluate(expression)) return;
      } catch {
        // Navigation can replace the execution context while it is polled.
      }
      await delay(75);
    }
    fail(code);
  }

  async waitForApiIdle() {
    const deadline = Date.now() + WAIT_TIMEOUT_MS;
    let idleSince = null;
    while (Date.now() < deadline) {
      if (this.apiInflight.size === 0) {
        idleSince ??= Date.now();
        if (Date.now() - idleSince >= 350) return;
      } else {
        idleSince = null;
      }
      await delay(50);
    }
    fail("BROWSER_API_IDLE_TIMEOUT");
  }

  async waitForProtectedApi(pathname, eventBoundary, code) {
    const deadline = Date.now() + WAIT_TIMEOUT_MS;
    while (Date.now() < deadline) {
      const events = this.apiResponseEvents
        .slice(eventBoundary)
        .filter((event) => event.path === pathname);
      if (events.length > 0) return events;
      await delay(50);
    }
    fail(code);
  }

  async clickElement(selector, code) {
    const point = await this.evaluate(`(() => {
      const elements = document.querySelectorAll(${JSON.stringify(selector)});
      for (const element of elements) {
        if (!(element instanceof HTMLElement)) continue;
        const box = element.getBoundingClientRect();
        const style = getComputedStyle(element);
        if (
          box.width > 0
          && box.height > 0
          && style.display !== "none"
          && style.visibility !== "hidden"
        ) {
          return { x: box.left + box.width / 2, y: box.top + box.height / 2 };
        }
      }
      return null;
    })()`);
    if (
      !point
      || !Number.isFinite(point.x)
      || !Number.isFinite(point.y)
    ) {
      fail(code);
    }
    await this.cdp.call(
      "Input.dispatchMouseEvent",
      { type: "mouseMoved", x: point.x, y: point.y },
      this.sessionId,
    );
    await this.cdp.call(
      "Input.dispatchMouseEvent",
      {
        type: "mousePressed",
        x: point.x,
        y: point.y,
        button: "left",
        clickCount: 1,
      },
      this.sessionId,
    );
    await this.cdp.call(
      "Input.dispatchMouseEvent",
      {
        type: "mouseReleased",
        x: point.x,
        y: point.y,
        button: "left",
        clickCount: 1,
      },
      this.sessionId,
    );
  }

  async pressKey(key, code, virtualKeyCode) {
    const event = {
      key,
      code,
      windowsVirtualKeyCode: virtualKeyCode,
      nativeVirtualKeyCode: virtualKeyCode,
    };
    await this.cdp.call(
      "Input.dispatchKeyEvent",
      { type: "keyDown", ...event },
      this.sessionId,
    );
    await this.cdp.call(
      "Input.dispatchKeyEvent",
      { type: "keyUp", ...event },
      this.sessionId,
    );
  }

  async navigate(route, { offline = false } = {}) {
    this.currentRoute = route;
    const target = `${this.origin}${route}`;
    const result = await this.cdp.call("Page.navigate", { url: target }, this.sessionId);
    if (result.errorText && !offline) fail("BROWSER_NAVIGATION_FAILED");
    await this.waitForExpression(
      `location.origin === ${JSON.stringify(this.origin)} && location.pathname === ${JSON.stringify(route)} && document.readyState === "complete" && Boolean(document.body)`,
      offline ? "PWA_OFFLINE_NAVIGATION_FAILED" : "BROWSER_NAVIGATION_TIMEOUT",
    );
    await this.waitForApiIdle();
  }

  close() {
    for (const unsubscribe of this.unsubscribe.splice(0)) unsubscribe();
  }
}

async function createPage(cdp, origin) {
  let browserContextId;
  try {
    const context = await cdp.call("Target.createBrowserContext", { disposeOnDetach: false });
    browserContextId = context.browserContextId;
    if (typeof browserContextId !== "string") fail("BROWSER_CONTEXT_CREATE_FAILED");
    const target = await cdp.call("Target.createTarget", { url: "about:blank", browserContextId });
    const attached = await cdp.call("Target.attachToTarget", { targetId: target.targetId, flatten: true });
    if (typeof attached.sessionId !== "string") fail("BROWSER_TARGET_ATTACH_FAILED");
    const page = new BrowserPage(cdp, attached.sessionId, origin);
    await page.initialize();
    return { browserContextId, page };
  } catch (error) {
    if (typeof browserContextId === "string") {
      await cdp.call("Target.disposeBrowserContext", { browserContextId }).catch(() => undefined);
    }
    throw error;
  }
}

async function disposePage(cdp, owned) {
  if (!owned) return;
  owned.page.close();
  await cdp.call("Target.disposeBrowserContext", {
    browserContextId: owned.browserContextId,
  }).catch(() => fail("BROWSER_CONTEXT_CLEANUP_FAILED"));
}

async function login(page, username, password) {
  await page.navigate("/login");
  await page.waitForExpression(
    `Array.from(document.querySelectorAll("button")).some((button) => button.textContent?.includes("通过 Keycloak 登录"))`,
    "OIDC_LOGIN_BUTTON_MISSING",
  );
  await page.evaluate(
    `Array.from(document.querySelectorAll("button")).find((button) => button.textContent?.includes("通过 Keycloak 登录"))?.click()`,
  );
  await page.waitForExpression(
    `Boolean(document.querySelector("#username") && document.querySelector("#password") && document.querySelector("#kc-login"))`,
    "OIDC_FORM_MISSING",
  );
  const formTargetValid = await page.evaluate(`(() => {
    const form = document.querySelector("#kc-form-login");
    if (!(form instanceof HTMLFormElement)) return false;
    const action = new URL(form.action, location.href);
    return action.origin === ${JSON.stringify(page.origin)} && action.pathname.startsWith("/realms/anhuan/login-actions/");
  })()`);
  if (!formTargetValid) fail("OIDC_FORM_TARGET_INVALID");
  const credentials = `(${function fillAndSubmit(user, secret) {
    const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
    const usernameInput = document.querySelector("#username");
    const passwordInput = document.querySelector("#password");
    const submit = document.querySelector("#kc-login");
    if (!setter || !(usernameInput instanceof HTMLInputElement) || !(passwordInput instanceof HTMLInputElement) || !(submit instanceof HTMLElement)) return false;
    setter.call(usernameInput, user);
    usernameInput.dispatchEvent(new Event("input", { bubbles: true }));
    setter.call(passwordInput, secret);
    passwordInput.dispatchEvent(new Event("input", { bubbles: true }));
    submit.click();
    return true;
  }.toString()})(${JSON.stringify(username)}, ${JSON.stringify(password)})`;
  if (!(await page.evaluate(credentials))) fail("OIDC_FORM_SUBMIT_FAILED");
  try {
    await page.waitForExpression(
      `location.origin === ${JSON.stringify(page.origin)} && location.pathname === "/workbench" && Boolean(document.querySelector(".ant-layout-header"))`,
      "OIDC_LOGIN_FAILED",
    );
  } catch (error) {
    if (!(error instanceof VerifyError) || error.code !== "OIDC_LOGIN_FAILED") throw error;
    const state = await page.evaluate(`({
      path: location.pathname,
      hasLoginForm: Boolean(document.querySelector("#username") && document.querySelector("#password")),
      hasCallbackFailure: document.body?.textContent?.includes("OIDC_CALLBACK_FAILED") ?? false,
      hasCookieError: /cookie|session cookie|会话|登录超时/i.test(document.body?.textContent ?? ""),
      hasClientError: /client|客户端/i.test(document.body?.textContent ?? ""),
      hasRedirectError: /redirect|重定向/i.test(document.body?.textContent ?? ""),
      hasCredentialError: /invalid (?:username|user name)|invalid credentials|username or password|用户名|密码错误/i.test(document.body?.textContent ?? ""),
      hasInternalError: /internal server error|unexpected error|内部服务器|意外错误/i.test(document.body?.textContent ?? ""),
    })`).catch(() => null);
    if (state?.path?.startsWith("/realms/anhuan/") && state.hasLoginForm) {
      fail("OIDC_CREDENTIALS_REJECTED");
    }
    if (state?.path === "/callback" || state?.hasCallbackFailure) {
      fail("OIDC_CALLBACK_FAILED");
    }
    if (state?.path === "/login") fail("OIDC_LOGIN_LOOP");
    if (state?.path?.startsWith("/realms/anhuan/")) {
      if (state.hasCookieError) fail("OIDC_KEYCLOAK_COOKIE_ERROR");
      if (state.hasRedirectError) fail("OIDC_KEYCLOAK_REDIRECT_ERROR");
      if (state.hasClientError) fail("OIDC_KEYCLOAK_CLIENT_ERROR");
      if (state.hasCredentialError) fail("OIDC_CREDENTIALS_REJECTED");
      if (state.hasInternalError) fail("OIDC_KEYCLOAK_INTERNAL_ERROR");
      fail("OIDC_KEYCLOAK_ERROR_PAGE");
    }
    if (state?.path === "/workbench") fail("OIDC_WORKBENCH_SHELL_MISSING");
    if (state?.path === "/") fail("OIDC_ROOT_REDIRECT_STALLED");
    fail("OIDC_REDIRECT_STALLED");
  }
  await page.waitForApiIdle();
}

async function waitForApplicationShell(page) {
  await page.waitForExpression(
    `Boolean(document.querySelector(".ant-layout-header") && document.querySelector(".ant-layout-content"))`,
    "APPLICATION_SHELL_MISSING",
  );
  await page.waitForApiIdle();
}

async function visitAdminPages(page) {
  await page.waitForExpression(
    `Boolean(localStorage.getItem(${JSON.stringify(SELECTED_ENTERPRISE_KEY)}))`,
    "ADMIN_TENANT_CONTEXT_MISSING",
  );
  for (const route of TOP_LEVEL_PAGES) {
    const responsesBeforeNavigation = page.apiResponses;
    const failuresBeforeNavigation = page.apiNon2xx;
    await page.navigate(route);
    await waitForApplicationShell(page);
    if (route !== "/internal-app" && page.apiResponses === responsesBeforeNavigation) {
      fail("ADMIN_PAGE_API_EVIDENCE_MISSING");
    }
    if (page.apiNon2xx !== failuresBeforeNavigation) {
      const key = ROUTE_REASON_KEYS[route];
      if (typeof key !== "string") fail("ADMIN_API_NON_2XX");
      const statuses = [...(page.apiFailureStatuses.get(route) ?? [])];
      if (
        statuses.length === 1
        && [401, 403, 404, 409, 422, 500, 503].includes(statuses[0])
      ) {
        fail(`ADMIN_${key}_API_${statuses[0]}`);
      }
      fail(`ADMIN_${key}_API_NON_2XX`);
    }
  }
  if (page.apiNon2xx !== 0) fail("ADMIN_API_NON_2XX");
}

async function visitRolePages(page, contract) {
  await page.waitForExpression(
    `Boolean(localStorage.getItem(${JSON.stringify(SELECTED_ENTERPRISE_KEY)}))`,
    "IDENTITY_TENANT_CONTEXT_MISSING",
  );
  const apiNon2xxBefore = page.apiNon2xx;
  let pagesVisited = 0;
  for (const pageContract of contract.pages) {
    const eventBoundary = page.apiResponseEvents.length;
    const failuresBeforeNavigation = page.apiNon2xx;
    await page.navigate(pageContract.route);
    await waitForApplicationShell(page);
    await page.waitForExpression(
      `Array.from(document.querySelectorAll(".ant-layout-content h1, .ant-layout-content h2, .ant-layout-content h3, .ant-layout-content h4, .ant-layout-content h5")).some((heading) => heading.textContent?.trim() === ${JSON.stringify(pageContract.title)})`,
      `${pageContract.reasonKey}_UI_MISSING`,
    );
    const events = await page.waitForProtectedApi(
      pageContract.protectedApiPath,
      eventBoundary,
      `${pageContract.reasonKey}_API_EVIDENCE_MISSING`,
    );
    if (page.apiNon2xx !== failuresBeforeNavigation) {
      const statuses = Array.from(
        new Set(events.map((event) => event.status).filter((status) => Number.isInteger(status))),
      );
      if (
        statuses.length === 1
        && [401, 403, 404, 409, 422, 500, 503].includes(statuses[0])
      ) {
        fail(`${pageContract.reasonKey}_API_${statuses[0]}`);
      }
      fail(`${pageContract.reasonKey}_API_NON_2XX`);
    }
    pagesVisited += 1;
  }
  return {
    pages: pagesVisited,
    apiNon2xx: page.apiNon2xx - apiNon2xxBefore,
  };
}

async function verifyTenantSwitch(page) {
  const oldRequestBoundary = page.tenantRequests.length;
  await page.navigate("/service-cases");
  await waitForApplicationShell(page);
  const oldTenant = await page.evaluate(
    `localStorage.getItem(${JSON.stringify(SELECTED_ENTERPRISE_KEY)})`,
  );
  if (typeof oldTenant !== "string" || !oldTenant) fail("TENANT_SELECTION_MISSING");
  const oldHeaderSeen = page.tenantRequests
    .slice(oldRequestBoundary)
    .some((value) => value === oldTenant);
  if (!oldHeaderSeen) fail("TENANT_HEADER_INITIAL_MISSING");
  const oldRows = await page.evaluate(
    `Array.from(document.querySelectorAll(".ant-table-tbody .ant-table-row"), (row) => row.textContent ?? "")`,
  );
  await page.clickElement(
    ".ant-layout-header .ant-select",
    "TENANT_SWITCH_CONTROL_MISSING",
  );
  await page.waitForExpression(
    `document.querySelector('.ant-layout-header [role="combobox"]')?.getAttribute("aria-expanded") === "true"`,
    "TENANT_SWITCH_OPEN_FAILED",
  );
  await page.pressKey("ArrowDown", "ArrowDown", 40);
  await page.pressKey("Enter", "Enter", 13);
  await page.waitForExpression(
    `Boolean(localStorage.getItem(${JSON.stringify(SELECTED_ENTERPRISE_KEY)}) && localStorage.getItem(${JSON.stringify(SELECTED_ENTERPRISE_KEY)}) !== ${JSON.stringify(oldTenant)})`,
    "TENANT_SWITCH_FAILED",
  );
  const newTenant = await page.evaluate(
    `localStorage.getItem(${JSON.stringify(SELECTED_ENTERPRISE_KEY)})`,
  );
  await page.waitForApiIdle();
  await page.evaluate(
    `new Promise((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve(true))))`,
  );
  const newRows = await page.evaluate(
    `Array.from(document.querySelectorAll(".ant-table-tbody .ant-table-row"), (row) => row.textContent ?? "")`,
  );
  const staleRows = Array.isArray(oldRows) && Array.isArray(newRows)
    ? oldRows.filter((row) => row && newRows.includes(row)).length
    : 1;
  const storedSelection = await page.evaluate(
    `localStorage.getItem(${JSON.stringify(SELECTED_ENTERPRISE_KEY)})`,
  );
  if (storedSelection !== newTenant || staleRows !== 0) fail("TENANT_OLD_STATE_RETAINED");
  const newRequestBoundary = page.tenantRequests.length;
  await page.navigate("/service-cases");
  await waitForApplicationShell(page);
  const subsequentHeaders = page.tenantRequests.slice(newRequestBoundary).filter(Boolean);
  if (
    typeof newTenant !== "string" ||
    !newTenant ||
    newTenant === oldTenant ||
    subsequentHeaders.length === 0 ||
    subsequentHeaders.some((value) => value !== newTenant)
  ) {
    fail("TENANT_HEADER_SWITCH_FAILED");
  }
  return { headerChanges: 1, stateClears: 1 };
}

async function auditPwaCaches(page) {
  await page.waitForExpression(
    `navigator.serviceWorker.getRegistration("/").then((registration) => Boolean(registration?.active))`,
    "PWA_REGISTRATION_MISSING",
  );
  await page.waitForExpression(
    `Boolean(navigator.serviceWorker.controller)`,
    "PWA_CONTROLLER_MISSING",
  );
  const audit = await page.evaluate(`(async () => {
    const registrations = (await navigator.serviceWorker.getRegistrations()).filter((registration) => {
      const worker = registration.active ?? registration.waiting ?? registration.installing;
      if (!worker) return false;
      const url = new URL(worker.scriptURL);
      return url.origin === location.origin && url.pathname === "/pwa-sw.js";
    });
    const names = (await caches.keys()).filter((name) => name.startsWith(${JSON.stringify(CACHE_PREFIX)}));
    let entries = 0;
    let sensitive = 0;
    for (const name of names) {
      const cache = await caches.open(name);
      for (const request of await cache.keys()) {
        entries += 1;
        const url = new URL(request.url);
        const pathSensitive = ["/api", "/realms", "/callback"].some((prefix) => url.pathname === prefix || url.pathname.startsWith(prefix + "/"));
        const querySensitive = ["code", "state", "session_state", "iss", "error"].some((key) => url.searchParams.has(key));
        if (url.origin !== location.origin || pathSensitive || querySensitive || request.headers.has("Authorization")) sensitive += 1;
      }
    }
    return { registrations: registrations.length, controlled: navigator.serviceWorker.controller ? 1 : 0, caches: names.length, entries, sensitive };
  })()`);
  if (
    !audit ||
    audit.registrations !== 1 ||
    audit.controlled !== 1 ||
    audit.caches < 1 ||
    audit.entries < 1 ||
    audit.sensitive !== 0
  ) {
    fail("PWA_CACHE_BOUNDARY_FAILED");
  }
  return audit;
}

async function auditPwaInstallability(page) {
  const manifest = await page.cdp.call(
    "Page.getAppManifest",
    {},
    page.sessionId,
  ).catch(() => fail("PWA_MANIFEST_AUDIT_UNAVAILABLE"));
  const manifestErrors = Array.isArray(manifest?.errors) ? manifest.errors : [];
  const installability = await page.cdp.call(
    "Page.getInstallabilityErrors",
    {},
    page.sessionId,
  ).catch(() => fail("PWA_INSTALLABILITY_AUDIT_UNAVAILABLE"));
  const installabilityErrors = Array.isArray(installability?.installabilityErrors)
    ? installability.installabilityErrors
    : [];
  if (manifestErrors.some((error) => Number(error?.critical) > 0)) {
    fail("PWA_MANIFEST_INVALID");
  }
  const installabilityIds = installabilityErrors
    .map((error) => typeof error?.errorId === "string" ? error.errorId : "")
    .filter(Boolean);
  if (installabilityIds.some((id) => /icon/i.test(id))) {
    fail("PWA_INSTALLABILITY_ICON_INVALID");
  }
  if (installabilityIds.some((id) => /service.?worker|offline/i.test(id))) {
    fail("PWA_INSTALLABILITY_WORKER_INVALID");
  }
  if (installabilityIds.some((id) => /manifest|name|display|start.?url/i.test(id))) {
    fail("PWA_INSTALLABILITY_MANIFEST_INVALID");
  }
  if (installabilityErrors.length !== 0) fail("PWA_INSTALLABILITY_FAILED");
  let manifestId;
  try {
    manifestId = new URL(manifest?.manifest?.id);
  } catch {
    fail("PWA_MANIFEST_ID_INVALID");
  }
  if (manifestId.origin !== page.origin || manifestId.pathname !== "/internal-app") {
    fail("PWA_MANIFEST_ID_INVALID");
  }
  return {
    installabilityErrors: installabilityErrors.length,
    manifestId: manifestId.href,
  };
}

async function verifyOfflineShell(page, { preserveCurrentClient = false } = {}) {
  page.offlineProbe = true;
  page.offlineDocumentFromServiceWorker = false;
  await page.cdp.call(
    "Network.emulateNetworkConditions",
    {
      offline: true,
      latency: 0,
      downloadThroughput: 0,
      uploadThroughput: 0,
      connectionType: "none",
    },
    page.sessionId,
  );
  await page.cdp.call(
    "Network.overrideNetworkState",
    {
      offline: true,
      latency: 0,
      downloadThroughput: 0,
      uploadThroughput: 0,
      connectionType: "none",
    },
    page.sessionId,
  );
  try {
    if (preserveCurrentClient) {
      await page.evaluate(`new Promise((resolve, reject) => {
        const previous = document.querySelector('[data-testid="pwa-offline-frame"]');
        previous?.remove();
        const frame = document.createElement("iframe");
        frame.dataset.testid = "pwa-offline-frame";
        frame.hidden = true;
        frame.addEventListener("load", () => resolve(true), { once: true });
        frame.addEventListener("error", () => reject(new Error("offline-frame")), { once: true });
        document.body.append(frame);
        frame.src = "/internal-app";
      })`);
    } else {
      await page.navigate("/internal-app", { offline: true });
    }
    await page.waitForExpression(
      preserveCurrentClient
        ? `document.querySelector('[data-testid="pwa-offline-frame"]')
            ?.contentDocument?.body?.textContent?.includes("内部 PWA 状态")`
        : `document.body?.textContent?.includes("内部 PWA 状态")`,
      "PWA_OFFLINE_SHELL_MISSING",
    );
    await page.waitForExpression(
      preserveCurrentClient
        ? `document.querySelector('[data-testid="pwa-offline-frame"]')
            ?.contentDocument?.body?.textContent?.includes("当前离线")`
        : `document.body?.textContent?.includes("当前离线")`,
      "PWA_OFFLINE_STATUS_MISSING",
    );
    if (!page.offlineDocumentFromServiceWorker) fail("PWA_OFFLINE_NOT_SERVICE_WORKER");
  } finally {
    page.offlineProbe = false;
    await page.cdp.call(
      "Network.emulateNetworkConditions",
      {
        offline: false,
        latency: 0,
        downloadThroughput: -1,
        uploadThroughput: -1,
        connectionType: "wifi",
      },
      page.sessionId,
    ).catch(() => undefined);
    await page.cdp.call(
      "Network.overrideNetworkState",
      {
        offline: false,
        latency: 0,
        downloadThroughput: -1,
        uploadThroughput: -1,
        connectionType: "wifi",
      },
      page.sessionId,
    ).catch(() => undefined);
    if (preserveCurrentClient) {
      await page.evaluate(`(() => {
        document.querySelector('[data-testid="pwa-offline-frame"]')?.remove();
        return true;
      })()`).catch(() => undefined);
    }
  }
  return 1;
}

async function verifyWaitingPwaUpdate(page, controlDirectory) {
  markUnexpectedFailure("PWA_STAGE_BASELINE_UNEXPECTED");
  const controllerProbeSource = `(() => {
    const key = ${JSON.stringify(CONTROLLER_CHANGE_KEY)};
    if (sessionStorage.getItem(key) === null) sessionStorage.setItem(key, "0");
    if (!window.__anhuanEngineeringControllerProbe) {
      window.__anhuanEngineeringControllerProbe = true;
      navigator.serviceWorker.addEventListener("controllerchange", () => {
        const previous = Number(sessionStorage.getItem(key) ?? "0");
        sessionStorage.setItem(key, String(Number.isInteger(previous) ? previous + 1 : 1));
      });
    }
    return true;
  })()`;
  await page.cdp.call(
    "Page.addScriptToEvaluateOnNewDocument",
    { source: controllerProbeSource },
    page.sessionId,
  );
  if (!(await page.evaluate(controllerProbeSource))) {
    fail("PWA_CONTROLLER_PROBE_FAILED");
  }

  const baseline = await page.evaluate(`(async () => {
    const registration = await navigator.serviceWorker.getRegistration("/");
    const ownedCaches = (await caches.keys())
      .filter((name) => name.startsWith(${JSON.stringify(CACHE_PREFIX)}))
      .sort();
    const oidcSessionKeys = Object.keys(sessionStorage)
      .filter((key) => key.startsWith("oidc.user:"))
      .sort();
    const tenant = localStorage.getItem(${JSON.stringify(SELECTED_ENTERPRISE_KEY)});
    const sentinel = await caches.open(${JSON.stringify(PWA_SENTINEL_CACHE)});
    await sentinel.put(
      new Request(${JSON.stringify(PWA_SENTINEL_PATH)}, { credentials: "omit" }),
      new Response("kept", { status: 200, headers: { "Content-Type": "text/plain" } }),
    );
    return {
      active: registration?.active?.state ?? null,
      waiting: registration?.waiting?.state ?? null,
      ownedCaches,
      oidcSessionKeyCount: oidcSessionKeys.length,
      tenant,
    };
  })()`);
  if (
    !baseline
    || baseline.active !== "activated"
    || baseline.waiting !== null
    || !Array.isArray(baseline.ownedCaches)
    || baseline.ownedCaches.length < 1
    || !baseline.ownedCaches.every((name) => typeof name === "string" && name.startsWith(CACHE_PREFIX))
    || !Number.isInteger(baseline.oidcSessionKeyCount)
    || baseline.oidcSessionKeyCount < 1
    || typeof baseline.tenant !== "string"
    || !baseline.tenant
  ) {
    fail("PWA_UPDATE_BASELINE_INVALID");
  }

  markUnexpectedFailure("PWA_STAGE_WAIT_B_UNEXPECTED");
  await writeControlSignal(controlDirectory, BROWSER_READY_SIGNAL);
  await waitForControlSignal(controlDirectory, IMAGE_READY_SIGNAL);
  markUnexpectedFailure("PWA_STAGE_REQUEST_UNEXPECTED");
  await page.clickElement(
    '[data-testid="pwa-check-update"]',
    "PWA_UPDATE_REQUEST_FAILED",
  );
  const oldCaches = baseline.ownedCaches;
  markUnexpectedFailure("PWA_STAGE_WAITING_UNEXPECTED");
  await page.waitForExpression(
    `(async () => {
      const registration = await navigator.serviceWorker.getRegistration("/");
      const names = (await caches.keys()).filter((name) => name.startsWith(${JSON.stringify(CACHE_PREFIX)}));
      const old = ${JSON.stringify(oldCaches)};
      return registration?.waiting?.state === "installed"
        && old.every((name) => names.includes(name))
        && names.some((name) => !old.includes(name));
    })()`,
    "PWA_WAITING_UPDATE_MISSING",
    UPDATE_TIMEOUT_MS,
  );
  const waiting = await page.evaluate(`(async () => {
    const registration = await navigator.serviceWorker.getRegistration("/");
    const names = (await caches.keys())
      .filter((name) => name.startsWith(${JSON.stringify(CACHE_PREFIX)}))
      .sort();
    const old = ${JSON.stringify(oldCaches)};
    return {
      active: registration?.active?.state ?? null,
      waiting: registration?.waiting?.state ?? null,
      oldPresent: old.filter((name) => names.includes(name)),
      newCaches: names.filter((name) => !old.includes(name)),
      controllerChanges: sessionStorage.getItem(${JSON.stringify(CONTROLLER_CHANGE_KEY)}),
    };
  })()`);
  if (
    !waiting
    || waiting.active !== "activated"
    || waiting.waiting !== "installed"
    || waiting.controllerChanges !== "0"
    || !Array.isArray(waiting.oldPresent)
    || waiting.oldPresent.length !== oldCaches.length
    || !Array.isArray(waiting.newCaches)
    || waiting.newCaches.length !== oldCaches.length
  ) {
    fail("PWA_WAITING_UPDATE_INVALID");
  }

  markUnexpectedFailure("PWA_STAGE_OFFLINE_UNEXPECTED");
  const offline = await verifyOfflineShell(page, { preserveCurrentClient: true });
  markUnexpectedFailure("PWA_STAGE_CONFIRM_UNEXPECTED");
  await page.waitForExpression(
    `Boolean(document.querySelector('[data-testid="pwa-apply-update"]'))`,
    "PWA_UPDATE_CONFIRMATION_MISSING",
  );
  const oldActiveStillServing = await page.evaluate(`(async () => {
    const registration = await navigator.serviceWorker.getRegistration("/");
    const names = await caches.keys();
    const old = ${JSON.stringify(oldCaches)};
    return registration?.active?.state === "activated"
      && registration?.waiting?.state === "installed"
      && old.every((name) => names.includes(name))
      && sessionStorage.getItem(${JSON.stringify(CONTROLLER_CHANGE_KEY)}) === "0";
  })()`);
  if (!oldActiveStillServing) fail("PWA_OLD_ACTIVE_NOT_PRESERVED");

  await page.clickElement(
    '[data-testid="pwa-apply-update"]',
    "PWA_UPDATE_CONFIRMATION_MISSING",
  );
  markUnexpectedFailure("PWA_STAGE_ACTIVATION_UNEXPECTED");
  await page.waitForExpression(
    `location.pathname === "/internal-app"
      && document.readyState === "complete"
      && Boolean(document.querySelector(".ant-layout-header"))
      && sessionStorage.getItem(${JSON.stringify(CONTROLLER_CHANGE_KEY)}) === "1"`,
    "PWA_UPDATE_CONTROLLER_CHANGE_MISSING",
    UPDATE_TIMEOUT_MS,
  );
  await page.waitForApiIdle();
  const activated = await page.evaluate(`(async () => {
    const registration = await navigator.serviceWorker.getRegistration("/");
    const ownedCaches = (await caches.keys())
      .filter((name) => name.startsWith(${JSON.stringify(CACHE_PREFIX)}))
      .sort();
    const newCaches = ${JSON.stringify(waiting.newCaches)};
    const oldCaches = ${JSON.stringify(oldCaches)};
    const sentinel = await caches.open(${JSON.stringify(PWA_SENTINEL_CACHE)});
    const sentinelResponse = await sentinel.match(${JSON.stringify(PWA_SENTINEL_PATH)});
    const sentinelBody = sentinelResponse ? await sentinelResponse.text() : null;
    const oidcSessionKeyCount = Object.keys(sessionStorage)
      .filter((key) => key.startsWith("oidc.user:"))
      .length;
    return {
      active: registration?.active?.state ?? null,
      waiting: registration?.waiting?.state ?? null,
      controlled: Boolean(navigator.serviceWorker.controller),
      ownedCaches,
      oldCacheCount: oldCaches.filter((name) => ownedCaches.includes(name)).length,
      expectedNewCaches: newCaches.every((name) => ownedCaches.includes(name)),
      sentinelBody,
      sentinelCachePresent: (await caches.keys()).includes(${JSON.stringify(PWA_SENTINEL_CACHE)}),
      oidcSessionKeyCount,
      tenant: localStorage.getItem(${JSON.stringify(SELECTED_ENTERPRISE_KEY)}),
      controllerChanges: sessionStorage.getItem(${JSON.stringify(CONTROLLER_CHANGE_KEY)}),
    };
  })()`);
  if (
    !activated
    || activated.active !== "activated"
    || activated.waiting !== null
    || activated.controlled !== true
    || activated.controllerChanges !== "1"
    || !Array.isArray(activated.ownedCaches)
    || activated.ownedCaches.length !== waiting.newCaches.length
    || activated.oldCacheCount !== 0
    || activated.expectedNewCaches !== true
    || activated.sentinelCachePresent !== true
    || activated.sentinelBody !== "kept"
    || activated.oidcSessionKeyCount !== baseline.oidcSessionKeyCount
    || activated.tenant !== baseline.tenant
  ) {
    fail("PWA_UPDATE_ACTIVATION_INVALID");
  }
  markUnexpectedFailure("BROWSER_STAGE_EXECUTE_UNEXPECTED");
  return {
    waitingUpdates: 1,
    controllerChanges: 1,
    oldCachesRemoved: oldCaches.length,
    newCaches: waiting.newCaches.length,
    sentinelCachesPreserved: 1,
    loginStatesPreserved: 1,
    offline,
  };
}

async function verifyCredentialAtIdentityProvider(origin, username, password) {
  const form = new URLSearchParams({
    grant_type: "password",
    client_id: "anhuan-web",
    username,
    password,
  });
  let response;
  try {
    response = await fetch(`${origin}/realms/anhuan/protocol/openid-connect/token`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: form,
      redirect: "error",
      signal: AbortSignal.timeout(COMMAND_TIMEOUT_MS),
    });
  } catch {
    fail("OIDC_CREDENTIAL_PREFLIGHT_UNAVAILABLE");
  }
  if (response.status === 200) {
    await response.body?.cancel().catch(() => undefined);
    return;
  }
  let oauthError = "";
  try {
    const payload = await response.json();
    if (typeof payload?.error === "string" && /^[a-z_]{1,40}$/.test(payload.error)) {
      oauthError = payload.error;
    }
  } catch {
    // Only the bounded OAuth error enum is consumed; descriptions are ignored.
  }
  if (oauthError === "invalid_grant") fail("OIDC_CREDENTIAL_PREFLIGHT_REJECTED");
  if (oauthError === "unauthorized_client") fail("OIDC_CREDENTIAL_PREFLIGHT_UNSUPPORTED");
  if (oauthError === "invalid_client") fail("OIDC_CREDENTIAL_PREFLIGHT_CLIENT_INVALID");
  fail("OIDC_CREDENTIAL_PREFLIGHT_UNAVAILABLE");
}

async function runIdentity(cdp, origin, secretDirectory, identity, operation) {
  let owned;
  try {
    owned = await createPage(cdp, origin);
    let password = await readSecret(secretDirectory, identity.secret);
    try {
      await login(owned.page, identity.username, password);
    } finally {
      password = null;
    }
    return await operation(owned.page);
  } finally {
    await disposePage(cdp, owned);
  }
}

async function preflightIdentities(origin, secretDirectory) {
  const rejected = [];
  for (const identity of IDENTITIES) {
    let password = await readSecret(secretDirectory, identity.secret);
    try {
      await verifyCredentialAtIdentityProvider(origin, identity.username, password);
    } catch (error) {
      const code = error instanceof VerifyError ? error.code : "OIDC_CREDENTIAL_PREFLIGHT_UNAVAILABLE";
      rejected.push({ key: identity.key, code });
    } finally {
      password = null;
    }
  }
  if (rejected.length === 0) return;
  if (rejected.every(({ code }) => code === "OIDC_CREDENTIAL_PREFLIGHT_UNSUPPORTED")) {
    fail("OIDC_CREDENTIAL_PREFLIGHT_UNSUPPORTED");
  }
  if (rejected.every(({ code }) => code === "OIDC_CREDENTIAL_PREFLIGHT_CLIENT_INVALID")) {
    fail("OIDC_CREDENTIAL_PREFLIGHT_CLIENT_INVALID");
  }
  if (rejected.every(({ code }) => code === "OIDC_CREDENTIAL_PREFLIGHT_UNAVAILABLE")) {
    fail("OIDC_CREDENTIAL_PREFLIGHT_UNAVAILABLE");
  }
  if (rejected.length > 1) fail("OIDC_MULTIPLE_CREDENTIALS_REJECTED");
  const [{ key, code }] = rejected;
  if (code === "OIDC_CREDENTIAL_PREFLIGHT_UNSUPPORTED") fail("OIDC_CREDENTIAL_PREFLIGHT_UNSUPPORTED");
  if (code === "OIDC_CREDENTIAL_PREFLIGHT_CLIENT_INVALID") fail("OIDC_CREDENTIAL_PREFLIGHT_CLIENT_INVALID");
  if (code === "OIDC_CREDENTIAL_PREFLIGHT_UNAVAILABLE") fail("OIDC_CREDENTIAL_PREFLIGHT_UNAVAILABLE");
  if (key === "admin") fail("OIDC_ADMIN_CREDENTIAL_REJECTED");
  if (key === "auditor") fail("OIDC_AUDITOR_CREDENTIAL_REJECTED");
  if (key === "tenant") fail("OIDC_TENANT_CREDENTIAL_REJECTED");
  fail("OIDC_CREDENTIAL_PREFLIGHT_REJECTED");
}

async function verifyPwaInstallation(cdp, origin, installPwa) {
  let manifestId = null;
  let installed = false;
  let installabilityErrors = null;
  let auditTargetId = null;
  let auditPage = null;
  let launchedTargetId = null;
  let primaryError = null;
  try {
    const auditTarget = await cdp.call("Target.createTarget", { url: "about:blank" });
    if (typeof auditTarget?.targetId !== "string" || !auditTarget.targetId) {
      fail("PWA_INSTALLABILITY_AUDIT_UNAVAILABLE");
    }
    auditTargetId = auditTarget.targetId;
    const attached = await cdp.call("Target.attachToTarget", {
      targetId: auditTargetId,
      flatten: true,
    });
    if (typeof attached?.sessionId !== "string") {
      fail("PWA_INSTALLABILITY_AUDIT_UNAVAILABLE");
    }
    auditPage = new BrowserPage(cdp, attached.sessionId, origin);
    await auditPage.initialize();
    await auditPage.navigate("/login");
    await auditPage.waitForExpression(
      `navigator.serviceWorker.getRegistration("/").then((registration) => Boolean(registration?.active))`,
      "PWA_REGISTRATION_MISSING",
    );
    const installability = await auditPwaInstallability(auditPage);
    installabilityErrors = installability.installabilityErrors;
    manifestId = installability.manifestId;
    if (installPwa) {
      await cdp.call("PWA.install", {
        manifestId,
      }).catch(() => fail("PWA_INSTALL_FAILED"));
      installed = true;
      const launched = await cdp.call("PWA.launch", {
        manifestId,
        url: manifestId,
      }).catch(() => fail("PWA_INSTALL_LAUNCH_FAILED"));
      if (typeof launched?.targetId !== "string" || !launched.targetId) {
        fail("PWA_INSTALL_LAUNCH_FAILED");
      }
      launchedTargetId = launched.targetId;
      const target = await cdp.call("Target.getTargetInfo", {
        targetId: launchedTargetId,
      }).catch(() => fail("PWA_INSTALL_LAUNCH_FAILED"));
      const targetUrl = new URL(target?.targetInfo?.url ?? "about:blank");
      if (
        target?.targetInfo?.type !== "page"
        || targetUrl.origin !== origin
        || !targetUrl.pathname.startsWith("/internal-app")
      ) {
        fail("PWA_INSTALL_LAUNCH_FAILED");
      }
    }
  } catch (error) {
    primaryError = error;
  }
  let cleanupFailed = false;
  if (launchedTargetId) {
    const closed = await cdp.call("Target.closeTarget", {
      targetId: launchedTargetId,
    }).catch(() => null);
    cleanupFailed ||= closed?.success !== true;
  }
  if (installed && manifestId) {
    await cdp.call("PWA.uninstall", { manifestId }).catch(() => {
      cleanupFailed = true;
    });
  }
  auditPage?.close();
  if (auditTargetId) {
    const closed = await cdp.call("Target.closeTarget", {
      targetId: auditTargetId,
    }).catch(() => null);
    cleanupFailed ||= closed?.success !== true;
  }
  if (cleanupFailed) fail("PWA_INSTALL_CLEANUP_FAILED");
  if (primaryError) {
    if (primaryError instanceof VerifyError) throw primaryError;
    fail("PWA_INSTALL_FAILED");
  }
  if (!Number.isInteger(installabilityErrors)) fail("PWA_INSTALLABILITY_AUDIT_UNAVAILABLE");
  return { installations: installed ? 1 : 0, installabilityErrors };
}

async function execute(cdp, origin, secretDirectory, installPwa, controlDirectory) {
  const admin = await runIdentity(
    cdp,
    origin,
    secretDirectory,
    IDENTITIES[0],
    async (page) => {
      await visitAdminPages(page);
      const tenant = await verifyTenantSwitch(page);
      await page.navigate("/internal-app");
      await waitForApplicationShell(page);
      const pwa = await auditPwaCaches(page);
      const update = controlDirectory
        ? await verifyWaitingPwaUpdate(page, controlDirectory)
        : null;
      const offline = update?.offline ?? await verifyOfflineShell(page);
      if (page.apiNon2xx !== 0) fail("ADMIN_API_NON_2XX");
      return {
        pages: TOP_LEVEL_PAGES.length,
        apiResponses: page.apiResponses,
        apiNon2xx: page.apiNon2xx,
        tenant,
        pwa,
        offline,
        update,
      };
    },
  );
  const roleResults = {};
  for (const [role, contract] of Object.entries(ROLE_PAGE_CONTRACTS)) {
    const identity = IDENTITIES.find((candidate) => candidate.key === contract.identityKey);
    if (!identity) fail("ROLE_IDENTITY_CONTRACT_INVALID");
    roleResults[role] = await runIdentity(
      cdp,
      origin,
      secretDirectory,
      identity,
      (page) => visitRolePages(page, contract),
    );
  }
  const pwaInstallation = await verifyPwaInstallation(cdp, origin, installPwa);
  return {
    identities_authenticated: IDENTITIES.length,
    admin_pages_visited: admin.pages,
    admin_api_responses: admin.apiResponses,
    admin_api_non_2xx: admin.apiNon2xx,
    consultant_pages_visited: roleResults.consultant.pages,
    enterprise_pages_visited: roleResults.enterprise.pages,
    role_api_non_2xx:
      roleResults.consultant.apiNon2xx + roleResults.enterprise.apiNon2xx,
    tenant_header_changes: admin.tenant.headerChanges,
    tenant_state_clears: admin.tenant.stateClears,
    pwa_registrations: admin.pwa.registrations,
    pwa_controlled_clients: admin.pwa.controlled,
    pwa_owned_caches: admin.pwa.caches,
    pwa_sensitive_cache_entries: admin.pwa.sensitive,
    pwa_installability_errors: pwaInstallation.installabilityErrors,
    pwa_offline_shell: admin.offline,
    pwa_installations: pwaInstallation.installations,
    pwa_os_install_status: installPwa
      ? "PWA_OS_INSTALL_PASSED"
      : "PWA_OS_INSTALL_NOT_TESTED",
    pwa_waiting_updates: admin.update?.waitingUpdates ?? 0,
    pwa_controller_changes: admin.update?.controllerChanges ?? 0,
    pwa_old_caches_removed: admin.update?.oldCachesRemoved ?? 0,
    pwa_new_caches: admin.update?.newCaches ?? 0,
    pwa_sentinel_caches_preserved: admin.update?.sentinelCachesPreserved ?? 0,
    pwa_login_states_preserved: admin.update?.loginStatesPreserved ?? 0,
    pwa_update_status: admin.update
      ? "PWA_WAITING_UPDATE_PASSED"
      : "PWA_WAITING_UPDATE_NOT_TESTED",
  };
}

async function main() {
  const { origin, secretDirectory, headed, controlDirectory } = parseInputs();
  await validateSecretDirectory(secretDirectory);
  if (controlDirectory) await validateControlDirectory(controlDirectory);
  markUnexpectedFailure("BROWSER_STAGE_PREFLIGHT_UNEXPECTED");
  await preflightIdentities(origin, secretDirectory);
  markUnexpectedFailure("BROWSER_STAGE_LAUNCH_UNEXPECTED");
  const runtime = await launchChrome(headed);
  let signalCleanupStarted = false;
  const onSignal = () => {
    if (signalCleanupStarted) return;
    signalCleanupStarted = true;
    void cleanupChrome(runtime).finally(() => process.exit(130));
  };
  process.once("SIGINT", onSignal);
  process.once("SIGTERM", onSignal);
  let summary;
  try {
    runtime.cdp = await CdpConnection.connect(runtime.websocketUrl);
    markUnexpectedFailure("BROWSER_STAGE_EXECUTE_UNEXPECTED");
    summary = await execute(
      runtime.cdp,
      origin,
      secretDirectory,
      headed,
      controlDirectory,
    );
    markUnexpectedFailure("BROWSER_STAGE_EXECUTE_UNEXPECTED");
  } finally {
    process.removeListener("SIGINT", onSignal);
    process.removeListener("SIGTERM", onSignal);
    await cleanupChrome(runtime);
  }
  if (signalCleanupStarted) fail("BROWSER_VERIFY_INTERRUPTED");
  process.stdout.write(`${JSON.stringify(summary)}\nLOCAL_BROWSER_VERIFY_OK\n`);
}

main().catch((error) => {
  const code = error instanceof VerifyError ? error.code : unexpectedFailureReason;
  process.stderr.write(`LOCAL_BROWSER_VERIFY_FAILED ${code}\n`);
  process.exitCode = 1;
});
