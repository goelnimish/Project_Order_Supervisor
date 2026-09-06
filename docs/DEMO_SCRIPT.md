# Order Supervisor Walkthrough & Video Recording Script

This is a step-by-step recording guide designed for a 3–5 minute screencast. It covers every required evaluation criterion in a clean, deterministic order.

---

## Pre-Flight Setup Checklist

Before pressing Record, ensure these 5 processes are active in separate terminal tabs:

1. **Infrastructure (PostgreSQL & Temporal):**
   ```bash
   make infra-up
   make temporal-infra-check
   make db-migrate
   ```
   *(Already confirmed working)*

2. **Temporal Worker (Leave running):**
   ```bash
   make temporal-worker
   ```
   *(Already running)*

3. **FastAPI Backend (Leave running):**
   ```bash
   make backend-dev
   ```

4. **Ollama Local LLM (Leave running):**
   ```bash
   ollama list
   ```
   *(Ensure `qwen3:1.7b` is listed and Ollama is serving on port 11434)*

5. **Next.js Frontend (Leave running):**
   ```bash
   make frontend-dev
   ```

Open your browser to: **`http://localhost:3000`**

---

## Demo Script & Narration Guide

### 1. Introduction (0:00 – 0:20)
- **Screen:** Open `http://localhost:3000` (Overview page showing Global Analytics).
- **Spoken Voiceover:**
  > "Welcome to the Order Supervisor POC demonstration. This system runs one durable Temporal Workflow per order. It evaluates incoming order events, decides when an AI supervisor must wake, executes only allowlisted simulated business actions, sleeps durably between triggers, accepts live human instructions, and produces an auditable PostgreSQL timeline."

---

### 2. Creating a Supervisor Configuration (0:20 – 0:50)
- **Screen:** Click **Supervisors** in the top navigation.
- **Action:** Fill in the supervisor creation form on the right:
  - **Name:** `Interview Qwen Supervisor`
  - **Base instruction:** `Monitor this order and surface important exceptions.`
  - **Allowed Actions:** Keep all 5 checked (`message_fulfillment_team`, `message_payments_team`, `message_logistics_team`, `message_customer`, `create_internal_note`).
  - **Default Wake Interval:** Change from `300` to `45` seconds *(allows the scheduled wake to happen live during the recording)*.
  - **Wake Aggressiveness:** `moderate`
  - **Model Provider:** Select **Ollama** *(Model defaults to `qwen3:1.7b`, temperature `0`)*.
- **Action:** Click **Create supervisor configuration**.
- **Spoken Voiceover:**
  > "First, we configure our supervisor. We name it, assign its base instructions, configure Ollama with local Qwen 1.7B, and set a 45-second wake interval. Notice the action list: these are five strict allowlisted business actions, not open-ended model execution tools."

---

### 3. Starting an Order Run & Initial Sleep (0:50 – 1:20)
- **Screen:** Click **Runs** in the navigation bar.
- **Action:**
  - **Order ID:** `demo-order-01` *(or any fresh unique ID)*
  - **Supervisor:** Select `Interview Qwen Supervisor`
  - **Initial State (JSON):** `{"item": "laptop", "amount": 1200}`
  - Click **Start order run**.
- **Screen:** Automatically navigates to the Run Detail page (`/runs/{run_id}`).
- **Highlight on Screen:**
  - Order ID: `demo-order-01`
  - Workflow ID: `order-supervisor:demo-order-01`
  - Status badge: Transitions from `Starting` → `Running` → **`Sleeping`**.
  - **Next wake:** Countdown shows ~45 seconds.
  - Timeline: Initial supervisor inference on workflow start and initial state recorded.
- **Spoken Voiceover:**
  > "Now we start a run for order demo-order-01. Temporal initializes a durable workflow with the deterministic ID 'order-supervisor:demo-order-01'. The main supervisor wakes immediately on startup, evaluates the initial context, updates compact memory, and puts the workflow to sleep for 45 seconds."

---

### 4. Sending Events: Routine Event & Wake Suppression (1:20 – 1:45)
- **Screen:** On the Run Detail page, look at the **Command deck** on the right side.
- **Action:** In **Inject event**:
  - **Event type:** Select `payment_confirmed`
  - **Payload:** `{"method": "card"}`
  - Click **Send event signal**.
- **Highlight on Screen:**
  - Status remains **`Sleeping`**.
  - The timeline adds `event_received: payment_confirmed` followed by **`supervisor_wake_suppressed`**.
  - Supervisor invocation count does not increment.
- **Spoken Voiceover:**
  > "Now we inject an order event via Temporal signals: payment_confirmed. Notice our wake policy: routine events are durably recorded in the PostgreSQL audit log, but the AI supervisor is not invoked. This suppresses unnecessary LLM cost and latency."

---

### 5. Important Event & Business Action Execution (1:45 – 2:30)
- **Screen:** In **Inject event** in the Command deck:
  - **Event type:** Select `shipment_delayed`
  - **Payload:** `{"delay_hours": 24, "carrier": "Apex Logistics"}`
  - Click **Send event signal**.
- **Highlight on Screen:**
  - Status changes to `Running`, then returns to **`Sleeping`**.
  - Timeline displays:
    1. `event_received: shipment_delayed`
    2. `supervisor_wake_requested`
    3. `supervisor_invocation` with decision summary
    4. **`business_action_executed: message_logistics_team`**
    5. `memory_updated`
  - In **Compact Memory** panel: Note the updated summary reflecting the delay and message to logistics.
- **Spoken Voiceover:**
  > "Next, we inject an important exception: shipment_delayed. The wake policy identifies this as wake-worthy and wakes the Qwen AI supervisor. The supervisor analyzes the order context, formulates a decision, and triggers the allowlisted action 'message_logistics_team'. The action executes via a Temporal Activity with an idempotency key, and compact memory is updated."

---

### 6. Durable Sleeping & Scheduled Wake-Up (2:30 – 2:55)
- **Screen:** Watch the **Next wake** timer or timeline.
- **Highlight on Screen:**
  - As the 45-second timer expires, the status briefly indicates `Running` as the scheduled wake triggers, then returns to **`Sleeping`**.
  - Timeline records a scheduled wake `supervisor_invocation` and subsequent sleep cycle.
- **Spoken Voiceover:**
  > "Notice the scheduled wake: Temporal timers wake the supervisor without tight polling loops. The supervisor reviews the order with compact rolling memory, confirms no further intervention is required, and sleeps until the next scheduled trigger or incoming event."

---

### 7. Adding Extra Instructions to a Live Run (2:55 – 3:20)
- **Screen:** In the Command deck, scroll to **Add live instruction**.
- **Action:**
  - Type: `For this order, prioritize speed over cost.`
  - Click **Send instruction signal**.
- **Highlight on Screen:**
  - Appears immediately under **Retained instructions** (count = 1).
  - Appears in the **Timeline** as `instruction_added`.
- **Spoken Voiceover:**
  > "An operator can send live steering instructions to a running workflow at any time. We inject: 'For this order, prioritize speed over cost.' This instruction is retained in durable workflow state and will be injected into every subsequent supervisor context."

---

### 8. Interrupting, Pausing, and Resuming Controls (3:20 – 3:55)
- **Screen:** In the Command deck under **Lifecycle controls**:
  1. Click **Pause** → enter Reason: `Operator spot check` → Click **Confirm Pause**.
     - Status changes to **`Paused`**.
     - Point out: Automation and wakes are suspended; signals remain safely queued.
  2. Click **Resume**.
     - Status returns to **`Sleeping`**.
  3. Click **Interrupt** → enter Reason: `Review carrier exception` → Click **Confirm Interrupt**.
     - Status changes to **`Interrupted`** *(indicating urgent human review required)*.
  4. Click **Resume**.
     - Status returns to **`Sleeping`**.
- **Spoken Voiceover:**
  > "Operators have full lifecycle control. We can Pause the run for routine checks, which holds automated wakeups while queueing events. We Resume back to normal operation. We can also Interrupt the run when urgent operator intervention is needed, blocking AI inference until an operator resumes."

---

### 9. Deterministic Completion & Final Summary (3:55 – 4:35)
- **Screen:** In **Inject event**:
  - **Event type:** Select `delivered`
  - Click **Send event signal**.
- **Highlight on Screen:**
  - Status updates to **`Completed`**.
  - Banner: *"Terminal state. Auto-refresh stopped."*
  - Scroll down to the **Final Output** panel:
    - **Final Summary:** Comprehensive summary of the order lifecycle.
    - **Important Actions Taken:** Authoritative log of actions taken (e.g. `message_logistics_team`).
    - **Key Learnings:** Observations noted across the run.
    - **Recommendations & Feedback:** Actionable post-order insights.
- **Spoken Voiceover:**
  > "Finally, we send the terminal event: delivered. Notice a key architectural boundary: the AI supervisor does not have authority to close the workflow. Deterministic lifecycle rules in the Temporal workflow decide completion. Upon completion, the workflow generates our required final report: Final Summary, Important Actions Taken, Key Learnings, and Recommendations."

---

### 10. Terminating a Run Proof (4:35 – 5:00)
- **Screen:** Click **Runs** → Start a new disposable run (Order ID: `disposable-order-99`, Supervisor: `Interview Qwen Supervisor`).
- **Action:**
  - Once the run opens, click **Terminate** in Lifecycle controls.
  - Reason: `Customer canceled order before processing.`
  - Click **Confirm Terminate**.
- **Highlight on Screen:**
  - Status changes to **`Terminated`**.
  - Timeline records termination reason and generates finalization report.
- **Spoken Voiceover:**
  > "We also support clean operator termination. Here on a disposable order, we trigger Terminate with an operator reason. Temporal gracefully cleans up the workflow, records the termination in PostgreSQL, and generates a terminal summary."

---

### 11. Closing & Analytics Overview (5:00 – 5:15)
- **Screen:** Return to the **Overview** dashboard (`/`).
- **Highlight on Screen:** Global counters: Active runs, Completed runs, Terminated runs, Wake suppression rate, and Action distribution.
- **Spoken Voiceover:**
  > "In summary: Temporal guarantees durable orchestration, state, and timers; PostgreSQL provides an auditable read model; and our AI supervisor acts within strict guardrails and allowlisted actions. Thank you!"

---

## Walkthrough Video Timestamp Template

After recording and uploading the video, record the exact timestamps below and update `docs/ACCEPTANCE_MATRIX.md`:

| Requirement | Video Timestamp |
|---|---|
| Creating supervisor config | `0:20` |
| Starting an order run (Temporal Workflow per order) | `0:50` |
| Routine event & wake suppression | `1:20` |
| Important event & AI supervisor wake | `1:45` |
| Business action execution (`message_logistics_team`) | `2:05` |
| Durable sleeping & scheduled wake-up | `2:30` |
| Adding extra instructions to a live run | `2:55` |
| Pausing, Resuming, and Interrupting | `3:20` |
| Deterministic delivered completion | `3:55` |
| Final summary, learnings, and recommendations | `4:15` |
| Terminating a run with reason | `4:35` |
| PostgreSQL read model and analytics | `5:00` |
