import { createHash, randomUUID } from "node:crypto";
import { constants } from "node:fs";
import { lstat, open, opendir, rename, rm } from "node:fs/promises";
import path from "node:path";

const PLACEHOLDER = "__ANHUAN_PWA_BUILD_ID__";
const WORKER_RELATIVE_PATH = "pwa-sw.js";
const HASH_DOMAIN = Buffer.from("ANHUAN_INTERNAL_PWA_BUILD_V1\0", "utf8");

function fail(code) {
  throw new Error(code);
}

function validateRelativePath(root, absolutePath) {
  const nativeRelative = path.relative(root, absolutePath);
  const segments = nativeRelative.split(path.sep);
  if (
    nativeRelative === "" ||
    path.isAbsolute(nativeRelative) ||
    segments.some((segment) => segment === "" || segment === "." || segment === ".." || segment.includes("\0"))
  ) {
    fail("PWA_BUILD_PATH_TRAVERSAL");
  }
  return segments.join("/");
}

function directorySnapshot(absolutePath, status) {
  return {
    absolutePath,
    device: status.dev,
    inode: status.ino,
    size: status.size,
    modifiedAt: status.mtimeMs,
    changedAt: status.ctimeMs,
  };
}

function matchesDirectorySnapshot(status, snapshot) {
  return (
    status.isDirectory() &&
    status.dev === snapshot.device &&
    status.ino === snapshot.inode &&
    status.size === snapshot.size &&
    status.mtimeMs === snapshot.modifiedAt &&
    status.ctimeMs === snapshot.changedAt
  );
}

async function collectRegularFiles(root) {
  const files = [];
  const directories = [];

  async function visit(directory) {
    const pathStatus = await lstat(directory);
    if (pathStatus.isSymbolicLink()) fail("PWA_BUILD_SYMLINK_REJECTED");
    if (!pathStatus.isDirectory()) fail("PWA_BUILD_NON_REGULAR_REJECTED");
    const snapshot = directorySnapshot(directory, pathStatus);
    let directoryFd;
    try {
      directoryFd = await open(
        directory,
        constants.O_RDONLY | constants.O_DIRECTORY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
      );
    } catch {
      fail("PWA_BUILD_DIRECTORY_OPEN_REJECTED");
    }
    try {
      if (!matchesDirectorySnapshot(await directoryFd.stat(), snapshot)) {
        fail("PWA_BUILD_DIRECTORY_CHANGED");
      }
      const stream = await opendir(directory);
      for await (const entry of stream) {
        const absolutePath = path.join(directory, entry.name);
        const relativePath = validateRelativePath(root, absolutePath);
        const status = await lstat(absolutePath);
        if (status.isSymbolicLink()) fail("PWA_BUILD_SYMLINK_REJECTED");
        if (status.isDirectory()) {
          await visit(absolutePath);
        } else if (status.isFile()) {
          files.push({
            absolutePath,
            relativePath,
            mode: status.mode,
            device: status.dev,
            inode: status.ino,
            size: status.size,
            modifiedAt: status.mtimeMs,
            changedAt: status.ctimeMs,
          });
        } else {
          fail("PWA_BUILD_NON_REGULAR_REJECTED");
        }
      }
      const finalFdStatus = await directoryFd.stat();
      const finalPathStatus = await lstat(directory);
      if (
        finalPathStatus.isSymbolicLink() ||
        !matchesDirectorySnapshot(finalFdStatus, snapshot) ||
        !matchesDirectorySnapshot(finalPathStatus, snapshot)
      ) {
        fail("PWA_BUILD_DIRECTORY_CHANGED");
      }
    } finally {
      await directoryFd.close();
    }
    directories.push(snapshot);
  }

  await visit(root);
  files.sort((left, right) => {
    if (left.relativePath < right.relativePath) return -1;
    if (left.relativePath > right.relativePath) return 1;
    return 0;
  });
  return { files, directories };
}

function lengthBoundary(length) {
  const boundary = Buffer.alloc(8);
  boundary.writeBigUInt64BE(BigInt(length));
  return boundary;
}

function matchesSnapshot(status, file) {
  return (
    status.isFile() &&
    status.dev === file.device &&
    status.ino === file.inode &&
    status.size === file.size &&
    status.mtimeMs === file.modifiedAt &&
    status.ctimeMs === file.changedAt
  );
}

async function readVerifiedRegularFile(file) {
  let handle;
  try {
    handle = await open(
      file.absolutePath,
      constants.O_RDONLY | constants.O_NOFOLLOW | constants.O_NONBLOCK,
    );
  } catch {
    fail("PWA_BUILD_FILE_OPEN_REJECTED");
  }
  try {
    const before = await handle.stat();
    if (!matchesSnapshot(before, file)) fail("PWA_BUILD_FILE_CHANGED");
    const contents = await handle.readFile();
    const after = await handle.stat();
    if (!matchesSnapshot(after, file) || contents.length !== file.size) {
      fail("PWA_BUILD_FILE_CHANGED");
    }
    return contents;
  } finally {
    await handle.close();
  }
}

async function calculateBuildId(files) {
  const hash = createHash("sha256");
  hash.update(HASH_DOMAIN);
  let workerContents;
  for (const file of files) {
    const relativeBytes = Buffer.from(file.relativePath, "utf8");
    const contents = await readVerifiedRegularFile(file);
    hash.update(lengthBoundary(relativeBytes.length));
    hash.update(relativeBytes);
    hash.update(lengthBoundary(contents.length));
    hash.update(contents);
    if (file.relativePath === WORKER_RELATIVE_PATH) workerContents = contents;
  }
  if (!workerContents) fail("PWA_BUILD_WORKER_MISSING");
  return { buildId: hash.digest("hex"), workerContents };
}

async function verifyTreeUnchanged(files, directories) {
  for (const directory of directories) {
    const status = await lstat(directory.absolutePath);
    if (status.isSymbolicLink() || !matchesDirectorySnapshot(status, directory)) {
      fail("PWA_BUILD_DIRECTORY_CHANGED");
    }
  }
  for (const file of files) {
    const status = await lstat(file.absolutePath);
    if (status.isSymbolicLink() || !matchesSnapshot(status, file)) {
      fail("PWA_BUILD_FILE_CHANGED");
    }
  }
}

async function injectBuildId(root, files, buildId, workerContents) {
  const worker = files.find((file) => file.relativePath === WORKER_RELATIVE_PATH);
  if (!worker) fail("PWA_BUILD_WORKER_MISSING");

  const source = workerContents.toString("utf8");
  const placeholderCount = source.split(PLACEHOLDER).length - 1;
  if (placeholderCount !== 1) fail("PWA_BUILD_PLACEHOLDER_COUNT_INVALID");
  const rendered = source.replace(PLACEHOLDER, buildId);
  const temporaryPath = path.join(root, `.pwa-sw.js.${process.pid}.${randomUUID()}.tmp`);
  let temporaryHandle;
  try {
    temporaryHandle = await open(
      temporaryPath,
      constants.O_CREAT | constants.O_EXCL | constants.O_WRONLY,
      worker.mode & 0o777,
    );
    await temporaryHandle.writeFile(rendered, "utf8");
    await temporaryHandle.sync();
    await temporaryHandle.close();
    temporaryHandle = undefined;
    const currentWorker = await lstat(worker.absolutePath);
    if (currentWorker.isSymbolicLink() || !matchesSnapshot(currentWorker, worker)) {
      fail("PWA_BUILD_FILE_CHANGED");
    }
    await rename(temporaryPath, worker.absolutePath);
  } catch (error) {
    await temporaryHandle?.close().catch(() => undefined);
    await rm(temporaryPath, { force: true }).catch(() => undefined);
    throw error;
  }
}

async function main() {
  const requestedRoot = process.argv[2] ?? "dist";
  if (requestedRoot.split(/[\\/]+/).includes("..")) fail("PWA_BUILD_PATH_TRAVERSAL");
  const root = path.resolve(requestedRoot);
  const rootStatus = await lstat(root);
  if (rootStatus.isSymbolicLink() || !rootStatus.isDirectory()) fail("PWA_BUILD_ROOT_INVALID");
  const { files, directories } = await collectRegularFiles(root);
  const { buildId, workerContents } = await calculateBuildId(files);
  if (!/^[0-9a-f]{64}$/.test(buildId)) fail("PWA_BUILD_ID_INVALID");
  await verifyTreeUnchanged(files, directories);
  await injectBuildId(root, files, buildId, workerContents);
  process.stdout.write(`PWA_BUILD_ID_INJECTED ${buildId}\n`);
}

main().catch((error) => {
  const message = error instanceof Error ? error.message : "PWA_BUILD_INJECTION_FAILED";
  process.stderr.write(`${message}\n`);
  process.exitCode = 1;
});
