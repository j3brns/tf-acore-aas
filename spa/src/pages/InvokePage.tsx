import React, { useState, useEffect } from "react";
import { useParams, Link } from "react-router-dom";
import { getApiClient } from "../api/client";
import type { AgentDetailDto } from "../api/contracts";
import { useAuth } from "../auth/useAuth";
import { AgentInvokeResponse } from "../types";
import { useJobPolling } from "../hooks/useJobPolling";
import { useSessionKeepalive } from "../hooks/useSessionKeepalive";
import {
    createInvokePayload,
    extractJobIdFromPollUrl,
    formatApiErrorMessage,
    isAsyncInvokeAccepted,
} from "./invokeContract";
import { Loading } from "../components/ui/loading";
import { PageBanner } from "../components/PageBanner";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Typography } from "../components/ui/typography";
import { StatusIndicator } from "../components/ui/status-indicator";
import { ResponseDisplay } from "../components/ui/response-display";
import { AsyncJobStatus } from "../components/ui/async-job-status";
import { 
  Bot, 
  ArrowLeft, 
  Send, 
  Info, 
  Zap, 
  Shield, 
  Clock, 
  Activity,
  Maximize2,
  Settings
} from "lucide-react";

export const InvokePage: React.FC = () => {
    const { agentName } = useParams<{ agentName: string }>();
    const { getAccessToken, isAuthenticated } = useAuth();
    
    const [agent, setAgent] = useState<AgentDetailDto | null>(null);
    const [prompt, setPrompt] = useState("");
    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState<string | null>(null);
    const [jobId, setJobId] = useState<string | null>(null);
    const [sessionId, setSessionId] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const { status: jobStatus, error: pollingError } = useJobPolling(jobId, getAccessToken);
    
    // Maintain session continuity with keepalive pings
    useSessionKeepalive(sessionId, agentName || null);

    const invocationMode = agent?.invocationMode ?? "sync";

    useEffect(() => {
        const fetchAgent = async () => {
            if (!isAuthenticated) return;
            try {
                const client = getApiClient(getAccessToken);
                const data = await client.request<AgentDetailDto>(`/v1/agents/${agentName}`);
                setAgent(data);
            } catch (err: unknown) {
                setError(formatApiErrorMessage(err));
            }
        };
        void fetchAgent();
    }, [agentName, getAccessToken, isAuthenticated]);

    const handleInvoke = async (e: React.FormEvent) => {
        e.preventDefault();
        setLoading(true);
        setResult(null);
        setJobId(null);
        setError(null);

        try {
            const client = getApiClient(getAccessToken);
            const body = JSON.stringify(createInvokePayload(prompt, sessionId));

            if (invocationMode === "streaming") {
                setResult("");
                const stream = client.stream(`/v1/agents/${agentName}/invoke`, {
                    method: "POST",
                    body,
                    headers: { "Content-Type": "application/json" }
                });

                for await (const chunk of stream) {
                    if (chunk.data === "[DONE]") continue;
                    
                    try {
                        const payload = JSON.parse(chunk.data);
                        if (payload.type === "session" && payload.sessionId) {
                            setSessionId(payload.sessionId);
                        } else if (payload.type === "text" && payload.content) {
                            setResult((prev) => (prev || "") + payload.content);
                        } else if (typeof payload.content === "string") {
                            setResult((prev) => (prev || "") + payload.content);
                        } else if (!payload.type && typeof payload.output === "string") {
                            setResult((prev) => (prev || "") + payload.output);
                        }
                    } catch {
                        // Fallback for raw text data
                        setResult((prev) => (prev || "") + chunk.data);
                    }
                }
            } else {
                const data = await client.request<AgentInvokeResponse>(`/v1/agents/${agentName}/invoke`, {
                    method: "POST",
                    body,
                    headers: { "Content-Type": "application/json" }
                });

                if (isAsyncInvokeAccepted(data)) {
                    const acceptedJobId = data.jobId || extractJobIdFromPollUrl(data.pollUrl);
                    if (!acceptedJobId) {
                        throw new Error("Async invoke response missing jobId");
                    }
                    setJobId(acceptedJobId);
                } else {
                    setResult(data.output);
                    if (data.sessionId) {
                        setSessionId(data.sessionId);
                    }
                }
            }
        } catch (err: unknown) {
            setError(formatApiErrorMessage(err));
        } finally {
            setLoading(false);
        }
    };

    if (!agent && !error) return <Loading message="Preparing execution workspace..." className="h-[400px]" />;

    return (
        <div className="grid gap-8 lg:grid-cols-[1fr_320px] animate-in fade-in slide-in-from-bottom-8 duration-700">
            <div className="space-y-8">
                <Card className="border-white/5 bg-slate-900/40 backdrop-blur-sm overflow-hidden ring-1 ring-white/5">
                    <CardHeader className="flex flex-row items-center justify-between border-b border-white/5 pb-4">
                        <div className="flex items-center gap-4">
                            <div className="h-12 w-12 rounded-2xl bg-cyan-500/10 flex items-center justify-center text-cyan-400">
                                <Bot className="h-7 w-7" />
                            </div>
                            <div>
                                <CardTitle className="text-xl font-bold text-white">Invoke: {agent?.agentName}</CardTitle>
                                <CardDescription className="text-slate-400 font-mono text-xs">
                                    Agent ARN: platform::agent::{agentName} v{agent?.latestVersion}
                                </CardDescription>
                            </div>
                        </div>
                        <Badge variant="outline" className="h-6 border-cyan-500/30 text-cyan-400 bg-cyan-500/5">
                            {agent?.invocationMode} mode
                        </Badge>
                    </CardHeader>

                    <CardContent className="pt-8">
                        <form onSubmit={handleInvoke} className="space-y-6">
                            <div className="space-y-2">
                                <div className="flex items-center justify-between px-1">
                                    <Typography variant="small" className="font-bold text-slate-300 uppercase tracking-widest text-[10px]">
                                        Input Prompt
                                    </Typography>
                                    <div className="flex gap-2">
                                       <Button type="button" variant="ghost" size="icon" className="h-6 w-6 text-slate-500">
                                          <Maximize2 className="h-3 w-3" />
                                       </Button>
                                       <Button type="button" variant="ghost" size="icon" className="h-6 w-6 text-slate-500">
                                          <Settings className="h-3 w-3" />
                                       </Button>
                                    </div>
                                </div>
                                <div className="relative group">
                                    <textarea
                                        id="prompt"
                                        rows={6}
                                        className="w-full bg-slate-950/50 border border-white/10 rounded-2xl p-4 text-sm text-white placeholder:text-slate-600 focus:outline-none focus:ring-2 focus:ring-cyan-500/50 transition-all resize-none group-hover:border-white/20"
                                        placeholder="Type your instructions for the agent here..."
                                        value={prompt}
                                        onChange={(e) => setPrompt(e.target.value)}
                                        required
                                    />
                                    <div className="absolute bottom-4 right-4 text-[10px] font-mono text-slate-600">
                                        {prompt.length} chars
                                    </div>
                                </div>
                            </div>

                            <Button
                                type="submit"
                                disabled={loading || !prompt.trim()}
                                variant="accent"
                                className="w-full h-12 rounded-xl text-white font-bold shadow-lg shadow-cyan-500/10 group"
                            >
                                {loading ? (
                                    <>
                                        <Activity className="mr-2 h-4 w-4 animate-pulse" />
                                        Invoking Agent...
                                    </>
                                ) : (
                                    <>
                                        Submit Instruction
                                        <Send className="ml-2 h-4 w-4 transition-transform group-hover:translate-x-1 group-hover:-translate-y-1" />
                                    </>
                                )}
                            </Button>
                        </form>
                    </CardContent>
                </Card>

                {error && (
                    <PageBanner title="Execution Error" severity="error">
                        {error}
                    </PageBanner>
                )}

                {jobId && (
                    <AsyncJobStatus 
                      jobId={jobId} 
                      status={jobStatus?.status || "pending"} 
                      resultUrl={jobStatus?.resultUrl || undefined} 
                      error={pollingError || undefined}
                    />
                )}

                {(result !== null || (loading && invocationMode === "streaming")) && (
                    <ResponseDisplay content={result || ""} isLoading={loading && invocationMode === "streaming"} />
                )}
            </div>

            {/* Context Sidebar */}
            <aside className="space-y-6">
                <div className="rounded-2xl border border-white/5 bg-white/5 p-5 space-y-4">
                   <Typography variant="small" className="font-bold text-slate-400 uppercase tracking-widest block">
                      Trust Cues
                   </Typography>
                   <div className="space-y-2">
                      <StatusIndicator 
                        label="Runtime" 
                        value="eu-west-1" 
                        tone="success" 
                        title="AgentCore execution region: Dublin"
                      />
                      <StatusIndicator 
                        label="Compute" 
                        value="Firecracker" 
                        tone="info" 
                        title="Isolated microVM runtime"
                      />
                      <StatusIndicator 
                        label="Session" 
                        value={sessionId ? "Active" : "New"} 
                        tone={sessionId ? "success" : "neutral"} 
                        pulse={!!sessionId}
                        title={sessionId ? "Long-running session active" : "No active session"}
                      />
                   </div>
                </div>

                <div className="rounded-2xl border border-white/5 bg-slate-900/40 p-5 space-y-4">
                   <Typography variant="small" className="font-bold text-slate-400 uppercase tracking-widest block">
                      Capabilities
                   </Typography>
                   <div className="space-y-3">
                      <div className="flex items-center gap-3 text-xs text-slate-300">
                         <Clock className="h-4 w-4 text-cyan-400 shrink-0" />
                         <span>Max timeout: 15 min</span>
                      </div>
                      <div className="flex items-center gap-3 text-xs text-slate-300">
                         <Zap className="h-4 w-4 text-cyan-400 shrink-0" />
                         <span>Mode: {agent?.invocationMode}</span>
                      </div>
                      <div className="flex items-center gap-3 text-xs text-slate-300">
                         <Shield className="h-4 w-4 text-cyan-400 shrink-0" />
                         <span>Tier: {agent?.tierMinimum}+</span>
                      </div>
                   </div>
                </div>

                <div className="rounded-2xl border border-white/5 bg-amber-500/5 p-5">
                   <div className="flex items-center gap-2 mb-2 text-amber-500">
                      <Info className="h-4 w-4" />
                      <Typography variant="small" className="font-bold">Usage Note</Typography>
                   </div>
                   <Typography variant="muted" className="text-[11px] leading-relaxed">
                      All prompts are logged for compliance. Do not include unencrypted secrets or PII unless 
                      the agent is explicitly configured for that data type.
                   </Typography>
                </div>
                
                <Button asChild variant="ghost" className="w-full text-slate-500 hover:text-white">
                   <Link to="/agents" className="gap-2">
                      <ArrowLeft className="h-4 w-4" />
                      Back to Catalogue
                   </Link>
                </Button>
            </aside>
        </div>
    );
};
