import { describe, expect, test } from "bun:test";

import { getCpaAccessToken, getSessionAccessToken, splitTokens } from "../src/app/accounts/components/account-import-dialog";

describe("account import UI contracts", () => {
  test("normalizes pasted token lists by trimming blank lines", () => {
    expect(splitTokens("  token-a  \n\n token-b\r\n")).toEqual(["token-a", "token-b"]);
  });

  test("Session JSON import extracts a trimmed accessToken", () => {
    expect(getSessionAccessToken({ accessToken: " session-token " })).toBe("session-token");
    expect(getSessionAccessToken({ access_token: "wrong-field" })).toBe("");
  });

  test("CPA JSON import accepts both access_token and accessToken fields", () => {
    expect(getCpaAccessToken({ access_token: " snake-token " })).toBe("snake-token");
    expect(getCpaAccessToken({ accessToken: " camel-token " })).toBe("camel-token");
    expect(getCpaAccessToken({ access_token: 123 })).toBe("");
  });
});
