export type StageName =
  | "ingest"
  | "preprocess"
  | "detect"
  | "deblend"
  | "measure"
  | "characterize"
  | "crossmatch"
  | "anomaly"
  | "report";

export type StageStatus = "idle" | "running" | "completed" | "failed";

export interface StageState {
  name: StageName;
  status: StageStatus;
  progress: number;
  message: string;
  duration?: number;
  result?: Record<string, unknown>;
}

export type LogLevel = "debug" | "info" | "warn" | "error";

export interface LogEntry {
  stageName?: string;
  level: LogLevel;
  message: string;
  timestamp: string;
}

export interface PipelineStatus {
  running: boolean;
  current_stage: string | null;
  progress: number;
  workspace: string | null;
}

