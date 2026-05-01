import { beforeEach, describe, expect, mock, test } from "bun:test";

const httpRequest = mock(async (path: string) => {
  if (path === "/api/settings") {
    return { config: { proxy: "http://proxy.local:8080" } };
  }
  if (path === "/api/image/generations" || path === "/api/image/edits") {
    return { data: [] };
  }
  return {};
});
const httpBlobRequest = mock(async () => new Blob());

mock.module("../src/lib/request", () => ({ httpBlobRequest, httpRequest }));
mock.module("@/lib/request", () => ({ httpBlobRequest, httpRequest }));

const api = await import("../src/lib/api");

beforeEach(() => {
  httpRequest.mockClear();
  httpBlobRequest.mockClear();
});

describe("frontend backend API contracts", () => {
  test("does not call the removed standalone proxy settings endpoints", async () => {
    await api.fetchProxy();
    await api.updateProxy({ enabled: true, url: " http://next-proxy.local:8080 " });
    await api.testProxy("http://probe-proxy.local:8080");

    expect(httpRequest.mock.calls.map((call) => call[0])).toEqual([
      "/api/settings",
      "/api/settings",
      "/api/settings",
      "/api/proxy/test",
    ]);
    expect(httpRequest.mock.calls.some((call) => call[0] === "/api/proxy")).toBe(false);
    expect(httpRequest.mock.calls[2]?.[1]).toMatchObject({
      method: "POST",
      body: { proxy: "http://next-proxy.local:8080" },
    });
  });

  test("exposes admin update center API contracts", async () => {
    await api.getSystemVersion();
    await api.checkSystemUpdates();
    await api.checkSystemUpdates(true);
    await api.performSystemUpdate();
    await api.rollbackSystemUpdate();
    await api.restartSystemService();
    await api.fetchUpdateStatus();
    await api.fetchUpdateStatus(true);
    await api.startSystemUpdate();
    await api.fetchUpdateJob("job-a");

    expect(httpRequest.mock.calls).toEqual([
      ["/api/admin/system/version"],
      ["/api/admin/system/check-updates"],
      ["/api/admin/system/check-updates?force=true"],
      ["/api/admin/system/update", { method: "POST" }],
      ["/api/admin/system/rollback", { method: "POST" }],
      ["/api/admin/system/restart", { method: "POST" }],
      ["/api/admin/update/status"],
      ["/api/admin/update/status?force=true"],
      ["/api/admin/update/start", { method: "POST" }],
      ["/api/admin/update/jobs/job-a"],
    ]);
  });

  test("exposes daily checkin API contracts", async () => {
    await api.fetchCheckinStatus();
    await api.claimDailyCheckin();

    expect(httpRequest.mock.calls).toEqual([
      ["/api/checkin/status"],
      ["/api/checkin", { method: "POST" }],
    ]);
  });

  test("uses bounded timeouts for redeem page account and history requests", async () => {
    await api.fetchMe();
    await api.fetchRedeemHistory();
    await api.redeemCode("IMG-TEST");

    const meOptions = httpRequest.mock.calls[0]?.[1] as Record<string, unknown>;
    const historyOptions = httpRequest.mock.calls[1]?.[1] as Record<string, unknown>;
    const redeemOptions = httpRequest.mock.calls[2]?.[1] as Record<string, unknown>;

    expect(httpRequest.mock.calls[0]?.[0]).toBe("/api/auth/me");
    expect(meOptions.redirectOnUnauthorized).toBe(true);
    expect(typeof meOptions.timeoutMs).toBe("number");
    expect(meOptions.timeoutMs).toBeGreaterThan(0);

    expect(httpRequest.mock.calls[1]?.[0]).toBe("/api/redeem/history");
    expect(typeof historyOptions.timeoutMs).toBe("number");
    expect(historyOptions.timeoutMs).toBeGreaterThan(0);

    expect(httpRequest.mock.calls[2]?.[0]).toBe("/api/redeem");
    expect(redeemOptions.method).toBe("POST");
    expect(redeemOptions.body).toEqual({ code: "IMG-TEST" });
    expect(typeof redeemOptions.timeoutMs).toBe("number");
    expect(redeemOptions.timeoutMs).toBeGreaterThan(0);
  });

  test("uses bounded timeouts for image queue quota refresh requests", async () => {
    await api.fetchMe();
    await api.fetchAccounts();

    const meOptions = httpRequest.mock.calls[0]?.[1] as Record<string, unknown>;
    const accountsOptions = httpRequest.mock.calls[1]?.[1] as Record<string, unknown>;

    expect(httpRequest.mock.calls[0]?.[0]).toBe("/api/auth/me");
    expect(typeof meOptions.timeoutMs).toBe("number");
    expect(meOptions.timeoutMs).toBeGreaterThan(0);

    expect(httpRequest.mock.calls[1]?.[0]).toBe("/api/accounts");
    expect(typeof accountsOptions.timeoutMs).toBe("number");
    expect(accountsOptions.timeoutMs).toBeGreaterThan(0);
  });

  test("uses bounded timeouts and scoped tokens for image generation requests", async () => {
    const file = new File(["image-bytes"], "reference.png", { type: "image/png" });

    await api.generateImage("prompt", "gpt-image-1", "1024x1024", "token-a");
    await api.editImage(file, "edit prompt", "gpt-image-1", "1536x864", "token-b");

    const generateOptions = httpRequest.mock.calls[0]?.[1] as Record<string, unknown>;
    const editOptions = httpRequest.mock.calls[1]?.[1] as Record<string, unknown>;

    expect(httpRequest.mock.calls[0]?.[0]).toBe("/api/image/generations");
    expect(generateOptions.method).toBe("POST");
    expect(generateOptions.headers).toEqual({ Authorization: "Bearer token-a" });
    expect(typeof generateOptions.timeoutMs).toBe("number");
    expect(generateOptions.timeoutMs).toBeGreaterThanOrEqual(900000);

    expect(httpRequest.mock.calls[1]?.[0]).toBe("/api/image/edits");
    expect(editOptions.method).toBe("POST");
    expect(editOptions.headers).toEqual({ Authorization: "Bearer token-b" });
    expect(typeof editOptions.timeoutMs).toBe("number");
    expect(editOptions.timeoutMs).toBeGreaterThanOrEqual(1800000);
  });
});
