import { isCompactMemory } from "@/lib/api/guards";
import type { JsonObject } from "@/lib/api/types";

function MemoryList({ items, label }: { items: string[]; label: string }) {
  return (
    <div className="border-t border-[var(--line-soft)] px-5 py-4 first:border-t-0 sm:px-6">
      <h3 className="text-xs font-semibold text-[var(--muted)]">{label}</h3>
      {items.length > 0 ? (
        <ul className="mt-2.5 space-y-2">
          {items.map((item, index) => (
            <li className="grid grid-cols-[4px_minmax(0,1fr)] gap-2.5 text-sm leading-5 text-[var(--ink)]" key={`${label}-${index}-${item}`}>
              <span aria-hidden="true" className="mt-2 size-1 rounded-full bg-[var(--accent)]" />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-sm text-[var(--quiet)]">None recorded.</p>
      )}
    </div>
  );
}

export function MemoryPanel({ memory }: { memory: JsonObject | null | undefined }) {
  const isMissing = memory === null || memory === undefined;
  const isValid = isCompactMemory(memory);

  return (
    <section className="panel overflow-hidden" aria-labelledby="memory-panel-heading">
      <header className="border-b border-[var(--line-soft)] px-5 py-4 sm:px-6">
        <h2
          className="text-lg font-semibold tracking-[-0.02em] text-[var(--ink)]"
          id="memory-panel-heading"
        >
          Compact memory
        </h2>
        <p className="mt-1 text-xs leading-5 text-[var(--quiet)]">
          Bounded context retained between supervisor reviews.
        </p>
      </header>

      {!isValid ? (
        <div className="px-5 py-7 sm:px-6">
          <p className="text-sm font-medium text-[var(--ink)]">
            {isMissing ? "Memory has not been written yet." : "Memory is unavailable in the expected compact format."}
          </p>
          <p className="mt-2 text-sm leading-6 text-[var(--muted)]">
            {isMissing
              ? "The first successful supervisor review will create a compact summary here."
              : "The stored value was preserved, but this view will not render unvalidated memory fields."}
          </p>
        </div>
      ) : (
        <>
          <dl className="grid gap-px bg-[var(--line-soft)] sm:grid-cols-2">
            <div className="bg-[var(--panel)] px-5 py-4 sm:px-6">
              <dt className="text-xs text-[var(--quiet)]">Order state</dt>
              <dd className="mt-1.5 text-sm font-medium leading-5 text-[var(--ink)]">
                {memory.order_state}
              </dd>
            </div>
            <div className="bg-[var(--panel)] px-5 py-4 sm:px-6">
              <dt className="text-xs text-[var(--quiet)]">Next review</dt>
              <dd className="mt-1.5 text-sm font-medium leading-5 text-[var(--ink)]">
                {memory.next_review}
              </dd>
            </div>
          </dl>
          <MemoryList items={memory.open_issues} label="Open issues" />
          <MemoryList items={memory.important_facts} label="Important facts" />
          <MemoryList items={memory.actions_taken} label="Actions taken" />
          <MemoryList items={memory.active_constraints} label="Active constraints" />
        </>
      )}
    </section>
  );
}
