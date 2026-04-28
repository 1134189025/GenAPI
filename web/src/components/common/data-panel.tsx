import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export function DataPanel({
  title,
  description,
  toolbar,
  children,
  className,
}: {
  title?: string;
  description?: string;
  toolbar?: ReactNode;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={cn("overflow-hidden rounded-3xl border border-white/75 bg-white/90 shadow-[0_24px_80px_-38px_rgba(15,23,42,0.35)] backdrop-blur", className)}>
      {(title || description || toolbar) ? (
        <div className="flex flex-col gap-3 border-b border-slate-100 px-5 py-4 md:flex-row md:items-center md:justify-between">
          <div>
            {title ? <h2 className="text-base font-black text-slate-950">{title}</h2> : null}
            {description ? <p className="mt-1 text-sm text-slate-500">{description}</p> : null}
          </div>
          {toolbar ? <div className="flex flex-wrap gap-2">{toolbar}</div> : null}
        </div>
      ) : null}
      <div>{children}</div>
    </section>
  );
}
