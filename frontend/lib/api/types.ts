export const BUSINESS_ACTION_NAMES = [
  "message_fulfillment_team",
  "message_payments_team",
  "message_logistics_team",
  "message_customer",
  "create_internal_note",
] as const;

export type BusinessActionName = (typeof BUSINESS_ACTION_NAMES)[number];

export const KNOWN_ORDER_EVENT_TYPES = [
  "order_created",
  "payment_confirmed",
  "payment_failed",
  "shipment_created",
  "shipment_delayed",
  "delivered",
  "refund_requested",
  "customer_message_received",
  "no_update_for_n_hours",
] as const;

export type KnownOrderEventType = (typeof KNOWN_ORDER_EVENT_TYPES)[number];

export const WORKFLOW_STATUSES = [
  "starting",
  "running",
  "sleeping",
  "paused",
  "interrupted",
  "completed",
  "terminated",
  "failed",
] as const;

export type WorkflowStatus = (typeof WORKFLOW_STATUSES)[number];
export type WakeAggressiveness = "low" | "moderate" | "high";
export type JsonObject = Record<string, unknown>;

export interface SupervisorModelConfig {
  provider?: string | null;
  model?: string | null;
  temperature?: number | null;
}

export interface SupervisorCreate {
  name: string;
  base_instruction: string;
  available_actions?: BusinessActionName[];
  default_wake_seconds?: number;
  wake_aggressiveness?: WakeAggressiveness;
  model_config?: SupervisorModelConfig;
}

export interface Supervisor {
  id: string;
  name: string;
  base_instruction: string;
  available_actions: BusinessActionName[];
  default_wake_seconds: number;
  wake_aggressiveness: WakeAggressiveness;
  model_config: {
    provider: string | null;
    model: string | null;
    temperature: number | null;
  };
  created_at: string;
  updated_at: string;
}

export interface RunCreate {
  order_id: string;
  supervisor_config_id: string;
  order_context: {
    customer_id?: string | null;
    initial_state?: JsonObject;
  };
}

export interface Run {
  id: string;
  order_id: string;
  workflow_id: string;
  temporal_run_id: string | null;
  supervisor_config_id: string;
  status: WorkflowStatus;
  current_order_state: JsonObject;
  order_context: JsonObject;
  additional_instructions: JsonObject[];
  memory_summary: JsonObject | null;
  final_output: JsonObject | null;
  next_wake_at: string | null;
  started_at: string;
  completed_at: string | null;
  completion_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface Activity {
  id: string;
  run_id: string;
  activity_key: string;
  activity_type: string;
  source: string;
  event_name: string | null;
  action_name: string | null;
  status: string;
  summary: string;
  payload: JsonObject;
  external_event_id: string | null;
  created_at: string;
}

export interface WorkflowState {
  order_id: string;
  workflow_status: WorkflowStatus;
  order_state: JsonObject;
  pending_event_count: number;
  seen_event_count: number;
  is_sleeping: boolean;
  next_wake_at: string | null;
  supervisor_invocation_count: number;
  recent_timeline: JsonObject[];
  completed: boolean;
  completion_reason: string | null;
  paused: boolean;
  interrupted: boolean;
  pause_reason: string | null;
  interrupt_reason: string | null;
  additional_instructions: JsonObject[];
  memory_summary: JsonObject | null;
  final_output: JsonObject | null;
}

export interface RunDetail extends Run {
  activities: Activity[];
  workflow_state: WorkflowState | null;
}

export interface OrderEventRequest {
  event_id: string;
  event_type: string;
  occurred_at: string;
  payload?: JsonObject;
}

export interface InstructionRequest {
  instruction: string;
}

export interface PauseRunRequest {
  reason?: string | null;
}

export interface InterruptRunRequest {
  reason: string;
}

export type TerminateRunRequest = InterruptRunRequest;

export interface SignalAccepted {
  status: "accepted";
  run_id: string;
  workflow_id: string;
  signal: string;
}

export interface RunCounts {
  total: number;
  active: number;
  completed: number;
  terminated: number;
}

export type ActionDistribution = Record<BusinessActionName, number>;

export interface GlobalAnalytics {
  runs: RunCounts;
  wake_suppression_rate: number | null;
  business_actions_executed: number;
  average_time_to_first_intervention_seconds: number | null;
  action_distribution: ActionDistribution;
}

export interface RunAnalytics {
  run_id: string;
  order_id: string;
  duration_seconds: number | null;
  events_received: number;
  supervisor_invocations: number;
  wake_suppressions: number;
  business_actions_executed: number;
}

export interface CurrentOrderState {
  lifecycle: string;
  payment: string;
  shipment: string;
  refund: string;
  last_event_type: string | null;
  event_count: number;
  attributes: JsonObject;
}

export interface RetainedInstruction {
  instruction_id: string;
  instruction: string;
  created_at: string;
}

export interface CompactMemory {
  order_state: string;
  important_facts: string[];
  open_issues: string[];
  actions_taken: string[];
  active_constraints: string[];
  next_review: string;
}

export interface FinalOutput {
  final_summary: string;
  important_actions: string[];
  key_learnings: string[];
  recommendations: string[];
}
