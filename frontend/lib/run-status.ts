import type { WorkflowStatus } from "./api/types";

export const TERMINAL_WORKFLOW_STATUSES: ReadonlySet<WorkflowStatus> = new Set([
  "completed",
  "terminated",
  "failed",
]);

export const ACTIVE_WORKFLOW_STATUSES: ReadonlySet<WorkflowStatus> = new Set([
  "starting",
  "running",
  "sleeping",
  "paused",
  "interrupted",
]);

export type WorkflowStatusTone = "neutral" | "info" | "success" | "warning" | "danger";

export interface RunControlAvailability {
  event: boolean;
  instruction: boolean;
  pause: boolean;
  resume: boolean;
  interrupt: boolean;
  terminate: boolean;
}

export function isTerminalStatus(status: WorkflowStatus): boolean {
  return TERMINAL_WORKFLOW_STATUSES.has(status);
}

export function isActiveStatus(status: WorkflowStatus): boolean {
  return ACTIVE_WORKFLOW_STATUSES.has(status);
}

export function getWorkflowStatusTone(status: WorkflowStatus): WorkflowStatusTone {
  switch (status) {
    case "completed":
      return "success";
    case "failed":
    case "terminated":
      return "danger";
    case "paused":
    case "interrupted":
      return "warning";
    case "running":
    case "sleeping":
      return "info";
    default:
      return "neutral";
  }
}

export function getRunControlAvailability(status: WorkflowStatus): RunControlAvailability {
  const acceptsLiveInput = !isTerminalStatus(status);
  const automated = status === "starting" || status === "running" || status === "sleeping";
  const blocked = status === "paused" || status === "interrupted";

  return {
    event: acceptsLiveInput,
    instruction: acceptsLiveInput,
    pause: automated,
    resume: blocked,
    interrupt: automated || status === "paused",
    terminate: acceptsLiveInput,
  };
}
