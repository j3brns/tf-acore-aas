import React, { useState, useEffect } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";
import { Agent, AgentInvokeResponse } from "../types";
import { useJobPolling } from "../hooks/useJobPolling";
import {
    createInvokePayload,
    extractJobIdFromPollUrl,
    formatApiErrorMessage,
    isAsyncInvokeAccepted,
} from "./invokeContract";

export const InvokePage: React.FC = () => {
    const { agentName } = useParams<{ agentName: string }>();
    const navigate = useNavigate();
    const { getAccessToken, isAuthenticated } = useAuth();
    
    const [agent, setAgent] = useState<Agent | null>(null);
    const [prompt, setPrompt] = useState("");
    const [loading, setLoading] = useState(false);
    const [result, setResult] = useState<string | null>(null);
    const [jobId, setJobId] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const { status: jobStatus, error: pollingError } = useJobPolling(jobId, getAccessToken);
    const invocationMode = agent?.invocation_mode ?? "sync";

    useEffect(() => {
        const fetchAgent = async () => {
            if (!isAuthenticated) return;
            try {
                const client = getApiClient(getAccessToken);
                const data = await client.request<Agent>(`/v1/agents/${agentName}`);
                setAgent(data);
            } catch (err: unknown) {
                setError(formatApiErrorMessage(err));
            }
        };
        void fetchAgent();
    }, [agentName, getAccessToken, isAuthenticated]);

    const handleInvoke = async (e: React.FormEvent) => {
        e.preventDefault();
        setLoading(true);
        setResult(null);
        setJobId(null);
        setError(null);

        try {
            const client = getApiClient(getAccessToken);
            const body = JSON.stringify(createInvokePayload(prompt));

            if (invocationMode === "streaming") {
                setResult("");
                const stream = client.stream(`/v1/agents/${agentName}/invoke`, {
                    method: "POST",
                    body,
                    headers: { "Content-Type": "application/json" }
                });

                for await (const chunk of stream) {
                    setResult((prev) => (prev || "") + chunk.data);
                }
            } else {
                const data = await client.request<AgentInvokeResponse>(`/v1/agents/${agentName}/invoke`, {
                    method: "POST",
                    body,
                    headers: { "Content-Type": "application/json" }
                });

                if (isAsyncInvokeAccepted(data)) {
                    const acceptedJobId = data.jobId || extractJobIdFromPollUrl(data.pollUrl);
                    if (!acceptedJobId) {
                        throw new Error("Async invoke response missing jobId");
                    }
                    setJobId(acceptedJobId);
                } else {
                    setResult(data.output);
                }
            }
        } catch (err: unknown) {
            setError(formatApiErrorMessage(err));
        } finally {
            setLoading(false);
        }
    };

    if (!agent && !error) return <div>Loading...</div>;

    return (
        <div className="max-w-4xl mx-auto">
            <button 
                onClick={() => navigate("/")}
                className="mb-4 text-sm text-blue-600 hover:underline flex items-center"
            >
                ← Back to Catalogue
            </button>
            
            <div className="bg-white shadow sm:rounded-lg overflow-hidden border border-gray-200">
                <div className="px-4 py-5 sm:p-6">
                    <h1 className="text-2xl font-bold text-gray-900 mb-2">Invoke: {agent?.agent_name}</h1>
                    <p className="text-sm text-gray-500 mb-6">
                        Version {agent?.version} • {agent?.invocation_mode} mode
                    </p>

                    <form onSubmit={handleInvoke} className="space-y-4">
                        <div>
                            <label htmlFor="prompt" className="block text-sm font-medium text-gray-700">
                                Prompt
                            </label>
                            <textarea
                                id="prompt"
                                rows={4}
                                className="mt-1 block w-full border border-gray-300 rounded-md shadow-sm p-2 focus:ring-blue-500 focus:border-blue-500"
                                placeholder="Enter your instructions for the agent..."
                                value={prompt}
                                onChange={(e) => setPrompt(e.target.value)}
                                required
                            />
                        </div>

                        <p className="text-sm text-gray-600">
                            Invocation mode is configured by the agent:{" "}
                            <span className="font-semibold capitalize">{invocationMode}</span>.
                        </p>

                        <button
                            type="submit"
                            disabled={loading}
                            className={`w-full inline-flex justify-center py-2 px-4 border border-transparent shadow-sm text-sm font-medium rounded-md text-white bg-blue-600 hover:bg-blue-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-blue-500 ${
                                loading ? "opacity-50 cursor-not-allowed" : ""
                            }`}
                        >
                            {loading ? "Invoking..." : "Submit"}
                        </button>
                    </form>
                </div>
            </div>

            {error && (
                <div className="mt-6 bg-red-50 border-l-4 border-red-400 p-4">
                    <p className="text-sm text-red-700">{error}</p>
                </div>
            )}

            {jobId && (
                <div className="mt-6 bg-blue-50 border-l-4 border-blue-400 p-4">
                    <h3 className="text-sm font-medium text-blue-800">Async Job Started</h3>
                    <p className="text-sm text-blue-700 mt-1">Job ID: {jobId}</p>
                    <p className="text-sm text-blue-700 mt-1 uppercase font-bold">Status: {jobStatus?.status || "pending"}</p>
                    {jobStatus?.status === "completed" && (
                        <div className="mt-2">
                            <a 
                                href={jobStatus.resultUrl || undefined} 
                                target="_blank" 
                                rel="noreferrer"
                                className="text-sm font-medium text-blue-600 hover:underline"
                            >
                                View Results
                            </a>
                        </div>
                    )}
                    {pollingError && <p className="text-sm text-red-700 mt-2">{pollingError}</p>}
                </div>
            )}

            {result && (
                <div className="mt-6">
                    <h3 className="text-lg font-medium text-gray-900 mb-2">Response</h3>
                    <div className="bg-gray-50 rounded-lg p-4 border border-gray-200 whitespace-pre-wrap font-mono text-sm">
                        {result}
                    </div>
                </div>
            )}
        </div>
    );
};
