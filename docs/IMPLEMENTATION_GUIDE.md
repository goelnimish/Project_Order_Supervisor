# Implementation Guide & Engineering Handbook

## 1. Repository Architecture Map

This repository implements an end-to-end, production-style proof of concept demonstrating durable workflow orchestration paired with bounded AI supervision.

```text
Order Supervisor Repository Structure
├── backend/
│   ├── app/
│   │   ├── analytics/           PostgreSQL aggregate queries for operator metrics
│   │   ├── models/              SQLAlchemy ORM models (SupervisorConfig, Run, Activity)
│   │   ├── repositories/        Async data access layer (supervisors, runs, activities)
│   │   ├── routers/             FastAPI HTTP endpoints (/supervisors, /runs, /health)
│   │   ├── supervisor/          Cognitive subsystem contracts & providers (Ollama, Fake)
│   │   ├── temporal/            Temporal Workflow, Activities, wake policy, & DTOs
│   │   ├── config.py            Pydantic settings loaded from environment
│   │   ├── database.py          Async engine & session factory (asyncpg)
│   │   └── main.py              FastAPI application entry point
│   ├── migrations/versions/     Reversible Alembic database schema revisions
│   ├── tests/                   Pytest suite (98 tests covering API, workflow, actions)
│   └── pyproject.toml           uv dependency definitions & tool configs
│
├── frontend/
│   ├── app/                     Next.js App Router pages (/supervisors, /runs, /runs/[id])
│   ├── components/              Modular React components (timeline, memory, controls)
│   ├── lib/                     Typed API client, status helpers, and data fetchers
│   └── package.json             Next.js 14, TypeScript, and Tailwind CSS dependencies
│
├── docs/                        Complete TPM documentation suite & assignment matrices
├── scripts/                     Deterministic demonstration and evaluation scripts
├── docker-compose.yml           Local PostgreSQL 16 and Temporal development services
├── Makefile                     Consolidated developer workflow commands
└── .env.example                 Safe local configuration template
```

---

## 2. Prerequisites & Environment Setup

### Required Tooling
* **Node.js:** Version `20.9.0` or newer (with `npm`)
* **Python:** Version `3.13`
* **`uv`:** Fast Python package and environment manager
* **Docker Desktop:** With Docker Compose support
* **`make`:** Build and task automation tool
* **Ollama (Optional):** Required only if running real local LLM evaluations (`qwen3:1.7b`)

### Step 1: Clone & Bootstrap
From the repository root:
```bash
# 1. Copy the safe local environment template
cp .env.example .env

# 2. Install backend dependencies via uv and frontend dependencies via npm
make install
```

The default `.env` configuration uses safe local parameters with zero external cloud dependencies:
```ini
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/order_supervisor
TEMPORAL_TARGET_HOST=localhost:7233
LLM_PROVIDER=deterministic
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:1.7b
```

---

## 3. The 5-Terminal Startup Sequence

To run the complete platform locally, open five terminal windows in the repository root:

```text
+-----------------------------------------------------------------------------------+
| TERMINAL 1: INFRASTRUCTURE (Docker Compose)                                       |
| Command: make up                                                                  |
| Launches PostgreSQL 16 (port 5432) and Temporal Dev Server (7233 gRPC / 8233 UI).|
+-----------------------------------------------------------------------------------+

+-----------------------------------------------------------------------------------+
| TERMINAL 2: DATABASE MIGRATIONS                                                   |
| Command: make migrate                                                             |
| Executes Alembic revisions up to head (20260904_02). Verifies with: make migrate-check|
+-----------------------------------------------------------------------------------+

+-----------------------------------------------------------------------------------+
| TERMINAL 3: TEMPORAL WORKER                                                       |
| Command: make worker                                                              |
| Registers OrderSupervisorWorkflow & Activities on queue: 'order-supervisor'.      |
+-----------------------------------------------------------------------------------+

+-----------------------------------------------------------------------------------+
| TERMINAL 4: FASTAPI CONTROL PLANE                                                 |
| Command: make api                                                                 |
| Boots API on http://localhost:8000. Interactive Swagger docs at /docs.            |
+-----------------------------------------------------------------------------------+

+-----------------------------------------------------------------------------------+
| TERMINAL 5: NEXT.JS OPERATOR CONSOLE                                              |
| Command: make frontend-dev                                                        |
| Serves dashboard on http://localhost:3000. Proxies API calls to localhost:8000.    |
+-----------------------------------------------------------------------------------+
```

*(Optional Terminal 6: If testing local AI inference with Ollama, run `ollama serve` and ensure the model is downloaded via `ollama pull qwen3:1.7b`.)*

---

## 4. Quality Gates & Automated Testing

The codebase includes an extensive automated test suite covering unit, integration, and workflow replay logic.

### 4.1 Run Backend Tests & Linters
```bash
# Run the complete 98-test pytest suite
make test-backend

# Run Ruff linter and code style checks
make lint-backend
```

**Key Test Coverage Highlights:**
* `backend/tests/test_temporal_workflow.py`: Validates one-workflow-per-order, routine wake suppression, important event wakes, durable sleeping, event ID deduplication, and delivered completion.
* `backend/tests/test_business_actions.py`: Validates allowlist enforcement, argument validation, and PostgreSQL technical idempotency deduplication across all five business actions.
* `backend/tests/test_supervisors_api.py` & `test_runs_api.py`: Validates FastAPI REST contracts, partial unique index conflict handling, and signal delivery.

### 4.2 Run Frontend Quality Checks
```bash
# Run ESLint across Next.js components
make lint-frontend

# Validate TypeScript typing without emitting files
make typecheck-frontend

# Verify production Webpack build
make frontend-build
```

---

## 5. End-to-End Walkthrough Script (Step-by-Step)

Follow this script to manually verify the platform via the browser interface:

### Step 1: Create a Supervisor Configuration
1. Open your browser to `http://localhost:3000/supervisors`.
2. Click **"New Supervisor"**.
3. Fill in:
   * **Name:** `Priority Logistics Supervisor`
   * **Base Instruction:** *"Monitor shipping milestones closely. If transit delays exceed 24 hours, contact logistics immediately and notify the customer."*
   * **Actions:** Check all 5 actions.
   * **Default Wake Interval:** `12` seconds.
   * **Provider:** Select `Deterministic` (or `Ollama` if Ollama is running).
4. Click **"Save Configuration"**.

### Step 2: Start an Order Run
1. From the supervisor detail page, click **"Start Order Run"**.
2. Enter Order ID: `ORD-90210`.
3. Initial State: `{"priority": "high", "sku": "WIDGET-01"}`.
4. Click **"Start Run"**.
5. Observe: Status moves from `STARTING` $\to$ `RUNNING` $\to$ performs initial review $\to$ enters `SLEEPING` with a live countdown timer.

### Step 3: Verify Routine Event Wake Suppression (Cost Savings)
1. In the **Event Injector** panel on the right, select:
   * **Event Type:** `payment_confirmed`
   * **Payload:** `{"amount": 149.99, "currency": "USD"}`
2. Click **"Inject Event"**.
3. **Expected Behavior:**
   * The event appears in the timeline (`order_event_received`).
   * A second entry appears: `supervisor_wake_suppressed` (*"Routine event payment_confirmed recorded without waking supervisor"*).
   * The workflow **remains sleeping**; no LLM call is made.

### Step 4: Verify Important Event Supervisor Wake & Action Execution
1. In the **Event Injector** panel, select:
   * **Event Type:** `shipment_delayed`
   * **Payload:** `{"tracking_number": "TRK-44910", "carrier": "FEDEX", "delay_hours": 36}`
2. Click **"Inject Event"**.
3. **Expected Behavior:**
   * Status switches to `RUNNING`.
   * Timeline records `supervisor_decision` with a structured rationale.
   * Timeline records `business_action_executed` for `message_logistics_team`.
   * The **Compact Memory** panel updates with the delay fact and executed action.
   * The workflow schedules a follow-up review and returns to `SLEEPING`.

### Step 5: Inject a Live Steering Instruction
1. In the **Instruction Form**, enter:
   * *"Customer reached out; waive any delivery surcharge and prioritize customer goodwill."*
2. Click **"Add Instruction"**.
3. **Expected Behavior:**
   * Timeline records `instruction_added`.
   * The instruction appears in the **Retained Instructions** panel.
   * All future supervisor reviews include this instruction in their prompt context.

### Step 6: Test Lifecycle Controls (Pause / Resume / Interrupt)
1. Click **"Pause"** $\to$ status turns **Amber** (`PAUSED`). The countdown timer freezes.
2. Click **"Resume"** $\to$ status turns **Green** (`RUNNING`), executes any catch-up reviews, and returns to `SLEEPING`.
3. Click **"Interrupt"**, enter reason *"Verifying possible address fraud"*, and submit $\to$ status turns **Red** (`INTERRUPTED`).
4. Click **"Resume"** to release the hold.

### Step 7: Finalize Order (Deterministic Completion)
1. In the **Event Injector**, select:
   * **Event Type:** `delivered`
   * **Payload:** `{"signed_by": "J. Doe", "location": "Front Porch"}`
2. Click **"Inject Event"**.
3. **Expected Behavior:**
   * Status transitions to **Slate** (`COMPLETED`).
   * Controls permanently lock (*"Run Closed — Controls Locked"*).
   * Polling automatically stops.
   * The **Final Output Panel** populates with:
     * **Final Summary**
     * **Important Actions Taken**
     * **Key Learnings**
     * **Recommendations**

---

## 6. Engineering Troubleshooting & FAQ

### Q1: Temporal Worker Replay Mismatch Error
* **Symptom:** Worker log throws `NonDeterministicWorkflowError` or fails on replay.
* **Cause:** Modifying workflow logic or activity signatures while an old workflow instance is still open on the dev server.
* **Resolution:** In local development, purge the local file-backed Temporal database by running:
  ```bash
  docker compose down -v
  docker compose up -d
  make migrate
  ```

### Q2: Port Collisions on Startup
* **Ports Used:**
  * `5432`: PostgreSQL
  * `7233`: Temporal gRPC
  * `8233`: Temporal Web UI
  * `8000`: FastAPI backend
  * `3000`: Next.js frontend
* **Resolution:** Check active port bindings using `lsof -i :<PORT>` and terminate conflicting processes.

### Q3: Ollama Connection Refused / Timeout
* **Symptom:** Activity logs `ConnectError: Failed to connect to localhost:11434`.
* **Resolution:** Ensure Ollama is running locally (`ollama serve`) and verify the model is pulled (`ollama list` should display `qwen3:1.7b`). If Ollama is not installed, set `LLM_PROVIDER=deterministic` in `.env` to run full deterministic simulations.
