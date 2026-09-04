import { apiRequest, type RequestOptions } from "./client";
import type { GlobalAnalytics, RunAnalytics } from "./types";

export function getGlobalAnalytics(
  options: RequestOptions = {},
): Promise<GlobalAnalytics> {
  return apiRequest<GlobalAnalytics>("/api/analytics/summary", {
    signal: options.signal,
  });
}

export function getRunAnalytics(
  runId: string,
  options: RequestOptions = {},
): Promise<RunAnalytics> {
  return apiRequest<RunAnalytics>(`/api/runs/${encodeURIComponent(runId)}/analytics`, {
    signal: options.signal,
  });
}
