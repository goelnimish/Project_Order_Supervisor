# Backend Schema Specification: Relational, Workflow & Agent Contracts

## 1. Overview & Data Tiering Strategy

The Order Supervisor platform employs a clean, tiered data architecture:

1. **Relational Storage Tier (PostgreSQL 16):** Stores supervisor configurations, run snapshot records, and an append-only unified activity audit log. Managed via async SQLAlchemy and reversible Alembic migrations.
2. **Workflow Orchestration Tier (Temporal Python SDK):** Stores active workflow execution history, signal queues, and durable timer deadlines.
3. **Cognitive Contract Tier (Pydantic v2):** Enforces strict serialization, validation, and JSON schemas across API endpoints and LLM prompts.

---

## 2. PostgreSQL Relational Schema (SQLAlchemy & Alembic)

```text
+-----------------------------------+       +-----------------------------------+
|            supervisors            |       |               runs                |
+-----------------------------------+       +-----------------------------------+
| id (PK, UUID)                     |1     *| id (PK, UUID)                     |
| name (VARCHAR(120), UNIQUE)       +-------+ supervisor_id (FK, UUID)          |
| base_instruction (TEXT)           |       | order_id (VARCHAR(120), INDEXED)  |
| available_actions (JSONB)         |       | temporal_workflow_id (VARCHAR(255)|
| default_wake_interval_seconds(INT)|       | temporal_run_id (VARCHAR(255))    |
| wake_aggressiveness (VARCHAR(32)) |       | status (VARCHAR(32), INDEXED)     |
| model_config (JSONB)              |       | current_order_state (JSONB)       |
| created_at (TIMESTAMPTZ)          |       | memory_summary (JSONB)            |
| updated_at (TIMESTAMPTZ)          |       | final_output (JSONB)              |
+-----------------------------------+       | created_at, updated_at, completed |
                                            +-----------------+-----------------+
                                                              |1
                                                              |
                                                              |*
                                            +-----------------+-----------------+
                                            |            activities             |
                                            +-----------------------------------+
                                            | id (PK, UUID)                     |
                                            | run_id (FK, UUID, INDEXED)        |
                                            | activity_key (VARCHAR(255))       |
                                            | activity_type (VARCHAR(64), INDEX)|
                                            | source (VARCHAR(64))              |
                                            | action_name (VARCHAR(64), NULLABLE|
                                            | status (VARCHAR(32))              |
                                            | summary (TEXT)                    |
                                            | payload (JSONB)                   |
                                            | created_at (TIMESTAMPTZ, INDEXED) |
                                            +-----------------------------------+
                                            | UNIQUE(run_id, activity_key)      |
                                            +-----------------------------------+
```

### 2.1 Table: `supervisors`
Stores operational configurations used to instantiate order runs.

| Column | Type | Constraints | Default | Description |
|---|---|---|---|---|
| `id` | `UUID` | PRIMARY KEY | `uuid_generate_v4()` | Unique supervisor configuration identifier. |
| `name` | `VARCHAR(120)` | NOT NULL, UNIQUE | — | Human-readable configuration name. |
| `base_instruction` | `TEXT` | NOT NULL | — | System prompt providing operational baseline instructions. |
| `available_actions` | `JSONB` | NOT NULL | `['message_fulfillment_team', ...]` | Subset of the 5 allowed actions enabled for this supervisor. |
| `default_wake_interval_seconds` | `INTEGER` | NOT NULL | `12` | Default duration between scheduled review wakes. |
| `wake_aggressiveness` | `VARCHAR(32)` | NOT NULL | `'moderate'` | Aggressiveness tier (`conservative`, `moderate`, `aggressive`). |
| `model_config` | `JSONB` | NOT NULL | `{'provider': 'deterministic'}` | Model execution settings (`provider`, `model`, `temperature`). |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | Record creation timestamp. |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | Record update timestamp. |

### 2.2 Table: `runs`
Tracks the lifecycle snapshot of a supervised order.

| Column | Type | Constraints | Default | Description |
|---|---|---|---|---|
| `id` | `UUID` | PRIMARY KEY | `uuid_generate_v4()` | Unique run identifier. |
| `supervisor_id` | `UUID` | NOT NULL, FK(`supervisors.id`) | — | Associated supervisor configuration ID. |
| `order_id` | `VARCHAR(120)` | NOT NULL, INDEXED | — | External commerce order identifier (e.g. `ORD-88219`). |
| `temporal_workflow_id` | `VARCHAR(255)`| NOT NULL | — | Deterministic workflow ID: `order-supervisor:{order_id}`. |
| `temporal_run_id` | `VARCHAR(255)`| NULLABLE | — | Temporal cluster run UUID assigned on start. |
| `status` | `VARCHAR(32)` | NOT NULL, INDEXED | `'starting'` | Current state (`starting`, `running`, `sleeping`, etc.). |
| `current_order_state`| `JSONB` | NOT NULL | `{}` | Projection of order attributes (lifecycle, payment, etc.). |
| `memory_summary` | `JSONB` | NULLABLE | `NULL` | Latest compact semantic memory object. |
| `final_output` | `JSONB` | NULLABLE | `NULL` | 4-quadrant synthesis generated upon completion. |
| `created_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | Run creation timestamp. |
| `updated_at` | `TIMESTAMPTZ` | NOT NULL | `now()` | Last run state update timestamp. |
| `completed_at` | `TIMESTAMPTZ` | NULLABLE | `NULL` | Terminal completion timestamp. |

#### Critical Database Invariant: Partial Unique Index
To enforce the core system requirement of **one active workflow per order**, PostgreSQL enforces a partial unique index:
```sql
CREATE UNIQUE INDEX uq_runs_active_order_id ON runs (order_id)
WHERE status NOT IN ('completed', 'terminated', 'failed');
```
*Attempting to start an order while an existing run is active raises a database integrity violation, mapped to HTTP 409 Conflict.*

### 2.3 Table: `activities`
The append-only unified audit timeline for all events, decisions, actions, and controls.

| Column | Type | Constraints | Default | Description |
|---|---|---|---|---|
| `id` | `UUID` | PRIMARY KEY | `uuid_generate_v4()` | Unique activity row identifier. |
| `run_id` | `UUID` | NOT NULL, FK(`runs.id`), INDEXED | — | Foreign key linking activity to the order run. |
| `activity_key` | `VARCHAR(255)`| NOT NULL | — | Deterministic idempotency key preventing duplicate rows. |
| `activity_type`| `VARCHAR(64)` | NOT NULL, INDEXED | — | Event type (e.g., `business_action_executed`, `order_event_received`). |
| `source` | `VARCHAR(64)` | NOT NULL | — | Emitter (`temporal_workflow`, `operator_console`, `external_signal`). |
| `action_name` | `VARCHAR(64)` | NULLABLE | `NULL` | Exact business action name if applicable. |
| `status` | `VARCHAR(32)` | NOT NULL | `'executed'` | Execution status (`executed`, `suppressed`, `failed`). |
| `summary` | `TEXT` | NOT NULL | — | Human-readable operational description. |
| `payload` | `JSONB` | NOT NULL | `{}` | Full structured evidence payload (arguments, tracking, etc.). |
| `created_at` | `TIMESTAMPTZ` | NOT NULL, INDEXED | `now()` | Activity occurrence timestamp. |

#### Idempotency Guard Constraint:
```sql
ALTER TABLE activities ADD CONSTRAINT uq_activities_run_key UNIQUE (run_id, activity_key);
```

---

## 3. Cognitive Supervisor Schemas (Pydantic Contracts)

All cognitive input, output, and business action proposals are strictly typed in `backend/app/supervisor/schemas.py`:

### 3.1 `AgentDecision` (Structured Model Output)
```python
class AgentDecision(BaseModel):
    """Strict structured output contract expected from every supervisor provider."""
    
    urgency: Literal["low", "medium", "high", "critical"] = Field(
        ..., description="Operational urgency of the current order situation."
    )
    decision_summary: str = Field(
        ..., min_length=5, max_length=500, description="Concise, human-readable rationale."
    )
    should_act: bool = Field(
        ..., description="True if one or more business actions are proposed."
    )
    actions: list[BusinessActionProposal] = Field(
        default_factory=list, description="Allowlisted business action proposals."
    )
    memory_update: CompactMemoryUpdate = Field(
        ..., description="Updated rolling semantic memory object."
    )
    next_wake_seconds: int = Field(
        default=12, ge=1, le=86400, description="Bounded duration until next scheduled review."
    )
    recommend_completion: bool = Field(
        default=False, description="Advisory flag recommending lifecycle completion."
    )
```

### 3.2 `CompactMemoryUpdate` (Rolling Memory Model)
```python
class CompactMemoryUpdate(BaseModel):
    """Rolling semantic memory preserved across review cycles."""
    
    order_state: str = Field(
        ..., description="High-level narrative sentence describing current order status."
    )
    important_facts: list[str] = Field(
        default_factory=list, description="Verified facts extracted from events and actions."
    )
    open_issues: list[str] = Field(
        default_factory=list, description="Current unresolved problems or blockers."
    )
    actions_taken: list[str] = Field(
        default_factory=list, description="Historical summary of executed business interventions."
    )
    active_constraints: list[str] = Field(
        default_factory=list, description="Active steering rules injected by human operators."
    )
    next_review: str = Field(
        default="", description="Operational focus for the upcoming scheduled review."
    )
```

---

## 4. The Five Allowlisted Business Action Schemas

Every proposed business action must validate against `BusinessActionArguments`:

```python
class BusinessActionArguments(BaseModel):
    """Unified argument container with strict discriminator validation."""
    
    recipient: str = Field(
        ..., min_length=2, max_length=120, description="Target team or customer identifier."
    )
    subject: str = Field(
        ..., min_length=3, max_length=200, description="Concise subject or headline."
    )
    body: str = Field(
        ..., min_length=5, max_length=2000, description="Action message body or internal note text."
    )
    priority: Literal["low", "normal", "high", "urgent"] = Field(
        default="normal", description="Dispatch priority level."
    )
    tracking_number: str | None = Field(
        default=None, description="Optional carrier tracking number if logistics action."
    )
    carrier_code: str | None = Field(
        default=None, description="Optional carrier identifier (e.g., 'FEDEX', 'UPS')."
    )
```

### Validation Invariants:
1. Unknown arguments (e.g., attempting to pass an unauthorized `refund_amount` field) are rejected by Pydantic's `extra="forbid"` configuration.
2. The action name must match exactly one of:
   * `message_fulfillment_team`
   * `message_payments_team`
   * `message_logistics_team`
   * `message_customer`
   * `create_internal_note`
3. If an action is proposed that is not enabled in the specific supervisor's `available_actions` list, execution is halted with `DisabledBusinessAction`.

---

## 5. Temporal DTOs & Signal Serialization

In `backend/app/temporal/models.py`, serialization-safe data transfer objects bridge the FastAPI control plane and the Temporal worker:

```python
@dataclass
class OrderEvent:
    """External trigger delivered via the 'receive_event' Temporal Signal."""
    event_id: str
    event_type: EventType
    occurred_at: str
    payload: dict[str, Any] = field(default_factory=dict)

@dataclass
class RunInstruction:
    """Human steering guidance delivered via 'add_instruction' Signal."""
    instruction_id: str
    instruction: str
    created_at: str

@dataclass
class WorkflowSnapshot:
    """Live state returned synchronously by Temporal Workflow Query."""
    workflow_id: str
    status: WorkflowStatus
    order_id: str
    order_state: OrderState
    memory_summary: CompactMemoryData | None
    next_wake_at: str | None
    paused: bool
    interrupted: bool
    timeline_count: int
```
