import React, { useEffect, useMemo, useState } from "react";
import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";

type Invite = {
    inviteId: string;
    email: string;
    role: string;
    status: string;
    expiresAt: string;
    createdAt?: string;
};

function resolveTenantId(claims: unknown): string | null {
    if (!claims || typeof claims !== "object") {
        return null;
    }
    const map = claims as Record<string, unknown>;
    const tenantId = map.tenantid ?? map.tenantId;
    return typeof tenantId === "string" ? tenantId.trim() : null;
}

export const TenantMembersPage: React.FC = () => {
    const { getAccessToken, account, isAuthenticated } = useAuth();
    const tenantId = useMemo(() => resolveTenantId(account?.idTokenClaims), [account?.idTokenClaims]);

    const [invites, setInvites] = useState<Invite[]>([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);
    const [inviteEmail, setInviteEmail] = useState("");
    const [inviteRole, setInviteRole] = useState("Agent.Invoke");
    const [submitting, setSubmitting] = useState(false);
    const [inviteMessage, setInviteMessage] = useState<{ type: 'success' | 'error', text: string } | null>(null);

    useEffect(() => {
        if (!isAuthenticated || !tenantId) {
            setLoading(false);
            return;
        }

        const run = async () => {
            try {
                const client = getApiClient(getAccessToken);
                const data = await client.request<{ items: Invite[] }>(`/v1/tenants/${tenantId}/users/invites`);
                setInvites(data.items);
            } catch (err) {
                setError(err instanceof Error ? err.message : "Failed to load invites.");
            } finally {
                setLoading(false);
            }
        };
        void run();
    }, [getAccessToken, isAuthenticated, tenantId]);

    const onInviteSubmit = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!tenantId) return;
        setSubmitting(true);
        setInviteMessage(null);
        try {
            const client = getApiClient(getAccessToken);
            const response = await client.request<{ invite: Invite }>(`/v1/tenants/${tenantId}/users/invite`, {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ email: inviteEmail.trim(), role: inviteRole }),
            });
            setInviteMessage({ type: 'success', text: `Invite sent to ${response.invite.email}.` });
            setInviteEmail("");
            setInvites(prev => [response.invite, ...prev]);
        } catch (err) {
            setInviteMessage({ type: 'error', text: err instanceof Error ? err.message : "Failed to send invite." });
        } finally {
            setSubmitting(false);
        }
    };

    if (loading) return <div className="p-8">Loading members...</div>;
    if (error) return <div className="p-8 text-red-600">Error: {error}</div>;

    return (
        <div className="space-y-6">
            <header>
                <h1 className="text-2xl font-bold text-gray-900">Members & Invites</h1>
                <p className="text-gray-600">Manage user access for {tenantId}</p>
            </header>

            <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                <section className="lg:col-span-2 bg-white shadow sm:rounded-lg border border-gray-200 overflow-hidden">
                    <div className="px-6 py-4 border-b border-gray-200 bg-gray-50 flex justify-between items-center">
                        <h2 className="font-semibold text-gray-900">Pending Invitations</h2>
                    </div>
                    <div className="overflow-x-auto">
                        {invites.length === 0 ? (
                            <div className="p-8 text-center text-gray-500 text-sm">No pending invites.</div>
                        ) : (
                            <table className="min-w-full divide-y divide-gray-200">
                                <thead className="bg-gray-50 text-xs font-medium text-gray-500 uppercase tracking-wider">
                                    <tr>
                                        <th className="px-6 py-3 text-left">Email</th>
                                        <th className="px-6 py-3 text-left">Role</th>
                                        <th className="px-6 py-3 text-left">Status</th>
                                        <th className="px-6 py-3 text-left">Expires</th>
                                    </tr>
                                </thead>
                                <tbody className="bg-white divide-y divide-gray-200 text-sm">
                                    {invites.map((invite) => (
                                        <tr key={invite.inviteId}>
                                            <td className="px-6 py-4 whitespace-nowrap text-gray-900">{invite.email}</td>
                                            <td className="px-6 py-4 whitespace-nowrap text-gray-500">{invite.role}</td>
                                            <td className="px-6 py-4 whitespace-nowrap">
                                                <span className="px-2 py-0.5 rounded-full text-xs bg-yellow-100 text-yellow-800 capitalize">
                                                    {invite.status}
                                                </span>
                                            </td>
                                            <td className="px-6 py-4 whitespace-nowrap text-gray-500">
                                                {new Date(invite.expiresAt).toLocaleDateString()}
                                            </td>
                                        </tr>
                                    ))}
                                </tbody>
                            </table>
                        )}
                    </div>
                </section>

                <section className="bg-white shadow sm:rounded-lg border border-gray-200 overflow-hidden h-fit">
                    <div className="px-6 py-4 border-b border-gray-200 bg-gray-50">
                        <h2 className="font-semibold text-gray-900">Invite New User</h2>
                    </div>
                    <form onSubmit={onInviteSubmit} className="p-6 space-y-4">
                        <div>
                            <label className="block text-sm font-medium text-gray-700">Email Address</label>
                            <input
                                type="email"
                                required
                                value={inviteEmail}
                                onChange={e => setInviteEmail(e.target.value)}
                                className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                                placeholder="name@example.com"
                            />
                        </div>
                        <div>
                            <label className="block text-sm font-medium text-gray-700">Role</label>
                            <select
                                value={inviteRole}
                                onChange={e => setInviteRole(e.target.value)}
                                className="mt-1 block w-full rounded-md border-gray-300 shadow-sm focus:ring-blue-500 focus:border-blue-500 sm:text-sm"
                            >
                                <option value="Agent.Invoke">Agent.Invoke (Basic Access)</option>
                                <option value="Platform.Operator">Platform.Operator (Admin Access)</option>
                            </select>
                        </div>
                        <div className="pt-2">
                            <button
                                type="submit"
                                disabled={submitting}
                                className="w-full inline-flex justify-center items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md shadow-sm text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 disabled:bg-gray-400"
                            >
                                {submitting ? "Sending..." : "Send Invite"}
                            </button>
                        </div>
                        {inviteMessage && (
                            <p className={`text-sm ${inviteMessage.type === 'error' ? 'text-red-600' : 'text-green-600'}`}>
                                {inviteMessage.text}
                            </p>
                        )}
                    </form>
                </section>
            </div>
        </div>
    );
};
