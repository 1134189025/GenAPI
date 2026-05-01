import { describe, expect, test } from "bun:test";

import {
  getImageConversationDeletedStorageKey,
  getImageConversationStorageKey,
  getScopedImagePreferenceStorageKey,
} from "../src/store/image-conversation-scope";

describe("image conversation user scope", () => {
  test("stores image history under a user-specific key instead of the legacy global key", () => {
    expect(getImageConversationStorageKey("admin-user-id")).toBe("items:admin-user-id");
    expect(getImageConversationStorageKey("normal-user-id")).toBe("items:normal-user-id");
    expect(getImageConversationStorageKey("admin-user-id")).not.toBe("items");
    expect(getImageConversationDeletedStorageKey("admin-user-id")).toBe("deleted:admin-user-id");
  });

  test("scopes image page local preferences by user id", () => {
    expect(getScopedImagePreferenceStorageKey("genapi:image_active_conversation_id", "admin-user-id")).toBe(
      "genapi:image_active_conversation_id:admin-user-id",
    );
    expect(getScopedImagePreferenceStorageKey("genapi:image_last_size", "normal-user-id")).toBe(
      "genapi:image_last_size:normal-user-id",
    );
  });

  test("rejects empty owner ids so history cannot fall back to a shared namespace", () => {
    expect(() => getImageConversationStorageKey("")).toThrow("owner id is required");
    expect(() => getImageConversationDeletedStorageKey("")).toThrow("owner id is required");
    expect(() => getScopedImagePreferenceStorageKey("genapi:image_last_size", "  ")).toThrow(
      "owner id is required",
    );
  });
});
