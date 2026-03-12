import React, { useEffect, useMemo, useState } from "react";
import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { resolveTenantId } from "../auth/identity";
import { PageBanner } from "../components/PageBanner";

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

type TenantPortalPageProps = {
    initialSection?: "overview" | "access" | "api-keys";
};

const sectionMessages: Record<NonNullable<TenantPortalPageProps["initialSection"]>, string> = {
    overview: "Usage, key posture, and invitation controls are grouped here for the current tenant.",
    access: "This route focuses the tenant access workflow, including invitation issuance.",
    "api-keys": "This route focuses machine identity hygiene and rotation cadence.",
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
            setError("Token is missing tenantid claim.");
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
        if (!tenantId) {
            return;
        }
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
        if (!tenantId || !inviteEmail.trim()) {
            return;
        }
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
                `Invite ${response.invite.inviteId} accepted for ${response.invite.email}; expires ${new Date(
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

    if (loading) {
        return <div>Loading tenant portal...</div>;
    }

    if (error) {
        return (
            <div className="bg-red-50 border-l-4 border-red-400 p-4">
                <p className="text-sm text-red-700">{error}</p>
            </div>
        );
    }

    if (!tenant) {
        return <div>No tenant data available.</div>;
    }

    return (
        <div className="space-y-8">
            <div>
                <h1 className="text-3xl font-bold text-gray-900">Tenant Portal</h1>
                <p className="mt-2 text-gray-600">
                    Self-service controls for tenant <span className="font-medium">{tenant.tenantId}</span>.
                </p>
            </div>

            <PageBanner title={`Tenant / ${initialSection}`} severity="info">
                {sectionMessages[initialSection]}
            </PageBanner>

            <section className="bg-white shadow sm:rounded-lg border border-gray-200 overflow-hidden">
                <div className="px-4 py-5 sm:px-6 bg-gray-50 border-b border-gray-200">
                    <h2 className="text-lg font-medium text-gray-900">Usage Snapshot</h2>
                </div>
                <div className="px-4 py-5 sm:p-6 grid grid-cols-1 sm:grid-cols-3 gap-4">
                    <div className="rounded border border-gray-200 p-4">
                        <p className="text-sm text-gray-500">Requests Today</p>
                        <p className="text-2xl font-semibold text-gray-900">{tenant.usage?.requestsToday ?? 0}</p>
                    </div>
                    <div className="rounded border border-gray-200 p-4">
                        <p className="text-sm text-gray-500">Budget Remaining (USD)</p>
                        <p className="text-2xl font-semibold text-gray-900">
                            {tenant.usage?.budgetRemainingUsd ?? "n/a"}
                        </p>
                    </div>
                    <div className="rounded border border-gray-200 p-4">
                        <p className="text-sm text-gray-500">Tier / Status</p>
                        <p className="text-2xl font-semibold text-gray-900">
                            {tenant.tier} / {tenant.status}
                        </p>
                    </div>
                </div>
            </section>

            <section className="bg-white shadow sm:rounded-lg border border-gray-200 overflow-hidden">
                <div className="px-4 py-5 sm:px-6 bg-gray-50 border-b border-gray-200">
                    <h2 className="text-lg font-medium text-gray-900">API Key Rotation</h2>
                </div>
                <div className="px-4 py-5 sm:p-6 space-y-4">
                    <p className="text-sm text-gray-600">Secret ARN: {tenant.apiKeySecretArn ?? "Not configured"}</p>
                    <button
                        onClick={onRotateApiKey}
                        disabled={rotating}
                        className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-blue-600 hover:bg-blue-700 disabled:bg-gray-400"
                    >
                        {rotating ? "Rotating..." : "Rotate API Key"}
                    </button>
                    {rotateMessage && <p className="text-sm text-gray-700">{rotateMessage}</p>}
                </div>
            </section>

            <section className="bg-white shadow sm:rounded-lg border border-gray-200 overflow-hidden">
                <div className="px-4 py-5 sm:px-6 bg-gray-50 border-b border-gray-200">
                    <h2 className="text-lg font-medium text-gray-900">Invite User</h2>
                </div>
                <form className="px-4 py-5 sm:p-6 space-y-4" onSubmit={onInviteSubmit}>
                    <div>
                        <label htmlFor="invite-email" className="block text-sm font-medium text-gray-700">
                            Email
                        </label>
                        <input
                            id="invite-email"
                            type="email"
                            required
                            value={inviteEmail}
                            onChange={(e) => setInviteEmail(e.target.value)}
                            className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
                            placeholder="new.user@example.com"
                        />
                    </div>
                    <div>
                        <label htmlFor="invite-role" className="block text-sm font-medium text-gray-700">
                            Role
                        </label>
                        <input
                            id="invite-role"
                            type="text"
                            value={inviteRole}
                            onChange={(e) => setInviteRole(e.target.value)}
                            className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:border-blue-500 focus:ring-blue-500 sm:text-sm"
                        />
                    </div>
                    <button
                        type="submit"
                        disabled={invitePending}
                        className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-blue-600 hover:bg-blue-700 disabled:bg-gray-400"
                    >
                        {invitePending ? "Sending..." : "Send Invite"}
                    </button>
                    {inviteMessage && <p className="text-sm text-gray-700">{inviteMessage}</p>}
                </form>
            </section>
        </div>
    );
};
