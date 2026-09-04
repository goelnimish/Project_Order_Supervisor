import type {
  CompactMemory,
  CurrentOrderState,
  FinalOutput,
  RetainedInstruction,
} from "./types";

export function isJsonObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isStringArray(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

export function isCurrentOrderState(value: unknown): value is CurrentOrderState {
  if (!isJsonObject(value)) {
    return false;
  }

  return (
    typeof value.lifecycle === "string" &&
    typeof value.payment === "string" &&
    typeof value.shipment === "string" &&
    typeof value.refund === "string" &&
    (typeof value.last_event_type === "string" || value.last_event_type === null) &&
    typeof value.event_count === "number" &&
    Number.isInteger(value.event_count) &&
    value.event_count >= 0 &&
    isJsonObject(value.attributes)
  );
}

export function isRetainedInstruction(value: unknown): value is RetainedInstruction {
  if (!isJsonObject(value)) {
    return false;
  }

  return (
    typeof value.instruction_id === "string" &&
    typeof value.instruction === "string" &&
    typeof value.created_at === "string"
  );
}

export function isCompactMemory(value: unknown): value is CompactMemory {
  if (!isJsonObject(value)) {
    return false;
  }

  return (
    typeof value.order_state === "string" &&
    isStringArray(value.important_facts) &&
    isStringArray(value.open_issues) &&
    isStringArray(value.actions_taken) &&
    isStringArray(value.active_constraints) &&
    typeof value.next_review === "string"
  );
}

export function isFinalOutput(value: unknown): value is FinalOutput {
  if (!isJsonObject(value)) {
    return false;
  }

  return (
    typeof value.final_summary === "string" &&
    isStringArray(value.important_actions) &&
    isStringArray(value.key_learnings) &&
    isStringArray(value.recommendations)
  );
}
