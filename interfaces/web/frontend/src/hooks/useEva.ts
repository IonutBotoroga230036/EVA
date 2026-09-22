import { useState, useEffect, useCallback } from "react";
import { useWebSocket } from "./useWebSocket";
import { useVoice } from "./useVoice";
import type { ChatMessage, SystemState, BudgetInfo } from "../types";

const WS_URL = `ws://${window.location.hostname}:8000/ws`;

export function useEva() {
  const { connected, lastMessage, send } = useWebSocket(WS_URL);
  const voice = useVoice();
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [typing, setTyping] = useState(false);
  const [autoSpeak, setAutoSpeak] = useState(true);
  const [activityLog, setActivityLog] = useState<string[]>([]);
  const [system, setSystem] = useState<SystemState>({
    connected: false,
    persona: "E.V.A.",
    version: "0.1.0",
    budget: { spent: 0, limit: 1.5, remaining: 1.5, num_calls: 0, exhausted: false },
    sessionMessages: 0,
  });

  useEffect(() => {
    setSystem((prev) => ({ ...prev, connected }));
  }, [connected]);

  // When voice transcript is ready, send it as a message
  useEffect(() => {
    if (voice.transcript && !voice.listening) {
      sendMessage(voice.transcript);
    }
  }, [voice.transcript, voice.listening]);

  useEffect(() => {
    if (!lastMessage) return;
    const { type, data, timestamp } = lastMessage;

    switch (type) {
      case "system_init":
        setSystem((prev) => ({
          ...prev,
          persona: data.persona as string,
          version: data.version as string,
          budget: data.budget as BudgetInfo,
        }));
        addActivity(`System online. Persona: ${data.persona}`);
        setMessages([
          {
            id: crypto.randomUUID(),
            role: "assistant",
            content: data.greeting as string,
            persona: data.persona as string,
            timestamp,
          },
        ]);
        break;

      case "typing":
        setTyping((data.active as boolean) ?? false);
        break;

      case "chat_response":
        setTyping(false);
        setMessages((prev) => [
          ...prev,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            content: data.content as string,
            persona: data.persona as string,
            timestamp,
          },
        ]);
        if (data.budget) {
          setSystem((prev) => ({
            ...prev,
            budget: data.budget as BudgetInfo,
          }));
        }
        // Auto-play audio response
        if (autoSpeak && data.audio_url) {
          voice.playAudio(`http://${window.location.hostname}:8000${data.audio_url}`);
        }
        addActivity(`Response delivered via ${data.persona}`);
        break;

      case "status_update":
        setSystem((prev) => ({
          ...prev,
          budget: data.budget as BudgetInfo,
          sessionMessages: data.session_messages as number,
        }));
        break;
    }
  }, [lastMessage, autoSpeak]);

  const addActivity = useCallback((text: string) => {
    const time = new Date().toLocaleTimeString("en-GB", {
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    setActivityLog((prev) => [`[${time}] ${text}`, ...prev].slice(0, 50));
  }, []);

  const sendMessage = useCallback(
    (content: string) => {
      const msg: ChatMessage = {
        id: crypto.randomUUID(),
        role: "user",
        content,
        timestamp: new Date().toISOString(),
      };
      setMessages((prev) => [...prev, msg]);
      send("chat", content);
      addActivity(`User: ${content.slice(0, 60)}${content.length > 60 ? "..." : ""}`);
    },
    [send, addActivity]
  );

  return {
    messages,
    typing,
    system,
    activityLog,
    sendMessage,
    voice,
    autoSpeak,
    setAutoSpeak,
  };
}