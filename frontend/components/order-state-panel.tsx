import { isCurrentOrderState } from "@/lib/api/guards";
import type { JsonObject } from "@/lib/api/types";
import { humanizeIdentifier } from "@/lib/format";

const ATTRIBUTE_LIMIT = 12;

function formatAttributeValue(value: unknown): string {
  if (value === null) {
    return "None";
  }
  if (typeof value === "string") {
    return value || "Empty";
  }
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  if (Array.isArray(value)) {
    return `${value.length} ${value.length === 1 ? "item" : "items"}`;
  }
  if (typeof value === "object") {
    const fieldCount = Object.keys(value).length;
    return `${fieldCount} ${fieldCount === 1 ? "field" : "fields"}`;
  }

  return "Unavailable";
}

export function OrderStatePanel({ state }: { state: JsonObject | null | undefined }) {
  const isMissing = state === null || state === undefined;
  const isValid = isCurrentOrderState(state);
  const attributes = isValid ? Object.entries(state.attributes).slice(0, ATTRIBUTE_LIMIT) : [];
  const hiddenAttributeCount = isValid
    ? Math.max(Object.keys(state.attributes).length - ATTRIBUTE_LIMIT, 0)
    : 0;

  return (
    <section className="panel overflow-hidden" aria-labelledby="order-state-heading">
      <header className="border-b border-[var(--line-soft)] px-5 py-4 sm:px-6">
        <h2
          className="text-lg font-semibold tracking-[-0.02em] text-[var(--ink)]"
          id="order-state-heading"
        >
          Current order state
        </h2>
        <p className="mt-1 text-xs leading-5 text-[var(--quiet)]">
          Workflow-owned state after the latest accepted event.
        </p>
      </header>

      {!isValid ? (
        <div className="px-5 py-7 sm:px-6">
          <p className="text-sm font-medium text-[var(--ink)]">
            {isMissing ? "Order state is not available yet." : "Order state does not match the expected workflow shape."}
          </p>
          <p className="mt-2 text-sm leading-6 text-[var(--muted)]">
            {isMissing
              ? "The persisted state will appear after the run has initialized."
              : "The raw value was preserved, but this panel only displays validated workflow state."}
          </p>
        </div>
      ) : (
        <>
          <dl className="grid gap-px bg-[var(--line-soft)] sm:grid-cols-2">
            {[
              ["Lifecycle", state.lifecycle],
              ["Payment", state.payment],
              ["Shipment", state.shipment],
              ["Refund", state.refund],
            ].map(([label, value]) => (
              <div className="bg-[var(--panel)] px-5 py-3.5 sm:px-6" key={label}>
                <dt className="text-xs text-[var(--quiet)]">{label}</dt>
                <dd className="mt-1 text-sm font-semibold text-[var(--ink)]">{value}</dd>
              </div>
            ))}
          </dl>

          <dl className="grid gap-px border-t border-[var(--line-soft)] bg-[var(--line-soft)] sm:grid-cols-2">
            <div className="bg-[var(--surface-strong)] px-5 py-3.5 sm:px-6">
              <dt className="text-xs text-[var(--quiet)]">Last event</dt>
              <dd className="mt-1 text-sm text-[var(--ink)]">
                {state.last_event_type ? humanizeIdentifier(state.last_event_type) : "None received"}
              </dd>
            </div>
            <div className="bg-[var(--surface-strong)] px-5 py-3.5 sm:px-6">
              <dt className="text-xs text-[var(--quiet)]">Events applied</dt>
              <dd className="mt-1 font-mono text-sm tabular-nums text-[var(--ink)]">
                {state.event_count}
              </dd>
            </div>
          </dl>

          <div className="border-t border-[var(--line-soft)] px-5 py-4 sm:px-6">
            <h3 className="text-xs font-semibold text-[var(--muted)]">Attributes</h3>
            {attributes.length > 0 ? (
              <dl className="mt-3 grid gap-x-5 gap-y-3 sm:grid-cols-2">
                {attributes.map(([key, value]) => (
                  <div className="min-w-0" key={key}>
                    <dt className="truncate text-xs text-[var(--quiet)]" title={key}>
                      {humanizeIdentifier(key)}
                    </dt>
                    <dd className="mt-0.5 break-words text-sm text-[var(--ink)]">
                      {formatAttributeValue(value)}
                    </dd>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="mt-2 text-sm text-[var(--quiet)]">No additional attributes.</p>
            )}
            {hiddenAttributeCount > 0 ? (
              <p className="mt-3 text-xs text-[var(--quiet)]">
                {hiddenAttributeCount} more {hiddenAttributeCount === 1 ? "attribute" : "attributes"} retained in workflow state.
              </p>
            ) : null}
          </div>
        </>
      )}
    </section>
  );
}
