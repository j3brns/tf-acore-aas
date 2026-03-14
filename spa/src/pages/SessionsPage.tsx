import React, { useEffect, useState } from "react";
import { SessionRow, SessionsListResponseDto, toSessionRow } from "../api/contracts";
import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { Loading } from "../components/ui/loading";
import { PageBanner } from "../components/PageBanner";
import { Badge } from "../components/ui/badge";
import { Typography } from "../components/ui/typography";
import { EmptyState } from "../components/ui/empty-state";
import { 
  Table, 
  TableBody, 
  TableCell, 
  TableHead, 
  TableHeader, 
  TableRow 
} from "../components/ui/table";
import { Activity, Clock, Terminal, Calendar, Zap } from "lucide-react";

export const SessionsPage: React.FC = () => {
    const [sessions, setSessions] = useState<SessionRow[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const { getAccessToken, isAuthenticated } = useAuth();

    useEffect(() => {
        const fetchSessions = async () => {
            if (!isAuthenticated) return;
            try {
                const client = getApiClient(getAccessToken);
                const data = await client.request<SessionsListResponseDto>("/v1/sessions");
                setSessions((data.items || []).map(toSessionRow));
            } catch (err: unknown) {
                console.error("Failed to fetch sessions", err);
                setError(err instanceof Error ? err.message : "Failed to load active sessions.");
            } finally {
                setLoading(false);
            }
        };
        fetchSessions();
    }, [getAccessToken, isAuthenticated]);

    if (loading) return <Loading message="Retrieving active sessions..." className="h-[400px]" />;
    
    if (error) {
        return (
            <PageBanner title="Session Sync Failed" severity="error">
                {error}
            </PageBanner>
        );
    }

    return (
        <div className="space-y-8 animate-in fade-in duration-500">
            <div>
                <Typography variant="h2" className="border-none pb-0">Runtime Sessions</Typography>
                <Typography variant="muted" className="mt-1">
                    Monitor and manage active agent sessions across your tenant.
                </Typography>
            </div>

            {sessions.length === 0 ? (
                <EmptyState 
                    title="No Active Sessions" 
                    description="You don't have any active agent sessions at the moment. Start an invocation to create a new session."
                    icon={Activity}
                />
            ) : (
                <Table>
                    <TableHeader>
                        <TableRow>
                            <TableHead>Session Context</TableHead>
                            <TableHead>Agent</TableHead>
                            <TableHead className="hidden sm:table-cell">Timeline</TableHead>
                            <TableHead>Posture</TableHead>
                        </TableRow>
                    </TableHeader>
                    <TableBody>
                        {sessions.map((session) => (
                            <TableRow key={session.sessionId}>
                                <TableCell>
                                    <div className="flex flex-col gap-1">
                                        <span className="font-mono text-xs text-white bg-white/5 px-2 py-0.5 rounded w-fit border border-white/5">
                                            {session.sessionId.substring(0, 12)}...
                                        </span>
                                    </div>
                                </TableCell>
                                <TableCell>
                                    <div className="flex items-center gap-2">
                                        <div className="h-8 w-8 rounded-lg bg-cyan-500/10 flex items-center justify-center text-cyan-400">
                                            <Terminal className="h-4 w-4" />
                                        </div>
                                        <span className="font-bold text-white text-sm">{session.agentName}</span>
                                    </div>
                                </TableCell>
                                <TableCell className="hidden sm:table-cell">
                                    <div className="flex flex-col gap-1 text-xs">
                                        <div className="flex items-center gap-2 text-slate-400">
                                            <Calendar className="h-3 w-3" />
                                            <span>Started: {new Date(session.startedAt).toLocaleString()}</span>
                                        </div>
                                        <div className="flex items-center gap-2 text-slate-400 font-semibold">
                                            <Clock className="h-3 w-3" />
                                            <span>Last: {new Date(session.lastActivityAt).toLocaleString()}</span>
                                        </div>
                                    </div>
                                </TableCell>
                                <TableCell>
                                    <Badge 
                                      variant={session.status === "active" ? "success" : "secondary"}
                                      className="capitalize tracking-widest text-[10px]"
                                    >
                                        {session.status}
                                    </Badge>
                                </TableCell>
                            </TableRow>
                        ))}
                    </TableBody>
                </Table>
            )}

            <div className="rounded-2xl border border-white/5 bg-slate-900/40 p-6 flex items-start gap-4">
               <div className="h-10 w-10 rounded-full bg-cyan-500/10 flex items-center justify-center text-cyan-400 shrink-0">
                  <Zap className="h-5 w-5" />
               </div>
               <div>
                  <Typography variant="small" className="font-bold text-white">Session Continuity</Typography>
                  <Typography variant="muted" className="mt-1 text-xs">
                     Active sessions are kept alive via periodic background pings. 
                     Sessions that remain idle for more than 15 minutes will be automatically expired by the platform.
                  </Typography>
               </div>
            </div>
        </div>
    );
};
