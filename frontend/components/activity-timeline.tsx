import type { ReactNode } from "react";

import type { Activity } from "@/lib/api/types";
import { isJsonObject } from "@/lib/api/guards";
import { formatDateTime, humanizeIdentifier } from "@/lib/format";

type ActivityTone = "neutral" | "info" | "success" | "warning" | "danger";

interface ActivityDetail {
  label: string;
  value: ReactNode;
}

const DOT_CLASSES: Record<ActivityTone, string> = {
  neutral: "border-[var(--line)] bg-[var(--quiet)]",
  info: "border-[var(--focus)] bg-[var(--accent)]",
  success: "border-[#89dd98] bg-[var(--positive)]",
  warning: "border-[#f2cb80] bg-[var(--warning)]",
  danger: "border-[#ff9c91] bg-[var(--danger)]",
};

function getActivityTone(activity: Activity): ActivityTone {
  const state = `${activity.activity_type} ${activity.status}`.toLowerCase();

  if (/(failed|rejected|terminated)/.test(state)) {
    return "danger";
  }
  if (/(fallback|paused|interrupted|adjusted|deferred)/.test(state)) {
    return "warning";
  }
  if (/(executed|completed|generated|authorized|validated|updated|resumed)/.test(state)) {
    return "success";
  }
  if (/(wake|sleep|event|instruction|invocation|requested|started)/.test(state)) {
    return "info";
  }

  return "neutral";
}

function readString(payload: Activity["payload"], key: string): string | null {
  const value = payload[key];
  return typeof value === "string" && value.trim() ? value : null;
}

function readNumber(payload: Activity["payload"], key: string): number | null {
  const value = payload[key];
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function getBusinessActionContent(activity: Activity): string | null {
  if (activity.activity_type !== "business_action_executed") {
    return null;
  }

  const argumentsValue = activity.payload.arguments;
  if (!isJsonObject(argumentsValue)) {
    return null;
  }

  const content = argumentsValue.content;
  return typeof content === "string" && content.trim() ? content : null;
}

function getInstructionContent(activity: Activity): string | null {
  if (activity.activity_type !== "instruction_added") {
    return null;
  }

  return readString(activity.payload, "instruction");
}

function getActivityDetails(activity: Activity): ActivityDetail[] {
  const details: ActivityDetail[] = [];
  const payload = activity.payload;
  const trigger = readString(payload, "trigger");
  const urgency = readString(payload, "urgency");
  const failureType = readString(payload, "failure_type");
  const reason = readString(payload, "reason");
  const provider = readString(payload, "provider");
  const model = readString(payload, "model");
  const nextWakeSeconds = readNumber(payload, "next_wake_seconds");
  const scheduledSeconds = readNumber(payload, "scheduled_seconds");
  const requestedSeconds = readNumber(payload, "requested_seconds");

  if (activity.event_name) {
    details.push({ label: "Event", value: humanizeIdentifier(activity.event_name) });
  }
  if (activity.action_name) {
    details.push({
      label: "Action",
      value: (
        <code className="break-all font-mono text-[11px] text-[var(--ink)]">
          {activity.action_name}
        </code>
      ),
    });
  }
  if (trigger) {
    details.push({ label: "Trigger", value: humanizeIdentifier(trigger) });
  }
  if (urgency) {
    details.push({ label: "Urgency", value: humanizeIdentifier(urgency) });
  }
  if (nextWakeSeconds !== null) {
    details.push({ label: "Next review", value: `${nextWakeSeconds}s` });
  } else if (scheduledSeconds !== null) {
    details.push({ label: "Scheduled", value: `${scheduledSeconds}s` });
  }
  if (requestedSeconds !== null) {
    details.push({ label: "Requested", value: `${requestedSeconds}s` });
  }
  if (provider) {
    details.push({
      label: "Model",
      value: model ? `${provider} / ${model}` : provider,
    });
  }
  if (failureType) {
    details.push({ label: "Failure", value: humanizeIdentifier(failureType) });
  }
  if (reason) {
    details.push({ label: "Reason", value: reason });
  }
  if (activity.external_event_id) {
    details.push({ label: "Event ID", value: activity.external_event_id });
  }

  return details;
}

function stableOldestFirst(activities: Activity[]): Activity[] {
  return activities
    .map((activity, index) => ({ activity, index }))
    .sort((left, right) => {
      const leftTimestamp = Date.parse(left.activity.created_at);
      const rightTimestamp = Date.parse(right.activity.created_at);

      if (Number.isNaN(leftTimestamp) || Number.isNaN(rightTimestamp)) {
        return left.index - right.index;
      }

      return leftTimestamp - rightTimestamp || left.index - right.index;
    })
    .map(({ activity }) => activity);
}

export function ActivityTimeline({ activities }: { activities: Activity[] }) {
  const orderedActivities = stableOldestFirst(activities);

  return (
    <section className="panel overflow-hidden" aria-labelledby="activity-timeline-heading">
      <header className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--line-soft)] px-5 py-4 sm:px-6">
        <div>
          <h2
            className="text-lg font-semibold tracking-[-0.02em] text-[var(--ink)]"
            id="activity-timeline-heading"
          >
            Persistent timeline
          </h2>
          <p className="mt-1 text-xs leading-5 text-[var(--quiet)]">
            Oldest activity first. Every row comes from the PostgreSQL audit trail.
          </p>
        </div>
        <p className="font-mono text-xs tabular-nums text-[var(--muted)]">
          {orderedActivities.length} {orderedActivities.length === 1 ? "entry" : "entries"}
        </p>
      </header>

      {orderedActivities.length === 0 ? (
        <div className="px-5 py-10 text-center sm:px-6">
          <p className="text-sm font-medium text-[var(--ink)]">No activity recorded yet.</p>
          <p className="mx-auto mt-2 max-w-[52ch] text-sm leading-6 text-[var(--muted)]">
            Persisted events, supervisor decisions, business actions, and lifecycle changes
            will appear here as the workflow advances.
          </p>
        </div>
      ) : (
        <ol className="relative divide-y divide-[var(--line-soft)]" aria-label="Run activity, oldest first">
          {orderedActivities.map((activity) => {
            const details = getActivityDetails(activity);
            const businessActionContent = getBusinessActionContent(activity);
            const instructionContent = getInstructionContent(activity);
            const tone = getActivityTone(activity);

            return (
              <li className="relative grid gap-3 px-5 py-5 pl-11 sm:grid-cols-[minmax(0,1fr)_auto] sm:px-6 sm:pl-12" key={activity.id}>
                <span
                  aria-hidden="true"
                  className={`absolute left-5 top-6 size-3 rounded-full border-2 sm:left-6 ${DOT_CLASSES[tone]}`}
                />
                <div className="min-w-0">
                  <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
                    <h3 className="text-sm font-semibold text-[var(--ink)]">
                      {humanizeIdentifier(activity.activity_type)}
                    </h3>
                    <span className="text-xs text-[var(--quiet)]">
                      {humanizeIdentifier(activity.source)}
                    </span>
                  </div>
                  <p className="mt-2 max-w-[72ch] text-sm leading-6 text-[var(--muted)]">
                    {activity.summary}
                  </p>

                  {details.length > 0 ? (
                    <dl className="mt-3 flex flex-wrap gap-x-5 gap-y-2">
                      {details.map((detail) => (
                        <div className="flex min-w-0 items-baseline gap-1.5 text-xs" key={`${activity.id}-${detail.label}`}>
                          <dt className="shrink-0 text-[var(--quiet)]">{detail.label}</dt>
                          <dd className="min-w-0 break-words font-medium text-[var(--muted)]">
                            {detail.value}
                          </dd>
                        </div>
                      ))}
                    </dl>
                  ) : null}

                  {businessActionContent || instructionContent ? (
                    <p className="mt-3 max-w-[72ch] rounded-lg bg-[var(--surface-strong)] px-3.5 py-3 text-sm leading-6 text-[var(--ink)]">
                      <span className="mr-2 text-xs font-semibold text-[var(--quiet)]">
                        {instructionContent ? "Instruction" : "Content"}
                      </span>
                      {instructionContent ?? businessActionContent}
                    </p>
                  ) : null}
                </div>

                <div className="flex items-start gap-3 sm:flex-col sm:items-end sm:gap-1">
                  <span className="text-xs font-medium text-[var(--muted)]">
                    {humanizeIdentifier(activity.status)}
                  </span>
                  <time
                    className="font-mono text-[11px] tabular-nums text-[var(--quiet)]"
                    dateTime={activity.created_at}
                  >
                    {formatDateTime(activity.created_at)}
                  </time>
                </div>
              </li>
            );
          })}
        </ol>
      )}
    </section>
  );
}
