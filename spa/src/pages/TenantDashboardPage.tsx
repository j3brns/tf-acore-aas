import React, { useEffect, useMemo, useState } from "react";
import { getApiClient } from "../api/client";
import type { TenantReadResponseDto } from "../api/contracts";
import { useAuth } from "../auth/useAuth";
import { Link } from "react-router-dom";

function resolveTenantId(claims: unknown): string | null {
    if (!claims || typeof claims !== "object") {
        return null;
    }
    const map = claims as Record<string, unknown>;
    const tenantId = map.tenantid ?? map.tenantId;
    return typeof tenantId === "string" ? tenantId.trim() : null;
}

export const TenantDashboardPage: React.FC = () => {
    const { getAccessToken, account, isAuthenticated } = useAuth();
    const tenantId = useMemo(() => resolveTenantId(account?.idTokenClaims), [account?.idTokenClaims]);

    const [tenant, setTenant] = useState<TenantReadResponseDto["tenant"] | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!isAuthenticated || !tenantId) {
            setLoading(false);
            return;
        }

        const run = async () => {
            try {
                const client = getApiClient(getAccessToken);
                const data = await client.request<TenantReadResponseDto>(`/v1/tenants/${tenantId}`);
                setTenant(data.tenant);
            } catch (err) {
                setError(err instanceof Error ? err.message : "Failed to load dashboard.");
            } finally {
                setLoading(false);
            }
        };
        void run();
    }, [getAccessToken, isAuthenticated, tenantId]);

    if (loading) return <div className="p-8">Loading dashboard...</div>;
    if (error) return <div className="p-8 text-red-600">Error: {error}</div>;
    if (!tenant) return <div className="p-8">No tenant data.</div>;

    return (
        <div className="space-y-6">
            <header>
                <h1 className="text-2xl font-bold text-gray-900">Dashboard</h1>
                <p className="text-gray-600">Overview for {tenant.displayName} ({tenant.tenantId})</p>
            </header>

            <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                <div className="bg-white p-6 rounded-lg border border-gray-200 shadow-sm">
                    <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wider">Requests Today</h3>
                    <p className="mt-2 text-3xl font-bold text-gray-900">{tenant.usage?.requestsToday ?? 0}</p>
                    <div className="mt-4">
                        <Link to="/tenant/usage" className="text-sm text-blue-600 hover:underline">View detailed usage &rarr;</Link>
                    </div>
                </div>

                <div className="bg-white p-6 rounded-lg border border-gray-200 shadow-sm">
                    <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wider">Budget Posture</h3>
                    <p className="mt-2 text-3xl font-bold text-gray-900">
                        ${tenant.usage?.budgetRemainingUsd?.toFixed(2) ?? "0.00"}
                    </p>
                    <p className="text-xs text-gray-500 mt-1">Remaining for current month</p>
                </div>

                <div className="bg-white p-6 rounded-lg border border-gray-200 shadow-sm">
                    <h3 className="text-sm font-medium text-gray-500 uppercase tracking-wider">Tier / Status</h3>
                    <div className="mt-2 flex items-center space-x-2">
                        <span className="px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800 capitalize">
                            {tenant.tier}
                        </span>
                        <span className={`px-2.5 py-0.5 rounded-full text-xs font-medium ${
                            tenant.status === 'active' ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                        } capitalize`}>
                            {tenant.status}
                        </span>
                    </div>
                    <div className="mt-4">
                        <Link to="/tenant/settings" className="text-sm text-blue-600 hover:underline">Manage settings &rarr;</Link>
                    </div>
                </div>
            </div>

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                <section className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
                    <div className="px-6 py-4 border-b border-gray-200 bg-gray-50">
                        <h2 className="font-semibold text-gray-900">Quick Actions</h2>
                    </div>
                    <div className="p-6 grid grid-cols-2 gap-4">
                        <Link to="/tenant/api-keys" className="p-4 border border-gray-200 rounded-md hover:bg-gray-50 transition-colors text-center">
                            <span className="block text-sm font-medium">Rotate API Key</span>
                        </Link>
                        <Link to="/tenant/members" className="p-4 border border-gray-200 rounded-md hover:bg-gray-50 transition-colors text-center">
                            <span className="block text-sm font-medium">Invite User</span>
                        </Link>
                        <Link to="/tenant/webhooks" className="p-4 border border-gray-200 rounded-md hover:bg-gray-50 transition-colors text-center">
                            <span className="block text-sm font-medium">Add Webhook</span>
                        </Link>
                        <Link to="/tenant/audit" className="p-4 border border-gray-200 rounded-md hover:bg-gray-50 transition-colors text-center">
                            <span className="block text-sm font-medium">Export Audit</span>
                        </Link>
                    </div>
                </section>

                <section className="bg-white rounded-lg border border-gray-200 shadow-sm overflow-hidden">
                    <div className="px-6 py-4 border-b border-gray-200 bg-gray-50">
                        <h2 className="font-semibold text-gray-900">Recent Sessions</h2>
                    </div>
                    <div className="p-6 text-center text-gray-500 text-sm">
                        Session listing will return once tenant-backed session tracking is implemented.
                    </div>
                </section>
            </div>
        </div>
    );
};
