"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { isAuthSessionChangedError, verifyStoredAuthSession } from "@/lib/auth-session";
import {
  getDefaultRouteForRole,
  getStoredAuthSession,
  type AuthRole,
  type StoredAuthSession,
} from "@/store/auth";

type UseAuthGuardResult = {
  isCheckingAuth: boolean;
  session: StoredAuthSession | null;
};

export function useAuthGuard(allowedRoles?: AuthRole[]): UseAuthGuardResult {
  const router = useRouter();
  const [session, setSession] = useState<StoredAuthSession | null>(null);
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);
  const allowedRolesKey = (allowedRoles || []).join(",");

  useEffect(() => {
    let active = true;

    const load = async () => {
      const roleList = allowedRolesKey ? (allowedRolesKey.split(",") as AuthRole[]) : [];
      const storedSession = await getStoredAuthSession();
      if (!active) {
        return;
      }

      if (!storedSession) {
        setSession(null);
        setIsCheckingAuth(false);
        router.replace("/login");
        return;
      }

      let verifiedSession: StoredAuthSession;
      try {
        verifiedSession = await verifyStoredAuthSession(storedSession);
      } catch (error) {
        if (isAuthSessionChangedError(error)) {
          const changedSession = error.latestSession;
          if (!active) {
            return;
          }
          if (!changedSession) {
            setSession(null);
            setIsCheckingAuth(false);
            router.replace("/login");
            return;
          }
          if (roleList.length > 0 && !roleList.includes(changedSession.role)) {
            setSession(changedSession);
            setIsCheckingAuth(false);
            router.replace(getDefaultRouteForRole(changedSession.role));
            return;
          }
          setSession(changedSession);
          setIsCheckingAuth(false);
          return;
        }
        if (!active) {
          return;
        }
        setSession(null);
        setIsCheckingAuth(false);
        router.replace("/login");
        return;
      }

      if (!active) {
        return;
      }

      if (roleList.length > 0 && !roleList.includes(verifiedSession.role)) {
        setSession(verifiedSession);
        setIsCheckingAuth(false);
        router.replace(getDefaultRouteForRole(verifiedSession.role));
        return;
      }

      setSession(verifiedSession);
      setIsCheckingAuth(false);
    };

    void load();
    return () => {
      active = false;
    };
  }, [allowedRolesKey, router]);

  return { isCheckingAuth, session };
}

export function useRedirectIfAuthenticated() {
  const router = useRouter();
  const [isCheckingAuth, setIsCheckingAuth] = useState(true);

  useEffect(() => {
    let active = true;

    const load = async () => {
      let didRedirect = false;
      try {
        const storedSession = await getStoredAuthSession();
        if (!active) {
          return;
        }

        if (storedSession) {
          const verifiedSession = await verifyStoredAuthSession(storedSession);
          if (active) {
            router.replace(getDefaultRouteForRole(verifiedSession.role));
            didRedirect = true;
          }
          return;
        }
      } catch (error) {
        if (isAuthSessionChangedError(error)) {
          if (active && error.latestSession) {
            router.replace(getDefaultRouteForRole(error.latestSession.role));
            didRedirect = true;
          }
          return;
        }
      } finally {
        if (active && !didRedirect) {
          setIsCheckingAuth(false);
        }
      }
    };

    void load();
    return () => {
      active = false;
    };
  }, [router]);

  return { isCheckingAuth };
}
