import { useEva } from "./hooks/useEva";
import { ChatWidget } from "./components/widgets/ChatWidget";
import { StatusWidget } from "./components/widgets/StatusWidget";
import { ClockWidget } from "./components/widgets/ClockWidget";
import { ActivityWidget } from "./components/widgets/ActivityWidget";

export default function App() {
  const { messages, typing, system, activityLog, sendMessage, voice, autoSpeak, setAutoSpeak } =
    useEva();

  return (
    <div className="h-screen w-screen bg-eva-bg p-4 flex flex-col gap-4 overflow-hidden">
      {/* Top bar */}
      <header className="flex items-center justify-between px-2">
        <div className="flex items-center gap-3">
          <div className="w-2 h-2 rounded-full bg-eva-cyan animate-pulse" />
          <h1 className="text-sm font-mono uppercase tracking-[0.3em] text-eva-cyan">
            E.V.A. <span className="text-eva-text-dim">// Extensible Virtual Agent</span>
          </h1>
        </div>
        <div className="flex items-center gap-4">
          {voice.listening && (
            <span className="text-xs font-mono text-eva-danger animate-pulse">
              LISTENING...
            </span>
          )}
          {voice.speaking && (
            <span className="text-xs font-mono text-eva-accent animate-pulse">
              SPEAKING...
            </span>
          )}
          <span className="text-xs font-mono text-eva-text-dim">
            v{system.version} :: {system.connected ? "CONNECTED" : "RECONNECTING..."}
          </span>
        </div>
      </header>

      {/* Main grid */}
      <div className="flex-1 grid grid-cols-4 grid-rows-3 gap-4 min-h-0">
        <div className="col-span-3 row-span-3 flex flex-col min-h-0">
          <ChatWidget
            messages={messages}
            typing={typing}
            persona={system.persona}
            onSend={sendMessage}
            voice={voice}
            autoSpeak={autoSpeak}
            onAutoSpeakToggle={setAutoSpeak}
          />
        </div>

        <div className="col-span-1 row-span-3 flex flex-col gap-4 min-h-0">
          <ClockWidget />
          <StatusWidget system={system} />
          <div className="flex-1 min-h-0">
            <ActivityWidget log={activityLog} />
          </div>
        </div>
      </div>
    </div>
  );
}