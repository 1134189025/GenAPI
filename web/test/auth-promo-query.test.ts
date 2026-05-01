import { describe, expect, test } from "bun:test";

import { resolvePromoQueryParam } from "../src/components/auth/promo-query";
import { createAuthorizationHeaders, shouldRedirectAfterUnauthorizedAuthFailure } from "../src/lib/request";

describe("register promo query prefill", () => {
  test("uses the promo query value when present", () => {
    expect(resolvePromoQueryParam(new URLSearchParams("promo=WELCOME100"))).toBe("WELCOME100");
  });

  test("trims encoded surrounding whitespace", () => {
    expect(resolvePromoQueryParam(new URLSearchParams("promo=%20SPRING%20"))).toBe("SPRING");
  });

  test("returns an empty string when the query is missing", () => {
    expect(resolvePromoQueryParam(new URLSearchParams("invite=ABC"))).toBe("");
    expect(resolvePromoQueryParam(null)).toBe("");
  });
});

describe("API authorization headers", () => {
  test("adds a bearer token when no authorization header is present", () => {
    expect(createAuthorizationHeaders(undefined, "jwt-token")).toEqual({
      Authorization: "Bearer jwt-token",
    });
  });

  test("preserves explicit authorization case-insensitively", () => {
    expect(createAuthorizationHeaders({ authorization: "Bearer caller-token" }, "jwt-token")).toEqual({
      authorization: "Bearer caller-token",
    });
  });

  test("does not attach authorization when the stored token is blank", () => {
    expect(createAuthorizationHeaders({ Accept: "application/json" }, "   ")).toEqual({
      Accept: "application/json",
    });
  });
});

describe("auth loading safety contracts", () => {
  test("auth and setup checks use bounded request timeouts instead of infinite loading", () => {
    const request = Bun.file(new URL("../src/lib/request.ts", import.meta.url)).text();
    const guard = Bun.file(new URL("../src/lib/use-auth-guard.ts", import.meta.url)).text();
    const authSession = Bun.file(new URL("../src/lib/auth-session.ts", import.meta.url)).text();

    return Promise.all([request, guard, authSession]).then(([requestSource, guardSource, authSessionSource]) => {
      expect(requestSource).toContain("timeoutMs?: number");
      expect(requestSource).toContain("resolveRequestTimeoutMs");
      expect(requestSource).toContain("config.timeout = timeout");
      expect(requestSource).toContain('"/api/setup/status"');
      expect(guardSource).toContain("verifyStoredAuthSession");
      expect(authSessionSource).toContain("AUTH_SESSION_VERIFY_TIMEOUT_MS");
      expect(authSessionSource).toContain('"/api/auth/me"');
      expect(authSessionSource).toContain("timeoutMs: AUTH_SESSION_VERIFY_TIMEOUT_MS");
    });
  });

  test("401 cleanup is scoped to the authorization token used by the request", () => {
    return Bun.file(new URL("../src/lib/request.ts", import.meta.url)).text().then((requestSource) => {
      expect(requestSource).toContain("authorizationTokenFromHeaders(nextHeaders)");
      expect(requestSource).toContain("authKeyUsed = authKeyUsed");
      expect(requestSource).toContain("shouldRedirectAfterUnauthorizedAuthFailure(authKeyUsed");
      expect(requestSource).toContain("clearCurrentSession: clearStoredAuthSessionIfCurrent");
      expect(requestSource).toContain("getSession: getStoredAuthSession");
      expect(requestSource).not.toContain("await clearStoredAuthSession()");
      expect(requestSource.indexOf("authorizationTokenFromHeaders(nextHeaders)")).toBeLessThan(
        requestSource.indexOf("shouldRedirectAfterUnauthorizedAuthFailure(authKeyUsed"),
      );
    });
  });

  test("401 redirect waits for conditional session ownership before navigating", async () => {
    const calls: string[] = [];

    await expect(
      shouldRedirectAfterUnauthorizedAuthFailure("old-token", {
        clearCurrentSession: async (expectedKey) => {
          calls.push(expectedKey);
          return false;
        },
        getSession: async () => ({ key: "new-token" }),
      }),
    ).resolves.toBe(false);
    expect(calls).toEqual(["old-token"]);

    await expect(
      shouldRedirectAfterUnauthorizedAuthFailure("current-token", {
        clearCurrentSession: async () => true,
        getSession: async () => ({ key: "current-token" }),
      }),
    ).resolves.toBe(true);

    await expect(
      shouldRedirectAfterUnauthorizedAuthFailure("", {
        clearCurrentSession: async () => {
          throw new Error("empty auth request must not clear");
        },
        getSession: async () => ({ key: "new-token" }),
      }),
    ).resolves.toBe(false);

    await expect(
      shouldRedirectAfterUnauthorizedAuthFailure("", {
        clearCurrentSession: async () => {
          throw new Error("empty auth request must not clear");
        },
        getSession: async () => null,
      }),
    ).resolves.toBe(true);
  });

  test("stored auth reads are bounded so auth screens cannot spin forever", () => {
    return Bun.file(new URL("../src/store/auth.ts", import.meta.url)).text().then((authSource) => {
      expect(authSource).toContain("AUTH_STORAGE_TIMEOUT_MS");
      expect(authSource).toContain("withAuthStorageTimeout");
      expect(authSource).toContain("Promise.race");
      expect(authSource).toContain("getStoredAuthSession");
      expect(authSource).toContain("return null");
    });
  });

  test("redirect-if-authenticated exits checking state after failed stored session validation", () => {
    return Bun.file(new URL("../src/lib/use-auth-guard.ts", import.meta.url)).text().then((guardSource) => {
      const hookSource = guardSource.slice(guardSource.indexOf("export function useRedirectIfAuthenticated"));

      expect(hookSource).toContain("finally");
      expect(hookSource).toContain("setIsCheckingAuth(false)");
      expect(hookSource).toContain("if (active");
    });
  });
});
