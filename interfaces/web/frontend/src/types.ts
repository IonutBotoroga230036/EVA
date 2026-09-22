export interface ChatMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  persona?: string;
  timestamp: string;
}

export interface BudgetInfo {
  spent: number;
  limit: number;
  remaining: number;
  num_calls: number;
  exhausted: boolean;
}

export interface SystemState {
  connected: boolean;
  persona: string;
  version: string;
  budget: BudgetInfo;
  sessionMessages: number;
}

export interface WSMessage {
  type: string;
  data: Record<string, unknown>;
  timestamp: string;
}

export interface Widget {
  id: string;
  type: "chat" | "status" | "clock" | "activity" | "calendar" | "news";
  title: string;
  gridArea: string;
  visible: boolean;
}