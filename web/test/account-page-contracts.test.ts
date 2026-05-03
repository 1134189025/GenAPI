import { describe, expect, test } from "bun:test";

import {
  buildAccountReferenceExport,
  confirmAccountDeletion,
  filterAccountsForAccountsPage,
  getAccountRefreshRefsForFilter,
  getProblemAccountRefsForFilter,
  getDisplayAccountReference,
  isProblemAccount,
  parseAccountQuotaInput,
} from "../src/app/accounts/page";
import type { Account } from "../src/lib/api";

function account(overrides: Partial<Account> & Pick<Account, "id" | "status">): Account {
  return {
    id: overrides.id,
    token_ref: `token:${overrides.id}`,
    type: "Free",
    status: overrides.status,
    quota: 10,
    imageQuotaUnknown: false,
    email: `${overrides.id}@example.test`,
    success: 0,
    fail: 0,
    lastUsedAt: null,
    ...overrides,
  };
}

describe("account page token redaction contracts", () => {
  test("displays and exports token references without falling back to full tokens", () => {
    const accounts = [
      { id: "id-a", token_ref: "token:aaaa", access_token: "raw-token-a" },
      { id: "id-b", token_ref: "", access_token: "raw-token-b" },
    ] as Account[];

    expect(getDisplayAccountReference(accounts[0])).toBe("token:aaaa");
    expect(getDisplayAccountReference(accounts[1])).toBe("id:id-b");
    expect(buildAccountReferenceExport(accounts)).toBe("token:aaaa\nid:id-b\n");
    expect(buildAccountReferenceExport(accounts)).not.toContain("raw-token");
  });
});

describe("account page destructive action contracts", () => {
  test("requires confirmation before deleting accounts", () => {
    let confirmCalls = 0;
    const confirm = () => {
      confirmCalls += 1;
      return false;
    };

    expect(confirmAccountDeletion([{ id: "id-a", token_ref: "token:aaaa" }], "删除所选", confirm)).toBe(false);
    expect(confirmCalls).toBe(1);
  });
});

describe("account quota edit contracts", () => {
  test("keeps current empty quota semantics and rejects invalid numbers before update", () => {
    expect(parseAccountQuotaInput("")).toEqual({ ok: true, quota: 0 });
    expect(parseAccountQuotaInput(" 12 ")).toEqual({ ok: true, quota: 12 });
    expect(parseAccountQuotaInput("abc").ok).toBe(false);
    expect(parseAccountQuotaInput("NaN").ok).toBe(false);
    expect(parseAccountQuotaInput("Infinity").ok).toBe(false);
  });
});

describe("account problem filter contracts", () => {
  test("classifies problem accounts without including disabled accounts", () => {
    const limited = account({ id: "limited", status: "限流" });
    const abnormal = account({ id: "abnormal", status: "异常" });
    const knownEmptyQuota = account({ id: "known-empty", status: "正常", quota: 0, imageQuotaUnknown: false });
    const knownNegativeQuota = account({ id: "known-negative", status: "正常", quota: -1, imageQuotaUnknown: false });
    const disabled = account({ id: "disabled", status: "禁用", quota: 0, imageQuotaUnknown: false });
    const unknownQuota = account({ id: "unknown", status: "正常", quota: 0, imageQuotaUnknown: true });
    const missingUnknownFlag = account({ id: "missing-flag", status: "正常", quota: 0, imageQuotaUnknown: undefined });
    const healthy = account({ id: "healthy", status: "正常", quota: 1, imageQuotaUnknown: false });

    expect([limited, abnormal, knownEmptyQuota, knownNegativeQuota].map(isProblemAccount)).toEqual([
      true,
      true,
      true,
      true,
    ]);
    expect([disabled, unknownQuota, missingUnknownFlag, healthy].map(isProblemAccount)).toEqual([
      false,
      false,
      false,
      false,
    ]);
  });

  test("filters problem accounts with the same search and type constraints as the table", () => {
    const accounts = [
      account({ id: "limited-plus", status: "限流", type: "Plus", email: "match-a@example.test" }),
      account({ id: "abnormal-free", status: "异常", type: "Free", email: "match-b@example.test" }),
      account({ id: "known-empty-plus", status: "正常", type: "Plus", quota: 0, email: "match-c@example.test" }),
      account({ id: "disabled-plus", status: "禁用", type: "Plus", quota: 0, email: "match-d@example.test" }),
      account({ id: "nomatch-plus", status: "异常", type: "Plus", email: "other@example.test" }),
    ];

    expect(
      filterAccountsForAccountsPage(accounts, {
        query: "match",
        typeFilter: "Plus",
        statusFilter: "problem",
      }).map((item) => item.id),
    ).toEqual(["limited-plus", "known-empty-plus"]);
  });

  test("builds refresh refs from every account in the active filter result", () => {
    const accounts = [
      account({ id: "problem-a", status: "限流", token_ref: "token:a" }),
      account({ id: "problem-b", status: "异常", token_ref: "token:b" }),
      account({ id: "problem-c", status: "正常", quota: 0, token_ref: "token:c" }),
      account({ id: "disabled", status: "禁用", quota: 0, token_ref: "token:disabled" }),
      account({ id: "healthy", status: "正常", quota: 10, token_ref: "token:healthy" }),
    ];

    expect(getAccountRefreshRefsForFilter(accounts, { statusFilter: "problem" })).toEqual([
      { id: "problem-a", token_ref: "token:a" },
      { id: "problem-b", token_ref: "token:b" },
      { id: "problem-c", token_ref: "token:c" },
    ]);
  });

  test("builds problem deletion refs from only the active filter result", () => {
    const accounts = [
      account({ id: "visible-problem", status: "限流", type: "Plus", email: "match@example.test", token_ref: "token:visible" }),
      account({ id: "hidden-by-query", status: "异常", type: "Plus", email: "other@example.test", token_ref: "token:hidden-query" }),
      account({ id: "hidden-by-type", status: "异常", type: "Free", email: "match-free@example.test", token_ref: "token:hidden-type" }),
      account({ id: "visible-healthy", status: "正常", type: "Plus", email: "match-healthy@example.test", token_ref: "token:healthy" }),
    ];

    expect(
      getProblemAccountRefsForFilter(accounts, {
        query: "match",
        typeFilter: "Plus",
        statusFilter: "all",
      }),
    ).toEqual([{ id: "visible-problem", token_ref: "token:visible" }]);
  });
});
