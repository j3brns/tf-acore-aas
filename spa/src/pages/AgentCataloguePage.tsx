import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { Agent } from "../types";

export const AgentCataloguePage: React.FC = () => {
    const [agents, setAgents] = useState<Agent[]>([]);
    const [loading, setLoading] = useState(true);
    const [_error, setError] = useState<string | null>(null);
    const { getAccessToken, isAuthenticated } = useAuth();

    useEffect(() => {
        const fetchAgents = async () => {
            if (!isAuthenticated) return;
            try {
                const client = getApiClient(getAccessToken);
                const data = await client.request<{ agents: Agent[] }>("/v1/agents");
                setAgents(data.agents || []);
            } catch (err: any) {
                console.error("Failed to fetch agents", err);
                setError(err.message || "Failed to load agents");
            } finally {
                setLoading(false);
            }
        };

        fetchAgents();
    }, [getAccessToken, isAuthenticated]);

    if (loading) {
        return (
            <div className="flex justify-center items-center h-64">
                <div className="animate-spin rounded-full h-12 w-12 border-b-2 border-blue-600"></div>
            </div>
        );
    }

    if (_error) {
        return (
            <div className="bg-red-50 border-l-4 border-red-400 p-4">
                <div className="flex">
                    <div className="ml-3">
                        <p className="text-sm text-red-700">{_error}</p>
                    </div>
                </div>
            </div>
        );
    }

    return (
        <div>
            <div className="mb-8">
                <h1 className="text-3xl font-bold text-gray-900">Agent Catalogue</h1>
                <p className="mt-2 text-gray-600">Explore and invoke available AI agents.</p>
            </div>

            <div className="grid grid-cols-1 gap-6 sm:grid-cols-2 lg:grid-cols-3">
                {agents.map((agent) => (
                    <div key={`${agent.agent_name}-${agent.version}`} className="bg-white overflow-hidden shadow rounded-lg border border-gray-200 flex flex-col">
                        <div className="px-4 py-5 sm:p-6 flex-1">
                            <div className="flex items-center justify-between mb-2">
                                <h3 className="text-lg font-medium text-gray-900 truncate">
                                    {agent.agent_name}
                                </h3>
                                <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                                    agent.tier_minimum === "premium" ? "bg-purple-100 text-purple-800" :
                                    agent.tier_minimum === "standard" ? "bg-blue-100 text-blue-800" :
                                    "bg-green-100 text-green-800"
                                }`}>
                                    {agent.tier_minimum}
                                </span>
                            </div>
                            <p className="text-sm text-gray-500 mb-4">
                                Version {agent.version} • {agent.invocation_mode}
                            </p>
                            <div className="mt-4 flex flex-wrap gap-2">
                                {agent.streaming_enabled && (
                                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-800">
                                        Streaming
                                    </span>
                                )}
                                <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-gray-100 text-gray-800">
                                    {agent.owner_team}
                                </span>
                            </div>
                        </div>
                        <div className="bg-gray-50 px-4 py-4 sm:px-6">
                            <Link
                                to={`/invoke/${agent.agent_name}`}
                                className="w-full inline-flex justify-center py-2 px-4 border border-transparent shadow-sm text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500"
                            >
                                Invoke
                            </Link>
                        </div>
                    </div>
                ))}
            </div>

            {agents.length === 0 && (
                <div className="text-center py-12">
                    <p className="text-gray-500">No agents found.</p>
                </div>
            )}
        </div>
    );
};
