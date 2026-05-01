"use client";

function getImageConversationOwnerKey(ownerId: string) {
  const normalizedOwnerId = String(ownerId || "").trim();
  if (!normalizedOwnerId) {
    throw new Error("owner id is required");
  }
  return encodeURIComponent(normalizedOwnerId);
}

export function getImageConversationStorageKey(ownerId: string) {
  return `items:${getImageConversationOwnerKey(ownerId)}`;
}

export function getImageConversationDeletedStorageKey(ownerId: string) {
  return `deleted:${getImageConversationOwnerKey(ownerId)}`;
}

export function getScopedImagePreferenceStorageKey(baseKey: string, ownerId: string) {
  return `${baseKey}:${getImageConversationOwnerKey(ownerId)}`;
}
