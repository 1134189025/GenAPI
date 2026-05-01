import { beforeEach, describe, expect, mock, test } from "bun:test";
import { readFileSync } from "node:fs";
import { join } from "node:path";

const root = join(import.meta.dir, "..");
const hung = Symbol("hung");
const contractTimeoutMs = 3600;

function source(path: string) {
  return readFileSync(join(root, path), "utf8");
}

function delay(ms: number) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

async function withContractTimeout<T>(promise: Promise<T>) {
  return Promise.race([promise, delay(contractTimeoutMs).then(() => hung)]);
}

async function settleWithContractTimeout<T>(promise: Promise<T>) {
  return Promise.race([
    promise.then(
      () => "resolved" as const,
      () => "rejected" as const,
    ),
    delay(contractTimeoutMs).then(() => hung),
  ]);
}

const storage = new Map<string, unknown>();
let authImportCounter = 0;
let authSessionImportCounter = 0;
let getItemImpl = async (key: string) => storage.get(key) ?? null;
let setItemImpl = async (key: string, value: unknown) => {
  storage.set(key, value);
  return value;
};
let removeItemImpl = async (key: string) => {
  storage.delete(key);
};

mock.module("localforage", () => ({
  default: {
    createInstance: () => ({
      getItem: mock((key: string) => getItemImpl(key)),
      setItem: mock((key: string, value: unknown) => setItemImpl(key, value)),
      removeItem: mock((key: string) => removeItemImpl(key)),
    }),
  },
}));

mock.module("@/lib/request", () => ({
  httpRequest: mock(async () => ({
    user: {
      id: "verified-user",
      email: "verified@example.com",
      role: "user" as const,
    },
  })),
  shouldRedirectAfterUnauthorizedAuthFailure: async (
    authKeyUsed: string,
    deps: {
      clearCurrentSession: (expectedKey: string) => Promise<boolean>;
      getSession: () => Promise<unknown>;
    },
  ) => {
    const token = String(authKeyUsed || "").trim();
    if (token) {
      return deps.clearCurrentSession(token);
    }
    return !(await deps.getSession());
  },
  createAuthorizationHeaders: (headers: Record<string, string> | undefined, authKey: string) => {
    const nextHeaders = { ...(headers || {}) };
    const hasAuthorization = Object.entries(nextHeaders).some(
      ([key, value]) => key.toLowerCase() === "authorization" && String(value || "").trim(),
    );
    const token = String(authKey || "").trim();
    if (token && !hasAuthorization) {
      nextHeaders.Authorization = `Bearer ${token}`;
    }
    return nextHeaders;
  },
}));

async function importAuthStore() {
  authImportCounter += 1;
  return import(`../src/store/auth.ts?auth-contract-${Date.now()}-${authImportCounter}`);
}

async function importAuthSession() {
  authSessionImportCounter += 1;
  return import(`../src/lib/auth-session.ts?auth-session-contract-${Date.now()}-${authSessionImportCounter}`);
}

function resetBrowserGlobals() {
  (globalThis as typeof globalThis & { window?: unknown }).window = {};
  (globalThis as typeof globalThis & { BroadcastChannel?: unknown }).BroadcastChannel = undefined;
}

beforeEach(() => {
  resetBrowserGlobals();
  storage.clear();
  getItemImpl = async (key: string) => storage.get(key) ?? null;
  setItemImpl = async (key: string, value: unknown) => {
    storage.set(key, value);
    return value;
  };
  removeItemImpl = async (key: string) => {
    storage.delete(key);
  };
});

describe("request bootstrap timeout contracts", () => {
  test("passes explicit timeoutMs through to axios without changing normal request defaults", () => {
    const requestSource = source("src/lib/request.ts");

    expect(requestSource).toContain("timeoutMs?: number");
    expect(requestSource).toContain("resolveRequestTimeoutMs(path, timeoutMs)");
    expect(requestSource).toContain("if (timeout !== undefined)");
    expect(requestSource).toContain("config.timeout = timeout");
    expect(requestSource).not.toContain("timeout: timeoutMs");
  });

  test("uses a short timeout for setup status checks even when the caller omits timeoutMs", () => {
    const requestSource = source("src/lib/request.ts");

    expect(requestSource).toContain("AUTH_SETUP_CHECK_TIMEOUT_MS");
    expect(requestSource).toContain('AUTH_SETUP_STATUS_PATH = "/api/setup/status"');
    expect(requestSource).toContain("requestPath === AUTH_SETUP_STATUS_PATH");
  });

  test("unauthorized redirects reject instead of leaving callers pending forever", () => {
    const requestSource = source("src/lib/request.ts");

    expect(requestSource).toContain('window.location.replace("/login")');
    expect(requestSource).not.toContain("return new Promise(() => {})");
    expect(requestSource).not.toContain("never-resolving");
    expect(requestSource).toContain("return Promise.reject(new Error(message))");
  });
});

describe("auth storage fail-closed contracts", () => {
  test("uses a mobile-tolerant storage timeout before failing closed", () => {
    const authSource = source("src/store/auth.ts");
    const match = authSource.match(/AUTH_STORAGE_TIMEOUT_MS\s*=\s*(\d+)/);

    expect(match).not.toBeNull();
    expect(Number(match?.[1] ?? 0)).toBeGreaterThanOrEqual(3000);
  });

  test("returns no session when auth storage reads never settle", async () => {
    getItemImpl = async () => new Promise(() => {});
    const auth = await importAuthStore();

    const result = await withContractTimeout(auth.getStoredAuthSession());

    expect(result).toBeNull();
  });

  test("returns an empty auth key when auth storage reads never settle", async () => {
    getItemImpl = async () => new Promise(() => {});
    const auth = await importAuthStore();

    const result = await withContractTimeout(auth.getStoredAuthKey());

    expect(result).toBe("");
  });

  test("returns an empty auth key when auth storage reads reject", async () => {
    getItemImpl = async () => {
      throw new Error("indexeddb unavailable");
    };
    const auth = await importAuthStore();

    await expect(auth.getStoredAuthKey()).resolves.toBe("");
  });

  test("does not hang when session writes never settle", async () => {
    setItemImpl = async () => new Promise(() => {});
    const auth = await importAuthStore();

    const result = await settleWithContractTimeout(
      auth.setStoredAuthSession({
        key: "token-a",
        role: "user",
        subjectId: "user-a",
        name: "User A",
      }),
    );

    expect(result).not.toBe(hung);
  });

  test("does not hang when auth cleanup never settles", async () => {
    removeItemImpl = async () => new Promise(() => {});
    const auth = await importAuthStore();

    const result = await withContractTimeout(auth.clearStoredAuthSession());

    expect(result).not.toBe(hung);
  });
});

describe("redirect auth guard loading contracts", () => {
  test("redirect guard exits checking state through a final failure-safe path", () => {
    const guard = source("src/lib/use-auth-guard.ts");
    const authSession = source("src/lib/auth-session.ts");
    const hook = guard.slice(guard.indexOf("export function useRedirectIfAuthenticated"));

    expect(authSession).toContain("AUTH_SESSION_VERIFY_TIMEOUT_MS");
    expect(authSession).toContain("timeoutMs: AUTH_SESSION_VERIFY_TIMEOUT_MS");
    expect(hook).toContain("finally");
    expect(hook).toContain("setIsCheckingAuth(false)");
    expect(hook).toContain("verifyStoredAuthSession");
    expect(hook.indexOf("finally")).toBeLessThan(hook.lastIndexOf("setIsCheckingAuth(false)"));
  });

  test("app shell reuses the bounded auth session verifier instead of a parallel fetchMe check", () => {
    const shell = source("src/components/layout/app-shell.tsx");
    const shellSessionEffect = shell.slice(shell.indexOf("const loadSession = async () => {"), shell.indexOf("const handleLogout"));

    expect(shell).toContain('verifyStoredAuthSession } from "@/lib/auth-session";');
    expect(shellSessionEffect).toContain("await verifyStoredAuthSession(storedSession)");
    expect(shellSessionEffect).not.toContain("fetchMe(false)");
    expect(shellSessionEffect).not.toContain("await setStoredAuthSession(verifiedSession)");
  });

  test("session verification binds the request to the stored key and ignores stale responses", () => {
    const authSession = source("src/lib/auth-session.ts");
    const verifyBody = authSession.slice(
      authSession.indexOf("export async function verifyAuthSessionWithDeps"),
      authSession.indexOf("export async function verifyStoredAuthSession"),
    );

    expect(authSession).toContain("class AuthSessionChangedError extends Error");
    expect(authSession).toContain("isAuthSessionChangedError");
    expect(authSession).toContain("verifyAuthSessionWithDeps");
    expect(verifyBody).toContain("const storedKey = String(storedSession.key || \"\").trim();");
    expect(authSession).toContain("requestAuthMe: (storedKey) =>");
    expect(authSession).toContain("headers: { Authorization: `Bearer ${storedKey}` }");
    expect(verifyBody).toContain("const latestSession = await deps.getSession();");
    expect(verifyBody).toContain("latestSession.key !== storedKey");
    expect(verifyBody).toContain("throw new AuthSessionChangedError(latestSession)");
    expect(verifyBody).toContain("catch (error)");
    expect(verifyBody).toContain("const latestSessionAfterError = await deps.getSession();");
    expect(verifyBody).toContain("latestSessionAfterError.key !== storedKey");
    expect(verifyBody).toContain("throw error;");
    expect(verifyBody).not.toContain("await setStoredAuthSession(verifiedSession)");
  });

  test("stale successful session verification cannot overwrite a newer stored session", async () => {
    const { AuthSessionChangedError, verifyAuthSessionWithDeps } = await importAuthSession();
    const oldSession = {
      key: "old-token",
      role: "user" as const,
      subjectId: "old-id",
      name: "old@example.com",
    };
    const newSession = {
      key: "new-token",
      role: "user" as const,
      subjectId: "new-id",
      name: "new@example.com",
    };

    let currentSession = oldSession;
    const result = await verifyAuthSessionWithDeps(oldSession, {
      requestAuthMe: async () => {
        currentSession = newSession;
        return {
          user: {
            id: "old-id",
            email: "old@example.com",
            role: "user" as const,
          },
        };
      },
      getSession: async () => currentSession,
      clearCurrentSession: async () => {
        throw new Error("success path must not clear");
      },
    }).catch((error: unknown) => error);

    expect(result).toBeInstanceOf(AuthSessionChangedError);
    expect((result as InstanceType<typeof AuthSessionChangedError>).latestSession).toEqual(newSession);
    expect(currentSession).toEqual(newSession);
  });

  test("current successful session verification returns verified server identity without writing storage", async () => {
    const { verifyAuthSessionWithDeps } = await importAuthSession();
    const oldSession = {
      key: "old-token",
      role: "user" as const,
      subjectId: "old-id",
      name: "old@example.com",
    };
    let writes = 0;

    await expect(
      verifyAuthSessionWithDeps(oldSession, {
        requestAuthMe: async () => ({
          user: {
            id: "verified-id",
            email: "verified@example.com",
            role: "admin" as const,
          },
        }),
        getSession: async () => oldSession,
        clearCurrentSession: async () => {
          writes += 1;
          return true;
        },
      }),
    ).resolves.toEqual({
      key: "old-token",
      role: "admin",
      subjectId: "verified-id",
      name: "verified@example.com",
    });
    expect(writes).toBe(0);
  });

  test("stale failed session verification cannot clear a newer stored session", async () => {
    const { AuthSessionChangedError, verifyAuthSessionWithDeps } = await importAuthSession();
    const oldSession = {
      key: "old-token",
      role: "user" as const,
      subjectId: "old-id",
      name: "old@example.com",
    };
    const newSession = {
      key: "new-token",
      role: "user" as const,
      subjectId: "new-id",
      name: "new@example.com",
    };

    let currentSession = oldSession;
    const result = await verifyAuthSessionWithDeps(oldSession, {
      requestAuthMe: async () => {
        currentSession = newSession;
        throw new Error("stale token rejected");
      },
      getSession: async () => currentSession,
      clearCurrentSession: async () => {
        currentSession = null as never;
        return true;
      },
    }).catch((error: unknown) => error);

    expect(result).toBeInstanceOf(AuthSessionChangedError);
    expect((result as InstanceType<typeof AuthSessionChangedError>).latestSession).toEqual(newSession);
    expect(currentSession).toEqual(newSession);
  });

  test("current failed session verification clears only the matching stored session", async () => {
    const { verifyAuthSessionWithDeps } = await importAuthSession();
    const oldSession = {
      key: "old-token",
      role: "user" as const,
      subjectId: "old-id",
      name: "old@example.com",
    };
    let currentSession: typeof oldSession | null = oldSession;

    await expect(
      verifyAuthSessionWithDeps(oldSession, {
        requestAuthMe: async () => {
          throw new Error("token rejected");
        },
        getSession: async () => currentSession,
        clearCurrentSession: async (expectedKey: string) => {
          if (currentSession?.key !== expectedKey) {
            return false;
          }
          currentSession = null;
          return true;
        },
      }),
    ).rejects.toThrow("token rejected");
    expect(currentSession).toBeNull();
  });

  test("failed session verification reports a race if conditional cleanup loses ownership", async () => {
    const { AuthSessionChangedError, verifyAuthSessionWithDeps } = await importAuthSession();
    const oldSession = {
      key: "old-token",
      role: "user" as const,
      subjectId: "old-id",
      name: "old@example.com",
    };
    const newSession = {
      key: "new-token",
      role: "user" as const,
      subjectId: "new-id",
      name: "new@example.com",
    };

    let currentSession = oldSession;
    const result = await verifyAuthSessionWithDeps(oldSession, {
      requestAuthMe: async () => {
        throw new Error("token rejected");
      },
      getSession: async () => currentSession,
      clearCurrentSession: async () => {
        currentSession = newSession;
        return false;
      },
    }).catch((error: unknown) => error);

    expect(result).toBeInstanceOf(AuthSessionChangedError);
    expect((result as InstanceType<typeof AuthSessionChangedError>).latestSession).toEqual(newSession);
    expect(currentSession).toEqual(newSession);
  });

  test("stored auth conditional cleanup preserves newer sessions", async () => {
    const auth = await importAuthStore();
    const oldSession = {
      key: "old-token",
      role: "user" as const,
      subjectId: "old-id",
      name: "old@example.com",
    };
    const newSession = {
      key: "new-token",
      role: "user" as const,
      subjectId: "new-id",
      name: "new@example.com",
    };
    await auth.setStoredAuthSession(newSession);

    await expect(auth.clearStoredAuthSessionIfCurrent(oldSession.key)).resolves.toBe(false);
    await expect(auth.getStoredAuthSession()).resolves.toEqual(newSession);

    await expect(auth.clearStoredAuthSessionIfCurrent(newSession.key)).resolves.toBe(true);
    await expect(auth.getStoredAuthSession()).resolves.toBeNull();
  });

  test("stale session verification does not clear a newer stored session", () => {
    const guard = source("src/lib/use-auth-guard.ts");
    const shell = source("src/components/layout/app-shell.tsx");
    const guardCatch = guard.slice(guard.indexOf("} catch (error) {"), guard.indexOf("let verifiedSession", guard.indexOf("} catch (error) {")));
    const redirectCatch = guard.slice(guard.lastIndexOf("} catch (error) {"), guard.indexOf("} finally {"));
    const shellCatch = shell.slice(shell.indexOf("} catch (error) {"), shell.indexOf("};", shell.indexOf("} catch (error) {")));
    const authSession = source("src/lib/auth-session.ts");

    expect(guard).toContain("isAuthSessionChangedError");
    expect(shell).toContain("isAuthSessionChangedError");
    expect(authSession).toContain("clearCurrentSession: clearStoredAuthSessionIfCurrent");
    expect(authSession).toContain("deps.clearCurrentSession(storedKey)");
    expect(guardCatch).toContain("if (isAuthSessionChangedError(error))");
    expect(guardCatch).toContain("const changedSession = error.latestSession;");
    expect(guardCatch).not.toContain("await clearStoredAuthSession()");
    expect(redirectCatch).toContain("if (isAuthSessionChangedError(error))");
    expect(redirectCatch).not.toContain("await clearStoredAuthSession()");
    expect(shellCatch).toContain("if (isAuthSessionChangedError(error))");
    expect(shellCatch).toContain("setSession(error.latestSession)");
    expect(shellCatch).not.toContain("await clearStoredAuthSession()");
  });
});
