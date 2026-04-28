import { describe, expect, test } from "bun:test";

import { mergeRegisterConfigForPoll } from "../src/app/settings/store";
import type { RegisterConfig } from "../src/lib/api";

function registerConfig(overrides: Partial<RegisterConfig> = {}): RegisterConfig {
  return {
    enabled: false,
    mail: {
      request_timeout: 10,
      wait_timeout: 120,
      wait_interval: 5,
      providers: [{ enable: true, type: "tempmail_lol", api_key: "local-key", domain: ["local.test"] }],
    },
    proxy: "http://local-proxy",
    total: 10,
    threads: 2,
    mode: "total",
    target_quota: 100,
    target_available: 3,
    check_interval: 30,
    stats: {
      success: 0,
      fail: 0,
      done: 0,
      running: 0,
      threads: 2,
    },
    logs: [],
    ...overrides,
  };
}

describe("register machine draft polling", () => {
  test("silent polling preserves unsaved register settings while refreshing runtime state", () => {
    const localDraft = registerConfig({
      proxy: "http://typing-proxy",
      threads: 9,
      mail: {
        request_timeout: 11,
        wait_timeout: 121,
        wait_interval: 6,
        providers: [{ enable: true, type: "tempmail_lol", api_key: "typing-key", domain: ["typing.test"] }],
      },
    });
    const serverPoll = registerConfig({
      enabled: true,
      proxy: "http://old-server-proxy",
      threads: 1,
      mail: {
        request_timeout: 1,
        wait_timeout: 2,
        wait_interval: 3,
        providers: [{ enable: true, type: "tempmail_lol", api_key: "server-key", domain: ["server.test"] }],
      },
      stats: {
        success: 7,
        fail: 1,
        done: 8,
        running: 2,
        threads: 2,
      },
      logs: [{ time: "20:00:00", text: "running", level: "info" }],
    });

    const merged = mergeRegisterConfigForPoll(localDraft, serverPoll, true);

    expect(merged.proxy).toBe("http://typing-proxy");
    expect(merged.threads).toBe(9);
    expect(merged.mail.providers[0].api_key).toBe("typing-key");
    expect(merged.enabled).toBe(true);
    expect(merged.stats.success).toBe(7);
    expect(merged.logs?.[0]?.text).toBe("running");
  });

  test("silent polling accepts server config when there is no local draft", () => {
    const serverPoll = registerConfig({ proxy: "http://server-proxy", threads: 4 });

    expect(mergeRegisterConfigForPoll(null, serverPoll, false)).toEqual(serverPoll);
    expect(mergeRegisterConfigForPoll(registerConfig(), serverPoll, false)).toEqual(serverPoll);
  });
});
