import { useState, useRef, useEffect } from "react";
import { Panel } from "../Panel";
import type { ChatMessage } from "../../types";

interface ChatWidgetProps {
  messages: ChatMessage[];
  typing: boolean;
  persona: string;
  onSend: (content: string) => void;
  voice: {
    listening: boolean;
    supported: boolean;
    speaking: boolean;
    startListening: () => void;
    stopListening: () => void;
    stopSpeaking: () => void;
  };
  autoSpeak: boolean;
  onAutoSpeakToggle: (value: boolean) => void;
}

export function ChatWidget({
  messages,
  typing,
  persona,
  onSend,
  voice,
  autoSpeak,
  onAutoSpeakToggle,
}: ChatWidgetProps) {
  const [input, setInput] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, typing]);

  const handleSubmit = () => {
    const trimmed = input.trim();
    if (!trimmed) return;
    onSend(trimmed);
    setInput("");
    inputRef.current?.focus();
  };

  const handleMicClick = () => {
    if (voice.listening) {
      voice.stopListening();
    } else {
      if (voice.speaking) voice.stopSpeaking();
      voice.startListening();
    }
  };

  return (
    <Panel title={`Comms :: ${persona}`} className="flex flex-col h-full">
      {/* Top controls */}
      <div className="flex items-center justify-between mb-3 pb-2 border-b border-eva-border">
        <div className="flex items-center gap-2">
          {voice.speaking && (
            <button
              onClick={voice.stopSpeaking}
              className="text-[10px] font-mono text-eva-warning border border-eva-warning px-2 py-0.5 rounded hover:bg-eva-warning hover:text-eva-bg transition-colors"
            >
              STOP AUDIO
            </button>
          )}
        </div>
        <button
          onClick={() => onAutoSpeakToggle(!autoSpeak)}
          className={`text-[10px] font-mono px-2 py-0.5 rounded border transition-colors ${
            autoSpeak
              ? "text-eva-accent border-eva-accent"
              : "text-eva-text-dim border-eva-border"
          }`}
        >
          VOICE {autoSpeak ? "ON" : "OFF"}
        </button>
      </div>

      {/* Messages */}
      <div className="flex-1 overflow-y-auto space-y-3 mb-4 min-h-0">
        {messages.map((msg) => (
          <div
            key={msg.id}
            className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
          >
            <div
              className={`
                max-w-[80%] px-3 py-2 rounded-lg text-sm leading-relaxed
                ${
                  msg.role === "user"
                    ? "bg-eva-user-bubble text-eva-text-bright border border-eva-border"
                    : "bg-eva-ai-bubble text-eva-text border border-eva-border"
                }
              `}
            >
              {msg.role === "assistant" && (
                <span className="text-[10px] font-mono text-eva-cyan block mb-1">
                  {msg.persona || persona}
                </span>
              )}
              <p className="whitespace-pre-wrap">{msg.content}</p>
              <span className="text-[10px] text-eva-text-dim block mt-1 text-right">
                {new Date(msg.timestamp).toLocaleTimeString("en-GB", {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
            </div>
          </div>
        ))}

        {typing && (
          <div className="flex justify-start">
            <div className="bg-eva-ai-bubble border border-eva-border px-3 py-2 rounded-lg">
              <span className="text-[10px] font-mono text-eva-cyan block mb-1">{persona}</span>
              <div className="flex gap-1">
                <span className="w-1.5 h-1.5 rounded-full bg-eva-cyan animate-bounce" style={{ animationDelay: "0ms" }} />
                <span className="w-1.5 h-1.5 rounded-full bg-eva-cyan animate-bounce" style={{ animationDelay: "150ms" }} />
                <span className="w-1.5 h-1.5 rounded-full bg-eva-cyan animate-bounce" style={{ animationDelay: "300ms" }} />
              </div>
            </div>
          </div>
        )}

        <div ref={bottomRef} />
      </div>

      {/* Input */}
      <div className="flex gap-2 border-t border-eva-border pt-3">
        {voice.supported && (
          <button
            onClick={handleMicClick}
            className={`
              w-10 h-10 rounded-md border flex items-center justify-center
              transition-all duration-200 flex-shrink-0
              ${
                voice.listening
                  ? "bg-eva-danger border-eva-danger text-eva-bg animate-pulse"
                  : "border-eva-border text-eva-text-dim hover:border-eva-cyan hover:text-eva-cyan"
              }
            `}
            title={voice.listening ? "Stop listening" : "Start voice input"}
          >
            <svg
              xmlns="http://www.w3.org/2000/svg"
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
              <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
              <line x1="12" x2="12" y1="19" y2="22" />
            </svg>
          </button>
        )}
        <input
          ref={inputRef}
          type="text"
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleSubmit()}
          placeholder={voice.listening ? "Listening..." : "Speak, sir..."}
          className="
            flex-1 bg-transparent border border-eva-border rounded-md px-3 py-2
            text-sm text-eva-text-bright placeholder-eva-text-dim
            focus:outline-none focus:border-eva-cyan
            font-mono
          "
          autoFocus
          disabled={voice.listening}
        />
        <button
          onClick={handleSubmit}
          disabled={voice.listening}
          className="
            px-4 py-2 rounded-md border border-eva-cyan
            text-eva-cyan text-sm font-mono uppercase tracking-wider
            hover:bg-eva-cyan hover:text-eva-bg
            transition-colors duration-200
            disabled:opacity-30
          "
        >
          Send
        </button>
      </div>
    </Panel>
  );
}