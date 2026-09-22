import { useEffect, useRef, useState, useCallback } from "react";
import type { WSMessage } from "../types";

export function useWebSocket(url: string) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [lastMessage, setLastMessage] = useState<WSMessage | null>(null);
  const reconnectTimer = useRef<ReturnType<typeof setTimeout>>();

  const connect = useCallback(() => {
    try {
      const ws = new WebSocket(url);

      ws.onopen = () => {
        setConnected(true);
        console.log("[PULSE] WebSocket connected");
      };

      ws.onmessage = (event) => {
        try {
          const msg: WSMessage = JSON.parse(event.data);
          setLastMessage(msg);
        } catch (e) {
          console.error("[PULSE] Parse error:", e);
        }
      };

      ws.onclose = () => {
        setConnected(false);
        console.log("[PULSE] WebSocket disconnected. Reconnecting...");
        reconnectTimer.current = setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        ws.close();
      };

      wsRef.current = ws;
    } catch (e) {
      console.error("[PULSE] Connection error:", e);
      reconnectTimer.current = setTimeout(connect, 3000);
    }
  }, [url]);

  useEffect(() => {
    connect();
    return () => {
      clearTimeout(reconnectTimer.current);
      wsRef.current?.close();
    };
  }, [connect]);

  const send = useCallback((type: string, content?: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type, content }));
    }
  }, []);

  return { connected, lastMessage, send };
}