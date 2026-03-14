import type { LucideIcon } from "lucide-react";
import { FolderOpen } from "lucide-react";
import { cn } from "../../lib/utils";
import { Button } from "./button";

interface EmptyStateProps {
  title: string;
  description: string;
  icon?: LucideIcon;
  actionLabel?: string;
  onAction?: () => void;
  className?: string;
}

export function EmptyState({
  title,
  description,
  icon: Icon = FolderOpen,
  actionLabel,
  onAction,
  className,
}: EmptyStateProps) {
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-6 rounded-3xl border border-dashed border-white/10 p-12 text-center",
        className
      )}
    >
      <div className="flex h-16 w-16 items-center justify-center rounded-2xl bg-white/5 text-slate-400">
        <Icon className="h-8 w-8" />
      </div>
      <div className="max-w-xs space-y-2">
        <h3 className="text-lg font-bold text-white">{title}</h3>
        <p className="text-sm font-medium text-slate-400 leading-relaxed">
          {description}
        </p>
      </div>
      {actionLabel && onAction && (
        <Button onClick={onAction} variant="accent" className="rounded-full shadow-lg shadow-cyan-500/10">
          {actionLabel}
        </Button>
      )}
    </div>
  );
}
