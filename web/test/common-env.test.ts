import { describe, expect, test } from "bun:test";

describe("web API base configuration", () => {
  test("uses the backend development port by default", async () => {
    const previousNodeEnv = process.env.NODE_ENV;
    const previousApiUrl = process.env.NEXT_PUBLIC_API_URL;
    process.env.NODE_ENV = "development";
    delete process.env.NEXT_PUBLIC_API_URL;

    const { default: webConfig } = await import(`../src/constants/common-env.ts?dev-default=${Date.now()}`);

    process.env.NODE_ENV = previousNodeEnv;
    if (previousApiUrl === undefined) {
      delete process.env.NEXT_PUBLIC_API_URL;
    } else {
      process.env.NEXT_PUBLIC_API_URL = previousApiUrl;
    }

    expect(webConfig.apiUrl).toBe("http://127.0.0.1:8000");
  });
});
