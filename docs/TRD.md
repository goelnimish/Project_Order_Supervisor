# Technical Requirements Document (TRD): Order Supervisor Platform

## 1. Architectural Philosophy & System Topology

Order Supervisor is designed around a fundamental distributed systems principle: **Orchestration belongs in a durable event-driven state machine, intelligence belongs in bounded activities, and operational visibility belongs in an optimized read model.**

```text
+-----------------------------------------------------------------------------------+
|                            OPERATOR INTERFACE (Next.js)                           |
|      - Real-time run monitoring           - Signal & instruction injection        |
|      - Activity timeline inspector        - Lifecycle controls (pause/interrupt)  |
+-----------------------------------------+-----------------------------------------+
                                          | HTTP REST / Polling (1.5s - 5s)
                                          v
+-----------------------------------------------------------------------------------+
|                             CONTROL PLANE (FastAPI)                               |
|      - Input validation (Pydantic v2)     - Idempotent run initialization         |
|      - Temporal client signal dispatch    - PostgreSQL operational read model     |
+-------------------+-------------------------------------+-------------------------+
                    | gRPC                                | Async SQLAlchemy / asyncpg
                    v                                     v
+---------------------------------------+   +---------------------------------------+
|          TEMPORAL CLUSTER             |   |        POSTGRESQL 16 DATABASE         |
|   - Task Queue: 'order-supervisor'    |   |   - Table: 'supervisors' (configs)    |
|   - Deterministic event history       |   |   - Table: 'runs' (run snapshots)     |
|   - Durable timers & sleep state      |   |   - Table: 'activities' (unified audit)   |
+-------------------+-------------------+   +-------------------+-------------------+
                    | Worker Poll                               ^
                    v                                           |
+---------------------------------------------------------------+-------------------+
|                        TEMPORAL WORKER & ACTIVITY HARNESS                         |
|                                                                                   |
|  [ OrderSupervisorWorkflow ]                                                      |
|    - Single workflow per order: 'order-supervisor:{order_id}'                     |
|    - Owns lifecycle state, wake timers, signal deduplication, & completion rules  |
|                                                                                   |
|  [ Temporal Activities ] (Nondeterministic boundary)                              |
|    +-- supervisor Activity --------> Ollama ('qwen3:1.7b') or Deterministic Fake  |
|    +-- execute_business_action ----> 5 Allowlisted Actions (Idempotent Simulation)|
|    +-- persist_workflow_transition > Append audit row + update run snapshot       |
|    +-- generate_final_output ------> Synthesize 4-quadrant completion report       |
+-----------------------------------------------------------------------------------+
```

---

## 2. CQRS Boundary & State Separation

A common pitfall in AI workflow systems is treating the database as the workflow coordinator or overloading the workflow state with unbounded database logs. Order Supervisor enforces strict Command Query Responsibility Segregation (CQRS):

| Dimension | Temporal Workflow History (Authoritative) | PostgreSQL 16 Projection (Read Model) |
|---|---|---|
| **Purpose** | Authoritative source of execution truth, signal queue, and replay state. | Operational read model for fast querying, UI dashboarding, and audit reporting. |
| **Persistence** | File-backed local Temporal cluster persistence. | Relational tables (`supervisors`, `runs`, `activities`). |
| **Write Path** | Appended exclusively through Temporal SDK signals and activity completions. | Written exclusively by bounded Temporal Activities (`persist_workflow_transition`). |
| **Determinism** | 100% deterministic replay required. | Relational transactions (`async_session_factory.begin()`). |
| **Consistency** | Strong consistency within workflow execution. | Eventual consistency (UI polls with 1.5s fallback to workflow query). |

---

## 3. Temporal Workflow Engine & Determinism Invariants

The core workflow implementation in `backend/app/temporal/workflows.py` (`OrderSupervisorWorkflow`) adheres strictly to the Temporal determinism contract:

### 3.1 Workflow Invariants
1. **Deterministic Identity:** Every workflow instance is bound to `order-supervisor:{order_id}`. Attempting to start a concurrent workflow with the same ID will trigger a workflow execution conflict.
2. **Forbidden Inside Workflow Code:**
   * No `datetime.now()` or wall-clock calls. The workflow uses `workflow.now()`.
   * No `time.sleep()`. The workflow uses `workflow.wait_condition()` combined with workflow timers.
   * No random number or UUID generation.
   * No direct network calls, database access, or file I/O.
   * No direct environment variable reads.
3. **Signal Handler Mutability:** Signal methods (`receive_event`, `add_instruction`, `pause`, `resume`, `interrupt`, `terminate`) only mutate workflow in-memory variables (`self._pending_events`, `self._paused`, etc.) and set `self._signal_wakeup = True`. They never execute activities directly.
4. **Durable Timers Without Busy Polling:**
   ```python
   # Workflow sleeps durably until either a signal arrives or the deadline expires:
   await workflow.wait_condition(
       lambda: self._signal_wakeup or self._is_blocked() or self._wake_deadline_has_passed(),
       timeout=remaining,
   )
   ```

### 3.2 Activity Retry & Timeout Policies
Every activity dispatched by the workflow must configure explicit timeouts and capped exponential backoffs to prevent hung execution:

```python
# Standard Activity Configuration
DEFAULT_ACTIVITY_TIMEOUT = timedelta(seconds=30)
DEFAULT_RETRY_POLICY = RetryPolicy(
    initial_interval=timedelta(seconds=1),
    backoff_coefficient=2.0,
    maximum_interval=timedelta(seconds=10),
    maximum_attempts=3,
    non_retryable_error_types=[
        "InvalidBusinessAction",
        "DisabledBusinessAction",
        "InvalidBusinessActionAllowlist",
        "BusinessActionIdempotencyConflict",
    ],
)
```

---

## 4. Cognitive Subsystem & Local Model Harness

The supervisor activity connects to either a local Ollama instance running `qwen3:1.7b` or a deterministic rule-based mock for testing.

### 4.1 Local Ollama Execution Constraints
To guarantee predictable execution on developer hardware and edge servers:
* **Direct HTTP Integration:** Calls `POST /api/chat` directly via `httpx.AsyncClient` inside the activity. No heavy frameworks (LangChain, LlamaIndex, CrewAI) are permitted.
* **Deterministic Inference Settings:**
  * `temperature: 0.0` (eliminates stochastic variance)
  * `stream: false` (atomic response delivery)
  * `think: false` (disables reasoning preamble tokens)
  * `num_ctx: 2048` (hard memory ceiling)
  * `num_predict: 512` (caps token generation to prevent runaway output)
  * `keep_alive: 0` (unloads model memory immediately after execution)
* **Structured Output Schema:** Enforced via JSON Schema parameter passed directly to Ollama's schema validator, mapping to the Pydantic `AgentDecision` model.

### 4.2 Safe Fallback Architecture
If the local model times out (default: 30 seconds), encounters an HTTP 500, or returns malformed JSON:
1. The activity catches the exception without crashing the workflow.
2. An audit row is persisted recording `supervisor_invocation_failed`.
3. No business action is proposed or executed.
4. The workflow schedules a safe catch-up review timer (default: 12 seconds).

---

## 5. Business Action Dispatcher & Idempotency Engineering

The system implements exactly five allowlisted business actions in `backend/app/temporal/business_actions.py`:
* `message_fulfillment_team`
* `message_payments_team`
* `message_logistics_team`
* `message_customer`
* `create_internal_note`

### 5.1 Two-Tier Idempotency Model

#### Tier 1: Technical Idempotency (Retry Safety)
Guarantees that network retries of a Temporal activity do not produce duplicate external side effects:
* **Key Derivation Formula:**
  $$\text{idempotency\_key} = \text{hash}(\text{run\_id} + \text{invocation\_count} + \text{action\_index})$$
* **Database Guard:** PostgreSQL enforces a unique constraint on `(run_id, activity_key)` in the `activities` table.
* If a retried activity runs against an existing key:
  * If the action name and payload match the existing record: Returns the existing evidence row gracefully (`created = False`).
  * If the action name or payload conflicts with existing evidence: Raises `BusinessActionIdempotencyConflict` (non-retryable error).

#### Tier 2: Semantic Idempotency (Event-Cause Guard)
Guarantees that the AI supervisor does not repeatedly dispatch the same communication for the same triggering external event:
* Governed by workflow patch `semantic-action-guard-v1`.
* When an action succeeds, the workflow stores a mapping of `(cause_event_id, action_name)`.
* If a subsequent evaluation proposes the identical action for the same cause event ID without a new external trigger, the action is suppressed and audited as `semantic_duplicate_suppressed`.

---

## 6. Failure Modes & Blast Radius Containment

```text
+-----------------------+---------------------------------------+---------------------------------------+
| Failure Mode          | Root Cause                            | System Containment & Recovery         |
+-----------------------+---------------------------------------+---------------------------------------+
| Worker Crash / Reboot | Host out-of-memory or SIGKILL during  | Temporal server detects heartbeat     |
|                       | active order supervision.             | timeout, reassigns task to live worker|
|                       |                                       | which deterministically replays state.|
+-----------------------+---------------------------------------+---------------------------------------+
| Ollama Process Hang   | Local GPU contention or model dead-   | Activity timeout (30s) aborts HTTP    |
|                       | lock on complex token sequences.      | call. Safe fallback schedules review. |
|                       |                                       | No unvalidated action ever dispatches.|
+-----------------------+---------------------------------------+---------------------------------------+
| Database Unavailable  | PostgreSQL container restart or pool  | Persistence activity exhausts retries |
|                       | exhaustion.                           | and raises ApplicationError. Workflow |
|                       |                                       | queues transition and retries in 5s.  |
+-----------------------+---------------------------------------+---------------------------------------+
| Duplicate Webhooks    | Upstream carrier sends identical scan | Workflow maintains seen_event_ids set.|
|                       | payload 4 times in 2 seconds.         | 1st event processed; duplicates 2-4   |
|                       |                                       | logged as deduplicated and dropped.   |
+-----------------------+---------------------------------------+---------------------------------------+
| Invalid Action Schema | Model invents new parameter or        | Pydantic ValidationError in activity; |
|                       | proposes unknown action name.         | activity fails non-retryable; workflow|
|                       |                                       | audits rejection and stays alive.     |
+-----------------------+---------------------------------------+---------------------------------------+
```

---

## 7. Security, Secrets & Environment Boundaries

* **No Committed Secrets:** Database credentials and local URLs default to safe local values in `.env.example`. The `.env` file is strictly ignored by Git.
* **Model Configuration Hygiene:** The supervisor model config schema accepts only `provider`, `model`, and `temperature`. Arbitrary prompt injection via model configuration parameters is rejected by strict Pydantic parsing.
* **No Unrestricted Execution Tools:** The model receives no shell execution tools, arbitrary HTTP clients, or database connection handles. Its only interface to the world is proposing structured payloads to the five allowed action handlers.
