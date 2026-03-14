import React, { useEffect, useMemo, useState } from "react";
import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";

type TenantRecord = {
    tenantId: string;
    displayName: string;
    tier: string;
    status: string;
    ownerEmail: string;
    ownerTeam: string;
    createdAt: string;
    updatedAt: string;
};

function resolveTenantId(claims: unknown): string | null {
    if (!claims || typeof claims !== "object") {
        return null;
    }
    const map = claims as Record<string, unknown>;
    const tenantId = map.tenantid ?? map.tenantId;
    return typeof tenantId === "string" ? tenantId.trim() : null;
}

export const TenantSettingsPage: React.FC = () => {
    const { getAccessToken, account, isAuthenticated } = useAuth();
    const tenantId = useMemo(() => resolveTenantId(account?.idTokenClaims), [account?.idTokenClaims]);

    const [tenant, setTenant] = useState<TenantRecord | null>(null);
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
                const data = await client.request<{ tenant: TenantRecord }>(`/v1/tenants/${tenantId}`);
                setTenant(data.tenant);
            } catch (err) {
                setError(err instanceof Error ? err.message : "Failed to load settings.");
            } finally {
                setLoading(false);
            }
        };
        void run();
    }, [getAccessToken, isAuthenticated, tenantId]);

    if (loading) return <div className="p-8">Loading settings...</div>;
    if (error) return <div className="p-8 text-red-600">Error: {error}</div>;
    if (!tenant) return <div className="p-8">No tenant data.</div>;

    return (
        <div className="space-y-6">
            <header>
                <h1 className="text-2xl font-bold text-gray-900">Tenant Settings</h1>
                <p className="text-gray-600">General configuration for {tenant.tenantId}</p>
            </header>

            <section className="bg-white shadow sm:rounded-lg border border-gray-200 overflow-hidden">
                <div className="px-6 py-4 border-b border-gray-200 bg-gray-50">
                    <h2 className="font-semibold text-gray-900">Tenant Profile</h2>
                </div>
                <div className="p-6 space-y-4 max-w-2xl">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                        <div>
                            <label className="block text-sm font-medium text-gray-500 uppercase">Tenant ID</label>
                            <p className="mt-1 text-sm text-gray-900 font-mono">{tenant.tenantId}</p>
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-500 uppercase">Display Name</label>
                            <p className="mt-1 text-sm text-gray-900">{tenant.displayName}</p>
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-500 uppercase">Current Tier</label>
                            <p className="mt-1 text-sm text-gray-900 capitalize">{tenant.tier}</p>
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-500 uppercase">Status</label>
                            <p className="mt-1 text-sm text-gray-900 capitalize">{tenant.status}</p>
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-500 uppercase">Owner Email</label>
                            <p className="mt-1 text-sm text-gray-900">{tenant.ownerEmail}</p>
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-500 uppercase">Owner Team</label>
                            <p className="mt-1 text-sm text-gray-900">{tenant.ownerTeam}</p>
                        </div>
                    </div>
                </div>
            </section>

            <section className="bg-white shadow sm:rounded-lg border border-gray-200 overflow-hidden">
                <div className="px-6 py-4 border-b border-gray-200 bg-gray-50">
                    <h2 className="font-semibold text-gray-900">Metadata</h2>
                </div>
                <div className="p-6 space-y-4">
                    <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 text-sm">
                        <p className="text-gray-500">Created At: <span className="text-gray-900">{new Date(tenant.createdAt).toLocaleString()}</span></p>
                        <p className="text-gray-500">Last Updated: <span className="text-gray-900">{new Date(tenant.updatedAt).toLocaleString()}</span></p>
                    </div>
                </div>
            </section>

            <section className="bg-red-50 p-6 rounded-lg border border-red-200">
                <h3 className="text-red-900 font-semibold mb-2">Advanced Actions</h3>
                <p className="text-red-800 text-sm mb-4">
                    To change your tier or request account deletion, please contact platform support.
                </p>
                <button
                    disabled
                    className="inline-flex items-center px-4 py-2 border border-red-300 text-sm font-medium rounded-md text-red-700 bg-white hover:bg-red-50 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500 disabled:opacity-50"
                >
                    Contact Support
                </button>
            </section>
        </div>
    );
};
