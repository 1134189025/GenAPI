import { beforeEach, describe, expect, mock, test } from "bun:test";

const httpRequest = mock(async (path: string) => {
  if (path === "/api/settings") {
    return { config: { proxy: "http://proxy.local:8080" } };
  }
  return {};
});

mock.module("../src/lib/request", () => ({ httpRequest }));
mock.module("@/lib/request", () => ({ httpRequest }));

const api = await import("../src/lib/api");

beforeEach(() => {
  httpRequest.mockClear();
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
});
