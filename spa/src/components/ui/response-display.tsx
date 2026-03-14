import * as React from "react"
import { cn } from "../../lib/utils"
import { Typography } from "./typography"
import { Copy, CheckCircle2 } from "lucide-react"
import { Button } from "./button"

interface ResponseDisplayProps {
  content: string;
  isLoading?: boolean;
  className?: string;
}

export function ResponseDisplay({ content, isLoading, className }: ResponseDisplayProps) {
  const [copied, setCopied] = React.useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  if (!content && !isLoading) return null;

  return (
    <div className={cn("space-y-4 animate-in fade-in slide-in-from-bottom-4 duration-500", className)}>
      <div className="flex items-center justify-between">
        <Typography variant="small" className="font-bold text-slate-400 uppercase tracking-widest">
          Agent Response
        </Typography>
        {content && (
          <Button 
            variant="ghost" 
            size="sm" 
            onClick={handleCopy}
            className="h-8 text-[10px] gap-2 text-slate-400 hover:text-white hover:bg-white/5"
          >
            {copied ? <CheckCircle2 className="h-3 w-3 text-emerald-400" /> : <Copy className="h-3 w-3" />}
            {copied ? "Copied" : "Copy Output"}
          </Button>
        )}
      </div>
      
      <div className="relative group">
        <div className="absolute -inset-0.5 bg-gradient-to-r from-cyan-500/20 to-blue-600/20 rounded-2xl blur opacity-30 group-hover:opacity-100 transition duration-1000 group-hover:duration-200"></div>
        <div className="relative rounded-2xl border border-white/10 bg-slate-900/80 p-6 font-mono text-sm leading-relaxed text-slate-200 shadow-2xl backdrop-blur-xl whitespace-pre-wrap overflow-x-auto min-h-[100px]">
          {content}
          {isLoading && (
            <span className="inline-block w-2 h-4 ml-1 bg-cyan-400 animate-pulse align-middle" />
          )}
        </div>
      </div>
    </div>
  );
}
