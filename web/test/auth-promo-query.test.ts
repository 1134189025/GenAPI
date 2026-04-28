import { describe, expect, test } from "bun:test";

import { resolvePromoQueryParam } from "../src/components/auth/promo-query";
import { createAuthorizationHeaders } from "../src/lib/request";

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
