import React, { useEffect, useMemo, useState } from "react";
import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { resolveTenantId } from "../auth/identity";
import { PageBanner } from "../components/PageBanner";
import { Loading } from "../components/ui/loading";
import { Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { Input } from "../components/ui/input";
import { Label } from "../components/ui/label";
import { Badge } from "../components/ui/badge";
import { Typography } from "../components/ui/typography";
import { 
  BarChart3, 
  Key, 
  UserPlus, 
  ShieldCheck, 
  RotateCcw, 
  Mail, 
  AlertCircle,
  CreditCard,
  Zap
} from "lucide-react";
import { cn } from "../lib/utils";

type TenantUsage = {
    requestsToday?: number;
    budgetRemainingUsd?: number;
    usageIdentifierKey?: string;
};

type TenantRecord = {
    tenantId: string;
    appId: string;
    displayName: string;
    tier: string;
    status: string;
    updatedAt: string;
    apiKeySecretArn?: string;
    usage?: TenantUsage;
};

type RotateResponse = {
    tenantId: string;
    apiKeySecretArn: string;
    rotatedAt: string;
    versionId?: string | null;
};

type InviteResponse = {
    invite: {
        inviteId: string;
        tenantId: string;
        email: string;
        role: string;
        status: string;
        expiresAt: string;
    };
};

import { EmptyState } from "../components/ui/empty-state";

type TenantPortalPageProps = {
    initialSection?: "overview" | "access" | "api-keys" | "webhooks";
};

const sectionMessages: Record<NonNullable<TenantPortalPageProps["initialSection"]>, string> = {
    overview: "Usage, key posture, and invitation controls are grouped here for the current tenant.",
    access: "This route focuses the tenant access workflow, including invitation issuance.",
    "api-keys": "This route focuses machine identity hygiene and rotation cadence.",
    webhooks: "This route focuses on configuring async result delivery destinations.",
};


export const TenantPortalPage: React.FC<TenantPortalPageProps> = ({ initialSection = "overview" }) => {
    const { getAccessToken, account, isAuthenticated } = useAuth();
    const tenantId = useMemo(() => resolveTenantId(account?.idTokenClaims), [account?.idTokenClaims]);

    const [tenant, setTenant] = useState<TenantRecord | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [rotating, setRotating] = useState(false);
    const [rotateMessage, setRotateMessage] = useState<string | null>(null);
    const [inviteEmail, setInviteEmail] = useState("");
    const [inviteRole, setInviteRole] = useState("Agent.Invoke");
    const [invitePending, setInvitePending] = useState(false);
    const [inviteMessage, setInviteMessage] = useState<string | null>(null);

    useEffect(() => {
        if (!isAuthenticated) {
            setLoading(false);
            return;
        }
        if (!tenantId) {
            setLoading(false);
            setError("Token is missing required tenantid claim.");
            return;
        }

        const run = async () => {
            try {
                const client = getApiClient(getAccessToken);
                const data = await client.request<{ tenant: TenantRecord }>(`/v1/tenants/${tenantId}`);
                setTenant(data.tenant);
                setError(null);
            } catch (err) {
                const message = err instanceof Error ? err.message : "Failed to load tenant portal data.";
                setError(message);
            } finally {
                setLoading(false);
            }
        };
        void run();
    }, [getAccessToken, isAuthenticated, tenantId]);

    const onRotateApiKey = async () => {
        if (!tenantId) return;
        setRotating(true);
        setRotateMessage(null);
        try {
            const client = getApiClient(getAccessToken);
            const result = await client.request<RotateResponse>(`/v1/tenants/${tenantId}/api-key/rotate`, {
                method: "POST",
            });
            setRotateMessage(
                `API key rotated at ${new Date(result.rotatedAt).toLocaleString()} (version ${result.versionId ?? "n/a"}).`,
            );
            setTenant((prev) =>
                prev
                    ? {
                        ...prev,
                        apiKeySecretArn: result.apiKeySecretArn,
                        updatedAt: result.rotatedAt,
                    }
                    : prev,
            );
        } catch (err) {
            const message = err instanceof Error ? err.message : "API key rotation failed.";
            setRotateMessage(message);
        } finally {
            setRotating(false);
        }
    };

    const onInviteSubmit = async (event: React.FormEvent<HTMLFormElement>) => {
        event.preventDefault();
        if (!tenantId || !inviteEmail.trim()) return;
        setInvitePending(true);
        setInviteMessage(null);
        try {
            const client = getApiClient(getAccessToken);
            const payload = {
                email: inviteEmail.trim(),
                role: inviteRole.trim() || "Agent.Invoke",
            };
            const response = await client.request<InviteResponse>(`/v1/tenants/${tenantId}/users/invite`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(payload),
            });
            setInviteMessage(
                `Invite accepted for ${response.invite.email}; expires ${new Date(
                    response.invite.expiresAt,
                ).toLocaleString()}.`,
            );
            setInviteEmail("");
        } catch (err) {
            const message = err instanceof Error ? err.message : "Failed to submit invite.";
            setInviteMessage(message);
        } finally {
            setInvitePending(false);
        }
    };

    if (loading) return <Loading message="Loading tenant workspace..." size="lg" className="h-[400px]" />;

    if (error) {
        return (
            <PageBanner title="Identity Resolution Error" severity="error">
                {error}
            </PageBanner>
        );
    }

    if (!tenant) {
        return (
            <EmptyState 
                title="No Tenant Context" 
                description="Unable to resolve tenant metadata for the current session. Please re-authenticate."
                icon={AlertCircle}
            />
        );
    }

    return (
        <div className="space-y-8 animate-in fade-in duration-500">
            <div>
                <Typography variant="h2" className="border-none pb-0">Tenant Settings</Typography>
                <Typography variant="muted" className="mt-1">
                    Self-service controls and usage visibility for <span className="font-mono text-cyan-400 font-bold">{tenant.tenantId}</span>.
                </Typography>
            </div>

            <PageBanner title={`Self-Service / ${initialSection}`} severity="info">
                {sectionMessages[initialSection]}
            </PageBanner>

            <div className="grid gap-6 lg:grid-cols-2">
                <Card className="border-white/5 bg-slate-900/40 backdrop-blur-sm overflow-hidden flex flex-col">
                    <CardHeader>
                        <div className="flex items-center gap-3">
                            <div className="h-10 w-10 rounded-xl bg-cyan-500/10 flex items-center justify-center text-cyan-400">
                                <BarChart3 className="h-5 w-5" />
                            </div>
                            <div>
                                <CardTitle className="text-lg">Usage Snapshot</CardTitle>
                                <CardDescription>Real-time platform consumption metrics.</CardDescription>
                            </div>
                        </div>
                    </CardHeader>
                    <CardContent className="flex-1 grid gap-4">
                        <div className="grid grid-cols-2 gap-4">
                            <MetricCard label="Requests Today" value={tenant.usage?.requestsToday ?? 0} icon={Zap} />
                            <MetricCard label="Remaining Budget" value={`$${tenant.usage?.budgetRemainingUsd ?? 0}`} icon={CreditCard} />
                        </div>
                        <div className="rounded-xl border border-white/5 bg-white/5 p-4 flex items-center justify-between">
                            <div className="space-y-1">
                                <Typography variant="muted" className="text-[10px] font-bold uppercase tracking-widest opacity-60">Status Posture</Typography>
                                <Typography variant="small" className="text-white font-bold capitalize">{tenant.tier} Tier / {tenant.status}</Typography>
                            </div>
                            <Badge variant={tenant.status === "active" ? "success" : "warning"} className="tracking-widest text-[10px]">
                                {tenant.status.toUpperCase()}
                            </Badge>
                        </div>
                    </CardContent>
                </Card>

                <Card className="border-white/5 bg-slate-900/40 backdrop-blur-sm overflow-hidden flex flex-col">
                    <CardHeader>
                        <div className="flex items-center gap-3">
                            <div className="h-10 w-10 rounded-xl bg-emerald-500/10 flex items-center justify-center text-emerald-400">
                                <Key className="h-5 w-5" />
                            </div>
                            <div>
                                <CardTitle className="text-lg">API Key Rotation</CardTitle>
                                <CardDescription>Manage machine identity and rotation cadence.</CardDescription>
                            </div>
                        </div>
                    </CardHeader>
                    <CardContent className="flex-1 space-y-4">
                        <div className="rounded-xl border border-white/5 bg-slate-950/50 p-4">
                           <Typography variant="muted" className="text-[10px] font-bold uppercase tracking-widest opacity-60 block mb-2">Secret ARN</Typography>
                           <Typography variant="small" className="font-mono text-xs text-slate-300 break-all leading-relaxed">
                              {tenant.apiKeySecretArn ?? "Identity store not yet provisioned."}
                           </Typography>
                        </div>
                        
                        {rotateMessage && (
                           <div className={cn(
                             "rounded-xl border p-4 text-xs font-medium animate-in slide-in-from-top-2",
                             rotateMessage.includes("rotated") ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-400" : "border-destructive/20 bg-destructive/10 text-destructive"
                           )}>
                              {rotateMessage}
                           </div>
                        )}
                    </CardContent>
                    <CardFooter className="pt-0">
                        <Button 
                            onClick={onRotateApiKey} 
                            disabled={rotating} 
                            variant="accent"
                            className="w-full rounded-xl gap-2 font-bold"
                        >
                            {rotating ? <Loading size="sm" className="p-0 h-4 w-4" message="" /> : <RotateCcw className="h-4 w-4" />}
                            {rotating ? "Initiating Rotation..." : "Rotate Secret Now"}
                        </Button>
                    </CardFooter>
                </Card>

                <Card className="border-white/5 bg-slate-900/40 backdrop-blur-sm overflow-hidden lg:col-span-2">
                    <CardHeader>
                        <div className="flex items-center gap-3">
                            <div className="h-10 w-10 rounded-xl bg-blue-500/10 flex items-center justify-center text-blue-400">
                                <UserPlus className="h-5 w-5" />
                            </div>
                            <div>
                                <CardTitle className="text-lg">Member Invitation</CardTitle>
                                <CardDescription>Issue new tenant-scoped human identities via Entra ID.</CardDescription>
                            </div>
                        </div>
                    </CardHeader>
                    <CardContent>
                        <form className="space-y-6 max-w-2xl" onSubmit={onInviteSubmit}>
                            <div className="grid gap-6 sm:grid-cols-2">
                                <div className="space-y-2">
                                    <Label htmlFor="invite-email" className="text-slate-400 font-bold uppercase tracking-widest text-[10px]">Email Address</Label>
                                    <Input
                                        id="invite-email"
                                        type="email"
                                        required
                                        value={inviteEmail}
                                        onChange={(e) => setInviteEmail(e.target.value)}
                                        className="rounded-xl border-white/10 bg-white/5 text-white focus:ring-blue-500/50"
                                        placeholder="user@enterprise.com"
                                    />
                                </div>
                                <div className="space-y-2">
                                    <Label htmlFor="invite-role" className="text-slate-400 font-bold uppercase tracking-widest text-[10px]">Assigned Role</Label>
                                    <Input
                                        id="invite-role"
                                        type="text"
                                        value={inviteRole}
                                        onChange={(e) => setInviteRole(e.target.value)}
                                        className="rounded-xl border-white/10 bg-white/5 text-white focus:ring-blue-500/50 font-mono text-xs"
                                    />
                                </div>
                            </div>

                            <div className="flex items-center justify-between gap-4 pt-2">
                               <div className="flex items-center gap-2 text-xs text-slate-500">
                                  <ShieldCheck className="h-4 w-4 text-emerald-500" />
                                  Role-based access will be enforced on next login.
                               </div>
                               <Button
                                    type="submit"
                                    disabled={invitePending || !inviteEmail.trim()}
                                    className="rounded-xl px-8 font-bold gap-2"
                                >
                                    {invitePending ? <Loading size="sm" className="p-0 h-4 w-4" message="" /> : <Mail className="h-4 w-4" />}
                                    {invitePending ? "Processing..." : "Issue Invitation"}
                                </Button>
                            </div>
                            
                            {inviteMessage && (
                                <div className={cn(
                                  "rounded-xl border p-4 text-xs font-medium animate-in slide-in-from-top-2",
                                  inviteMessage.includes("accepted") ? "border-emerald-500/20 bg-emerald-500/10 text-emerald-400" : "border-destructive/20 bg-destructive/10 text-destructive"
                                )}>
                                   {inviteMessage}
                                </div>
                            )}
                        </form>
                    </CardContent>
                </Card>
            </div>
        </div>
    );
};

function MetricCard({ label, value, icon: Icon }: { label: string; value: string | number; icon: any }) {
    return (
        <div className="rounded-xl border border-white/5 bg-white/5 p-4 space-y-2">
            <Typography variant="muted" className="text-[10px] font-bold uppercase tracking-widest opacity-60 flex items-center gap-1.5">
                <Icon className="h-3 w-3" />
                {label}
            </Typography>
            <Typography variant="h3" className="text-2xl font-bold text-white tracking-tight">{value}</Typography>
        </div>
    );
}
