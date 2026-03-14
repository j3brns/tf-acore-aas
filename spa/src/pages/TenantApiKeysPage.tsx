import React, { useEffect, useMemo, useState } from "react";
import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";

type TenantRecord = {
    tenantId: string;
    apiKeySecretArn?: string;
    updatedAt: string;
};

type RotateResponse = {
    tenantId: string;
    apiKeySecretArn: string;
    rotatedAt: string;
    versionId?: string | null;
};

function resolveTenantId(claims: unknown): string | null {
    if (!claims || typeof claims !== "object") {
        return null;
    }
    const map = claims as Record<string, unknown>;
    const tenantId = map.tenantid ?? map.tenantId;
    return typeof tenantId === "string" ? tenantId.trim() : null;
}

export const TenantApiKeysPage: React.FC = () => {
    const { getAccessToken, account, isAuthenticated } = useAuth();
    const tenantId = useMemo(() => resolveTenantId(account?.idTokenClaims), [account?.idTokenClaims]);

    const [tenant, setTenant] = useState<TenantRecord | null>(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [rotating, setRotating] = useState(false);
    const [rotateMessage, setRotateMessage] = useState<string | null>(null);

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
                setError(err instanceof Error ? err.message : "Failed to load API key data.");
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
            setRotateMessage(`API key rotated successfully at ${new Date(result.rotatedAt).toLocaleString()}.`);
            setTenant((prev) => prev ? { ...prev, apiKeySecretArn: result.apiKeySecretArn, updatedAt: result.rotatedAt } : prev);
        } catch (err) {
            setRotateMessage(err instanceof Error ? err.message : "API key rotation failed.");
        } finally {
            setRotating(false);
        }
    };

    if (loading) return <div className="p-8">Loading API keys...</div>;
    if (error) return <div className="p-8 text-red-600">Error: {error}</div>;
    if (!tenant) return <div className="p-8">No tenant data.</div>;

    return (
        <div className="space-y-6">
            <header>
                <h1 className="text-2xl font-bold text-gray-900">API Keys</h1>
                <p className="text-gray-600">Manage integration credentials for {tenant.tenantId}</p>
            </header>

            <section className="bg-white shadow sm:rounded-lg border border-gray-200 overflow-hidden">
                <div className="px-6 py-4 border-b border-gray-200 bg-gray-50">
                    <h2 className="font-semibold text-gray-900">Current API Key</h2>
                </div>
                <div className="p-6 space-y-4">
                    <div>
                        <label className="block text-sm font-medium text-gray-500 uppercase">Secret ARN</label>
                        <p className="mt-1 text-sm font-mono bg-gray-50 p-2 rounded border border-gray-200 break-all">
                            {tenant.apiKeySecretArn ?? "Not configured"}
                        </p>
                    </div>
                    <div>
                        <label className="block text-sm font-medium text-gray-500 uppercase">Last Rotated</label>
                        <p className="mt-1 text-sm text-gray-900">
                            {tenant.updatedAt ? new Date(tenant.updatedAt).toLocaleString() : "Never"}
                        </p>
                    </div>

                    <div className="pt-4">
                        <button
                            onClick={onRotateApiKey}
                            disabled={rotating}
                            className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:bg-gray-400"
                        >
                            {rotating ? "Rotating..." : "Rotate API Key"}
                        </button>
                    </div>
                    {rotateMessage && (
                        <p className={`text-sm ${rotateMessage.includes('failed') ? 'text-red-600' : 'text-green-600'}`}>
                            {rotateMessage}
                        </p>
                    )}
                </div>
            </section>

            <section className="bg-blue-50 p-6 rounded-lg border border-blue-200">
                <h3 className="text-blue-900 font-semibold mb-2">Integration Guide</h3>
                <p className="text-blue-800 text-sm mb-4">
                    Use this API key to authenticate machine-to-machine requests. Include it in the `X-API-Key` header (or similar depending on your integration).
                </p>
                <div className="bg-gray-900 rounded p-4 text-white font-mono text-xs">
                    <pre>
                        curl -X POST https://api.example.com/v1/agents/echo-agent/invoke \<br/>
                        &nbsp;&nbsp;-H "X-API-Key: YOUR_API_KEY" \<br/>
                        &nbsp;&nbsp;-H "Content-Type: application/json" \<br/>
                        &nbsp;&nbsp;-d '{"{"}"input": "Hello world"{"}"}'
                    </pre>
                </div>
            </section>
        </div>
    );
};
