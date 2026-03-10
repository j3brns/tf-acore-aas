import { useEffect, useRef } from "react";
import { getApiClient } from "../api/client";
import { useAuth } from "../auth/useAuth";

const KEEPALIVE_INTERVAL_MS = 10 * 60 * 1000; // 10 minutes (AgentCore limit is 15)

export function useSessionKeepalive(sessionId: string | null, agentName: string | null) {
  const { getAccessToken, isAuthenticated } = useAuth();
  const intervalRef = useRef<NodeJS.Timeout | null>(null);

  useEffect(() => {
    if (!isAuthenticated || !sessionId || !agentName) {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
      return;
    }

    const ping = async () => {
      try {
        const client = getApiClient(getAccessToken);
        await client.bffSessionKeepalive({
          sessionId,
          agentName,
        });
        console.debug(`[Keepalive] Pinged session ${sessionId}`);
      } catch (err) {
        console.error(`[Keepalive] Failed to ping session ${sessionId}:`, err);
      }
    };

    // Initial ping
    void ping();

    // Periodic ping
    intervalRef.current = setInterval(() => {
      void ping();
    }, KEEPALIVE_INTERVAL_MS);

    return () => {
      if (intervalRef.current) {
        clearInterval(intervalRef.current);
        intervalRef.current = null;
      }
    };
  }, [sessionId, agentName, getAccessToken, isAuthenticated]);
}
