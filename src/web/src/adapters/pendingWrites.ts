// Tab-scoped write identities survive reloads without storing business payloads
// or credentials. Unknown commands are retained until a validated receipt.
import { ApiError } from "./errors";

export interface PendingWrite {
  key: string;
  requestId: string;
}
const PREFIX = "anhuan.pending-write.v1.";
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function pendingWrite(
  operation: string, enterpriseId: string, subject: string, canonicalPayload: string,
): Promise<PendingWrite> {
  if (!operation || !enterpriseId || !subject) throw new ApiError(409, "PENDING_WRITE_IDENTITY_REQUIRED", false);
  const bytes = new TextEncoder().encode(JSON.stringify([operation, enterpriseId, subject, canonicalPayload]));
  let key: string;
  try {
    const digest = await crypto.subtle.digest("SHA-256", bytes);
    key = PREFIX + Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, "0")).join("");
  } catch {
    throw new ApiError(409, "PENDING_WRITE_STORAGE_UNAVAILABLE", false);
  }
  try {
    const prior = sessionStorage.getItem(key);
    if (prior !== null) {
      // Never replace an unreadable pending identity with a new POST identity.
      if (!UUID.test(prior)) throw new Error("INVALID_PENDING_WRITE");
      return {key, requestId: prior};
    }
    const requestId = crypto.randomUUID();
    sessionStorage.setItem(key, requestId);
    if (sessionStorage.getItem(key) !== requestId) throw new Error("WRITE_NOT_RETAINED");
    return {key, requestId};
  } catch {
    throw new ApiError(409, "PENDING_WRITE_STORAGE_UNAVAILABLE", false);
  }
}

export function completePendingWrite(write: PendingWrite): void {
  try {
    // A delayed receipt may only clear its own operation identity.
    if (sessionStorage.getItem(write.key) === write.requestId) {
      sessionStorage.removeItem(write.key);
    }
  } catch {
    // The receipt has already confirmed success. Cleanup failure must never
    // tell the caller to retry a command whose identity may now be deleted.
    // A retained identity safely replays the existing result on a later call.
  }
}
