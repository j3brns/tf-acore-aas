import type { ReactNode } from "react";

type BannerSeverity = "info" | "success" | "warning" | "error";

const severityStyles: Record<BannerSeverity, string> = {
  info: "border-sky-200 bg-sky-50 text-sky-900",
  success: "border-emerald-200 bg-emerald-50 text-emerald-900",
  warning: "border-amber-200 bg-amber-50 text-amber-900",
  error: "border-rose-200 bg-rose-50 text-rose-900",
};

type PageBannerProps = {
  title: string;
  severity?: BannerSeverity;
  children: ReactNode;
};

export function PageBanner({ title, severity = "info", children }: PageBannerProps) {
  return (
    <section
      aria-live="polite"
      className={`rounded-3xl border px-5 py-4 shadow-sm ${severityStyles[severity]}`}
    >
      <p className="text-sm font-semibold uppercase tracking-[0.2em]">{title}</p>
      <div className="mt-1 text-sm">{children}</div>
    </section>
  );
}
