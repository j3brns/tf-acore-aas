import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { AgentsListResponseDto, AgentCatalogueItem, toAgentCatalogueItem } from "../api/contracts";
import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { Loading } from "../components/ui/loading";
import { PageBanner } from "../components/PageBanner";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Typography } from "../components/ui/typography";
import { EmptyState } from "../components/ui/empty-state";
import { Bot, Zap, Search, ArrowRight, Cpu, Shield } from "lucide-react";

export const AgentCataloguePage: React.FC = () => {
    const [agents, setAgents] = useState<AgentCatalogueItem[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const { getAccessToken, isAuthenticated } = useAuth();

    useEffect(() => {
        const fetchAgents = async () => {
            if (!isAuthenticated) return;
            try {
                const client = getApiClient(getAccessToken);
                const data = await client.request<AgentsListResponseDto>("/v1/agents");
                setAgents((data.items || []).map(toAgentCatalogueItem));
            } catch (err: unknown) {
                console.error("Failed to fetch agents", err);
                setError(err instanceof Error ? err.message : "Failed to load platform agents. Please check your connectivity.");
            } finally {
                setLoading(false);
            }
        };

        fetchAgents();
    }, [getAccessToken, isAuthenticated]);

    if (loading) {
        return <Loading message="Retrieving agent catalogue..." size="lg" className="h-[400px]" />;
    }

    if (error) {
        return (
            <PageBanner title="Catalogue Unavailable" severity="error">
                {error}
            </PageBanner>
        );
    }

    return (
        <div className="space-y-8 animate-in fade-in duration-500">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div>
                    <Typography variant="h2" className="border-none pb-0">Available Agents</Typography>
                    <Typography variant="muted" className="mt-1">
                        Discover and invoke specialized AI agents available for your current tier.
                    </Typography>
                </div>
                
                <div className="relative max-w-sm w-full">
                    <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-slate-500" />
                    <input 
                        type="search" 
                        placeholder="Search agents..." 
                        className="w-full pl-10 pr-4 py-2 rounded-xl border border-white/10 bg-white/5 text-sm focus:outline-none focus:ring-2 focus:ring-cyan-500/50 transition-all"
                    />
                </div>
            </div>

            {agents.length === 0 ? (
                <EmptyState 
                    title="No Agents Found" 
                    description="The agent catalogue is currently empty for your tenant or tier. Contact your operator to register new agents."
                    icon={Bot}
                />
            ) : (
                <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
                    {agents.map((agent) => (
                        <Card key={`${agent.agentName}-${agent.version}`} className="group border-white/5 bg-slate-900/40 hover:bg-slate-900/60 transition-all hover:shadow-2xl hover:shadow-cyan-500/5 overflow-hidden flex flex-col">
                            <CardHeader className="pb-4">
                                <div className="flex items-start justify-between">
                                    <div className="h-10 w-10 rounded-xl bg-cyan-500/10 flex items-center justify-center text-cyan-400 mb-3 group-hover:scale-110 transition-transform">
                                        <Bot className="h-6 w-6" />
                                    </div>
                                    <Badge variant={
                                        agent.tier === "premium" ? "destructive" :
                                        agent.tier === "standard" ? "default" :
                                        "success"
                                    } className="uppercase tracking-widest text-[10px]">
                                        {agent.tier}
                                    </Badge>
                                </div>
                                <CardTitle className="text-xl font-bold text-white group-hover:text-cyan-400 transition-colors">
                                    {agent.agentName}
                                </CardTitle>
                                <CardDescription className="text-slate-400 flex items-center gap-2">
                                    <span className="font-mono text-[10px] bg-white/5 px-1.5 py-0.5 rounded border border-white/5">v{agent.version}</span>
                                    <span>•</span>
                                    <span className="capitalize">{agent.invocationMode}</span>
                                </CardDescription>
                            </CardHeader>
                            
                            <CardContent className="flex-1 space-y-4">
                                <div className="grid grid-cols-2 gap-3">
                                    <div className="space-y-1">
                                        <Typography variant="muted" className="text-[10px] uppercase font-bold tracking-wider opacity-60 flex items-center gap-1.5">
                                            <Cpu className="h-3 w-3" />
                                            Owner
                                        </Typography>
                                        <Typography variant="small" className="text-slate-300 truncate">{agent.ownerTeam}</Typography>
                                    </div>
                                    <div className="space-y-1">
                                        <Typography variant="muted" className="text-[10px] uppercase font-bold tracking-wider opacity-60 flex items-center gap-1.5">
                                            <Zap className="h-3 w-3" />
                                            Capabilities
                                        </Typography>
                                        <div className="flex flex-wrap gap-1.5">
                                            {agent.streamingEnabled && (
                                                <Badge variant="outline" className="text-[9px] h-4 border-cyan-500/20 text-cyan-400">Streaming</Badge>
                                            )}
                                            <Badge variant="outline" className="text-[9px] h-4 border-white/10 text-slate-400">{agent.invocationMode}</Badge>
                                        </div>
                                    </div>
                                </div>
                            </CardContent>

                            <CardFooter className="pt-4 border-t border-white/5">
                                <Button asChild variant="accent" className="w-full rounded-xl group/btn">
                                    <Link to={`/invoke/${agent.agentName}`} className="flex items-center justify-center w-full">
                                        Invoke Agent
                                        <ArrowRight className="ml-2 h-4 w-4 transition-transform group-hover/btn:translate-x-1" />
                                    </Link>
                                </Button>
                            </CardFooter>
                        </Card>
                    ))}
                </div>
            )}

            <div className="rounded-2xl border border-white/5 bg-white/5 p-6 flex items-start gap-4">
               <div className="h-10 w-10 rounded-full bg-amber-500/10 flex items-center justify-center text-amber-500 shrink-0">
                  <Shield className="h-5 w-5" />
               </div>
               <div>
                  <Typography variant="small" className="font-bold text-white">Tier Gating Active</Typography>
                  <Typography variant="muted" className="mt-1 text-xs">
                     Some agents may require a higher tier (Standard or Premium) to invoke. 
                     Contact your tenant administrator for account upgrades or usage quota increases.
                  </Typography>
               </div>
            </div>
        </div>
    );
};
