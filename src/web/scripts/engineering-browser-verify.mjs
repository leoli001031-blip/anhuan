import { execFile, spawn } from "node:child_process";
import { constants } from "node:fs";
import {
  access,
  chmod,
  lstat,
  mkdir,
  mkdtemp,
  open,
  readFile,
  readdir,
  rm,
} from "node:fs/promises";
import os from "node:os";
import path from "node:path";

const CACHE_PREFIX = "anhuan-internal-pwa-";
const SELECTED_ENTERPRISE_KEY = "f1-selected-enterprise";
const PWA_SENTINEL_CACHE = "anhuan-engineering-pwa-update-sentinel";
const PWA_SENTINEL_PATH = "/pwa-update-sentinel";
const CONTROLLER_CHANGE_KEY = "anhuan-engineering-controller-changes";
const PWA_APPLY_CLICK_KEY = "anhuan-engineering-pwa-apply-clicks";
const LOCAL_DURABILITY_CASE_TITLE = "Local durability canary";
const SERVICE_CASE_CREATE_ACTION = "创建服务任务";
const ASSIGNMENT_CANDIDATE_ROLE = "auditor";
const ASSIGNMENT_CAPACITY = "consultant";
const BROWSER_READY_SIGNAL = "browser-ready";
const IMAGE_READY_SIGNAL = "image-b-ready";
const MINIO_FAULT_READY_SIGNAL = "minio-fault-ready";
const MINIO_STOPPED_SIGNAL = "minio-stopped";
const MINIO_503_OBSERVED_SIGNAL = "minio-503-observed";
const MINIO_RESTORED_SIGNAL = "minio-restored";
const CLAMD_FAULT_READY_SIGNAL = "clamd-fault-ready";
const CLAMD_STOPPED_SIGNAL = "clamd-stopped";
const CLAMD_UNAVAILABLE_OBSERVED_SIGNAL = "clamd-unavailable-observed";
const CLAMD_RESTORED_SIGNAL = "clamd-restored";
const PWA_OS_OFFLINE_READY_SIGNAL = "pwa-os-offline-ready";
const PWA_OS_WEB_STOPPED_SIGNAL = "pwa-os-web-stopped";
const PWA_OS_OFFLINE_OBSERVED_SIGNAL = "pwa-os-offline-observed";
const PWA_OS_WEB_RESTORED_SIGNAL = "pwa-os-web-restored";
const RUNNER_CONTROL_SIGNALS = new Set([
  BROWSER_READY_SIGNAL,
  MINIO_FAULT_READY_SIGNAL,
  MINIO_503_OBSERVED_SIGNAL,
  CLAMD_FAULT_READY_SIGNAL,
  CLAMD_UNAVAILABLE_OBSERVED_SIGNAL,
  PWA_OS_OFFLINE_READY_SIGNAL,
  PWA_OS_OFFLINE_OBSERVED_SIGNAL,
]);
const ALL_CONTROL_SIGNALS = new Set([
  ...RUNNER_CONTROL_SIGNALS,
  IMAGE_READY_SIGNAL,
  MINIO_STOPPED_SIGNAL,
  MINIO_RESTORED_SIGNAL,
  CLAMD_STOPPED_SIGNAL,
  CLAMD_RESTORED_SIGNAL,
  PWA_OS_WEB_STOPPED_SIGNAL,
  PWA_OS_WEB_RESTORED_SIGNAL,
]);
const CONTROL_SIGNAL = Buffer.from("ready\n", "ascii");
const INGESTION_UPLOAD_PATH = "/api/v1/ingestion/documents";
const INGESTION_CAPABILITIES_PATH = "/api/v1/ingestion/capabilities";
const MINIO_FAULT_DOCUMENT_NAME = "Engineering MinIO recovery canary";
const MINIO_FAULT_PDF_NAME = "engineering-minio-recovery-canary.pdf";
const COMMAND_TIMEOUT_MS = 15_000;
const WAIT_TIMEOUT_MS = 20_000;
const UPDATE_TIMEOUT_MS = 180_000;
const PWA_OS_COMMAND_TIMEOUT_MS = 60_000;
const PWA_OS_SHIM_TIMEOUT_MS = 20_000;
const PWA_APP_NAME = "安环内部工作台";
const RUN_STAGES = new Set(["all", "business", "faults", "pwa-update", "pwa-os"]);
const STAGE_SUCCESS_TAGS = Object.freeze({
  all: "LOCAL_BROWSER_VERIFY_OK",
  business: "LOCAL_BROWSER_BUSINESS_VERIFY_OK",
  faults: "LOCAL_BROWSER_FAULTS_VERIFY_OK",
  "pwa-update": "LOCAL_PWA_UPDATE_VERIFY_OK",
});
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
    actionCheck: Object.freeze({
      route: "/service-cases",
      protectedApiPath: "/api/v1/service-cases",
      actionText: SERVICE_CASE_CREATE_ACTION,
      visible: false,
      reasonKey: "CONSULTANT_SERVICE_CASE_CREATE",
    }),
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

class CdpCommandError extends VerifyError {
  constructor(category) {
    super("CDP_COMMAND_REJECTED");
    this.category = category;
  }
}

function classifyCdpCommandError(error) {
  if (error?.code === -32601) return "method_missing";
  const message = typeof error?.message === "string" ? error.message : "";
  if (
    message === "Webapps are not available in current profile."
    || message === "Web apps can't be installed in the current user profile."
  ) {
    return "profile_unavailable";
  }
  if (message === "Web app is not a valid installable web app.") {
    return "not_installable";
  }
  return "other";
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
  if (inputs.length < 2 || inputs.length > 8) fail("BROWSER_INPUT_INVALID");
  let headed = false;
  let installPwa = false;
  let controlDirectory = null;
  let stage = "all";
  let stageSpecified = false;
  for (let index = 2; index < inputs.length; index += 1) {
    if (inputs[index] === "--headed" && !headed) {
      headed = true;
      continue;
    }
    if (inputs[index] === "--install-pwa" && !installPwa) {
      installPwa = true;
      continue;
    }
    if (
      inputs[index] === "--stage"
      && !stageSpecified
      && index + 1 < inputs.length
    ) {
      stage = inputs[index + 1];
      stageSpecified = true;
      index += 1;
      if (!RUN_STAGES.has(stage)) fail("BROWSER_STAGE_INVALID");
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
  if (installPwa && controlDirectory === null) fail("PWA_OS_CONTROL_REQUIRED");
  if (["faults", "pwa-update", "pwa-os"].includes(stage) && controlDirectory === null) {
    fail("BROWSER_STAGE_CONTROL_REQUIRED");
  }
  if (stage === "pwa-os") fail("PWA_OS_BLOCKED_BY_BROWSER_AUTOMATION_BOUNDARY");
  if (installPwa && !["all", "pwa-os"].includes(stage)) {
    fail("BROWSER_STAGE_OPTION_INVALID");
  }
  return {
    origin: parsed.origin,
    secretDirectory: path.resolve(inputs[1]),
    headed,
    installPwa,
    stage,
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

async function writeControlSignal(
  directory,
  name,
  invalidCode = "PWA_UPDATE_CONTROL_SIGNAL_INVALID",
) {
  if (!RUNNER_CONTROL_SIGNALS.has(name)) {
    fail(invalidCode);
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
      fail(invalidCode);
    }
    await handle.sync();
  } catch (error) {
    if (error instanceof VerifyError) throw error;
    fail(invalidCode);
  } finally {
    await handle?.close().catch(() => undefined);
  }
}

async function controlSignalPresent(
  directory,
  name,
  invalidCode = "PWA_UPDATE_CONTROL_SIGNAL_INVALID",
) {
  if (!ALL_CONTROL_SIGNALS.has(name)) {
    fail(invalidCode);
  }
  let handle;
  try {
    handle = await open(
      path.join(directory, name),
      constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
    );
  } catch (error) {
    if (error?.code === "ENOENT") return false;
    fail(invalidCode);
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
      fail(invalidCode);
    }
    const bytes = await handle.readFile();
    const after = await handle.stat();
    if (
      !sameFile(before, after)
      || !bytes.equals(CONTROL_SIGNAL)
    ) {
      fail(invalidCode);
    }
    return true;
  } finally {
    await handle.close();
  }
}

async function waitForControlSignal(
  directory,
  name,
  timeoutCode = "PWA_UPDATE_CONTROL_TIMEOUT",
  invalidCode = "PWA_UPDATE_CONTROL_SIGNAL_INVALID",
) {
  const deadline = Date.now() + UPDATE_TIMEOUT_MS;
  while (Date.now() < deadline) {
    if (await controlSignalPresent(directory, name, invalidCode)) return;
    await delay(75);
  }
  fail(timeoutCode);
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

async function realUserHome() {
  if (process.platform !== "darwin") fail("PWA_OS_INSTALL_UNSUPPORTED");
  let user;
  try {
    user = os.userInfo();
  } catch {
    fail("PWA_OS_HOME_INVALID");
  }
  const home = path.resolve(user?.homedir ?? "");
  let info;
  try {
    info = await lstat(home);
  } catch {
    fail("PWA_OS_HOME_INVALID");
  }
  if (
    !path.isAbsolute(user?.homedir ?? "")
    || user?.uid !== process.geteuid()
    || home !== user.homedir
    || home === path.parse(home).root
    || info.isSymbolicLink()
    || !info.isDirectory()
    || info.uid !== process.geteuid()
    || (info.mode & 0o022) !== 0
  ) {
    fail("PWA_OS_HOME_INVALID");
  }
  return home;
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

async function launchChrome(headed, installPwa, controlDirectory) {
  const executable = await findChrome();
  const temporaryRoot = path.resolve(os.tmpdir());
  const systemHome = installPwa ? await realUserHome() : null;
  let profile;
  if (controlDirectory) {
    const controlName = path.basename(controlDirectory);
    if (
      path.dirname(controlDirectory) !== temporaryRoot
      || !/^pwa-update-[0-9a-f]{24}$/.test(controlName)
    ) {
      fail("CHROME_PROFILE_BOUNDARY_INVALID");
    }
    const probe = controlName.slice("pwa-update-".length);
    profile = path.join(temporaryRoot, `anhuan-engineering-browser-${probe}`);
    await mkdir(profile, { mode: 0o700 });
  } else {
    profile = await mkdtemp(path.join(temporaryRoot, "anhuan-engineering-browser-"));
  }
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
  if (headed || installPwa) {
    args.unshift("--window-position=-10000,-10000", "--window-size=1200,900");
  } else {
    args.unshift("--headless=new");
  }
  if (installPwa) args.unshift("--enable-devtools-pwa-handler");
  const child = spawn(executable, args, {
    detached: false,
    stdio: "ignore",
    env: {
      HOME: systemHome ?? profile,
      LANG: "C",
      PATH: process.env.PATH ?? "/usr/bin:/bin",
      TMPDIR: temporaryRoot,
    },
  });
  child.spawnFailed = false;
  child.on("error", () => { child.spawnFailed = true; });
  try {
    const websocketUrl = await waitForDevTools(profile, child);
    return {
      child,
      executable,
      profile,
      systemHome,
      temporaryRoot,
      websocketUrl,
    };
  } catch (error) {
    await cleanupChrome({ child, profile, temporaryRoot, cdp: null })
      .catch(() => undefined);
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
    child.kill(signal);
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
      if (message.error) {
        pending.reject(new CdpCommandError(classifyCdpCommandError(message.error)));
      }
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

function requestHeader(headers, expectedName) {
  for (const [name, value] of Object.entries(headers ?? {})) {
    if (name.toLowerCase() === expectedName && typeof value === "string") return value;
  }
  return null;
}

function tenantHeader(headers) {
  return requestHeader(headers, "x-enterprise-id");
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
    this.ingestionUploadRequests = [];
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
        if (
          apiPath(event.request?.url, this.origin) === INGESTION_UPLOAD_PATH
          && event.request?.method === "POST"
        ) {
          this.ingestionUploadRequests.push({
            idempotencyKey: requestHeader(
              event.request?.headers,
              "idempotency-key",
            ),
          });
        }
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

  async waitForApiStatus(
    pathname,
    eventBoundary,
    expectedStatus,
    code,
    timeout = WAIT_TIMEOUT_MS,
  ) {
    const deadline = Date.now() + timeout;
    while (Date.now() < deadline) {
      const events = this.apiResponseEvents
        .slice(eventBoundary)
        .filter((event) => event.path === pathname);
      if (events.some((event) => event.status === expectedStatus)) return events;
      if (events.some((event) => event.status !== null)) fail(code);
      await delay(50);
    }
    fail(code);
  }

  async clickElement(selector, code) {
    const deadline = Date.now() + WAIT_TIMEOUT_MS;
    let point = null;
    while (Date.now() < deadline) {
      point = await this.evaluate(`(() => {
        const elements = document.querySelectorAll(${JSON.stringify(selector)});
        for (const element of elements) {
          if (!(element instanceof HTMLElement)) continue;
          element.scrollIntoView({ block: "center", inline: "center" });
          const box = element.getBoundingClientRect();
          const style = getComputedStyle(element);
          const x = box.left + box.width / 2;
          const y = box.top + box.height / 2;
          const hit = document.elementFromPoint(x, y);
          if (
            box.width > 0
            && box.height > 0
            && style.display !== "none"
            && style.visibility !== "hidden"
            && style.pointerEvents !== "none"
            && x >= 0
            && y >= 0
            && x <= window.innerWidth
            && y <= window.innerHeight
            && hit instanceof Element
            && (hit === element || element.contains(hit))
          ) {
            return { x, y };
          }
        }
        return null;
      })()`);
      if (point && Number.isFinite(point.x) && Number.isFinite(point.y)) break;
      await delay(75);
    }
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

  async clickElementWithText(selector, textValue, code, { prefix = false } = {}) {
    const deadline = Date.now() + WAIT_TIMEOUT_MS;
    let point = null;
    while (Date.now() < deadline) {
      point = await this.evaluate(`(() => {
        const elements = document.querySelectorAll(${JSON.stringify(selector)});
        for (const element of elements) {
          if (!(element instanceof HTMLElement)) continue;
          const text = (element.textContent ?? "").trim();
          const matches = ${prefix ? "text.startsWith(" : "text === ("}${JSON.stringify(textValue)}${prefix ? ")" : ")"};
          if (!matches) continue;
          element.scrollIntoView({ block: "center", inline: "center" });
          const box = element.getBoundingClientRect();
          const style = getComputedStyle(element);
          const x = box.left + box.width / 2;
          const y = box.top + box.height / 2;
          const hit = document.elementFromPoint(x, y);
          if (
            box.width > 0
            && box.height > 0
            && style.display !== "none"
            && style.visibility !== "hidden"
            && style.pointerEvents !== "none"
            && x >= 0
            && y >= 0
            && x <= window.innerWidth
            && y <= window.innerHeight
            && hit instanceof Element
            && (hit === element || element.contains(hit))
          ) {
            return { x, y };
          }
        }
        return null;
      })()`);
      if (point && Number.isFinite(point.x) && Number.isFinite(point.y)) break;
      await delay(75);
    }
    if (!point || !Number.isFinite(point.x) || !Number.isFinite(point.y)) fail(code);
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

  async setFileInputFiles(selector, files, code) {
    if (
      !Array.isArray(files)
      || files.length !== 1
      || files.some((file) => typeof file !== "string" || !path.isAbsolute(file))
    ) {
      fail(code);
    }
    const documentNode = await this.cdp.call(
      "DOM.getDocument",
      { depth: -1, pierce: true },
      this.sessionId,
    ).catch(() => fail(code));
    const rootNodeId = documentNode?.root?.nodeId;
    if (!Number.isInteger(rootNodeId)) fail(code);
    const selected = await this.cdp.call(
      "DOM.querySelector",
      { nodeId: rootNodeId, selector },
      this.sessionId,
    ).catch(() => fail(code));
    if (!Number.isInteger(selected?.nodeId) || selected.nodeId < 1) fail(code);
    await this.cdp.call(
      "DOM.setFileInputFiles",
      { nodeId: selected.nodeId, files },
      this.sessionId,
    ).catch(() => fail(code));
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

async function verifyActionVisibility(page, actionText, visible, reasonKey) {
  const expression = `Array.from(document.querySelectorAll(".ant-layout-content button"))
    .some((button) => {
      if ((button.textContent ?? "").trim() !== ${JSON.stringify(actionText)}) return false;
      const box = button.getBoundingClientRect();
      const style = getComputedStyle(button);
      return box.width > 0 && box.height > 0 && style.display !== "none" && style.visibility !== "hidden";
    })`;
  if (visible) {
    await page.waitForExpression(expression, `${reasonKey}_VISIBLE_ACTION_MISSING`);
  } else if (await page.evaluate(expression)) {
    fail(`${reasonKey}_HIDDEN_ACTION_VISIBLE`);
  }
  return 1;
}

async function visitAdminPages(page) {
  await page.waitForExpression(
    `Boolean(localStorage.getItem(${JSON.stringify(SELECTED_ENTERPRISE_KEY)}))`,
    "ADMIN_TENANT_CONTEXT_MISSING",
  );
  let allowedActionUiChecks = 0;
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
    if (route === "/service-cases") {
      allowedActionUiChecks += await verifyActionVisibility(
        page,
        SERVICE_CASE_CREATE_ACTION,
        true,
        "ADMIN_SERVICE_CASE_CREATE",
      );
    }
  }
  if (page.apiNon2xx !== 0) fail("ADMIN_API_NON_2XX");
  return { allowedActionUiChecks };
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
  let allowedActionUiChecks = 0;
  if (contract.actionCheck) {
    const actionBoundary = page.apiResponseEvents.length;
    const failuresBeforeActionCheck = page.apiNon2xx;
    await page.navigate(contract.actionCheck.route);
    await waitForApplicationShell(page);
    const events = await page.waitForProtectedApi(
      contract.actionCheck.protectedApiPath,
      actionBoundary,
      `${contract.actionCheck.reasonKey}_API_EVIDENCE_MISSING`,
    );
    if (page.apiNon2xx !== failuresBeforeActionCheck) {
      const statuses = Array.from(
        new Set(events.map((event) => event.status).filter((status) => Number.isInteger(status))),
      );
      if (
        statuses.length === 1
        && [401, 403, 404, 409, 422, 500, 503].includes(statuses[0])
      ) {
        fail(`${contract.actionCheck.reasonKey}_API_${statuses[0]}`);
      }
      fail(`${contract.actionCheck.reasonKey}_API_NON_2XX`);
    }
    allowedActionUiChecks += await verifyActionVisibility(
      page,
      contract.actionCheck.actionText,
      contract.actionCheck.visible,
      contract.actionCheck.reasonKey,
    );
  }
  return {
    pages: pagesVisited,
    apiNon2xx: page.apiNon2xx - apiNon2xxBefore,
    allowedActionUiChecks,
  };
}

function syntheticPdfBytes() {
  const objects = [
    "1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n",
    "2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n",
    "3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 72 72] >>\nendobj\n",
  ];
  let document = "%PDF-1.4\n";
  const offsets = [];
  for (const object of objects) {
    offsets.push(Buffer.byteLength(document, "ascii"));
    document += object;
  }
  const xrefOffset = Buffer.byteLength(document, "ascii");
  document += "xref\n0 4\n0000000000 65535 f \n";
  for (const offset of offsets) {
    document += `${String(offset).padStart(10, "0")} 00000 n \n`;
  }
  document += `trailer\n<< /Size 4 /Root 1 0 R >>\nstartxref\n${xrefOffset}\n%%EOF\n`;
  return Buffer.from(document, "ascii");
}

async function createSyntheticPdfArtifact(controlDirectory) {
  const temporaryRoot = path.resolve(os.tmpdir());
  const controlName = path.basename(controlDirectory ?? "");
  if (
    path.dirname(controlDirectory ?? "") !== temporaryRoot
    || !/^pwa-update-[0-9a-f]{24}$/.test(controlName)
  ) {
    fail("MINIO_FAULT_PDF_CREATE_FAILED");
  }
  const probe = controlName.slice("pwa-update-".length);
  let directory = null;
  let createdDirectory = false;
  let handle;
  try {
    directory = path.join(temporaryRoot, `anhuan-minio-fault-${probe}`);
    await mkdir(directory, { mode: 0o700 });
    createdDirectory = true;
    const file = path.join(directory, MINIO_FAULT_PDF_NAME);
    const bytes = syntheticPdfBytes();
    handle = await open(
      file,
      constants.O_WRONLY | constants.O_CREAT | constants.O_EXCL | constants.O_NOFOLLOW,
      0o600,
    );
    await handle.writeFile(bytes);
    await handle.sync();
    const info = await handle.stat();
    if (
      !info.isFile()
      || info.nlink !== 1
      || info.uid !== process.geteuid()
      || (info.mode & 0o777) !== 0o600
      || info.size !== bytes.length
    ) {
      fail("MINIO_FAULT_PDF_CREATE_FAILED");
    }
    await handle.close();
    handle = null;
    return { directory, file, size: bytes.length, temporaryRoot };
  } catch (error) {
    await handle?.close().catch(() => undefined);
    if (
      createdDirectory
      && directory
      && path.dirname(directory) === temporaryRoot
      && /^anhuan-minio-fault-[0-9a-f]{24}$/.test(path.basename(directory))
    ) {
      await rm(directory, { recursive: true, force: true }).catch(() => undefined);
    }
    if (error instanceof VerifyError) throw error;
    fail("MINIO_FAULT_PDF_CREATE_FAILED");
  }
}

async function cleanupSyntheticPdfArtifact(artifact) {
  if (
    !artifact
    || path.dirname(artifact.directory) !== artifact.temporaryRoot
    || !/^anhuan-minio-fault-[0-9a-f]{24}$/.test(path.basename(artifact.directory))
    || artifact.file !== path.join(artifact.directory, MINIO_FAULT_PDF_NAME)
  ) {
    fail("MINIO_FAULT_PDF_CLEANUP_FAILED");
  }
  await rm(artifact.directory, { recursive: true, force: false })
    .catch(() => fail("MINIO_FAULT_PDF_CLEANUP_FAILED"));
}

async function verifyMinio503Recovery(page, controlDirectory) {
  if (!controlDirectory) {
    return {
      expectedNon2xx: 0,
      serviceUnavailable503Ui: 0,
      status: "NOT_TESTED_ORCHESTRATION_REQUIRED",
    };
  }
  let artifact;
  let primaryError = null;
  try {
    artifact = await createSyntheticPdfArtifact(controlDirectory);
    await page.navigate("/service-cases");
    await waitForApplicationShell(page);
    await page.waitForExpression(
      `Array.from(document.querySelectorAll(".ant-table-tbody button"))
        .some((button) => (button.textContent ?? "").trim() === ${JSON.stringify(LOCAL_DURABILITY_CASE_TITLE)})`,
      "MINIO_FAULT_TENANT_A_REQUIRED",
    );
    await page.navigate("/controlled-documents");
    await waitForApplicationShell(page);
    await page.waitForExpression(
      `Array.from(document.querySelectorAll(".ant-layout-content h1, .ant-layout-content h2, .ant-layout-content h3"))
        .some((heading) => (heading.textContent ?? "").trim() === "受控文档库")`,
      "MINIO_FAULT_DOCUMENT_LIBRARY_MISSING",
    );
    await page.clickElementWithText(
      ".ant-layout-content button",
      "新建文档",
      "MINIO_FAULT_UPLOAD_TRIGGER_MISSING",
    );
    await page.waitForExpression(
      `Boolean(document.querySelector(".ant-modal")
        && Array.from(document.querySelectorAll(".ant-modal .ant-modal-title"))
          .some((title) => (title.textContent ?? "").trim() === "新建文档"))`,
      "MINIO_FAULT_UPLOAD_MODAL_MISSING",
    );
    const displayNameFilled = await page.evaluate(`(() => {
      const input = document.querySelector('.ant-modal input[placeholder="例如：季度检查记录"]');
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value")?.set;
      if (!(input instanceof HTMLInputElement) || !setter) return false;
      setter.call(input, ${JSON.stringify(MINIO_FAULT_DOCUMENT_NAME)});
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.dispatchEvent(new Event("change", { bubbles: true }));
      return true;
    })()`);
    if (!displayNameFilled) fail("MINIO_FAULT_DISPLAY_NAME_INPUT_MISSING");
    await page.setFileInputFiles(
      '.ant-modal input[type="file"]',
      [artifact.file],
      "MINIO_FAULT_FILE_INPUT_MISSING",
    );
    // rc-upload copies the selected File into its controlled fileList and then
    // clears the native input so the same path can be selected again.  The
    // exact list item plus the later server-side preflight/503 is the stable
    // end-to-end evidence; input.files is intentionally not persistent.
    await page.waitForExpression(
      `Array.from(document.querySelectorAll(".ant-modal .ant-upload-list-item-name"))
        .some((item) => (item.textContent ?? "").trim() === ${JSON.stringify(MINIO_FAULT_PDF_NAME)})`,
      "MINIO_FAULT_UPLOAD_LIST_INVALID",
    );
    await page.waitForExpression(
      `Array.from(document.querySelectorAll(".ant-modal button"))
        .some((button) => (button.textContent ?? "").trim() === "上传到隔离区" && !button.disabled)`,
      "MINIO_FAULT_UPLOAD_BUTTON_DISABLED",
    );

    await writeControlSignal(
      controlDirectory,
      MINIO_FAULT_READY_SIGNAL,
      "MINIO_FAULT_CONTROL_SIGNAL_INVALID",
    );
    await waitForControlSignal(
      controlDirectory,
      MINIO_STOPPED_SIGNAL,
      "MINIO_FAULT_STOP_TIMEOUT",
      "MINIO_FAULT_CONTROL_SIGNAL_INVALID",
    );
    const non2xxBefore = page.apiNon2xx;
    const firstResponseBoundary = page.apiResponseEvents.length;
    const uploadRequestBoundary = page.ingestionUploadRequests.length;
    await page.clickElementWithText(
      ".ant-modal button",
      "上传到隔离区",
      "MINIO_FAULT_UPLOAD_BUTTON_MISSING",
    );
    const unavailableEvents = await page.waitForApiStatus(
      INGESTION_UPLOAD_PATH,
      firstResponseBoundary,
      503,
      "MINIO_FAULT_503_API_MISSING",
      UPDATE_TIMEOUT_MS,
    );
    await page.waitForApiIdle();
    if (
      unavailableEvents.length !== 1
      || unavailableEvents[0].status !== 503
      || page.apiNon2xx - non2xxBefore !== 1
    ) {
      fail("MINIO_FAULT_503_API_INVALID");
    }
    await page.waitForExpression(
      `Array.from(document.querySelectorAll(".ant-modal .ant-alert-error"))
        .some((alert) => (alert.textContent ?? "").trim().length > 0)
        && Array.from(document.querySelectorAll(".ant-modal button"))
          .some((button) => (button.textContent ?? "").trim() === "上传到隔离区" && !button.disabled)`,
      "MINIO_FAULT_503_RETRY_UI_MISSING",
    );
    await writeControlSignal(
      controlDirectory,
      MINIO_503_OBSERVED_SIGNAL,
      "MINIO_FAULT_CONTROL_SIGNAL_INVALID",
    );
    await waitForControlSignal(
      controlDirectory,
      MINIO_RESTORED_SIGNAL,
      "MINIO_FAULT_RESTORE_TIMEOUT",
      "MINIO_FAULT_CONTROL_SIGNAL_INVALID",
    );

    const retryResponseBoundary = page.apiResponseEvents.length;
    await page.clickElementWithText(
      ".ant-modal button",
      "上传到隔离区",
      "MINIO_FAULT_RETRY_BUTTON_MISSING",
    );
    const retryEvents = await page.waitForApiStatus(
      INGESTION_UPLOAD_PATH,
      retryResponseBoundary,
      202,
      "MINIO_FAULT_RETRY_API_MISSING",
      UPDATE_TIMEOUT_MS,
    );
    await page.waitForApiIdle();
    if (
      retryEvents.length !== 1
      || retryEvents[0].status !== 202
      || page.apiNon2xx - non2xxBefore !== 1
    ) {
      fail("MINIO_FAULT_RETRY_API_INVALID");
    }
    const uploadRequests = page.ingestionUploadRequests.slice(uploadRequestBoundary);
    if (
      uploadRequests.length !== 2
      || typeof uploadRequests[0].idempotencyKey !== "string"
      || !/^[\x21-\x7e]{8,128}$/.test(uploadRequests[0].idempotencyKey)
      || uploadRequests[0].idempotencyKey !== uploadRequests[1].idempotencyKey
    ) {
      fail("MINIO_FAULT_IDEMPOTENCY_MISMATCH");
    }
    await page.waitForExpression(
      `/^\\/controlled-documents\\/[0-9a-f-]{36}$/i.test(location.pathname)
        && !document.querySelector(".ant-modal")
        && Array.from(document.querySelectorAll(".ant-layout-content h1, .ant-layout-content h2, .ant-layout-content h3"))
          .some((heading) => (heading.textContent ?? "").trim() === ${JSON.stringify(MINIO_FAULT_DOCUMENT_NAME)})`,
      "MINIO_FAULT_RETRY_UI_FAILED",
    );
    return {
      expectedNon2xx: 1,
      serviceUnavailable503Ui: 1,
      status: "PASSED",
    };
  } catch (error) {
    primaryError = error;
    throw error;
  } finally {
    if (artifact) {
      try {
        await cleanupSyntheticPdfArtifact(artifact);
      } catch (cleanupError) {
        if (!primaryError) throw cleanupError;
      }
    }
  }
}

async function waitForScannerUiState(page, state, code) {
  if (!["ready", "unavailable"].includes(state)) fail(code);
  const expectedCopy = state === "ready" ? "本地扫描可用" : "本地扫描不可用";
  const forbiddenCopy = state === "ready" ? "本地扫描不可用" : "本地扫描可用";
  await page.waitForExpression(`(() => {
    const card = Array.from(document.querySelectorAll(".ant-layout-content .ant-card"))
      .find((candidate) => {
        const title = candidate.querySelector(".ant-card-head-title");
        return (title?.textContent ?? "").trim() === "受控导入能力";
      });
    if (!(card instanceof HTMLElement)) return false;
    const tags = Array.from(card.querySelectorAll(".ant-tag"))
      .map((tag) => (tag.textContent ?? "").trim());
    const alertCopies = Array.from(card.querySelectorAll('[role="alert"]'))
      .map((alert) => (alert.textContent ?? "").replace(/\\s+/g, " ").trim());
    const hasExpectedTag = tags.includes(${JSON.stringify(expectedCopy)});
    const hasForbiddenCopy = Array.from(card.querySelectorAll(".ant-tag, .ant-alert-message"))
      .some((element) => (element.textContent ?? "").trim() === ${JSON.stringify(forbiddenCopy)});
    if (${JSON.stringify(state)} === "unavailable") {
      return hasExpectedTag
        && alertCopies.some((copy) => copy.includes("本地扫描不可用")
          && copy.includes("新文件会继续留在隔离区，不会降级放行。"))
        && !hasForbiddenCopy;
    }
    return hasExpectedTag
      && !alertCopies.some((copy) => copy.includes("本地扫描不可用"))
      && !hasForbiddenCopy;
  })()`, code);
}

async function refreshScannerCapabilities(
  page,
  expectedState,
  {
    apiMissingCode,
    apiInvalidCode,
    buttonMissingCode,
    uiMissingCode,
  },
) {
  const responseBoundary = page.apiResponseEvents.length;
  const non2xxBefore = page.apiNon2xx;
  await page.waitForExpression(
    `(() => {
      const button = document.querySelector('[data-testid="ingestion-refresh"]');
      return button instanceof HTMLButtonElement && !button.disabled;
    })()`,
    buttonMissingCode,
  );
  await page.clickElement(
    '[data-testid="ingestion-refresh"]',
    buttonMissingCode,
  );
  const events = await page.waitForApiStatus(
    INGESTION_CAPABILITIES_PATH,
    responseBoundary,
    200,
    apiMissingCode,
  );
  await page.waitForApiIdle();
  if (
    events.length !== 1
    || events[0].status !== 200
    || page.apiNon2xx !== non2xxBefore
  ) {
    fail(apiInvalidCode);
  }
  await waitForScannerUiState(page, expectedState, uiMissingCode);
}

async function verifyClamdUnavailableRecovery(page, controlDirectory) {
  if (!controlDirectory) {
    return { unavailableUi: 0, recoveryUi: 0, expectedNon2xx: 0 };
  }
  const initialBoundary = page.apiResponseEvents.length;
  const initialNon2xx = page.apiNon2xx;
  await page.navigate("/controlled-documents");
  await waitForApplicationShell(page);
  const initialEvents = await page.waitForApiStatus(
    INGESTION_CAPABILITIES_PATH,
    initialBoundary,
    200,
    "CLAMD_FAULT_INITIAL_CAPABILITIES_API_MISSING",
  );
  if (
    initialEvents.length !== 1
    || initialEvents[0].status !== 200
    || page.apiNon2xx !== initialNon2xx
  ) {
    fail("CLAMD_FAULT_INITIAL_CAPABILITIES_API_INVALID");
  }
  await waitForScannerUiState(
    page,
    "ready",
    "CLAMD_FAULT_INITIAL_READY_UI_MISSING",
  );

  await writeControlSignal(
    controlDirectory,
    CLAMD_FAULT_READY_SIGNAL,
    "CLAMD_FAULT_CONTROL_SIGNAL_INVALID",
  );
  await waitForControlSignal(
    controlDirectory,
    CLAMD_STOPPED_SIGNAL,
    "CLAMD_FAULT_STOP_TIMEOUT",
    "CLAMD_FAULT_CONTROL_SIGNAL_INVALID",
  );
  await refreshScannerCapabilities(page, "unavailable", {
    apiMissingCode: "CLAMD_FAULT_UNAVAILABLE_API_MISSING",
    apiInvalidCode: "CLAMD_FAULT_UNAVAILABLE_API_INVALID",
    buttonMissingCode: "CLAMD_FAULT_UNAVAILABLE_REFRESH_MISSING",
    uiMissingCode: "CLAMD_FAULT_UNAVAILABLE_UI_MISSING",
  });
  await writeControlSignal(
    controlDirectory,
    CLAMD_UNAVAILABLE_OBSERVED_SIGNAL,
    "CLAMD_FAULT_CONTROL_SIGNAL_INVALID",
  );
  await waitForControlSignal(
    controlDirectory,
    CLAMD_RESTORED_SIGNAL,
    "CLAMD_FAULT_RESTORE_TIMEOUT",
    "CLAMD_FAULT_CONTROL_SIGNAL_INVALID",
  );
  await refreshScannerCapabilities(page, "ready", {
    apiMissingCode: "CLAMD_FAULT_RECOVERY_API_MISSING",
    apiInvalidCode: "CLAMD_FAULT_RECOVERY_API_INVALID",
    buttonMissingCode: "CLAMD_FAULT_RECOVERY_REFRESH_MISSING",
    uiMissingCode: "CLAMD_FAULT_RECOVERY_UI_MISSING",
  });
  return { unavailableUi: 1, recoveryUi: 1, expectedNon2xx: 0 };
}

async function openLocalDurabilityCase(page) {
  const detailBoundary = page.apiResponseEvents.length;
  await page.navigate("/service-cases");
  await waitForApplicationShell(page);
  await page.waitForExpression(
    `Array.from(document.querySelectorAll(".ant-table-tbody button"))
      .some((button) => (button.textContent ?? "").trim() === ${JSON.stringify(LOCAL_DURABILITY_CASE_TITLE)})`,
    "LOCAL_DURABILITY_CASE_MISSING",
  );
  await page.clickElementWithText(
    ".ant-table-tbody button",
    LOCAL_DURABILITY_CASE_TITLE,
    "LOCAL_DURABILITY_CASE_OPEN_FAILED",
  );
  await page.waitForExpression(
    `/^\\/service-cases\\/[0-9a-f-]{36}$/i.test(location.pathname)`,
    "LOCAL_DURABILITY_CASE_ROUTE_INVALID",
  );
  await waitForApplicationShell(page);
  const detailRoute = await page.evaluate("location.pathname");
  if (
    typeof detailRoute !== "string"
    || !/^\/service-cases\/[0-9a-f-]{36}$/i.test(detailRoute)
  ) {
    fail("LOCAL_DURABILITY_CASE_ROUTE_INVALID");
  }
  page.currentRoute = detailRoute;
  const detailEvents = await page.waitForApiStatus(
    `/api/v1${detailRoute}`,
    detailBoundary,
    200,
    "LOCAL_DURABILITY_CASE_API_INVALID",
  );
  if (detailEvents.some((event) => event.status !== 200)) {
    fail("LOCAL_DURABILITY_CASE_API_INVALID");
  }
  await page.waitForExpression(
    `Array.from(document.querySelectorAll(".ant-layout-content h1, .ant-layout-content h2, .ant-layout-content h3"))
      .some((heading) => (heading.textContent ?? "").trim() === ${JSON.stringify(LOCAL_DURABILITY_CASE_TITLE)})`,
    "LOCAL_DURABILITY_CASE_DETAIL_MISSING",
  );
  return detailRoute;
}

async function fillAssignmentCanaryForm(page) {
  await page.clickElement(
    '[data-testid="assignment-candidate-select"]',
    "ASSIGNMENT_CANDIDATE_SELECT_MISSING",
  );
  await page.waitForExpression(
    `Array.from(document.querySelectorAll(".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option"))
      .some((option) => (option.textContent ?? "").trim().startsWith(${JSON.stringify(`${ASSIGNMENT_CANDIDATE_ROLE} ·`)}))`,
    "ASSIGNMENT_CANDIDATE_OPTION_MISSING",
  );
  await page.clickElementWithText(
    ".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option",
    `${ASSIGNMENT_CANDIDATE_ROLE} ·`,
    "ASSIGNMENT_CANDIDATE_OPTION_MISSING",
    { prefix: true },
  );
  await page.waitForExpression(
    `(() => {
      const select = document.querySelector(
        '[data-testid="assignment-capacity-select"]',
      );
      return select instanceof HTMLElement
        && !select.classList.contains("ant-select-disabled");
    })()`,
    "ASSIGNMENT_CAPACITY_SELECT_DISABLED",
  );
  await page.clickElement(
    '[data-testid="assignment-capacity-select"]',
    "ASSIGNMENT_CAPACITY_SELECT_MISSING",
  );
  await page.waitForExpression(
    `Array.from(document.querySelectorAll(".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option"))
      .some((option) => (option.textContent ?? "").trim() === ${JSON.stringify(ASSIGNMENT_CAPACITY)})`,
    "ASSIGNMENT_CAPACITY_OPTION_MISSING",
  );
  await page.clickElementWithText(
    ".ant-select-dropdown:not(.ant-select-dropdown-hidden) .ant-select-item-option",
    ASSIGNMENT_CAPACITY,
    "ASSIGNMENT_CAPACITY_OPTION_MISSING",
  );
  await page.waitForExpression(
    `Array.from(document.querySelectorAll(".ant-drawer button"))
      .some((button) => (button.textContent ?? "").trim() === "确认分配" && !button.disabled)`,
    "ASSIGNMENT_SUBMIT_DISABLED",
  );
}

async function submitAssignmentCanary(page, assignmentApiPath) {
  await fillAssignmentCanaryForm(page);
  const eventBoundary = page.apiResponseEvents.length;
  await page.clickElementWithText(
    ".ant-drawer button",
    "确认分配",
    "ASSIGNMENT_SUBMIT_MISSING",
  );
  const events = await page.waitForProtectedApi(
    assignmentApiPath,
    eventBoundary,
    "ASSIGNMENT_SUBMIT_API_MISSING",
  );
  await page.waitForApiIdle();
  const statuses = events
    .map((event) => event.status)
    .filter((status) => Number.isInteger(status));
  if (statuses.length !== 1 || ![201, 409].includes(statuses[0])) {
    fail("ASSIGNMENT_SUBMIT_API_INVALID");
  }
  return statuses[0];
}

async function verifyIllegalAssignmentStateUi(page, detailRoute) {
  const candidateBoundary = page.apiResponseEvents.length;
  await page.clickElementWithText(
    ".ant-layout-content button",
    "管理分配",
    "ASSIGNMENT_DRAWER_TRIGGER_MISSING",
  );
  await page.waitForExpression(
    `Boolean(document.querySelector(".ant-drawer")
      && Array.from(document.querySelectorAll(".ant-drawer h1, .ant-drawer h2, .ant-drawer h3, .ant-drawer h4, .ant-drawer h5, .ant-drawer .ant-drawer-title"))
        .some((heading) => (heading.textContent ?? "").trim() === "人员分配"))`,
    "ASSIGNMENT_DRAWER_MISSING",
  );
  await page.waitForProtectedApi(
    "/api/v1/service-cases/assignment-candidates",
    candidateBoundary,
    "ASSIGNMENT_CANDIDATE_API_MISSING",
  );
  await page.waitForExpression(
    `Boolean(document.querySelector(".ant-drawer form"))`,
    "ASSIGNMENT_FORM_MISSING",
  );
  const non2xxBefore = page.apiNon2xx;
  const assignmentApiPath = `/api/v1${detailRoute}/assignments`;
  let status = await submitAssignmentCanary(page, assignmentApiPath);
  if (status === 201) {
    await page.waitForExpression(
      `Boolean(document.querySelector(".ant-drawer form"))`,
      "ASSIGNMENT_CANARY_REFRESH_FAILED",
    );
    status = await submitAssignmentCanary(page, assignmentApiPath);
  }
  if (status !== 409 || page.apiNon2xx - non2xxBefore !== 1) {
    fail("ASSIGNMENT_ILLEGAL_STATE_409_MISSING");
  }
  await page.waitForExpression(
    `Array.from(document.querySelectorAll(".ant-drawer .ant-alert-error"))
      .some((alert) => {
        const text = alert.textContent ?? "";
        return text.includes("操作未完成") && text.includes("ACTIVE_ASSIGNMENT_EXISTS");
      })`,
    "ASSIGNMENT_ILLEGAL_STATE_UI_MISSING",
  );
  return { illegalState409Ui: 1, expectedNon2xx: 1 };
}

async function verifyTenantSwitch(page) {
  const oldRequestBoundary = page.tenantRequests.length;
  const detailRoute = await openLocalDurabilityCase(page);
  const assignmentFault = await verifyIllegalAssignmentStateUi(page, detailRoute);
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
  const crossTenantFailureBefore = page.apiNon2xx;
  const crossTenantBoundary = page.apiResponseEvents.length;
  await page.navigate(detailRoute);
  await waitForApplicationShell(page);
  const crossTenantEvents = await page.waitForApiStatus(
    `/api/v1${detailRoute}`,
    crossTenantBoundary,
    404,
    "CROSS_TENANT_DETAIL_404_MISSING",
  );
  if (
    crossTenantEvents.length !== 1
    || crossTenantEvents[0].status !== 404
    || page.apiNon2xx - crossTenantFailureBefore !== 1
  ) {
    fail("CROSS_TENANT_DETAIL_404_INVALID");
  }
  try {
    await page.waitForExpression(
      `(() => {
        const shell = document.querySelector('[data-testid="service-case-not-found"]');
        const retry = Array.from(shell?.querySelectorAll("button") ?? [])
          .find((button) => (button.textContent ?? "").replace(/\\s+/g, "") === "重试");
        return shell instanceof HTMLElement
          && (shell.textContent ?? "").includes("无法打开服务任务")
          && retry instanceof HTMLButtonElement
          && (retry.textContent ?? "").replace(/\\s+/g, "") === "重试";
      })()`,
      "CROSS_TENANT_DETAIL_404_UI_MISSING",
    );
  } catch (reason) {
    if (!(reason instanceof VerifyError) || reason.code !== "CROSS_TENANT_DETAIL_404_UI_MISSING") {
      throw reason;
    }
    const diagnosis = await page.evaluate(`(() => {
      const shell = document.querySelector('[data-testid="service-case-not-found"]');
      const retry = Array.from(shell?.querySelectorAll("button") ?? [])
        .find((button) => (button.textContent ?? "").replace(/\\s+/g, "") === "重试");
      const content = document.querySelector(".ant-layout-content");
      return {
        routeMatches: location.pathname === ${JSON.stringify(detailRoute)},
        loading: Boolean(content?.querySelector(".ant-spin")),
        oldContent: (content?.textContent ?? "").includes(${JSON.stringify(LOCAL_DURABILITY_CASE_TITLE)}),
        shell: shell instanceof HTMLElement,
        message: (shell?.textContent ?? "").includes("无法打开服务任务"),
        retry: retry instanceof HTMLButtonElement
          && (retry.textContent ?? "").replace(/\\s+/g, "") === "重试",
      };
    })()`);
    if (!diagnosis || typeof diagnosis !== "object") {
      fail("CROSS_TENANT_DETAIL_404_UI_STATE_INVALID");
    }
    if (!diagnosis.routeMatches) fail("CROSS_TENANT_DETAIL_404_ROUTE_MISMATCH");
    if (diagnosis.loading) fail("CROSS_TENANT_DETAIL_404_LOADING_STUCK");
    if (diagnosis.oldContent) fail("CROSS_TENANT_DETAIL_404_OLD_CONTENT_RETAINED");
    if (!diagnosis.shell) fail("CROSS_TENANT_DETAIL_404_SHELL_MISSING");
    if (!diagnosis.message) fail("CROSS_TENANT_DETAIL_404_MESSAGE_MISSING");
    if (!diagnosis.retry) fail("CROSS_TENANT_DETAIL_404_RETRY_MISSING");
    throw reason;
  }
  return {
    headerChanges: 1,
    stateClears: 1,
    crossTenant404Ui: 1,
    illegalState409Ui: assignmentFault.illegalState409Ui,
    expectedNon2xx: assignmentFault.expectedNon2xx + 1,
  };
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
    if (window !== window.top) return true;
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
  await page.navigate("/internal-app");
  const controllerProbeReady = await page.evaluate(`window === window.top
    && window.__anhuanEngineeringControllerProbe === true
    && sessionStorage.getItem(${JSON.stringify(CONTROLLER_CHANGE_KEY)}) === "0"`);
  if (!controllerProbeReady) {
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
    `(() => {
      const button = document.querySelector('[data-testid="pwa-apply-update"]');
      return button instanceof HTMLButtonElement
        && !button.disabled
        && !button.classList.contains("ant-btn-loading");
    })()`,
    "PWA_UPDATE_CONFIRMATION_NOT_READY",
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

  const applyClickCaptureReady = await page.evaluate(`(() => {
    if (window !== window.top) return false;
    const selector = '[data-testid="pwa-apply-update"]';
    const button = document.querySelector(selector);
    if (!(button instanceof HTMLButtonElement) || button.disabled) return false;
    const key = ${JSON.stringify(PWA_APPLY_CLICK_KEY)};
    sessionStorage.setItem(key, "0");
    if (!window.__anhuanEngineeringPwaApplyClickCapture) {
      window.__anhuanEngineeringPwaApplyClickCapture = true;
      document.addEventListener("click", (event) => {
        if (!event.isTrusted) return;
        const matched = event.target instanceof Element
          ? event.target.closest(selector)
          : null;
        if (!(matched instanceof HTMLButtonElement)) return;
        const previous = Number(sessionStorage.getItem(key) ?? "0");
        sessionStorage.setItem(
          key,
          String(Number.isInteger(previous) ? previous + 1 : 1),
        );
      }, true);
    }
    return true;
  })()`);
  if (!applyClickCaptureReady) fail("PWA_UPDATE_APPLY_CAPTURE_FAILED");
  await page.clickElement(
    '[data-testid="pwa-apply-update"]',
    "PWA_UPDATE_CONFIRMATION_MISSING",
  );
  markUnexpectedFailure("PWA_STAGE_ACTIVATION_UNEXPECTED");
  await page.waitForExpression(
    `sessionStorage.getItem(${JSON.stringify(PWA_APPLY_CLICK_KEY)}) === "1"`,
    "PWA_UPDATE_APPLY_CLICK_NOT_CAPTURED",
  );
  try {
    await page.waitForExpression(
      `sessionStorage.getItem(${JSON.stringify(CONTROLLER_CHANGE_KEY)}) === "1"`,
      "PWA_UPDATE_CONTROLLER_CHANGE_MISSING",
      UPDATE_TIMEOUT_MS,
    );
  } catch (error) {
    if (!(error instanceof VerifyError) || error.code !== "PWA_UPDATE_CONTROLLER_CHANGE_MISSING") {
      throw error;
    }
    const stalled = await page.evaluate(`(async () => {
      const registration = await navigator.serviceWorker.getRegistration("/");
      const names = (await caches.keys())
        .filter((name) => name.startsWith(${JSON.stringify(CACHE_PREFIX)}));
      const oldCaches = ${JSON.stringify(oldCaches)};
      const newCaches = ${JSON.stringify(waiting.newCaches)};
      return {
        applyClicks: sessionStorage.getItem(${JSON.stringify(PWA_APPLY_CLICK_KEY)}),
        controllerChanges: sessionStorage.getItem(${JSON.stringify(CONTROLLER_CHANGE_KEY)}),
        waiting: registration?.waiting?.state ?? null,
        oldCacheCount: oldCaches.filter((name) => names.includes(name)).length,
        newCacheCount: newCaches.filter((name) => names.includes(name)).length,
      };
    })()`);
    if (
      stalled?.applyClicks === "1"
      && stalled.controllerChanges === "0"
      && stalled.waiting === "installed"
    ) {
      fail("PWA_UPDATE_APPLY_NOT_TRIGGERED");
    }
    if (
      stalled?.controllerChanges === "0"
      && stalled.waiting === null
      && stalled.oldCacheCount === 0
      && stalled.newCacheCount === waiting.newCaches.length
    ) {
      fail("PWA_UPDATE_CONTROLLER_PROBE_MISSED");
    }
    if (/^[2-9][0-9]*$/.test(stalled?.controllerChanges ?? "")) {
      fail("PWA_UPDATE_CONTROLLER_CHANGE_COUNT_INVALID");
    }
    throw error;
  }
  await page.waitForExpression(
    `location.pathname === "/internal-app"
      && document.readyState === "complete"
      && Boolean(document.querySelector(".ant-layout-header"))`,
    "PWA_UPDATE_RELOAD_MISSING",
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
      applyClicks: sessionStorage.getItem(${JSON.stringify(PWA_APPLY_CLICK_KEY)}),
      controllerChanges: sessionStorage.getItem(${JSON.stringify(CONTROLLER_CHANGE_KEY)}),
    };
  })()`);
  if (
    !activated
    || activated.active !== "activated"
    || activated.waiting !== null
    || activated.controlled !== true
    || activated.applyClicks !== "1"
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
    applyClicks: 1,
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
  let primaryError = null;
  try {
    owned = await createPage(cdp, origin);
    let password = await readSecret(secretDirectory, identity.secret);
    try {
      await login(owned.page, identity.username, password);
    } finally {
      password = null;
    }
    return await operation(owned.page);
  } catch (error) {
    primaryError = error;
    throw error;
  } finally {
    try {
      await disposePage(cdp, owned);
    } catch (cleanupError) {
      if (!primaryError) throw cleanupError;
    }
  }
}

async function preflightIdentities(origin, secretDirectory, identities = IDENTITIES) {
  const rejected = [];
  for (const identity of identities) {
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

function readPlist(plistPath) {
  return new Promise((resolve) => {
    execFile(
      "/usr/bin/plutil",
      ["-convert", "json", "-o", "-", plistPath],
      {
        encoding: "utf8",
        env: { HOME: "/", LANG: "C", PATH: "/usr/bin:/bin" },
        maxBuffer: 64 * 1024,
        timeout: 5_000,
        windowsHide: true,
      },
      (error, stdout) => {
        if (error || typeof stdout !== "string" || stdout.length > 64 * 1024) {
          resolve(null);
          return;
        }
        try {
          const payload = JSON.parse(stdout);
          resolve(payload && typeof payload === "object" && !Array.isArray(payload) ? payload : null);
        } catch {
          resolve(null);
        }
      },
    );
  });
}

function pwaShimContract(executable, systemHome, profile, manifestId) {
  let appsFolder;
  let browserBundleId;
  if (
    executable === "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
  ) {
    appsFolder = "Chrome Apps.localized";
    browserBundleId = "com.google.Chrome";
  } else if (
    executable === "/Applications/Chromium.app/Contents/MacOS/Chromium"
  ) {
    appsFolder = "Chromium Apps.localized";
    browserBundleId = "org.chromium.Chromium";
  } else {
    fail("PWA_OS_INSTALL_UNSUPPORTED");
  }
  let parsedManifest;
  try {
    parsedManifest = new URL(manifestId);
  } catch {
    fail("PWA_MANIFEST_ID_INVALID");
  }
  if (
    typeof systemHome !== "string"
    || !path.isAbsolute(systemHome)
    || typeof profile !== "string"
    || !path.isAbsolute(profile)
    || parsedManifest.pathname !== "/internal-app"
    || parsedManifest.search
    || parsedManifest.hash
  ) {
    fail("PWA_OS_SHIM_IDENTITY_INVALID");
  }
  const applicationsDirectory = path.join(systemHome, "Applications");
  return {
    applicationsDirectory,
    appsDirectory: path.join(applicationsDirectory, appsFolder),
    browserBundleId,
    manifestId: parsedManifest.href,
    profile,
  };
}

async function ownedDirectoryPresent(directory, allowMissing) {
  let info;
  try {
    info = await lstat(directory);
  } catch (error) {
    if (allowMissing && error?.code === "ENOENT") return false;
    fail("PWA_OS_SHIM_DIRECTORY_INVALID");
  }
  if (
    info.isSymbolicLink()
    || !info.isDirectory()
    || info.uid !== process.geteuid()
    || (info.mode & 0o022) !== 0
  ) {
    fail("PWA_OS_SHIM_DIRECTORY_INVALID");
  }
  return true;
}

function appDataIdentity(userDataDirectory, profile, shortcutId) {
  if (typeof userDataDirectory !== "string" || !path.isAbsolute(userDataDirectory)) {
    return false;
  }
  const resolved = path.resolve(userDataDirectory);
  const relative = path.relative(profile, resolved);
  if (!relative || relative.startsWith(`..${path.sep}`) || path.isAbsolute(relative)) {
    return false;
  }
  const parts = relative.split(path.sep);
  return (
    parts.length >= 3
    && parts.at(-2) === "Web Applications"
    && parts.at(-1) === `_crx_${shortcutId}`
  );
}

function shortcutUrlIdentity(raw, manifestId) {
  let shortcut;
  let manifest;
  try {
    shortcut = new URL(raw);
    manifest = new URL(manifestId);
  } catch {
    return false;
  }
  return (
    shortcut.origin === manifest.origin
    && ["/internal-app", "/internal-app/"].includes(shortcut.pathname)
    && !shortcut.username
    && !shortcut.password
    && !shortcut.search
    && !shortcut.hash
  );
}

async function inspectPwaShim(bundlePath, contract) {
  if (
    path.dirname(bundlePath) !== contract.appsDirectory
    || !path.basename(bundlePath).endsWith(".app")
  ) {
    fail("PWA_OS_SHIM_IDENTITY_INVALID");
  }
  const plistPath = path.join(bundlePath, "Contents", "Info.plist");
  let bundleInfo;
  let plistInfo;
  try {
    [bundleInfo, plistInfo] = await Promise.all([lstat(bundlePath), lstat(plistPath)]);
  } catch {
    return null;
  }
  if (
    bundleInfo.isSymbolicLink()
    || !bundleInfo.isDirectory()
    || bundleInfo.uid !== process.geteuid()
    || (bundleInfo.mode & 0o022) !== 0
    || plistInfo.isSymbolicLink()
    || !plistInfo.isFile()
    || plistInfo.nlink !== 1
    || plistInfo.uid !== process.geteuid()
    || (plistInfo.mode & 0o022) !== 0
    || plistInfo.size < 1
    || plistInfo.size > 64 * 1024
  ) {
    return null;
  }
  const plist = await readPlist(plistPath);
  if (!plist) return null;
  const shortcutId = plist.CrAppModeShortcutID;
  const belongsToProfile =
    typeof shortcutId === "string"
    && /^[a-p]{32}$/.test(shortcutId)
    && appDataIdentity(plist.CrAppModeUserDataDir, contract.profile, shortcutId);
  if (!belongsToProfile) return null;
  if (
    !shortcutUrlIdentity(plist.CrAppModeShortcutURL, contract.manifestId)
    || plist.CrAppModeShortcutName !== PWA_APP_NAME
    || plist.CrBundleIdentifier !== contract.browserBundleId
    || typeof plist.CFBundleIdentifier !== "string"
    || !plist.CFBundleIdentifier.startsWith(`${contract.browserBundleId}.`)
    || !plist.CFBundleIdentifier.endsWith(`.${shortcutId}`)
    || plist.CFBundleExecutable !== "app_mode_loader"
    || plist.CFBundlePackageType !== "APPL"
  ) {
    fail("PWA_OS_SHIM_IDENTITY_INVALID");
  }
  return {
    bundlePath,
    dev: bundleInfo.dev,
    ino: bundleInfo.ino,
    shortcutId,
  };
}

async function findPwaShim(contract, allowMissingDirectory = false) {
  const applicationsPresent = await ownedDirectoryPresent(
    contract.applicationsDirectory,
    allowMissingDirectory,
  );
  if (!applicationsPresent) return null;
  const appsPresent = await ownedDirectoryPresent(contract.appsDirectory, allowMissingDirectory);
  if (!appsPresent) return null;
  let entries;
  try {
    entries = await readdir(contract.appsDirectory, { withFileTypes: true });
  } catch {
    fail("PWA_OS_SHIM_DIRECTORY_INVALID");
  }
  if (entries.length > 256) fail("PWA_OS_SHIM_DIRECTORY_INVALID");
  const shims = [];
  for (const entry of entries) {
    if (!entry.name.endsWith(".app") || entry.name.length > 255) continue;
    const shim = await inspectPwaShim(path.join(contract.appsDirectory, entry.name), contract);
    if (shim) shims.push(shim);
  }
  if (shims.length > 1) fail("PWA_OS_SHIM_IDENTITY_INVALID");
  return shims[0] ?? null;
}

async function waitForPwaShim(contract) {
  const deadline = Date.now() + PWA_OS_SHIM_TIMEOUT_MS;
  while (Date.now() < deadline) {
    const shim = await findPwaShim(contract, true);
    if (shim) return shim;
    await delay(100);
  }
  fail("PWA_OS_SHIM_MISSING");
}

async function waitForPwaShimRemoval(contract, shim) {
  const deadline = Date.now() + PWA_OS_SHIM_TIMEOUT_MS;
  while (Date.now() < deadline) {
    let namedInfo = null;
    try {
      namedInfo = await lstat(shim.bundlePath);
    } catch (error) {
      if (error?.code !== "ENOENT") fail("PWA_OS_SHIM_IDENTITY_INVALID");
    }
    const discovered = await findPwaShim(contract, true);
    if (namedInfo === null && discovered === null) return true;
    if (
      namedInfo
      && (namedInfo.isSymbolicLink() || namedInfo.dev !== shim.dev || namedInfo.ino !== shim.ino)
    ) {
      fail("PWA_OS_SHIM_IDENTITY_INVALID");
    }
    await delay(100);
  }
  return false;
}

async function removeVerifiedPwaShim(contract, shim) {
  let current;
  try {
    current = await lstat(shim.bundlePath);
  } catch (error) {
    if (error?.code === "ENOENT") return;
    fail("PWA_OS_SHIM_CLEANUP_FAILED");
  }
  if (
    current.isSymbolicLink()
    || current.dev !== shim.dev
    || current.ino !== shim.ino
    || (await inspectPwaShim(shim.bundlePath, contract))?.shortcutId !== shim.shortcutId
  ) {
    fail("PWA_OS_SHIM_IDENTITY_INVALID");
  }
  try {
    await rm(shim.bundlePath, { recursive: true, force: false });
  } catch {
    fail("PWA_OS_SHIM_CLEANUP_FAILED");
  }
  try {
    await lstat(shim.bundlePath);
  } catch (error) {
    if (error?.code === "ENOENT") return;
  }
  fail("PWA_OS_SHIM_CLEANUP_FAILED");
}

async function attachPwaTarget(cdp, targetId, origin, failureCode) {
  const target = await cdp.call("Target.getTargetInfo", { targetId })
    .catch(() => fail(failureCode));
  if (target?.targetInfo?.type !== "page") fail(failureCode);
  const attached = await cdp.call("Target.attachToTarget", {
    targetId,
    flatten: true,
  }).catch(() => fail(failureCode));
  if (typeof attached?.sessionId !== "string" || !attached.sessionId) fail(failureCode);
  const page = new BrowserPage(cdp, attached.sessionId, origin);
  await page.initialize();
  return page;
}

async function closePwaTarget(cdp, targetId, page) {
  page?.close();
  if (!targetId) return true;
  const closed = await cdp.call("Target.closeTarget", { targetId }).catch(() => null);
  return closed?.success === true;
}

async function verifyPwaTargetShell(page, origin, failureCode) {
  await page.waitForExpression(
    `location.origin === ${JSON.stringify(origin)}
      && ["/internal-app", "/login"].includes(location.pathname)
      && document.readyState === "complete"
      && Boolean(document.body)
      && window.matchMedia("(display-mode: standalone)").matches
      && (document.body.textContent?.includes("内部 PWA 状态")
        || Array.from(document.querySelectorAll("button"))
          .some((button) => button.textContent?.includes("通过 Keycloak 登录")))`,
    failureCode,
  );
}

async function verifyPwaInstallation(
  cdp,
  origin,
  installPwa,
  controlDirectory,
  runtime,
  secretDirectory,
  { auditRuntime = false } = {},
) {
  if (installPwa) fail("PWA_OS_BLOCKED_BY_BROWSER_AUTOMATION_BOUNDARY");
  let installabilityErrors = null;
  let auditTargetId = null;
  let auditPage = null;
  let runtimeAudit = null;
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
    if (auditRuntime) runtimeAudit = await auditPwaCaches(auditPage);
  } catch (error) {
    primaryError = error;
  }
  let cleanupFailed = false;
  auditPage?.close();
  if (auditTargetId) {
    const closed = await cdp.call("Target.closeTarget", {
      targetId: auditTargetId,
    }).catch(() => null);
    cleanupFailed ||= closed?.success !== true;
  }
  if (primaryError) {
    if (primaryError instanceof VerifyError) throw primaryError;
    fail("PWA_INSTALL_FAILED");
  }
  if (cleanupFailed) fail("PWA_INSTALL_CLEANUP_FAILED");
  if (!Number.isInteger(installabilityErrors)) fail("PWA_INSTALLABILITY_AUDIT_UNAVAILABLE");
  return {
    installations: 0,
    installabilityErrors,
    runtimeAudit,
    osOfflineReopens: 0,
    osOnlineLaunches: 0,
    osShimResiduals: 0,
    osShimsCreated: 0,
    osUninstallations: 0,
    osUninstallProbe: 0,
  };
}

async function executeAll(
  cdp,
  origin,
  secretDirectory,
  installPwa,
  controlDirectory,
  runtime,
) {
  const admin = await runIdentity(
    cdp,
    origin,
    secretDirectory,
    IDENTITIES[0],
    async (page) => {
      const pageChecks = await visitAdminPages(page);
      const minioRecovery = await verifyMinio503Recovery(page, controlDirectory);
      const clamdRecovery = await verifyClamdUnavailableRecovery(
        page,
        controlDirectory,
      );
      const tenant = await verifyTenantSwitch(page);
      await page.navigate("/internal-app");
      await waitForApplicationShell(page);
      const pwa = await auditPwaCaches(page);
      const update = controlDirectory
        ? await verifyWaitingPwaUpdate(page, controlDirectory)
        : null;
      const offline = update?.offline ?? await verifyOfflineShell(page);
      const expectedNon2xx =
        tenant.expectedNon2xx
        + minioRecovery.expectedNon2xx
        + clamdRecovery.expectedNon2xx;
      if (page.apiNon2xx !== expectedNon2xx) {
        fail("ADMIN_UNEXPECTED_API_NON_2XX");
      }
      return {
        pages: TOP_LEVEL_PAGES.length,
        apiResponses: page.apiResponses,
        apiNon2xx: page.apiNon2xx - expectedNon2xx,
        allowedActionUiChecks: pageChecks.allowedActionUiChecks,
        tenant,
        minioRecovery,
        clamdRecovery,
        expectedNon2xx,
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
  const pwaInstallation = await verifyPwaInstallation(
    cdp,
    origin,
    installPwa,
    controlDirectory,
    runtime,
    secretDirectory,
  );
  return {
    stage: "all",
    identities_authenticated: IDENTITIES.length,
    admin_pages_visited: admin.pages,
    admin_api_responses: admin.apiResponses,
    admin_api_non_2xx: admin.apiNon2xx,
    consultant_pages_visited: roleResults.consultant.pages,
    enterprise_pages_visited: roleResults.enterprise.pages,
    role_api_non_2xx:
      roleResults.consultant.apiNon2xx + roleResults.enterprise.apiNon2xx,
    role_allowed_action_ui_checks:
      admin.allowedActionUiChecks
      + roleResults.consultant.allowedActionUiChecks
      + roleResults.enterprise.allowedActionUiChecks,
    tenant_header_changes: admin.tenant.headerChanges,
    tenant_state_clears: admin.tenant.stateClears,
    cross_tenant_404_ui_count: admin.tenant.crossTenant404Ui,
    illegal_state_409_ui_count: admin.tenant.illegalState409Ui,
    expected_fault_api_non_2xx: admin.expectedNon2xx,
    service_unavailable_503_ui_count:
      admin.minioRecovery.serviceUnavailable503Ui,
    service_unavailable_503_ui_status: admin.minioRecovery.status,
    clamd_unavailable_ui_count: admin.clamdRecovery.unavailableUi,
    clamd_recovery_ui_count: admin.clamdRecovery.recoveryUi,
    pwa_registrations: admin.pwa.registrations,
    pwa_controlled_clients: admin.pwa.controlled,
    pwa_owned_caches: admin.pwa.caches,
    pwa_sensitive_cache_entries: admin.pwa.sensitive,
    pwa_installability_errors: pwaInstallation.installabilityErrors,
    pwa_offline_shell: admin.offline,
    pwa_installations: pwaInstallation.installations,
    pwa_os_offline_reopens: pwaInstallation.osOfflineReopens,
    pwa_os_online_launches: pwaInstallation.osOnlineLaunches,
    pwa_os_shim_residuals: pwaInstallation.osShimResiduals,
    pwa_os_shims_created: pwaInstallation.osShimsCreated,
    pwa_os_install_status: "PWA_OS_INSTALL_NOT_TESTED",
    pwa_os_uninstall_probe: pwaInstallation.osUninstallProbe,
    pwa_os_uninstallations: pwaInstallation.osUninstallations,
    pwa_waiting_updates: admin.update?.waitingUpdates ?? 0,
    pwa_apply_clicks: admin.update?.applyClicks ?? 0,
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

async function executeBusiness(cdp, origin, secretDirectory) {
  const admin = await runIdentity(
    cdp,
    origin,
    secretDirectory,
    IDENTITIES[0],
    async (page) => {
      const pageChecks = await visitAdminPages(page);
      const tenant = await verifyTenantSwitch(page);
      if (page.apiNon2xx !== tenant.expectedNon2xx) {
        fail("BUSINESS_ADMIN_UNEXPECTED_API_NON_2XX");
      }
      return {
        pages: TOP_LEVEL_PAGES.length,
        apiResponses: page.apiResponses,
        allowedActionUiChecks: pageChecks.allowedActionUiChecks,
        apiNon2xx: page.apiNon2xx - tenant.expectedNon2xx,
        tenant,
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
  const { consultant, enterprise } = roleResults;
  if (consultant.apiNon2xx !== 0 || enterprise.apiNon2xx !== 0) {
    fail("BUSINESS_ROLE_UNEXPECTED_API_NON_2XX");
  }
  return {
    stage: "business",
    identities_authenticated: 3,
    admin_pages_visited: admin.pages,
    admin_api_responses: admin.apiResponses,
    admin_api_non_2xx: admin.apiNon2xx,
    consultant_pages_visited: consultant.pages,
    enterprise_pages_visited: enterprise.pages,
    role_api_non_2xx: consultant.apiNon2xx + enterprise.apiNon2xx,
    role_allowed_action_ui_checks:
      admin.allowedActionUiChecks
      + consultant.allowedActionUiChecks
      + enterprise.allowedActionUiChecks,
    tenant_header_changes: admin.tenant.headerChanges,
    tenant_state_clears: admin.tenant.stateClears,
    cross_tenant_404_ui_count: admin.tenant.crossTenant404Ui,
    illegal_state_409_ui_count: admin.tenant.illegalState409Ui,
  };
}

async function executeFaults(cdp, origin, secretDirectory, controlDirectory) {
  const admin = await runIdentity(
    cdp,
    origin,
    secretDirectory,
    IDENTITIES[0],
    async (page) => {
      const minioRecovery = await verifyMinio503Recovery(page, controlDirectory);
      const clamdRecovery = await verifyClamdUnavailableRecovery(
        page,
        controlDirectory,
      );
      const expectedNon2xx =
        minioRecovery.expectedNon2xx + clamdRecovery.expectedNon2xx;
      if (page.apiNon2xx !== expectedNon2xx) {
        fail("FAULTS_ADMIN_UNEXPECTED_API_NON_2XX");
      }
      return { minioRecovery, clamdRecovery, expectedNon2xx };
    },
  );
  return {
    stage: "faults",
    expected_fault_api_non_2xx: admin.expectedNon2xx,
    service_unavailable_503_ui_count:
      admin.minioRecovery.serviceUnavailable503Ui,
    service_unavailable_503_ui_status: admin.minioRecovery.status,
    clamd_unavailable_ui_count: admin.clamdRecovery.unavailableUi,
    clamd_recovery_ui_count: admin.clamdRecovery.recoveryUi,
  };
}

async function executePwaUpdate(cdp, origin, secretDirectory, controlDirectory) {
  const admin = await runIdentity(
    cdp,
    origin,
    secretDirectory,
    IDENTITIES[0],
    async (page) => {
      await page.navigate("/internal-app");
      await waitForApplicationShell(page);
      const pwa = await auditPwaCaches(page);
      const update = await verifyWaitingPwaUpdate(page, controlDirectory);
      if (page.apiNon2xx !== 0) fail("PWA_UPDATE_ADMIN_UNEXPECTED_API_NON_2XX");
      return { pwa, update };
    },
  );
  return {
    stage: "pwa-update",
    pwa_registrations: admin.pwa.registrations,
    pwa_controlled_clients: admin.pwa.controlled,
    pwa_owned_caches: admin.pwa.caches,
    pwa_sensitive_cache_entries: admin.pwa.sensitive,
    pwa_offline_shell: admin.update.offline,
    pwa_waiting_updates: admin.update.waitingUpdates,
    pwa_apply_clicks: admin.update.applyClicks,
    pwa_controller_changes: admin.update.controllerChanges,
    pwa_old_caches_removed: admin.update.oldCachesRemoved,
    pwa_new_caches: admin.update.newCaches,
    pwa_sentinel_caches_preserved: admin.update.sentinelCachesPreserved,
    pwa_login_states_preserved: admin.update.loginStatesPreserved,
    pwa_update_status: "PWA_WAITING_UPDATE_PASSED",
  };
}

async function executePwaOs() {
  fail("PWA_OS_BLOCKED_BY_BROWSER_AUTOMATION_BOUNDARY");
}

function preflightIdentitiesForStage(stage) {
  if (stage === "pwa-os") return [];
  if (["faults", "pwa-update"].includes(stage)) return [IDENTITIES[0]];
  return IDENTITIES;
}

async function executeStage(
  stage,
  cdp,
  origin,
  secretDirectory,
  installPwa,
  controlDirectory,
  runtime,
) {
  if (stage === "all") {
    return executeAll(
      cdp,
      origin,
      secretDirectory,
      installPwa,
      controlDirectory,
      runtime,
    );
  }
  if (stage === "business") return executeBusiness(cdp, origin, secretDirectory);
  if (stage === "faults") {
    return executeFaults(cdp, origin, secretDirectory, controlDirectory);
  }
  if (stage === "pwa-update") {
    return executePwaUpdate(cdp, origin, secretDirectory, controlDirectory);
  }
  if (stage === "pwa-os") {
    return executePwaOs(cdp, origin, secretDirectory, controlDirectory, runtime);
  }
  fail("BROWSER_STAGE_INVALID");
}

async function main() {
  const {
    origin,
    secretDirectory,
    headed,
    installPwa,
    stage,
    controlDirectory,
  } = parseInputs();
  if (stage === "pwa-os" || installPwa) {
    fail("PWA_OS_BLOCKED_BY_BROWSER_AUTOMATION_BOUNDARY");
  }
  await validateSecretDirectory(secretDirectory);
  if (controlDirectory) await validateControlDirectory(controlDirectory);
  markUnexpectedFailure("BROWSER_STAGE_PREFLIGHT_UNEXPECTED");
  await preflightIdentities(
    origin,
    secretDirectory,
    preflightIdentitiesForStage(stage),
  );
  markUnexpectedFailure("BROWSER_STAGE_LAUNCH_UNEXPECTED");
  const runtime = await launchChrome(headed, installPwa, controlDirectory);
  let signalCleanupStarted = false;
  const onSignal = () => {
    if (signalCleanupStarted) return;
    signalCleanupStarted = true;
    void cleanupChrome(runtime).catch(() => undefined);
  };
  process.on("SIGINT", onSignal);
  process.on("SIGTERM", onSignal);
  let summary;
  let primaryError = null;
  let cleanupError = null;
  try {
    runtime.cdp = await CdpConnection.connect(runtime.websocketUrl);
    markUnexpectedFailure("BROWSER_STAGE_EXECUTE_UNEXPECTED");
    summary = await executeStage(
      stage,
      runtime.cdp,
      origin,
      secretDirectory,
      installPwa,
      controlDirectory,
      runtime,
    );
    markUnexpectedFailure("BROWSER_STAGE_EXECUTE_UNEXPECTED");
  } catch (error) {
    primaryError = error;
  } finally {
    try {
      await cleanupChrome(runtime);
    } catch (error) {
      cleanupError = error;
    }
    process.removeListener("SIGINT", onSignal);
    process.removeListener("SIGTERM", onSignal);
  }
  if (primaryError) throw primaryError;
  if (cleanupError) throw cleanupError;
  if (signalCleanupStarted) fail("BROWSER_VERIFY_INTERRUPTED");
  process.stdout.write(`${JSON.stringify(summary)}\n${STAGE_SUCCESS_TAGS[stage]}\n`);
}

main().catch((error) => {
  const code = error instanceof VerifyError ? error.code : unexpectedFailureReason;
  process.stderr.write(`LOCAL_BROWSER_VERIFY_FAILED ${code}\n`);
  process.exitCode = 1;
});
