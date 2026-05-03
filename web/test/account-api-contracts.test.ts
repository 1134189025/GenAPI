import { beforeEach, describe, expect, mock, test } from "bun:test";

const httpRequest = mock(async () => ({}));
const httpBlobRequest = mock(async () => new Blob());

mock.module("../src/lib/request", () => ({ httpBlobRequest, httpRequest }));
mock.module("@/lib/request", () => ({ httpBlobRequest, httpRequest }));

const api = await import("../src/lib/api");
const accountPage = await import("../src/app/accounts/page");

beforeEach(() => {
  httpBlobRequest.mockClear();
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

  test("refresh account API sends the full active filter result payload", async () => {
    const refs = accountPage.getAccountRefreshRefsForFilter(
      [
        {
          id: "problem-a",
          token_ref: "token:a",
          type: "Free",
          status: "限流",
          quota: 10,
          imageQuotaUnknown: false,
          email: "a@example.test",
          success: 0,
          fail: 0,
          lastUsedAt: null,
        },
        {
          id: "problem-b",
          token_ref: "token:b",
          type: "Free",
          status: "正常",
          quota: 0,
          imageQuotaUnknown: false,
          email: "b@example.test",
          success: 0,
          fail: 0,
          lastUsedAt: null,
        },
        {
          id: "disabled",
          token_ref: "token:disabled",
          type: "Free",
          status: "禁用",
          quota: 0,
          imageQuotaUnknown: false,
          email: "disabled@example.test",
          success: 0,
          fail: 0,
          lastUsedAt: null,
        },
      ],
      { statusFilter: "problem" },
    );

    await api.refreshAccounts(refs);

    expect(httpRequest).toHaveBeenCalledWith("/api/accounts/refresh", {
      method: "POST",
      body: { account_ids: ["problem-a", "problem-b"], token_refs: ["token:a", "token:b"] },
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
