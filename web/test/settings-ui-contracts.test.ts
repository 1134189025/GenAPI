import { describe, expect, test } from "bun:test";

import { confirmSystemUpdateStart, getManualUpdateGuidance, getReleaseSyncState } from "../src/app/settings/components/update-card";
import { isSub2APIAuthModeChanged, validateSub2APIServerForm } from "../src/app/settings/components/sub2api-connections";
import type { Sub2APIServer, UpdateStatus } from "../src/lib/api";

describe("settings UI helper contracts", () => {
  const passwordServer: Sub2APIServer = {
    id: "server-a",
    name: "Server A",
    base_url: "http://sub2api.local",
    email: "admin@example.test",
    has_api_key: false,
    group_id: "",
  };

  test("Sub2API edit requires a new secret when auth mode changes", () => {
    expect(isSub2APIAuthModeChanged(passwordServer, "api_key")).toBe(true);
    expect(
      validateSub2APIServerForm({
        editingServer: passwordServer,
        baseUrl: "http://sub2api.local",
        email: "",
        password: "",
        apiKey: "",
        authMode: "api_key",
      }),
    ).toBe("切换认证方式后请输入 Admin API Key");
    expect(
      validateSub2APIServerForm({
        editingServer: { ...passwordServer, has_api_key: true },
        baseUrl: "http://sub2api.local",
        email: "admin@example.test",
        password: "",
        apiKey: "",
        authMode: "password",
      }),
    ).toBe("切换认证方式后请输入管理员密码");
  });

  test("update card only displays synced when latest version is known and no update is available", () => {
    const baseStatus: UpdateStatus = {
      deployment_mode: "systemd",
      build_type: "release",
      can_update: true,
      has_update: false,
      current_version: "0.1.4",
    };

    expect(getReleaseSyncState(undefined).label).toBe("未检测");
    expect(getReleaseSyncState({ ...baseStatus, error: "network" }).label).toBe("检查失败");
    expect(getReleaseSyncState({ ...baseStatus, latest_version: undefined }).label).toBe("未检测");
    expect(getReleaseSyncState({ ...baseStatus, latest_version: "0.1.4" }).label).toBe("已同步");
    expect(getReleaseSyncState({ ...baseStatus, has_update: true, latest_version: "0.1.5" }).label).toBe("可更新");
    expect(getReleaseSyncState({ ...baseStatus, update_available: true, latest_version: "0.1.5" }).label).toBe("可更新");
  });

  test("update card guides non-systemd deployments through manual updates", () => {
    const dockerStatus: UpdateStatus = {
      deployment_mode: "docker",
      build_type: "release",
      can_update: false,
      has_update: true,
      current_version: "0.1.5",
      latest_version: "0.1.6",
    };
    const sourceStatus: UpdateStatus = {
      deployment_mode: "source",
      build_type: "source",
      can_update: false,
      has_update: true,
      current_version: "0.1.5",
      latest_version: "0.1.6",
    };
    const systemdStatus: UpdateStatus = {
      deployment_mode: "systemd",
      build_type: "release",
      can_update: true,
      has_update: true,
      current_version: "0.1.5",
      latest_version: "0.1.6",
    };

    expect(getManualUpdateGuidance(dockerStatus)?.command).toBe("docker compose pull app && docker compose up -d app");
    expect(getManualUpdateGuidance(sourceStatus)?.command).toBe("git pull && restart service manually");
    expect(getManualUpdateGuidance(systemdStatus)).toBeNull();
  });

  test("system update start requires explicit confirmation", () => {
    let confirmCalls = 0;
    const confirm = () => {
      confirmCalls += 1;
      return false;
    };

    expect(confirmSystemUpdateStart(confirm)).toBe(false);
    expect(confirmCalls).toBe(1);
  });
});
