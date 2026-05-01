"use client";

import { httpRequest } from "@/lib/request";
import {
  clearStoredAuthSessionIfCurrent,
  getStoredAuthSession,
  type AuthRole,
  type StoredAuthSession,
} from "@/store/auth";

export const AUTH_SESSION_VERIFY_TIMEOUT_MS = 3000;

type AuthMeResponse = {
  user: {
    id: string;
    email: string;
    role: AuthRole;
  };
};

type AuthSessionVerifierDeps = {
  requestAuthMe: (storedKey: string) => Promise<AuthMeResponse>;
  getSession: () => Promise<StoredAuthSession | null>;
  clearCurrentSession: (storedKey: string) => Promise<boolean>;
};

export class AuthSessionChangedError extends Error {
  latestSession: StoredAuthSession | null;

  constructor(latestSession: StoredAuthSession | null) {
    super("Stored auth session changed while verifying");
    this.name = "AuthSessionChangedError";
    this.latestSession = latestSession;
  }
}

export function isAuthSessionChangedError(error: unknown): error is AuthSessionChangedError {
  return error instanceof AuthSessionChangedError;
}

export async function verifyAuthSessionWithDeps(storedSession: StoredAuthSession, deps: AuthSessionVerifierDeps) {
  const storedKey = String(storedSession.key || "").trim();
  if (!storedKey) {
    throw new Error("Missing stored auth session key");
  }
  let data: AuthMeResponse;
  try {
    data = await deps.requestAuthMe(storedKey);
  } catch (error) {
    const latestSessionAfterError = await deps.getSession();
    if (latestSessionAfterError && latestSessionAfterError.key !== storedKey) {
      throw new AuthSessionChangedError(latestSessionAfterError);
    }
    const didClear = await deps.clearCurrentSession(storedKey);
    if (!didClear) {
      throw new AuthSessionChangedError(await deps.getSession());
    }
    throw error;
  }
  const latestSession = await deps.getSession();
  if (!latestSession || latestSession.key !== storedKey) {
    throw new AuthSessionChangedError(latestSession);
  }
  const verifiedSession: StoredAuthSession = {
    key: storedKey,
    role: data.user.role,
    subjectId: data.user.id,
    name: data.user.email,
  };
  return verifiedSession;
}

export async function verifyStoredAuthSession(storedSession: StoredAuthSession) {
  return verifyAuthSessionWithDeps(storedSession, {
    requestAuthMe: (storedKey) =>
      httpRequest<AuthMeResponse>("/api/auth/me", {
        headers: { Authorization: `Bearer ${storedKey}` },
        redirectOnUnauthorized: false,
        timeoutMs: AUTH_SESSION_VERIFY_TIMEOUT_MS,
      }),
    getSession: getStoredAuthSession,
    clearCurrentSession: clearStoredAuthSessionIfCurrent,
  });
}
