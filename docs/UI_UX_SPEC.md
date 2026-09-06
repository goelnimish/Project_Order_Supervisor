# UI/UX Specification: Operator Console & Design System

## 1. Design Philosophy: High-Density Operational Ergonomics

The Order Supervisor console is designed for **incident triage under time pressure**, not passive browsing. Frontline operations teams manage hundreds of order exceptions daily. Every pixel must serve operational clarity:

* **High Information Density:** Fast scanning beats excessive whitespace. Key order attributes, statuses, and recent actions must be visible above the fold without scrolling.
* **Instant State Comprehension:** Color-coded status badges, countdown wake timers, and explicit blocked-state banners allow operators to evaluate an order's health in under 2 seconds.
* **Explicit Control Affordance:** Critical lifecycle actions (Pause, Interrupt, Graceful Terminate) require clear visual differentiation, dedicated confirmation prompts, and mandatory reason documentation.
* **Zero Decorative Clutter:** No gratuitous illustrations, animations, or vanity metric cards. Visual elements are strictly functional.

---

## 2. Information Architecture & Navigation

The Next.js App Router application organizes functionality into three core workspaces:

```text
Operator Console Layout
├── Global Navigation Header (Logo, Active Run Counter, Health Status, Navigation Links)
│
├── /supervisors (Supervisors Workspace)
│   ├── Active Configurations Table (Name, Model, Wake Aggressiveness, Created Date)
│   └── Create / Edit Configuration Drawer (Prompt editor, action allowlist, provider selection)
│
├── /runs (Runs Monitor)
│   ├── Analytics Overview Strip (Active Runs, Sleeping Runs, Interrupted Triage, MTTR)
│   ├── Active Supervised Orders Table (Order ID, Status, Last Event, Next Wake, Direct Link)
│   └── Historical / Closed Orders Table (Order ID, Terminal Reason, Completion Time)
│
└── /runs/[id] (Run Operational Command Center)
    ├── Run Header Bar (Order ID, Supervisor Name, Status Badge, Timer Pill, Mode Indicator)
    ├── Left Column: Order Context & Memory
    │   ├── Order State Panel (Lifecycle, Payment, Shipment, Refund status badges)
    │   └── Compact Memory Panel (Rolling facts, open issues, actions taken, constraints)
    ├── Center Column: Auditable Activity Timeline
    │   ├── Timeline Stream (Reverse chronological events, wake decisions, actions, notes)
    │   └── Activity Detail Drawer / JSON Inspector (Raw payload evidence)
    └── Right Column: Operator Controls & Action Tray
        ├── Lifecycle Controls Card (Pause, Interrupt, Resume, Graceful Terminate buttons)
        ├── Live Instruction Form (Steering guidance input + retained instruction history)
        ├── External Event Injector (Dropdown selector, JSON payload builder, submit trigger)
        └── Final Output Panel (Renders on completion: Summary, Actions, Learnings, Next Steps)
```

---

## 3. Status Badge System & Semantic Color Tokens

Every workflow state maps to an unambiguous visual token across badges, border highlights, and text labels:

```text
+---------------+---------------+-----------------------+---------------------------------------+
| State         | Badge Color   | Tailwind Classes      | Operational Meaning                   |
+---------------+---------------+-----------------------+---------------------------------------+
| STARTING      | Cyan / Blue   | bg-blue-100 text-blue-800      | Initializing workflow & first review. |
| RUNNING       | Emerald Green | bg-emerald-100 text-emerald-800| Actively processing an event/action.  |
| SLEEPING      | Indigo / Slate| bg-slate-100 text-slate-800    | Durably waiting for timer or signal.  |
| PAUSED        | Amber / Yellow| bg-amber-100 text-amber-800    | Automated wake frozen; signals queue. |
| INTERRUPTED   | Crimson / Red | bg-rose-100 text-rose-800      | Urgent human investigation required.  |
| COMPLETED     | Slate / Neutral| bg-zinc-100 text-zinc-800      | Successfully finalized (Delivered).   |
| TERMINATED    | Purple / Gray | bg-purple-100 text-purple-800  | Manually closed with documented reason|
+---------------+---------------+-----------------------+---------------------------------------+
```

---

## 4. Component Deep-Dive & Interaction Specifications

### 4.1 Order State Panel
* **Component File:** `frontend/components/order-state-panel.tsx`
* **Purpose:** Displays the deterministic projection of order progress extracted from incoming signals.
* **Fields Rendered:**
  * `Lifecycle`: `open` $\to$ `in_fulfillment` $\to$ `shipped` $\to$ `delivered` $\to$ `closed`.
  * `Payment`: `pending` $\to$ `confirmed` / `failed`.
  * `Shipment`: `not_created` $\to$ `in_transit` $\to$ `delayed` $\to$ `delivered`.
  * `Refund`: `not_requested` $\to$ `requested` $\to$ `processed`.
* **Visual Treatment:** A 4-grid tile layout with uppercase micro-labels and colored indicator pills.

### 4.2 Compact Memory Panel
* **Component File:** `frontend/components/memory-panel.tsx`
* **Purpose:** Visualizes the rolling semantic context passed into the AI supervisor, preventing operators from having to decipher raw JSON.
* **Sections:**
  1. **Current Assessment:** 1-sentence synthesis of order situation.
  2. **Important Facts:** Bulleted list of confirmed operational truths (e.g., *"Package delayed at Chicago hub due to snowstorm"*).
  3. **Open Issues:** Unresolved exception flags (e.g., *"Awaiting logistics team update on scan SLA"*).
  4. **Actions Dispatched:** Historical record of actions taken by the supervisor.
  5. **Active Constraints:** Operator-injected steering rules currently in effect.

### 4.3 Unified Activity Timeline
* **Component File:** `frontend/components/activity-timeline.tsx`
* **Purpose:** The auditable chronological source of operational record.
* **Visual Hierarchy:**
  * Left rail with vertical connector lines.
  * Distinct icon and header for each activity type:
    * `order_event_received`: Blue ingress badge with event type.
    * `supervisor_wake_suppressed`: Muted slate badge indicating cost-saving routine skip.
    * `supervisor_decision`: Violet badge displaying model reasoning summary.
    * `business_action_executed`: Green badge displaying targeted department and simulation payload.
    * `instruction_added`: Orange badge displaying operator name and guidance.
    * `workflow_paused` / `workflow_interrupted`: High-contrast warning flags.
  * **Payload Expand/Collapse:** Each entry features a collapsible JSON accordion showing the exact data payload and idempotency key for forensic auditing.

### 4.4 Operator Action & Control Trays
* **Component Files:**
  * `frontend/components/lifecycle-controls.tsx`
  * `frontend/components/instruction-form.tsx`
  * `frontend/components/event-injector.tsx`
* **Interaction Rules:**
  * **Pending States & Optimistic Indicators:** When an operator clicks `Pause`, `Resume`, or `Inject Event`, the button immediately enters a disabled spinning state. It remains disabled until the corresponding signal receipt is verified in the timeline query response.
  * **Modal Confirmations for Destructive Actions:** Clicking `Interrupt` or `Terminate` opens a dedicated dialog requiring the operator to enter a mandatory reason before submission. The submit button remains disabled if the reason field has fewer than 5 characters.
  * **Dynamic Action Availability:** If a run is in `COMPLETED` or `TERMINATED` status, all injection forms and lifecycle buttons are permanently disabled, rendering an explicit badge: *"Run Closed — Controls Locked"*.

### 4.5 Final Output Panel
* **Component File:** `frontend/components/final-output-panel.tsx`
* **Purpose:** Renders the 4-quadrant post-order synthesis generated by the final output activity.
* **Layout:**
  * **Top-Left:** **Final Summary** (Narrative summary of the entire lifecycle).
  * **Top-Right:** **Important Actions Taken** (Bulleted list of key interventions).
  * **Bottom-Left:** **Key Learnings** (Root cause analysis of delays or exceptions).
  * **Bottom-Right:** **Recommendations** (Actionable advice for vendor reviews or policy tweaks).
* **State Behavior:** Displays a polite placeholder card while the run is active (*"Final synthesis will generate upon delivery or graceful termination"*), transitioning to full structured display when finalization completes.

---

## 5. Polling, Revalidation & Latency Ergonomics

Because Order Supervisor utilizes a CQRS architecture, there is a small sub-second window of eventual consistency between a Temporal Signal dispatch and its projection into the PostgreSQL read model.

### Polling Cadence
The frontend client (`frontend/lib/api/runs.ts`) implements adaptive interval polling:
* **Active Running State (`RUNNING`, `STARTING`):** Polls every **1,500ms** to capture fast action sequences.
* **Sleeping State (`SLEEPING`, `PAUSED`, `INTERRUPTED`):** Backs off to **5,000ms** to minimize network traffic and server CPU.
* **Terminal State (`COMPLETED`, `TERMINATED`):** **Zero polling**. Polling timers are cancelled immediately upon detecting a terminal status badge.

### Error Boundaries & Toast Notifications
* Network disconnects or API 500s trigger unobtrusive floating amber warning banners (*"Live sync delayed — retrying..."*) rather than crashing the workspace layout.
* Successful event injections and instruction submissions emit transient 3-second green confirmation toasts.
