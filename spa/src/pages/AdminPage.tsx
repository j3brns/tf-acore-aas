import React, { useEffect, useState } from "react";
import {
  HealthResponseDto,
  PlatformQuotaResponseDto,
  TenantAdminRow,
  TenantsListResponseDto,
  toTenantAdminRow,
} from "../api/contracts";
import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { hasPlatformOperatorRole } from "../auth/identity";
import { PageBanner } from "../components/PageBanner";
import { Loading } from "../components/ui/loading";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { 
  Table, 
  TableBody, 
  TableCell, 
  TableHead, 
  TableHeader, 
  TableRow 
} from "../components/ui/table";
import { Badge } from "../components/ui/badge";
import { Typography } from "../components/ui/typography";
import { Progress } from "../components/ui/progress";
import { 
  Activity, 
  Users, 
  Cpu, 
  ShieldAlert, 
  Server, 
  ArrowUpRight,
  Database,
  Globe
} from "lucide-react";
import { cn } from "../lib/utils";

type AdminPageProps = {
  initialSection?: "overview" | "tenants" | "quota";
};

const sectionBannerCopy: Record<NonNullable<AdminPageProps["initialSection"]>, string> = {
  overview: "Platform health and cross-region runtime posture are in view.",
  tenants: "Tenant portfolio data is surfaced here for operator review.",
  quota: "Quota headroom is highlighted here before runtime saturation becomes an incident.",
};

export const AdminPage: React.FC<AdminPageProps> = ({ initialSection = "overview" }) => {
  const [health, setHealth] = useState<HealthResponseDto | null>(null);
  const [tenants, setTenants] = useState<TenantAdminRow[]>([]);
  const [quota, setQuota] = useState<PlatformQuotaResponseDto["utilisation"]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const { getAccessToken, account, isAuthenticated } = useAuth();

  const isAdmin = hasPlatformOperatorRole(account?.idTokenClaims);

  useEffect(() => {
    if (!isAdmin || !isAuthenticated) {
      if (!isAdmin) setLoading(false);
      return;
    }

    const fetchAdminData = async () => {
      try {
        const client = getApiClient(getAccessToken);

        const [healthData, tenantsData, quotaData] = await Promise.all([
          client.request<HealthResponseDto>("/v1/health"),
          client.request<TenantsListResponseDto>("/v1/tenants"),
          client
            .request<PlatformQuotaResponseDto>("/v1/platform/quota")
            .catch(() => ({ utilisation: [] })),
        ]);

        setHealth(healthData);
        setTenants((tenantsData.items || []).map(toTenantAdminRow));
        setQuota(quotaData.utilisation || []);
      } catch (err: unknown) {
        setError(err instanceof Error ? err.message : "Failed to load admin telemetry data.");
      } finally {
        setLoading(false);
      }
    };

    void fetchAdminData();
  }, [getAccessToken, isAdmin, isAuthenticated]);

  if (loading) return <Loading message="Syncing operator console..." size="lg" className="h-[400px]" />;

  if (!isAdmin) {
    return (
      <PageBanner title="Elevated Privilege Required" severity="error">
        This route is reserved for platform operators. Your current token does not carry the <code>Platform.Operator</code> claim.
      </PageBanner>
    );
  }

  if (error) {
    return (
      <PageBanner title="Telemetry Sync Failed" severity="error">
        {error}
      </PageBanner>
    );
  }

  return (
    <div className="space-y-8 animate-in fade-in duration-500">
      <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <Typography variant="h2" className="border-none pb-0">Operator Console</Typography>
          <Typography variant="muted" className="mt-1">
            Global platform oversight and cross-tenant administration.
          </Typography>
        </div>
        <Badge variant="outline" className="h-8 gap-2 border-emerald-500/20 bg-emerald-500/5 text-emerald-400 font-bold uppercase tracking-widest text-[10px]">
           <ShieldAlert className="h-3 w-3" />
           Live Supervision
        </Badge>
      </div>

      <PageBanner title={`Operations / ${initialSection}`} severity="info">
        {sectionBannerCopy[initialSection]}
      </PageBanner>

      <div className="grid gap-6 lg:grid-cols-3">
        <Card className="border-white/5 bg-slate-900/40 backdrop-blur-sm overflow-hidden flex flex-col">
          <CardHeader className="pb-4 border-b border-white/5">
            <div className="flex items-center gap-3">
              <div className="h-10 w-10 rounded-xl bg-emerald-500/10 flex items-center justify-center text-emerald-400">
                <Activity className="h-5 w-5" />
              </div>
              <div>
                <CardTitle className="text-base">System Health</CardTitle>
                <CardDescription className="text-[10px] uppercase tracking-tighter font-bold">API & Control Plane</CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent className="pt-6">
            <div className="flex items-center justify-between mb-6">
               <div className="flex items-center gap-3">
                  <div className={cn(
                    "h-3 w-3 rounded-full animate-pulse shadow-[0_0_8px_rgba(0,0,0,0.5)]",
                    health?.status === "ok" ? "bg-emerald-500 shadow-emerald-500/50" : "bg-destructive shadow-destructive/50"
                  )} />
                  <Typography variant="large" className="text-white font-bold uppercase">{health?.status || "Unknown"}</Typography>
               </div>
               <Badge variant="secondary" className="text-[10px] font-mono">v{health?.version || "?.?.?"}</Badge>
            </div>
            <div className="space-y-3">
               <div className="flex items-center justify-between text-xs">
                  <span className="text-slate-500 font-bold uppercase tracking-widest text-[9px]">Last Telemetry</span>
                  <span className="text-slate-300 font-mono">{health?.timestamp ? new Date(health.timestamp).toLocaleTimeString() : "N/A"}</span>
               </div>
               <div className="flex items-center justify-between text-xs">
                  <span className="text-slate-500 font-bold uppercase tracking-widest text-[9px]">Uptime Tier</span>
                  <span className="text-emerald-400 font-bold">99.99% Guaranteed</span>
               </div>
            </div>
          </CardContent>
        </Card>

        <Card className="border-white/5 bg-slate-900/40 backdrop-blur-sm overflow-hidden flex flex-col lg:col-span-2">
          <CardHeader className="pb-4 border-b border-white/5">
            <div className="flex items-center gap-3">
              <div className="h-10 w-10 rounded-xl bg-cyan-500/10 flex items-center justify-center text-cyan-400">
                <Cpu className="h-5 w-5" />
              </div>
              <div>
                <CardTitle className="text-base">AgentCore Quota Utilisation</CardTitle>
                <CardDescription className="text-[10px] uppercase tracking-tighter font-bold">Runtime Capacity Planning</CardDescription>
              </div>
            </div>
          </CardHeader>
          <CardContent className="pt-6 grid gap-6 sm:grid-cols-2">
            {quota.map((q) => (
              <div key={q.region} className="space-y-3">
                <div className="flex justify-between items-end">
                  <div className="space-y-0.5">
                     <div className="flex items-center gap-1.5">
                        <Globe className="h-3 w-3 text-slate-500" />
                        <span className="text-[10px] font-bold text-white uppercase tracking-widest">{q.region}</span>
                     </div>
                     <p className="text-[11px] text-slate-400 font-medium">{q.quotaName}</p>
                  </div>
                  <span className={cn(
                    "text-xs font-bold font-mono",
                    q.utilisationPercentage > 80 ? "text-destructive" : q.utilisationPercentage > 60 ? "text-warning" : "text-cyan-400"
                  )}>{q.utilisationPercentage}%</span>
                </div>
                <Progress 
                  value={q.utilisationPercentage} 
                  indicatorClassName={cn(
                    q.utilisationPercentage > 80 ? "bg-destructive" : q.utilisationPercentage > 60 ? "bg-warning" : "bg-cyan-500"
                  )}
                />
                <div className="flex justify-between text-[10px] font-bold text-slate-500 uppercase tracking-tighter">
                  <span>Current: {q.currentValue}</span>
                  <span>Limit: {q.limit}</span>
                </div>
              </div>
            ))}
            {quota.length === 0 && (
               <div className="col-span-2 py-4 text-center">
                  <Typography variant="muted" className="text-xs">No active runtime quotas detected.</Typography>
               </div>
            )}
          </CardContent>
        </Card>
      </div>

      <Card className="border-white/5 bg-slate-900/40 backdrop-blur-sm overflow-hidden">
        <CardHeader className="flex flex-row items-center justify-between border-b border-white/5 pb-4">
          <div className="flex items-center gap-3">
            <div className="h-10 w-10 rounded-xl bg-blue-500/10 flex items-center justify-center text-blue-400">
              <Users className="h-5 w-5" />
            </div>
            <div>
              <CardTitle className="text-base">Tenant Portfolio</CardTitle>
              <CardDescription className="text-[10px] uppercase tracking-tighter font-bold">Cross-Tenant Visibility</CardDescription>
            </div>
          </div>
          <Badge variant="secondary" className="font-mono h-6">{tenants.length} Tenants</Badge>
        </CardHeader>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Tenant Identity</TableHead>
              <TableHead>Service Tier</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Compute Region</TableHead>
              <TableHead className="text-right">Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {tenants.map((tenant) => (
              <TableRow key={tenant.tenantId}>
                <TableCell>
                  <div className="flex flex-col gap-0.5">
                    <span className="font-bold text-white text-sm">{tenant.displayName}</span>
                    <span className="font-mono text-[10px] text-cyan-400/70">{tenant.tenantId}</span>
                  </div>
                </TableCell>
                <TableCell>
                  <Badge variant="outline" className={cn(
                    "uppercase tracking-widest text-[9px] border-white/10",
                    tenant.tier === "premium" ? "text-destructive border-destructive/20" : "text-blue-400"
                  )}>
                    {tenant.tier}
                  </Badge>
                </TableCell>
                <TableCell>
                  <div className="flex items-center gap-2">
                     <div className={cn(
                       "h-1.5 w-1.5 rounded-full",
                       tenant.status === "active" ? "bg-emerald-500" : "bg-destructive"
                     )} />
                     <span className="capitalize font-medium text-xs text-slate-300">{tenant.status}</span>
                  </div>
                </TableCell>
                <TableCell>
                   <div className="flex items-center gap-2 text-xs text-slate-400">
                      <Server className="h-3 w-3" />
                      {tenant.runtimeRegion || "Unassigned"}
                   </div>
                </TableCell>
                <TableCell className="text-right">
                   <Button variant="ghost" size="icon" className="h-8 w-8 text-slate-500 hover:text-white hover:bg-white/5">
                      <ArrowUpRight className="h-4 w-4" />
                   </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </Card>
      
      <div className="grid gap-6 sm:grid-cols-2 lg:grid-cols-4">
         <OperationalMetric icon={Database} label="Data Residency" value="EU-Only" tone="success" />
         <OperationalMetric icon={Server} label="Compute Mesh" value="eu-west-1 / eu-central-1" tone="info" />
         <OperationalMetric icon={ShieldAlert} label="Security Engine" value="Cedar Enabled" tone="success" />
         <OperationalMetric icon={Activity} label="Monitoring" value="CloudWatch Native" tone="neutral" />
      </div>
    </div>
  );
};

function OperationalMetric({ icon: Icon, label, value, tone }: { icon: any; label: string; value: string; tone: "success" | "info" | "neutral" }) {
   const toneClasses = {
      success: "text-emerald-400 bg-emerald-500/10",
      info: "text-cyan-400 bg-cyan-500/10",
      neutral: "text-slate-400 bg-slate-500/10"
   };
   
   return (
      <div className="rounded-2xl border border-white/5 bg-slate-900/40 p-4 space-y-3">
         <div className={cn("h-8 w-8 rounded-lg flex items-center justify-center", toneClasses[tone])}>
            <Icon className="h-4 w-4" />
         </div>
         <div className="space-y-1">
            <p className="text-[9px] font-bold text-slate-500 uppercase tracking-widest">{label}</p>
            <p className="text-xs font-bold text-white">{value}</p>
         </div>
      </div>
   );
}
