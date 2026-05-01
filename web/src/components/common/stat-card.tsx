import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function StatCard({
  label,
  value,
  hint,
  icon,
  tone = "teal",
  className,
}: {
  label: string;
  value: ReactNode;
  hint?: ReactNode;
  icon?: ReactNode;
  tone?: "teal" | "blue" | "emerald" | "amber" | "rose" | "slate";
  className?: string;
}) {
  const toneClass = {
    teal: "bg-teal-50 text-teal-700",
    blue: "bg-sky-50 text-sky-700",
    emerald: "bg-emerald-50 text-emerald-700",
    amber: "bg-amber-50 text-amber-700",
    rose: "bg-rose-50 text-rose-700",
    slate: "bg-slate-100 text-slate-700",
  }[tone];

  return (
    <div className={cn("rounded-3xl border border-white/75 bg-white/88 p-4 shadow-[0_20px_60px_-34px_rgba(15,23,42,0.35)] backdrop-blur sm:p-5", className)}>
      <div className="flex items-start gap-4">
        {icon ? <div className={cn("grid size-12 shrink-0 place-items-center rounded-2xl", toneClass)}>{icon}</div> : null}
        <div className="min-w-0">
          <div className="text-xs font-bold uppercase tracking-[0.14em] text-slate-400">{label}</div>
          <div className="mt-1 break-words text-2xl font-black leading-tight tracking-tight text-slate-950">{value}</div>
          {hint ? <div className="mt-1 text-xs font-medium text-slate-500">{hint}</div> : null}
        </div>
      </div>
    </div>
  );
}
