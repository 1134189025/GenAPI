import { beforeEach, describe, expect, mock, test } from "bun:test";

const httpRequest = mock(async () => ({}));

mock.module("../src/lib/request", () => ({ httpRequest }));
mock.module("@/lib/request", () => ({ httpRequest }));

const api = await import("../src/lib/api");

beforeEach(() => {
  httpRequest.mockClear();
});

describe("account management API contracts", () => {
  test("builds id and token_ref operation payloads without full access tokens", () => {
    const refs = api.getAccountOperationRefs([
      { id: "id-a", token_ref: "token:aaaa", access_token: "raw-token-a" } as api.Account,
      { id: "id-b", token_ref: "", access_token: "raw-token-b" } as api.Account,
      { id: "", token_ref: "token:cccc", access_token: "raw-token-c" } as api.Account,
    ]);

    expect(refs).toEqual([
      { id: "id-a", token_ref: "token:aaaa" },
      { id: "id-b", token_ref: "" },
      { id: "", token_ref: "token:cccc" },
    ]);
    expect(JSON.stringify(api.buildAccountOperationPayload(refs))).not.toContain("raw-token");
    expect(api.buildAccountOperationPayload(refs)).toEqual({
      account_ids: ["id-a", "id-b"],
      token_refs: ["token:aaaa", "token:cccc"],
    });
  });

  test("delete and refresh account APIs send id/token_ref references", async () => {
    const refs = [
      { id: "id-a", token_ref: "token:aaaa" },
      { id: "id-b", token_ref: "" },
    ];

    await api.deleteAccounts(refs);
    await api.refreshAccounts(refs);

    expect(httpRequest).toHaveBeenNthCalledWith(1, "/api/accounts", {
      method: "DELETE",
      body: { account_ids: ["id-a", "id-b"], token_refs: ["token:aaaa"] },
    });
    expect(httpRequest).toHaveBeenNthCalledWith(2, "/api/accounts/refresh", {
      method: "POST",
      body: { account_ids: ["id-a", "id-b"], token_refs: ["token:aaaa"] },
    });
  });

  test("export account API uses explicit raw-token endpoint", async () => {
    await api.exportAccounts();

    expect(httpRequest).toHaveBeenCalledWith("/api/accounts/export");
  });

  test("update account API identifies the account by id/token_ref", async () => {
    await api.updateAccount({ id: "id-a", token_ref: "token:aaaa" }, { status: "禁用", quota: 0 });

    expect(httpRequest).toHaveBeenCalledWith("/api/accounts/update", {
      method: "POST",
      body: {
        account_id: "id-a",
        token_ref: "token:aaaa",
        status: "禁用",
        quota: 0,
      },
    });
  });
});
