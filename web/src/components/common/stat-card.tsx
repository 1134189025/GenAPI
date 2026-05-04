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
    teal: "bg-primary/5 text-primary",
    blue: "bg-blue-500/5 text-blue-500",
    emerald: "bg-emerald-500/5 text-emerald-500",
    amber: "bg-amber-500/5 text-amber-500",
    rose: "bg-rose-500/5 text-rose-500",
    slate: "bg-foreground/5 text-foreground/60",
  }[tone];

  return (
    <div className={cn("apple-card p-5", className)}>
      <div className="flex flex-col gap-4">
        <div className="flex items-center justify-between">
          <div className="text-[10px] font-bold uppercase tracking-[0.2em] text-muted-foreground">{label}</div>
          {icon ? <div className={cn("grid size-10 place-items-center rounded-xl transition-colors", toneClass)}>{icon}</div> : null}
        </div>
        <div className="min-w-0">
          <div className="break-words text-2xl font-bold tracking-tight text-foreground sm:text-3xl">{value}</div>
          {hint ? <div className="mt-2 text-xs font-medium text-muted-foreground">{hint}</div> : null}
        </div>
      </div>
    </div>
  );
}
