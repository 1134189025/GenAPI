"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { LoaderCircle } from "lucide-react";

import { fetchSetupStatus } from "@/lib/api";
import { getDefaultRouteForRole, getStoredAuthSession } from "@/store/auth";

export default function HomePage() {
  const router = useRouter();

  useEffect(() => {
    let active = true;

    const redirect = async () => {
      const session = await getStoredAuthSession();
      if (!active) {
        return;
      }
      if (session) {
        router.replace(getDefaultRouteForRole(session.role));
        return;
      }
      try {
        const status = await fetchSetupStatus();
        router.replace(status.requires_setup ? "/setup" : "/login");
      } catch {
        router.replace("/login");
      }
    };

    void redirect();
    return () => {
      active = false;
    };
  }, [router]);

  return (
    <div className="grid min-h-[calc(100vh-1rem)] place-items-center px-4">
      <div className="flex items-center gap-3 rounded-2xl border border-white/80 bg-white/90 px-5 py-4 text-sm text-stone-500 shadow-sm">
        <LoaderCircle className="size-4 animate-spin" />
        正在进入...
      </div>
    </div>
  );
}
