import { describe, expect, test } from "bun:test";

import {
  buildAccountReferenceExport,
  confirmAccountDeletion,
  getDisplayAccountReference,
  parseAccountQuotaInput,
} from "../src/app/accounts/page";
import type { Account } from "../src/lib/api";

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
