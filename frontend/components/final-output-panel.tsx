import { isFinalOutput } from "@/lib/api/guards";
import type { JsonObject } from "@/lib/api/types";

function OutputList({ items, label }: { items: string[]; label: string }) {
  return (
    <section className="border-t border-[var(--line-soft)] px-5 py-4 first:border-t-0 sm:px-6">
      <h3 className="text-sm font-semibold text-[var(--ink)]">{label}</h3>
      {items.length > 0 ? (
        <ul className="mt-3 space-y-2.5">
          {items.map((item, index) => (
            <li className="grid grid-cols-[1.5rem_minmax(0,1fr)] gap-2 text-sm leading-6 text-[var(--muted)]" key={`${label}-${index}-${item}`}>
              <span aria-hidden="true" className="font-mono text-xs tabular-nums text-[var(--quiet)]">
                {String(index + 1).padStart(2, "0")}
              </span>
              <span>{item}</span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-sm text-[var(--quiet)]">None recorded.</p>
      )}
    </section>
  );
}

export function FinalOutputPanel({ output }: { output: JsonObject | null | undefined }) {
  const isMissing = output === null || output === undefined;
  const isValid = isFinalOutput(output);

  return (
    <section className="panel overflow-hidden" aria-labelledby="final-output-heading">
      <header className="border-b border-[var(--line-soft)] px-5 py-4 sm:px-6">
        <h2
          className="text-lg font-semibold tracking-[-0.02em] text-[var(--ink)]"
          id="final-output-heading"
        >
          Final output
        </h2>
        <p className="mt-1 text-xs leading-5 text-[var(--quiet)]">
          Validated report generated after deterministic lifecycle completion.
        </p>
      </header>

      {!isValid ? (
        <div className="px-5 py-8 sm:px-6">
          <p className="text-sm font-medium text-[var(--ink)]">
            {isMissing ? "Final output is not available yet." : "Final output does not match the expected report format."}
          </p>
          <p className="mt-2 max-w-[62ch] text-sm leading-6 text-[var(--muted)]">
            {isMissing
              ? "The workflow will generate this report when lifecycle rules authorize completion or termination."
              : "The stored value remains intact, but unvalidated report fields are not rendered in the operator console."}
          </p>
        </div>
      ) : (
        <div>
          <section className="px-5 py-5 sm:px-6">
            <h3 className="text-sm font-semibold text-[var(--ink)]">Final summary</h3>
            <p className="mt-2 max-w-[72ch] whitespace-pre-wrap text-sm leading-6 text-[var(--muted)]">
              {output.final_summary}
            </p>
          </section>
          <OutputList items={output.important_actions} label="Important actions" />
          <OutputList items={output.key_learnings} label="Key learnings" />
          <OutputList items={output.recommendations} label="Recommendations" />
        </div>
      )}
    </section>
  );
}
