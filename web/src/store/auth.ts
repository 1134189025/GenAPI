"use client";

import localforage from "localforage";

export type AuthRole = "admin" | "user";

export type StoredAuthSession = {
  key: string;
  role: AuthRole;
  subjectId: string;
  name: string;
};

export const AUTH_KEY_STORAGE_KEY = "genapi_auth_key";
export const AUTH_SESSION_STORAGE_KEY = "genapi_auth_session";
export const AUTH_SESSION_BROADCAST_CHANNEL = "genapi_auth_session_events";
const AUTH_STORAGE_TIMEOUT_MS = 3000;

const authStorage = localforage.createInstance({
  name: "genapi",
  storeName: "auth",
});

function broadcastAuthSessionChanged(session: StoredAuthSession | null) {
  if (typeof window === "undefined" || typeof BroadcastChannel === "undefined") {
    return;
  }
  const channel = new BroadcastChannel(AUTH_SESSION_BROADCAST_CHANNEL);
  channel.postMessage({
    type: "auth-session-changed",
    subjectId: session?.subjectId || "",
    hasSession: Boolean(session),
  });
  channel.close();
}

function normalizeSession(value: unknown, fallbackKey = ""): StoredAuthSession | null {
  if (!value || typeof value !== "object") {
    return null;
  }

  const candidate = value as Partial<StoredAuthSession>;
  const key = String(candidate.key || fallbackKey || "").trim();
  const role = candidate.role === "admin" || candidate.role === "user" ? candidate.role : null;
  if (!key || !role) {
    return null;
  }

  return {
    key,
    role,
    subjectId: String(candidate.subjectId || "").trim(),
    name: String(candidate.name || "").trim(),
  };
}

type AuthStorageResult<T> = {
  ok: boolean;
  value: T;
};

async function withAuthStorageTimeout<T>(operation: Promise<T>, fallback: T): Promise<AuthStorageResult<T>> {
  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  try {
    return await Promise.race([
      operation.then(
        (value) => ({ ok: true, value }),
        () => ({ ok: false, value: fallback }),
      ),
      new Promise<AuthStorageResult<T>>((resolve) => {
        timeoutId = setTimeout(() => resolve({ ok: false, value: fallback }), AUTH_STORAGE_TIMEOUT_MS);
      }),
    ]);
  } catch {
    return { ok: false, value: fallback };
  } finally {
    if (timeoutId) {
      clearTimeout(timeoutId);
    }
  }
}

export function getDefaultRouteForRole(role: AuthRole) {
  return role === "admin" ? "/admin/accounts" : "/image";
}

export async function getStoredAuthKey() {
  if (typeof window === "undefined") {
    return "";
  }
  const result = await withAuthStorageTimeout(authStorage.getItem<string>(AUTH_KEY_STORAGE_KEY), "");
  if (!result.ok) {
    await clearStoredAuthSession();
    return "";
  }
  return String(result.value || "").trim();
}

export async function getStoredAuthSession() {
  if (typeof window === "undefined") {
    return null;
  }

  const storedResult = await withAuthStorageTimeout(
    Promise.all([
      authStorage.getItem<string>(AUTH_KEY_STORAGE_KEY),
      authStorage.getItem<StoredAuthSession>(AUTH_SESSION_STORAGE_KEY),
    ]),
    ["", null] as [string, StoredAuthSession | null],
  );
  if (!storedResult.ok) {
    await clearStoredAuthSession();
    return null;
  }

  const [storedKey, storedSession] = storedResult.value;

  const normalizedSession = normalizeSession(storedSession, String(storedKey || ""));
  if (normalizedSession) {
    if (normalizedSession.key !== String(storedKey || "").trim()) {
      const writeResult = await withAuthStorageTimeout(
        authStorage.setItem(AUTH_KEY_STORAGE_KEY, normalizedSession.key),
        undefined,
      );
      if (!writeResult.ok) {
        await clearStoredAuthSession();
        return null;
      }
    }
    return normalizedSession;
  }

  if (String(storedKey || "").trim()) {
    await clearStoredAuthSession();
  }
  return null;
}

export async function setStoredAuthSession(session: StoredAuthSession) {
  const normalizedSession = normalizeSession(session);
  if (!normalizedSession) {
    await clearStoredAuthSession();
    return;
  }

  const writeResult = await withAuthStorageTimeout(
    Promise.all([
      authStorage.setItem(AUTH_KEY_STORAGE_KEY, normalizedSession.key),
      authStorage.setItem(AUTH_SESSION_STORAGE_KEY, normalizedSession),
    ]),
    undefined,
  );
  if (!writeResult.ok) {
    await clearStoredAuthSession();
    throw new Error("Failed to persist auth session");
  }
  broadcastAuthSessionChanged(normalizedSession);
}

export async function setStoredAuthKey(authKey: string) {
  const normalizedAuthKey = String(authKey || "").trim();
  if (!normalizedAuthKey) {
    await clearStoredAuthSession();
    return;
  }
  const writeResult = await withAuthStorageTimeout(authStorage.setItem(AUTH_KEY_STORAGE_KEY, normalizedAuthKey), undefined);
  if (!writeResult.ok) {
    await clearStoredAuthSession();
    throw new Error("Failed to persist auth session");
  }
  broadcastAuthSessionChanged(null);
}

export async function clearStoredAuthSession() {
  if (typeof window === "undefined") {
    return;
  }
  await withAuthStorageTimeout(
    Promise.all([
      authStorage.removeItem(AUTH_KEY_STORAGE_KEY),
      authStorage.removeItem(AUTH_SESSION_STORAGE_KEY),
    ]),
    undefined,
  );
  broadcastAuthSessionChanged(null);
}

export async function clearStoredAuthSessionIfCurrent(expectedKey: string) {
  if (typeof window === "undefined") {
    return false;
  }
  const normalizedExpectedKey = String(expectedKey || "").trim();
  if (!normalizedExpectedKey) {
    return false;
  }
  const storedResult = await withAuthStorageTimeout(
    Promise.all([
      authStorage.getItem<string>(AUTH_KEY_STORAGE_KEY),
      authStorage.getItem<StoredAuthSession>(AUTH_SESSION_STORAGE_KEY),
    ]),
    ["", null] as [string, StoredAuthSession | null],
  );
  if (!storedResult.ok) {
    return false;
  }

  const [storedKey, storedSession] = storedResult.value;
  const normalizedSession = normalizeSession(storedSession, String(storedKey || ""));
  const currentKey = normalizedSession?.key || String(storedKey || "").trim();
  if (currentKey !== normalizedExpectedKey) {
    return false;
  }

  await clearStoredAuthSession();
  return true;
}

export async function clearStoredAuthKey() {
  await clearStoredAuthSession();
}
