import { cn } from "../../lib/utils"
import { Typography } from "./typography"
import { Clock, CheckCircle2, AlertCircle, ExternalLink, Loader2 } from "lucide-react"
import { Button } from "./button"
import { Badge } from "./badge"

interface AsyncJobStatusProps {
  jobId: string;
  status: "pending" | "running" | "completed" | "failed" | string;
  resultUrl?: string;
  error?: string;
  className?: string;
}

export function AsyncJobStatus({ jobId, status, resultUrl, error, className }: AsyncJobStatusProps) {
  const isCompleted = status === "completed";
  const isFailed = status === "failed";
  const isRunning = status === "running" || status === "pending";

  const statusConfig = {
    pending: { color: "text-slate-400", bg: "bg-slate-500/10", icon: Clock },
    running: { color: "text-cyan-400", bg: "bg-cyan-500/10", icon: Loader2 },
    completed: { color: "text-emerald-400", bg: "bg-emerald-500/10", icon: CheckCircle2 },
    failed: { color: "text-destructive", bg: "bg-destructive/10", icon: AlertCircle },
  };

  const current = (statusConfig[status as keyof typeof statusConfig] || statusConfig.pending);
  const Icon = current.icon;

  return (
    <div className={cn("space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-500", className)}>
      <div className="flex items-center justify-between">
        <Typography variant="small" className="font-bold text-slate-400 uppercase tracking-widest">
           Async Operation Status
        </Typography>
        <Badge variant="outline" className={cn("capitalize tracking-widest", current.color, current.bg, "border-white/5")}>
           {status}
        </Badge>
      </div>

      <div className="rounded-2xl border border-white/5 bg-slate-900/40 p-6 backdrop-blur-sm">
        <div className="flex items-start gap-4">
          <div className={cn("flex h-10 w-10 items-center justify-center rounded-xl shrink-0", current.bg, current.color)}>
            <Icon className={cn("h-6 w-6", isRunning && "animate-spin-slow")} />
          </div>
          <div className="flex-1 space-y-1">
             <div className="flex items-center justify-between">
                <Typography variant="small" className="font-bold text-white">
                  {isCompleted ? "Research Task Complete" : isFailed ? "Operation Failed" : "Job in Progress"}
                </Typography>
                <span className="text-[10px] font-mono text-slate-500 uppercase tracking-tighter">ID: {jobId.slice(0, 12)}...</span>
             </div>
             <Typography variant="muted" className="text-xs leading-relaxed">
                {isCompleted ? "The agent has finished the async research task. The results are now available for retrieval." : 
                 isFailed ? (error || "An unexpected error occurred during the async execution of this task.") : 
                 "This task is being processed in the background. You can safely close this page or wait for a notification."}
             </Typography>
          </div>
        </div>

        {isCompleted && resultUrl && (
          <div className="mt-6 pt-6 border-t border-white/5">
             <Button asChild variant="accent" className="w-full rounded-xl gap-2">
                <a href={resultUrl} target="_blank" rel="noreferrer">
                   <ExternalLink className="h-4 w-4" />
                   Retrieve Execution Results
                </a>
             </Button>
          </div>
        )}
      </div>
    </div>
  );
}
