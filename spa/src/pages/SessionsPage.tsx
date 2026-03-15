import React from "react";
import { useAuth } from "../auth/useAuth";
import { PageBanner } from "../components/PageBanner";
import { Typography } from "../components/ui/typography";
import { EmptyState } from "../components/ui/empty-state";
import { Activity, Zap } from "lucide-react";

export const SessionsPage: React.FC = () => {
    const { isAuthenticated, isLoading: authLoading } = useAuth();

    if (authLoading) return null;

    if (!isAuthenticated) {
        return (
            <PageBanner title="Authentication Required" severity="warning">
                Sign in with your Entra account to view active runtime sessions for this tenant.
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

            <PageBanner title="Session Listing Pending" severity="info">
                Tenant-backed session listing is not deployed yet. Use the invoke flow to maintain an active session, and check back once the northbound route is published.
            </PageBanner>

            <EmptyState 
                title="Session Listing Not Yet Available" 
                description="The current tenant API still returns not implemented for session enumeration. Existing sessions remain active, but this page will not call the undeployed route."
                icon={Activity}
            />

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
