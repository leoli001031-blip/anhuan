import { execFile, spawn } from "node:child_process";
import { randomUUID } from "node:crypto";
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
import { fileURLToPath } from "node:url";

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
const RUN_STAGES = new Set(["all", "business", "faults", "pwa-update", "pwa-os", "material-rag-uat", "material-rag-uat-human"]);
const STAGE_SUCCESS_TAGS = Object.freeze({
  all: "LOCAL_BROWSER_VERIFY_OK",
  business: "LOCAL_BROWSER_BUSINESS_VERIFY_OK",
  faults: "LOCAL_BROWSER_FAULTS_VERIFY_OK",
  "pwa-update": "LOCAL_PWA_UPDATE_VERIFY_OK",
  "material-rag-uat": "LOCAL_MATERIAL_RAG_UAT_LIVE_BROWSER_OK",
  "material-rag-uat-human": "LOCAL_MATERIAL_RAG_UAT_HUMAN_SESSION_READY",
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
  constructor(code, evidence = null) {
    super(code);
    this.code = code;
    this.evidence = evidence;
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

function limitedJourneyEvidence(evidence) {
  if (!evidence || typeof evidence !== "object" || Array.isArray(evidence)) return null;
  const status = evidence.http_status;
  const stage = evidence.action_stage;
  return {
    journey: typeof evidence.journey === "string" ? evidence.journey : null,
    expected_phase: typeof evidence.expected_phase === "string" ? evidence.expected_phase : null,
    actual_phase: typeof evidence.actual_phase === "string" ? evidence.actual_phase : null,
    request_seen: evidence.request_seen === 1 || evidence.request_seen === true ? 1 : 0,
    http_status: Number.isInteger(status) ? status : null,
    action_stage: stage === "select" || stage === "ask" || stage === "observe_request" ? stage : null,
  };
}

function withActionStage(evidence, stage) {
  const next = evidence && typeof evidence === "object" && !Array.isArray(evidence)
    ? evidence
    : {
      journey: null,
      expected_phase: null,
      actual_phase: null,
      request_seen: 0,
      http_status: null,
    };
  next.action_stage = stage;
  return next;
}

function fail(code, evidence = null) {
  throw new VerifyError(code, limitedJourneyEvidence(evidence));
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

async function launchChrome(headed, installPwa, controlDirectory, visible = false) {
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
  if (visible) {
    args.unshift("--window-size=1200,900");
  } else if (headed || installPwa) {
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

function headerPresent(headers, expectedName) {
  const value = requestHeader(headers, expectedName);
  return typeof value === "string" && value.length > 0;
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
    this.apiRequestEvents = [];
    this.apiRequestsById = new Map();
    this.currentRoute = "/";
    this.tenantRequests = [];
    this.uatHeaderSnapshots = [];
    this.uatUiAskSnapshots = [];
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
        const requestPath = apiPath(event.request?.url, this.origin);
        const method = typeof event.request?.method === "string" ? event.request.method : "";
        this.apiRequestsById.set(event.requestId, { path: requestPath, method });
        if (typeof requestPath === "string") {
          this.apiRequestEvents.push({
            path: requestPath,
            method,
            requestId: typeof event.requestId === "string" ? event.requestId : "",
            authorization: headerPresent(event.request?.headers, "authorization"),
            enterprise: headerPresent(event.request?.headers, "x-enterprise-id"),
            actor: headerPresent(event.request?.headers, "x-uat-actor"),
          });
        }
        if (
          typeof requestPath === "string"
          && requestPath.startsWith("/api/v1/local-uat/material-qa")
        ) {
          const headers = event.request?.headers;
          this.uatHeaderSnapshots.push({
            authorization: Boolean(requestHeader(headers, "authorization")),
            enterprise: Boolean(requestHeader(headers, "x-enterprise-id")),
            actor: Boolean(requestHeader(headers, "x-uat-actor")),
          });
        }
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
          const requestMeta = this.apiRequestsById.get(event.requestId);
          this.apiResponseEvents.push({
            path: responsePath,
            route: this.currentRoute,
            status: Number.isInteger(status) ? status : null,
            method: requestMeta?.method ?? "",
            requestId: typeof event.requestId === "string" ? event.requestId : "",
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
  if (stage === "material-rag-uat" || stage === "material-rag-uat-human") return [IDENTITIES[0]];
  if (["faults", "pwa-update"].includes(stage)) return [IDENTITIES[0]];
  return IDENTITIES;
}

const UAT_QUERY_LABELS = Object.freeze({
  "provider.shared": "服务商共享域",
  "client.current": "当前客户域",
  "combo.provider_client": "服务商共享域 + 当前客户域",
  "cross.denied": "跨范围拒绝（固定）",
  "fail.clear": "失败并清空旧结果",
  "progress.wait": "处理中",
});
const UAT_FOREIGN_ENTERPRISE = "52000000-0000-4000-8000-00000000ffff";
const UAT_UNKNOWN_CLIENT = "52000000-0000-4000-8000-00000000eeee";
const UAT_SEED_ENTERPRISE_A = "20000000-0000-4000-8000-00000000000a";
const UAT_SEED_ENTERPRISE_B = "20000000-0000-4000-8000-00000000000b";
const UAT_ENTERPRISE_A_RECORD = "41000000-0000-4000-8000-000000000011";
const UAT_ENTERPRISE_A_VERSION = "41000000-0000-4000-8000-000000000012";
const UAT_ENTERPRISE_B_RECORD = "41000000-0000-4000-8000-000000000091";
const UAT_ENTERPRISE_B_VERSION = "41000000-0000-4000-8000-000000000092";
const UAT_CLIENT_A_NAME = "UAT-SYNTH-CLIENT-A";
const UAT_CLIENT_B_NAME = "UAT-SYNTH-CLIENT-B";

const UAT_ASK_PATH = "/api/v1/local-uat/material-qa";
const UAT_PHASE_PREFIX = "material-rag-phase-";
const UAT_CLOSED_HTTP = new Set([200, 202, 401, 403, 404, 409, 503]);
const UAT_JOURNEYS = Object.freeze({
  J1_PROVIDER: Object.freeze({
    queryId: "provider.shared",
    expectedPhase: "ready",
    expectedStatus: 200,
  }),
  J2_CLIENT_A: Object.freeze({
    queryId: "client.current",
    expectedPhase: "ready",
    expectedStatus: 200,
  }),
  J3_COMBO_A: Object.freeze({
    queryId: "combo.provider_client",
    expectedPhase: "ready",
    expectedStatus: 200,
  }),
  J3_COMBO_B: Object.freeze({
    queryId: "combo.provider_client",
    expectedPhase: "ready",
    expectedStatus: 200,
  }),
  J4_CLIENT_B_EMPTY: Object.freeze({
    queryId: "client.current",
    expectedPhase: "empty",
    expectedStatus: 200,
  }),
  J6_FAIL_CLEAR: Object.freeze({
    queryId: "fail.clear",
    expectedPhase: "unavailable",
    expectedStatus: 503,
  }),
});

function qaSearch(search = "") {
  if (search == null || search === "") return "";
  return String(search).startsWith("?") ? String(search) : `?${search}`;
}

function isAskPost(event) {
  return Boolean(
    event
    && event.path === UAT_ASK_PATH
    && event.method === "POST",
  );
}

function networkRequestId(event) {
  return typeof event?.requestId === "string" && event.requestId.length > 0 ? event.requestId : null;
}

function httpFailureCode(status) {
  return UAT_CLOSED_HTTP.has(status) ? `HTTP_${status}` : "HTTP_OTHER";
}

async function waitForPageCondition(page, check, code, evidence) {
  const timeout = Number.isInteger(page.waitTimeout) ? page.waitTimeout : WAIT_TIMEOUT_MS;
  const poll = Number.isInteger(page.pollMs) ? page.pollMs : 50;
  const deadline = Date.now() + timeout;
  while (Date.now() < deadline) {
    if (await check()) return;
    await delay(poll);
  }
  fail(code, evidence);
}

async function readUiPhase(page) {
  const testid = await page.evaluate(
    `document.querySelector("[data-testid^='material-rag-phase-']")?.getAttribute("data-testid") ?? ""`,
  );
  if (typeof testid !== "string" || !testid.startsWith(UAT_PHASE_PREFIX)) return null;
  return testid.slice(UAT_PHASE_PREFIX.length) || null;
}

async function navigateQa(page, search = "") {
  const targetSearch = qaSearch(search);
  page.currentRoute = "/qa";
  const target = `${page.origin}/qa${targetSearch}`;
  const result = await page.cdp.call("Page.navigate", { url: target }, page.sessionId);
  if (result?.errorText) fail("BROWSER_NAVIGATION_FAILED");
  await page.waitForExpression(
    `location.origin === ${JSON.stringify(page.origin)} && location.pathname === "/qa" && location.search === ${JSON.stringify(targetSearch)} && document.readyState === "complete"`,
    "BROWSER_NAVIGATION_TIMEOUT",
  );
  await waitForApplicationShell(page);
}

function selectedQueryMatchesExpression(label) {
  return `(() => {
    const marked = document.querySelector("[data-testid=\\"material-rag-query\\"]");
    if (!marked) return false;
    const root = (typeof marked.closest === "function" && marked.closest(".ant-select")) || marked;
    const labeled = root.querySelector(".ant-select-selection-item, .ant-select-content-value, .ant-select-content");
    if (!labeled) return false;
    const wanted = ${JSON.stringify(label)};
    const title = (labeled.getAttribute("title") ?? "").trim();
    const text = (labeled.textContent ?? "").trim();
    return title === wanted || text === wanted;
  })()`;
}

function querySelectExpandedExpression() {
  return `(() => {
    const marked = document.querySelector("[data-testid=\\"material-rag-query\\"]");
    if (!marked) return false;
    const root = (typeof marked.closest === "function" && marked.closest(".ant-select")) || marked;
    const combobox = root.querySelector("[role=\\"combobox\\"]");
    if (combobox && combobox.getAttribute("aria-expanded") === "true") return true;
    return String(root.className || "").includes("ant-select-open");
  })()`;
}

function isVisibleNodeExpression(identifier) {
  return `(${identifier} && ${identifier}.hidden !== true && ${identifier}.getAttribute("aria-hidden") !== "true" && (typeof ${identifier}.getBoundingClientRect !== "function" || (${identifier}.getBoundingClientRect().width > 0 && ${identifier}.getBoundingClientRect().height > 0)) && (typeof getComputedStyle !== "function" || (getComputedStyle(${identifier}).display !== "none" && getComputedStyle(${identifier}).visibility !== "hidden")))`;
}

function clickVerifiedQueryOptionExpression(label) {
  return `(() => {
    const wanted = ${JSON.stringify(label)};
    const dropdowns = Array.from(document.querySelectorAll(".ant-select-dropdown")).filter((dd) => {
      if (!dd) return false;
      if (String(dd.className || "").includes("ant-select-dropdown-hidden")) return false;
      return ${isVisibleNodeExpression("dd")};
    });
    if (dropdowns.length !== 1) return false;
    const options = Array.from(dropdowns[0].querySelectorAll(".ant-select-item-option")).filter((el) => {
      if (!el) return false;
      if (String(el.className || "").includes("ant-select-item-option-disabled")) return false;
      if (el.getAttribute("aria-disabled") === "true") return false;
      return ${isVisibleNodeExpression("el")};
    });
    const byTitle = options.filter((el) => (el.getAttribute("title") ?? "").trim() === wanted);
    let match = null;
    if (byTitle.length === 1) {
      match = byTitle[0];
    } else if (byTitle.length > 1) {
      return false;
    } else {
      const byContent = [];
      for (const el of options) {
        const inner = el.querySelector(".ant-select-item-option-content");
        if ((inner?.textContent ?? "").trim() !== wanted) continue;
        const wrapper = (typeof el.closest === "function" && el.closest(".ant-select-item-option")) || el;
        if (!byContent.includes(wrapper)) byContent.push(wrapper);
      }
      if (byContent.length !== 1) return false;
      match = byContent[0];
    }
    if (!match || typeof match.click !== "function") return false;
    match.click();
    return true;
  })()`;
}

async function selectClosedQuery(page, queryId, evidence = null) {
  const label = UAT_QUERY_LABELS[queryId];
  if (typeof label !== "string") fail("UAT_QUERY_ID_INVALID");
  const staged = withActionStage(evidence, "select");
  if (await page.evaluate(selectedQueryMatchesExpression(label))) {
    return;
  }
  await page.clickElement("[data-testid=\"material-rag-query\"]", "UAT_QUERY_SELECT_MISSING");
  await waitForPageCondition(
    page,
    async () => page.evaluate(querySelectExpandedExpression()),
    "QUERY_NOT_COMMITTED",
    staged,
  );
  const clicked = await page.evaluate(clickVerifiedQueryOptionExpression(label));
  if (clicked !== true) fail("QUERY_NOT_COMMITTED", staged);
  await waitForPageCondition(
    page,
    async () => page.evaluate(selectedQueryMatchesExpression(label)),
    "QUERY_NOT_COMMITTED",
    staged,
  );
}

async function clickAsk(page, evidence = null) {
  const staged = withActionStage(evidence, "ask");
  const state = await page.evaluate(`(() => {
    const el = document.querySelector("[data-testid=\\"material-rag-ask\\"]");
    if (!el) return "missing";
    if (el.disabled === true || el.getAttribute("disabled") != null || el.getAttribute("aria-disabled") === "true") {
      return "disabled";
    }
    return "ok";
  })()`);
  if (state === "missing") fail("UAT_ASK_BUTTON_MISSING", staged);
  if (state !== "ok") fail("ASK_NOT_AVAILABLE", staged);
  await page.clickElement("[data-testid=\"material-rag-ask\"]", "UAT_ASK_BUTTON_MISSING");
}

function summarizeUiAskHeaders(page) {
  let authorizationHeaderPresent = 0;
  let enterpriseHeaderPresent = 0;
  let uatActorHeaderPresent = 0;
  for (const snapshot of page.uatUiAskSnapshots ?? []) {
    if (snapshot.authorization) authorizationHeaderPresent = 1;
    if (snapshot.enterprise) enterpriseHeaderPresent = 1;
    if (snapshot.actor) uatActorHeaderPresent = 1;
  }
  return {
    authorization_header_present: authorizationHeaderPresent,
    enterprise_header_present: enterpriseHeaderPresent,
    uat_actor_header_present: uatActorHeaderPresent,
  };
}

async function runUiAskJourney(page, journey, options = {}) {
  const spec = UAT_JOURNEYS[journey];
  if (!spec) fail("UAT_QUERY_ID_INVALID");
  const evidence = {
    journey,
    expected_phase: spec.expectedPhase,
    actual_phase: null,
    request_seen: 0,
    http_status: null,
  };
  if (options.navigate !== false) {
    await navigateQa(page, options.search ?? "");
  }
  await selectClosedQuery(page, spec.queryId, evidence);
  if (!Array.isArray(page.apiRequestEvents)) page.apiRequestEvents = [];
  if (!Array.isArray(page.apiResponseEvents)) page.apiResponseEvents = [];
  if (!Array.isArray(page.uatUiAskSnapshots)) page.uatUiAskSnapshots = [];
  const requestBoundary = page.apiRequestEvents.length;
  const responseBoundary = page.apiResponseEvents.length;
  await clickAsk(page, evidence);
  await waitForPageCondition(
    page,
    async () => page.apiRequestEvents.slice(requestBoundary).some(isAskPost),
    "POST_NOT_OBSERVED",
    withActionStage(evidence, "observe_request"),
  );
  evidence.request_seen = 1;
  const uiRequest = page.apiRequestEvents.slice(requestBoundary).find(isAskPost);
  await waitForPageCondition(
    page,
    async () => page.apiResponseEvents.slice(responseBoundary).some(isAskPost),
    "RESPONSE_TIMEOUT",
    withActionStage(evidence, "observe_request"),
  );
  const uiResponse = page.apiResponseEvents.slice(responseBoundary).find(
    (event) => isAskPost(event) && networkRequestId(event) !== null && networkRequestId(event) === networkRequestId(uiRequest),
  );
  if (!uiResponse) {
    fail("REQUEST_RESPONSE_ID_MISMATCH", withActionStage(evidence, "observe_request"));
  }
  evidence.http_status = uiResponse?.status ?? null;
  if (evidence.http_status !== spec.expectedStatus) {
    fail(httpFailureCode(evidence.http_status), evidence);
  }
  page.uatUiAskSnapshots.push({
    authorization: Boolean(uiRequest?.authorization),
    enterprise: Boolean(uiRequest?.enterprise),
    actor: Boolean(uiRequest?.actor),
  });
  await waitForPageCondition(
    page,
    async () => {
      evidence.actual_phase = await readUiPhase(page);
      return Boolean(evidence.actual_phase) && evidence.actual_phase !== "loading";
    },
    "UI_NO_TERMINAL",
    evidence,
  );
  if (evidence.actual_phase !== spec.expectedPhase) {
    fail(`UI_GOT_${evidence.actual_phase}`, evidence);
  }
  return evidence;
}

const J6_DOC_ATTR = "data-j6-doc";

async function observeQaSurface(page) {
  const observed = await page.evaluate(`(() => {
    const answer = document.querySelector("[data-testid=\\"material-rag-answer\\"]");
    const table = document.querySelector("[data-testid=\\"material-rag-citations\\"]");
    const rows = table ? table.querySelectorAll("tbody tr.ant-table-row") : [];
    return {
      answer_present: answer ? 1 : 0,
      citation_rows: rows.length,
    };
  })()`);
  return {
    answer_present: observed && observed.answer_present === 1 ? 1 : 0,
    citation_rows: Number.isInteger(observed?.citation_rows) ? observed.citation_rows : 0,
  };
}

function computeJ6Clearance(prior, after, sameDocument) {
  const j6_prior_answer = prior?.answer_present === 1 ? 1 : 0;
  const j6_prior_citations = Number(prior?.citation_rows) >= 1 ? 1 : 0;
  const j6_same_document = sameDocument === true || sameDocument === 1 ? 1 : 0;
  const j6_answer_cleared = after?.answer_present === 0 ? 1 : 0;
  const j6_citations_cleared = after?.citation_rows === 0 ? 1 : 0;
  const cleared_on_failure = (
    j6_prior_answer === 1
    && j6_prior_citations === 1
    && j6_same_document === 1
    && j6_answer_cleared === 1
    && j6_citations_cleared === 1
  );
  return {
    j6_prior_answer,
    j6_prior_citations,
    j6_same_document,
    j6_answer_cleared,
    j6_citations_cleared,
    cleared_on_failure,
  };
}

async function stampJ6Document(page) {
  const token = randomUUID();
  const ok = await page.evaluate(`(() => {
    document.documentElement.setAttribute(${JSON.stringify(J6_DOC_ATTR)}, ${JSON.stringify(token)});
    return document.documentElement.getAttribute(${JSON.stringify(J6_DOC_ATTR)});
  })()`);
  if (ok !== token) fail("J6_DOCUMENT_STAMP_FAILED");
  return token;
}

async function readJ6DocumentStamp(page) {
  return page.evaluate(`document.documentElement.getAttribute(${JSON.stringify(J6_DOC_ATTR)})`);
}

async function runJ6FailClear(page) {
  await navigateQa(page);
  const stamp = await stampJ6Document(page);
  await runUiAskJourney(page, "J1_PROVIDER", { navigate: false });
  const prior = await observeQaSurface(page);
  if (prior.answer_present !== 1) fail("J6_PRIOR_ANSWER_MISSING");
  if (!(prior.citation_rows >= 1)) fail("J6_PRIOR_CITATIONS_MISSING");
  await runUiAskJourney(page, "J6_FAIL_CLEAR", { navigate: false });
  const after = await observeQaSurface(page);
  const same = (await readJ6DocumentStamp(page)) === stamp;
  if (!same) fail("J6_DOCUMENT_REMOUNTED");
  const summary = computeJ6Clearance(prior, after, same);
  if (summary.j6_answer_cleared !== 1) fail("J6_ANSWER_NOT_CLEARED");
  if (summary.j6_citations_cleared !== 1) fail("J6_CITATIONS_NOT_CLEARED");
  if (summary.cleared_on_failure !== true) fail("J6_NOT_CLEARED_ON_FAILURE");
  await page.evaluate(`(() => {
    const marked = document.querySelector("[data-testid=\\"material-rag-query\\"]");
    const root = marked && typeof marked.closest === "function" ? marked.closest(".ant-select") : marked;
    const expanded = root?.querySelector("[role=\\"combobox\\"]");
    if (expanded && expanded.getAttribute("aria-expanded") === "true" && typeof root.click === "function") {
      root.click();
    }
    const active = document.activeElement;
    if (active && typeof active.blur === "function") active.blur();
  })()`);
  return summary;
}

async function pageJson(page, expression) {
  return page.evaluate(expression);
}

function tenantDisplayValue(membership) {
  if (!membership || typeof membership !== "object" || Array.isArray(membership)) return null;
  const name = typeof membership.name === "string" ? membership.name.trim() : "";
  const role = typeof membership.role === "string" ? membership.role.trim() : "";
  if (!name || !role) return null;
  return `${name} (${role})`;
}

function limitedTenantSwitchEvidence(evidence) {
  if (!evidence || typeof evidence !== "object" || Array.isArray(evidence)) {
    return {
      step: null,
      action: null,
      visible_dropdown_count: null,
      target_dropdown_count: null,
      target_option_count: null,
    };
  }
  const step = evidence.step;
  const action = evidence.action;
  const count = (value) => (Number.isInteger(value) ? value : null);
  return {
    step: step === "A0" || step === "B1" || step === "A2" || step === "B3" ? step : null,
    action: action === "locate" || action === "click" || action === "commit" ? action : null,
    visible_dropdown_count: count(evidence.visible_dropdown_count),
    target_dropdown_count: count(evidence.target_dropdown_count),
    target_option_count: count(evidence.target_option_count),
  };
}

function failTenantSwitch(family, evidence) {
  const limited = limitedTenantSwitchEvidence(evidence);
  const step = limited.step || "XX";
  const action = limited.action ? limited.action.toUpperCase() : "LOCATE";
  const v = Number.isInteger(limited.visible_dropdown_count) ? limited.visible_dropdown_count : 0;
  const t = Number.isInteger(limited.target_dropdown_count) ? limited.target_dropdown_count : 0;
  const o = Number.isInteger(limited.target_option_count) ? limited.target_option_count : 0;
  const prefix = family === "SWITCH" || family === "OPEN" || family === "CONTROL" || family === "STEP"
    ? family
    : "OPTION";
  throw new VerifyError(
    `UAT_TENANT_${prefix}_${step}_${action}_V${v}_T${t}_O${o}`,
    limited,
  );
}

function tenantSwitchCounts(snapshot) {
  return {
    visible_dropdown_count: Number.isInteger(snapshot?.visible_dropdown_count) ? snapshot.visible_dropdown_count : 0,
    target_dropdown_count: Number.isInteger(snapshot?.target_dropdown_count) ? snapshot.target_dropdown_count : 0,
    target_option_count: Number.isInteger(snapshot?.target_option_count) ? snapshot.target_option_count : 0,
  };
}

function headerTenantDisplay() {
  const header = document.querySelector(".ant-layout-header");
  if (!header || typeof header.querySelector !== "function") return "";
  const select = header.querySelector(".ant-select");
  const root = select || header;
  const item = root.querySelector(".ant-select-selection-item")
    || root.querySelector(".ant-select-content")
    || root.querySelector(".ant-select-selection-item-content");
  if (!item) return "";
  const title = (item.getAttribute("title") ?? "").trim();
  const text = (item.textContent ?? "").trim();
  return text || title;
}

function headerTenantMatches(wanted) {
  const header = document.querySelector(".ant-layout-header");
  if (!header || typeof header.querySelector !== "function") return false;
  const select = header.querySelector(".ant-select");
  const root = select || header;
  const item = root.querySelector(".ant-select-selection-item")
    || root.querySelector(".ant-select-content")
    || root.querySelector(".ant-select-selection-item-content");
  if (!item) return false;
  const title = (item.getAttribute("title") ?? "").trim();
  const text = (item.textContent ?? "").trim();
  return title === wanted || text === wanted;
}

function tenantSwitchBudget(page) {
  if (Number.isFinite(page?.waitTimeout) && page.waitTimeout > 0) {
    return Math.min(page.waitTimeout, WAIT_TIMEOUT_MS);
  }
  return WAIT_TIMEOUT_MS;
}

async function switchMembershipTenant(page, membership, step) {
  if (step !== "A0" && step !== "B1" && step !== "A2" && step !== "B3") {
    failTenantSwitch("STEP", {
      step: null,
      action: "locate",
      visible_dropdown_count: 0,
      target_dropdown_count: 0,
      target_option_count: 0,
    });
  }
  const wanted = tenantDisplayValue(membership);
  const enterpriseId = membership && typeof membership.enterprise_id === "string"
    ? membership.enterprise_id
    : "";
  if (!wanted || !enterpriseId) fail("UAT_VALID_TENANT_MISSING");
  const current = await pageJson(
    page,
    `localStorage.getItem(${JSON.stringify(SELECTED_ENTERPRISE_KEY)})`,
  );
  if (current === enterpriseId) return;
  const emptyCounts = {
    step,
    visible_dropdown_count: 0,
    target_dropdown_count: 0,
    target_option_count: 0,
  };
  try {
    await page.clickElement(".ant-layout-header .ant-select", "UAT_TENANT_SWITCH_CONTROL_MISSING");
  } catch (error) {
    if (error instanceof VerifyError && error.code === "UAT_TENANT_SWITCH_CONTROL_MISSING") {
      failTenantSwitch("CONTROL", { ...emptyCounts, action: "locate" });
    }
    throw error;
  }
  try {
    await page.waitForExpression(
      `document.querySelector('.ant-layout-header [role="combobox"]')?.getAttribute("aria-expanded") === "true"`,
      "UAT_TENANT_SWITCH_OPEN_FAILED",
    );
  } catch (error) {
    if (error instanceof VerifyError && error.code === "UAT_TENANT_SWITCH_OPEN_FAILED") {
      failTenantSwitch("OPEN", { ...emptyCounts, action: "locate" });
    }
    throw error;
  }
  const inspectExpr = `(${inspectTenantSwitchLocate.toString()})(${JSON.stringify(wanted)})`;
  const deadline = Date.now() + tenantSwitchBudget(page);
  let located = {
    visible_dropdown_count: 0,
    target_dropdown_count: 0,
    target_option_count: 0,
    x: null,
    y: null,
  };
  while (Date.now() < deadline) {
    located = await page.evaluate(inspectExpr);
    const counts = tenantSwitchCounts(located);
    if (counts.target_dropdown_count > 1 || counts.target_option_count > 1) {
      failTenantSwitch("OPTION", { step, action: "locate", ...counts });
    }
    if (
      counts.target_dropdown_count === 1
      && counts.target_option_count === 1
      && Number.isFinite(located?.x)
      && Number.isFinite(located?.y)
    ) {
      break;
    }
    await delay(75);
  }
  const counts = tenantSwitchCounts(located);
  if (counts.target_dropdown_count !== 1 || counts.target_option_count !== 1) {
    failTenantSwitch("OPTION", { step, action: "locate", ...counts });
  }
  if (!Number.isFinite(located?.x) || !Number.isFinite(located?.y)) {
    failTenantSwitch("OPTION", { step, action: "click", ...counts });
  }
  await dispatchTenantOptionClick(page, located.x, located.y);
  const commitExpr = `localStorage.getItem(${JSON.stringify(SELECTED_ENTERPRISE_KEY)}) === ${JSON.stringify(enterpriseId)} && (${headerTenantMatches.toString()})(${JSON.stringify(wanted)})`;
  try {
    await page.waitForExpression(commitExpr, "UAT_TENANT_SWITCH_FAILED");
  } catch (error) {
    if (error instanceof VerifyError && error.code === "UAT_TENANT_SWITCH_FAILED") {
      failTenantSwitch("SWITCH", { step, action: "commit", ...counts });
    }
    throw error;
  }
}

async function dispatchTenantOptionClick(page, x, y) {
  await page.cdp.call(
    "Input.dispatchMouseEvent",
    { type: "mouseMoved", x, y },
    page.sessionId,
  );
  await page.cdp.call(
    "Input.dispatchMouseEvent",
    { type: "mousePressed", x, y, button: "left", clickCount: 1 },
    page.sessionId,
  );
  await page.cdp.call(
    "Input.dispatchMouseEvent",
    { type: "mouseReleased", x, y, button: "left", clickCount: 1 },
    page.sessionId,
  );
}

function inspectTenantSwitchLocate(wanted) {
  function isVisibleLayoutBox(el, extraHiddenClass) {
    if (!el || el.hidden) return false;
    if (typeof el.getAttribute === "function" && el.getAttribute("aria-hidden") === "true") return false;
    const className = el.className == null ? "" : String(el.className);
    if (extraHiddenClass && className.includes(extraHiddenClass)) return false;
    const style = getComputedStyle(el);
    const box = el.getBoundingClientRect();
    if (style.display === "none" || style.visibility === "hidden") return false;
    if (box.width <= 0 || box.height <= 0) return false;
    return true;
  }
  function optionExactMatch(el) {
    const title = (el.getAttribute("title") ?? "").trim();
    const contentNode = typeof el.querySelector === "function"
      ? el.querySelector(".ant-select-item-option-content")
      : null;
    const content = ((contentNode && contentNode.textContent) || el.textContent || "").trim();
    return title === wanted || content === wanted;
  }
  function isEnabledOption(el) {
    if (!el || el.hidden) return false;
    if (typeof el.getAttribute === "function" && el.getAttribute("aria-disabled") === "true") return false;
    const className = el.className == null ? "" : String(el.className);
    if (className.includes("ant-select-item-option-disabled")) return false;
    return isVisibleLayoutBox(el);
  }
  function collectTargetOptions(dropdown) {
    const matches = [];
    if (!dropdown || typeof dropdown.querySelectorAll !== "function") return matches;
    for (const el of dropdown.querySelectorAll(".ant-select-item-option")) {
      if (!isEnabledOption(el)) continue;
      if (optionExactMatch(el)) matches.push(el);
    }
    return matches;
  }
  function resolveControlledDropdown(controls) {
    if (!controls || typeof document.getElementById !== "function") return null;
    const node = document.getElementById(controls);
    if (!node) return null;
    const className = node.className == null ? "" : String(node.className);
    if (className.includes("ant-select-dropdown")) return node;
    if (typeof node.closest === "function") {
      const parent = node.closest(".ant-select-dropdown");
      if (parent) return parent;
    }
    return null;
  }
  const visible = [];
  for (const el of document.querySelectorAll(".ant-select-dropdown")) {
    if (isVisibleLayoutBox(el, "ant-select-dropdown-hidden")) visible.push(el);
  }
  const combobox = document.querySelector('.ant-layout-header [role="combobox"]');
  const controls = combobox && typeof combobox.getAttribute === "function"
    ? (combobox.getAttribute("aria-controls") ?? "").trim()
    : "";
  const targetDropdowns = [];
  if (controls) {
    const controlled = resolveControlledDropdown(controls);
    if (controlled && visible.includes(controlled)) targetDropdowns.push(controlled);
  } else {
    for (const dropdown of visible) {
      if (collectTargetOptions(dropdown).length > 0) targetDropdowns.push(dropdown);
    }
  }
  const matches = [];
  for (const dropdown of targetDropdowns) {
    matches.push(...collectTargetOptions(dropdown));
  }
  let x = null;
  let y = null;
  if (matches.length === 1) {
    const match = matches[0];
    if (typeof match.scrollIntoView === "function") {
      match.scrollIntoView({ block: "center", inline: "center" });
    }
    const box = match.getBoundingClientRect();
    if (box.width > 0 && box.height > 0) {
      x = box.left + box.width / 2;
      y = box.top + box.height / 2;
      if (typeof document.elementFromPoint === "function") {
        const hit = document.elementFromPoint(x, y);
        if (!(hit && (hit === match || (typeof match.contains === "function" && match.contains(hit))))) {
          x = null;
          y = null;
        }
      }
    }
  }
  return {
    visible_dropdown_count: visible.length,
    target_dropdown_count: targetDropdowns.length,
    target_option_count: matches.length,
    x,
    y,
  };
}

async function executeMaterialRagUat(cdp, origin, secretDirectory) {
  return runIdentity(
    cdp,
    origin,
    secretDirectory,
    IDENTITIES[0],
    async (page) => {
      await page.waitForExpression(
        `Boolean(localStorage.getItem(${JSON.stringify(SELECTED_ENTERPRISE_KEY)}))`,
        "ADMIN_TENANT_CONTEXT_MISSING",
      );
      const memberships = await pageJson(page, `(async () => {
        const key = Object.keys(sessionStorage).find((name) => name.startsWith("oidc.user:"));
        const session = JSON.parse(sessionStorage.getItem(key) || "null");
        const token = session && typeof session.access_token === "string" ? session.access_token : "";
        if (!token) return { ok: false, reason: "SESSION" };
        const response = await fetch("/api/v1/users/me/enterprises", {
          headers: { Authorization: "Bearer " + token },
        });
        if (response.status !== 200) return { ok: false, reason: "MEMBERSHIP", status: response.status };
        const payload = await response.json();
        const items = Array.isArray(payload) ? payload : [];
        return { ok: true, items };
      })()`);
      const membershipItems = Array.isArray(memberships?.items) ? memberships.items : [];
      const memberA = membershipItems.find((item) => item && item.enterprise_id === UAT_SEED_ENTERPRISE_A);
      const memberB = membershipItems.find((item) => item && item.enterprise_id === UAT_SEED_ENTERPRISE_B);
      if (!memberships?.ok || !tenantDisplayValue(memberA) || !tenantDisplayValue(memberB)) {
        fail("UAT_VALID_TENANT_MISSING");
      }
      const validTenantCount = 2;

      await switchMembershipTenant(page, memberA, "A0");
      const created = await pageJson(page, `(async () => {
        const key = Object.keys(sessionStorage).find((name) => name.startsWith("oidc.user:"));
        const session = JSON.parse(sessionStorage.getItem(key) || "null");
        const token = session && typeof session.access_token === "string" ? session.access_token : "";
        const enterprise = localStorage.getItem(${JSON.stringify(SELECTED_ENTERPRISE_KEY)});
        if (!token || !enterprise) return { ok: false, reason: "SESSION" };
        const listed = await fetch("/api/v1/views-reports/crm/accounts", {
          headers: {
            Authorization: "Bearer " + token,
            "X-Enterprise-Id": enterprise,
          },
        });
        if (listed.status !== 200) return { ok: false, reason: "CRM_LIST", status: listed.status };
        const listing = await listed.json();
        const items = Array.isArray(listing.items) ? listing.items : [];
        const ids = {};
        for (const display_name of [${JSON.stringify(UAT_CLIENT_A_NAME)}, ${JSON.stringify(UAT_CLIENT_B_NAME)}]) {
          const existing = items.find((item) => item && item.display_name === display_name);
          if (existing && typeof existing.id === "string") {
            ids[display_name] = existing.id;
            continue;
          }
          const response = await fetch("/api/v1/views-reports/crm/accounts", {
            method: "POST",
            headers: {
              Authorization: "Bearer " + token,
              "X-Enterprise-Id": enterprise,
              "Content-Type": "application/json",
            },
            body: JSON.stringify({ display_name, stage: "lead" }),
          });
          if (response.status !== 201) return { ok: false, reason: "CRM", status: response.status };
          const payload = await response.json();
          if (typeof payload.id !== "string") return { ok: false, reason: "CRM_ID" };
          ids[display_name] = payload.id;
        }
        return {
          ok: true,
          first: ids[${JSON.stringify(UAT_CLIENT_A_NAME)}],
          second: ids[${JSON.stringify(UAT_CLIENT_B_NAME)}],
        };
      })()`);
      if (!created?.ok || typeof created.first !== "string" || typeof created.second !== "string") {
        fail("UAT_CRM_ACCOUNT_CREATE_FAILED");
      }

      async function uatPost(path, body, enterpriseOverride) {
        return pageJson(page, `(async () => {
          const key = Object.keys(sessionStorage).find((name) => name.startsWith("oidc.user:"));
          const session = JSON.parse(sessionStorage.getItem(key) || "null");
          const token = session && typeof session.access_token === "string" ? session.access_token : "";
          const enterprise = ${JSON.stringify(enterpriseOverride ?? null)}
            || localStorage.getItem(${JSON.stringify(SELECTED_ENTERPRISE_KEY)});
          if (!token || !enterprise) return { status: 0, detail: "SESSION" };
          const response = await fetch(${JSON.stringify(path)}, {
            method: "POST",
            headers: {
              Authorization: "Bearer " + token,
              "X-Enterprise-Id": enterprise,
              "Content-Type": "application/json",
            },
            body: JSON.stringify(${JSON.stringify(body)}),
          });
          let payload = {};
          try { payload = await response.json(); } catch { payload = {}; }
          return {
            status: response.status,
            detail: typeof payload.detail === "string" ? payload.detail : null,
            residual_count: typeof payload.residual_count === "number" ? payload.residual_count : null,
            answer: payload.answer === null || typeof payload.answer === "string" ? payload.answer : null,
            citation_count: Array.isArray(payload.citations) ? payload.citations.length : 0,
            citation_record_id: Array.isArray(payload.citations)
              && payload.citations[0]
              && typeof payload.citations[0].document_record_id === "string"
              ? payload.citations[0].document_record_id
              : null,
            refusal_reason: typeof payload.refusal_reason === "string" ? payload.refusal_reason : null,
          };
        })()`);
      }

      await runUiAskJourney(page, "J1_PROVIDER");
      const providerText = await pageJson(
        page,
        `document.querySelector("[data-testid='material-rag-answer']")?.textContent ?? ""`,
      );
      if (!String(providerText).includes("SYNTH_PROVIDER")) fail("UAT_PROVIDER_ANSWER_MISSING");

      await runUiAskJourney(page, "J2_CLIENT_A", {
        search: `?client=${encodeURIComponent(created.first)}&query=client.current`,
      });
      await runUiAskJourney(page, "J3_COMBO_A", {
        search: `?client=${encodeURIComponent(created.first)}&query=combo.provider_client`,
      });
      await runUiAskJourney(page, "J3_COMBO_B", {
        search: `?client=${encodeURIComponent(created.second)}&query=combo.provider_client`,
      });
      await runUiAskJourney(page, "J4_CLIENT_B_EMPTY", {
        search: `?client=${encodeURIComponent(created.second)}&query=client.current`,
      });

      const foreignEnterprise = await uatPost(
        "/api/v1/local-uat/material-qa",
        { query_id: "provider.shared", request_id: randomUUID() },
        UAT_FOREIGN_ENTERPRISE,
      );
      const unknownClient = await uatPost(
        "/api/v1/local-uat/material-qa",
        {
          query_id: "client.current",
          request_id: randomUUID(),
          client_account_id: UAT_UNKNOWN_CLIENT,
        },
      );
      const foreignCitation = await uatPost(
        "/api/v1/local-uat/material-qa/citation",
        {
          document_record_id: UAT_ENTERPRISE_B_RECORD,
          document_version_id: UAT_ENTERPRISE_B_VERSION,
        },
      );
      if (
        foreignEnterprise.status !== 404
        || unknownClient.status !== 404
        || unknownClient.detail !== "MATERIAL_CONTEXT_NOT_FOUND"
        || foreignCitation.status !== 404
        || foreignCitation.detail !== "MATERIAL_CITATION_NOT_FOUND"
      ) {
        fail("UAT_DENIED_404_MISSING");
      }

      const replayId = randomUUID();
      const firstAsk = await uatPost(
        "/api/v1/local-uat/material-qa",
        { query_id: "provider.shared", request_id: replayId },
      );
      const replayAsk = await uatPost(
        "/api/v1/local-uat/material-qa",
        { query_id: "provider.shared", request_id: replayId },
      );
      const conflictAsk = await uatPost(
        "/api/v1/local-uat/material-qa",
        {
          query_id: "client.current",
          request_id: replayId,
          client_account_id: created.first,
        },
      );
      await uatPost(
        "/api/v1/local-uat/material-qa/rebuild",
        { client_account_id: created.first },
      );
      const deleted = await uatPost(
        "/api/v1/local-uat/material-qa/delete",
        { client_account_id: created.first },
      );
      if (
        firstAsk.status !== 200
        || replayAsk.status !== 200
        || conflictAsk.status !== 409
        || conflictAsk.detail !== "REQUEST_ID_CONFLICT"
        || deleted.status !== 200
        || deleted.residual_count !== 0
      ) {
        fail("UAT_IDEMPOTENT_OR_RESIDUAL_FAILED");
      }

      if (!UAT_JOURNEYS.J6_FAIL_CLEAR) fail("UAT_QUERY_ID_INVALID");
      const j6Clearance = await runJ6FailClear(page);

      const isolationRequestId = randomUUID();
      const tenantAAsk = await uatPost(
        "/api/v1/local-uat/material-qa",
        { query_id: "provider.shared", request_id: isolationRequestId },
        UAT_SEED_ENTERPRISE_A,
      );
      await switchMembershipTenant(page, memberB, "B1");
      const tenantBAsk = await uatPost(
        "/api/v1/local-uat/material-qa",
        { query_id: "provider.shared", request_id: isolationRequestId },
      );
      if (
        tenantAAsk.status !== 200
        || tenantBAsk.status !== 200
        || tenantAAsk.citation_record_id !== UAT_ENTERPRISE_A_RECORD
        || tenantBAsk.citation_record_id !== UAT_ENTERPRISE_B_RECORD
        || tenantAAsk.citation_record_id === tenantBAsk.citation_record_id
      ) {
        fail("UAT_CROSS_TENANT_STATE_LEAK");
      }
      const bOpensA = await uatPost(
        "/api/v1/local-uat/material-qa/citation",
        {
          document_record_id: UAT_ENTERPRISE_A_RECORD,
          document_version_id: UAT_ENTERPRISE_A_VERSION,
        },
      );
      await switchMembershipTenant(page, memberA, "A2");
      const aOpensB = await uatPost(
        "/api/v1/local-uat/material-qa/citation",
        {
          document_record_id: UAT_ENTERPRISE_B_RECORD,
          document_version_id: UAT_ENTERPRISE_B_VERSION,
        },
      );
      if (
        bOpensA.status !== 404
        || bOpensA.detail !== "MATERIAL_CITATION_NOT_FOUND"
        || aOpensB.status !== 404
        || aOpensB.detail !== "MATERIAL_CITATION_NOT_FOUND"
      ) {
        fail("UAT_CROSS_TENANT_CITATION_LEAK");
      }
      const deletedAfterIsolation = await uatPost(
        "/api/v1/local-uat/material-qa/delete",
        { client_account_id: created.first },
        UAT_SEED_ENTERPRISE_A,
      );
      await switchMembershipTenant(page, memberB, "B3");
      const tenantBAfterDelete = await uatPost(
        "/api/v1/local-uat/material-qa",
        { query_id: "provider.shared", request_id: isolationRequestId },
      );
      if (
        deletedAfterIsolation.status !== 200
        || deletedAfterIsolation.residual_count !== 0
        || tenantBAfterDelete.status !== 200
        || tenantBAfterDelete.citation_record_id !== UAT_ENTERPRISE_B_RECORD
      ) {
        fail("UAT_CROSS_TENANT_DELETE_LEAK");
      }

      const headerSummary = summarizeUiAskHeaders(page);
      if (headerSummary.authorization_header_present !== 1) fail("UAT_AUTHORIZATION_HEADER_MISSING");
      if (headerSummary.enterprise_header_present !== 1) fail("UAT_ENTERPRISE_HEADER_MISSING");
      if (headerSummary.uat_actor_header_present !== 0) fail("UAT_ACTOR_HEADER_PRESENT");

      return {
        stage: "material-rag-uat",
        journeys_passed: 6,
        residual_count: deleted.residual_count,
        authorization_header_present: headerSummary.authorization_header_present,
        enterprise_header_present: headerSummary.enterprise_header_present,
        uat_actor_header_present: headerSummary.uat_actor_header_present,
        denied_404: 1,
        conflict_409: 1,
        unavailable_503: 1,
        valid_tenant_count: validTenantCount,
        cross_tenant_state_isolated: 1,
        cross_tenant_citation_denied: 2,
        cross_tenant_delete_isolated: 1,
        human_uat_url_ready: 1,
        j6_prior_answer: j6Clearance.j6_prior_answer,
        j6_prior_citations: j6Clearance.j6_prior_citations,
        j6_same_document: j6Clearance.j6_same_document,
        j6_answer_cleared: j6Clearance.j6_answer_cleared,
        j6_citations_cleared: j6Clearance.j6_citations_cleared,
        cleared_on_failure: j6Clearance.cleared_on_failure,
      };
    },
  );
}

async function executeMaterialRagUatHuman(cdp, origin, secretDirectory) {
  return runIdentity(
    cdp,
    origin,
    secretDirectory,
    IDENTITIES[0],
    async (page) => {
      await page.waitForExpression(
        `Boolean(localStorage.getItem(${JSON.stringify(SELECTED_ENTERPRISE_KEY)}))`,
        "ADMIN_TENANT_CONTEXT_MISSING",
      );
      await navigateQa(page);
      await page.waitForExpression(
        `Boolean(document.querySelector("[data-testid=\\"material-rag-query\\"]"))`,
        "UAT_HUMAN_QA_MISSING",
      );
      return {
        stage: "material-rag-uat-human",
        human_uat_url_ready: 1,
      };
    },
  );
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
  if (stage === "material-rag-uat") {
    return executeMaterialRagUat(cdp, origin, secretDirectory);
  }
  if (stage === "material-rag-uat-human") {
    return executeMaterialRagUatHuman(cdp, origin, secretDirectory);
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
  const visibleHuman = stage === "material-rag-uat-human";
  const runtime = await launchChrome(headed || visibleHuman, installPwa, controlDirectory, visibleHuman);
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
      if (visibleHuman && primaryError == null) {
        if (runtime.cdp) {
          runtime.cdp.close();
          runtime.cdp = null;
        }
      } else {
        await cleanupChrome(runtime);
      }
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

function isMainModule() {
  try {
    return fileURLToPath(import.meta.url) === path.resolve(process.argv[1] ?? "");
  } catch {
    return false;
  }
}

if (isMainModule()) {
  main().catch((error) => {
    const code = error instanceof VerifyError ? error.code : unexpectedFailureReason;
    const lines = [`LOCAL_BROWSER_VERIFY_FAILED ${code}`];
    if (error instanceof VerifyError && error.evidence) {
      lines.push(JSON.stringify(error.evidence));
    }
    process.stderr.write(`${lines.join("\n")}\n`);
    process.exitCode = 1;
  });
}

export {
  UAT_JOURNEYS,
  UAT_QUERY_LABELS,
  VerifyError,
  clickAsk,
  computeJ6Clearance,
  headerTenantDisplay,
  inspectTenantSwitchLocate,
  limitedTenantSwitchEvidence,
  navigateQa,
  observeQaSurface,
  runJ6FailClear,
  runUiAskJourney,
  selectClosedQuery,
  summarizeUiAskHeaders,
  switchMembershipTenant,
  tenantDisplayValue,
};
