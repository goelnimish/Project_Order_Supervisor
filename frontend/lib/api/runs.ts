import { apiRequest, type RequestOptions } from "./client";
import type {
  InstructionRequest,
  InterruptRunRequest,
  OrderEventRequest,
  PauseRunRequest,
  Run,
  RunCreate,
  RunDetail,
  SignalAccepted,
  TerminateRunRequest,
  WorkflowStatus,
} from "./types";

export interface ListRunsOptions extends RequestOptions {
  status?: WorkflowStatus;
}

export function listRuns(options: ListRunsOptions = {}): Promise<Run[]> {
  const query = options.status
    ? `?${new URLSearchParams({ status: options.status }).toString()}`
    : "";
  return apiRequest<Run[]>(`/api/runs${query}`, { signal: options.signal });
}

export function createRun(payload: RunCreate, options: RequestOptions = {}): Promise<Run> {
  return apiRequest<Run>("/api/runs", {
    method: "POST",
    body: JSON.stringify(payload),
    signal: options.signal,
  });
}

export function getRunDetail(
  runId: string,
  options: RequestOptions = {},
): Promise<RunDetail> {
  return apiRequest<RunDetail>(`/api/runs/${encodeURIComponent(runId)}`, {
    signal: options.signal,
  });
}

export function injectOrderEvent(
  runId: string,
  payload: OrderEventRequest,
  options: RequestOptions = {},
): Promise<SignalAccepted> {
  return postRunSignal(runId, "events", payload, options);
}

export function addRunInstruction(
  runId: string,
  payload: InstructionRequest,
  options: RequestOptions = {},
): Promise<SignalAccepted> {
  return postRunSignal(runId, "instructions", payload, options);
}

export function pauseRun(
  runId: string,
  payload?: PauseRunRequest,
  options: RequestOptions = {},
): Promise<SignalAccepted> {
  return postRunSignal(runId, "pause", payload, options);
}

export function resumeRun(
  runId: string,
  options: RequestOptions = {},
): Promise<SignalAccepted> {
  return postRunSignal(runId, "resume", undefined, options);
}

export function interruptRun(
  runId: string,
  payload: InterruptRunRequest,
  options: RequestOptions = {},
): Promise<SignalAccepted> {
  return postRunSignal(runId, "interrupt", payload, options);
}

export function terminateRun(
  runId: string,
  payload: TerminateRunRequest,
  options: RequestOptions = {},
): Promise<SignalAccepted> {
  return postRunSignal(runId, "terminate", payload, options);
}

function postRunSignal(
  runId: string,
  action: "events" | "instructions" | "pause" | "resume" | "interrupt" | "terminate",
  payload: object | undefined,
  options: RequestOptions,
): Promise<SignalAccepted> {
  return apiRequest<SignalAccepted>(
    `/api/runs/${encodeURIComponent(runId)}/${action}`,
    {
      method: "POST",
      body: payload === undefined ? undefined : JSON.stringify(payload),
      signal: options.signal,
    },
  );
}
