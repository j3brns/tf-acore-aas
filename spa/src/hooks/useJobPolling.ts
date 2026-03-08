import { useEffect, useState, useRef } from "react";
import { getApiClient, AccessTokenProvider } from "../api/client";
import { Job } from "../types";
import { formatApiErrorMessage, isTerminalJob } from "../pages/invokeContract";

export function useJobPolling(jobId: string | null, getAccessToken: AccessTokenProvider, interval = 2000) {
    const [status, setStatus] = useState<Job | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const inFlightRef = useRef(false);

    useEffect(() => {
        if (timerRef.current !== null) {
            globalThis.clearInterval(timerRef.current);
            timerRef.current = null;
        }

        setStatus(null);
        setError(null);

        if (!jobId) {
            setLoading(false);
            inFlightRef.current = false;
            return;
        }

        let active = true;

        const poll = async () => {
            if (!active || inFlightRef.current) {
                return;
            }
            inFlightRef.current = true;
            try {
                const client = getApiClient(getAccessToken);
                const data = await client.request<Job>(`/v1/jobs/${jobId}`);
                if (!active) {
                    return;
                }
                setStatus(data);

                if (isTerminalJob(data)) {
                    if (timerRef.current !== null) {
                        globalThis.clearInterval(timerRef.current);
                        timerRef.current = null;
                    }
                    setLoading(false);
                }
            } catch (err: unknown) {
                if (!active) {
                    return;
                }
                setError(formatApiErrorMessage(err));
                setLoading(false);
                if (timerRef.current !== null) {
                    globalThis.clearInterval(timerRef.current);
                    timerRef.current = null;
                }
            } finally {
                inFlightRef.current = false;
            }
        };

        setLoading(true);
        void poll();
        timerRef.current = globalThis.setInterval(() => {
            void poll();
        }, interval);

        return () => {
            active = false;
            if (timerRef.current !== null) {
                globalThis.clearInterval(timerRef.current);
                timerRef.current = null;
            }
            inFlightRef.current = false;
        };
    }, [jobId, getAccessToken, interval]);

    return { status, loading, error };
}
