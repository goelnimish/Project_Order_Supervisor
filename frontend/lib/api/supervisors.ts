import { apiRequest, type RequestOptions } from "./client";
import type { Supervisor, SupervisorCreate } from "./types";

export function listSupervisors(options: RequestOptions = {}): Promise<Supervisor[]> {
  return apiRequest<Supervisor[]>("/api/supervisors", {
    signal: options.signal,
  });
}

export function createSupervisor(
  payload: SupervisorCreate,
  options: RequestOptions = {},
): Promise<Supervisor> {
  return apiRequest<Supervisor>("/api/supervisors", {
    method: "POST",
    body: JSON.stringify(payload),
    signal: options.signal,
  });
}
