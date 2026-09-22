import { useState, useCallback, useRef } from "react";

interface UseVoiceReturn {
  listening: boolean;
  supported: boolean;
  transcript: string;
  startListening: () => void;
  stopListening: () => void;
  playAudio: (url: string) => Promise<void>;
  speaking: boolean;
  stopSpeaking: () => void;
}

export function useVoice(): UseVoiceReturn {
  const [listening, setListening] = useState(false);
  const [transcript, setTranscript] = useState("");
  const [speaking, setSpeaking] = useState(false);
  const recognitionRef = useRef<SpeechRecognition | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  const SpeechRecognition =
    typeof window !== "undefined"
      ? window.SpeechRecognition || window.webkitSpeechRecognition
      : null;

  const supported = SpeechRecognition !== null;

  const startListening = useCallback(() => {
    if (!SpeechRecognition) return;

    const recognition = new SpeechRecognition();
    recognition.lang = "en-US";
    recognition.interimResults = false;
    recognition.continuous = false;
    recognition.maxAlternatives = 1;

    recognition.onstart = () => {
      setListening(true);
      setTranscript("");
    };

    recognition.onresult = (event: SpeechRecognitionEvent) => {
      const result = event.results[0][0].transcript;
      setTranscript(result);
    };

    recognition.onerror = (event) => {
      console.error("[ECHO] Speech recognition error:", event.error);
      setListening(false);
    };

    recognition.onend = () => {
      setListening(false);
    };

    recognitionRef.current = recognition;
    recognition.start();
  }, [SpeechRecognition]);

  const stopListening = useCallback(() => {
    recognitionRef.current?.stop();
    setListening(false);
  }, []);

  const playAudio = useCallback(async (url: string) => {
    try {
      // Stop any currently playing audio
      if (audioRef.current) {
        audioRef.current.pause();
        audioRef.current = null;
      }

      const audio = new Audio(url);
      audioRef.current = audio;

      setSpeaking(true);

      await new Promise<void>((resolve, reject) => {
        audio.onended = () => {
          setSpeaking(false);
          resolve();
        };
        audio.onerror = (e) => {
          setSpeaking(false);
          reject(e);
        };
        audio.play();
      });
    } catch (e) {
      console.error("[ECHO] Audio playback error:", e);
      setSpeaking(false);
    }
  }, []);

  const stopSpeaking = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    setSpeaking(false);
  }, []);

  return {
    listening,
    supported,
    transcript,
    startListening,
    stopListening,
    playAudio,
    speaking,
    stopSpeaking,
  };
}