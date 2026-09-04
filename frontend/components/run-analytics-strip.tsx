import type { RunAnalytics } from "@/lib/api/types";
import { formatCount, formatDuration } from "@/lib/format";

interface RunAnalyticsStripProps {
  analytics: RunAnalytics | null;
  isLoading?: boolean;
  error?: string | null;
}

export function RunAnalyticsStrip({
  analytics,
  isLoading = false,
  error = null,
}: RunAnalyticsStripProps) {
  const metrics = [
    {
      label: "Duration",
      value: analytics ? formatDuration(analytics.duration_seconds) : "Not available",
    },
    {
      label: "Events Received",
      value: analytics ? formatCount(analytics.events_received) : "Not available",
    },
    {
      label: "AI Supervisor Invocations",
      value: analytics ? formatCount(analytics.supervisor_invocations) : "Not available",
    },
    {
      label: "Wake Suppressions",
      value: analytics ? formatCount(analytics.wake_suppressions) : "Not available",
    },
    {
      label: "Business Actions",
      value: analytics ? formatCount(analytics.business_actions_executed) : "Not available",
    },
  ];

  return (
    <section className="panel overflow-hidden" aria-labelledby="run-analytics-heading" aria-busy={isLoading}>
      <header className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--line-soft)] px-5 py-3.5 sm:px-6">
        <h2 className="text-sm font-semibold text-[var(--ink)]" id="run-analytics-heading">
          Run analytics
        </h2>
        <p className="text-xs text-[var(--quiet)]">Persisted evidence</p>
      </header>

      {isLoading && !analytics ? (
        <div className="grid gap-px bg-[var(--line-soft)] sm:grid-cols-2 lg:grid-cols-5" role="status">
          <span className="sr-only">Loading run analytics.</span>
          {metrics.map((metric) => (
            <div className="bg-[var(--panel)] px-5 py-4" key={metric.label}>
              <span aria-hidden="true" className="block h-3 w-24 animate-pulse rounded bg-white/[0.08]" />
              <span aria-hidden="true" className="mt-3 block h-5 w-16 animate-pulse rounded bg-white/10" />
            </div>
          ))}
        </div>
      ) : (
        <dl className="grid gap-px bg-[var(--line-soft)] sm:grid-cols-2 lg:grid-cols-5">
          {metrics.map((metric) => (
            <div className="bg-[var(--panel)] px-5 py-4" key={metric.label}>
              <dt className="text-xs leading-5 text-[var(--quiet)]">{metric.label}</dt>
              <dd className="mt-1 font-mono text-base font-semibold tabular-nums text-[var(--ink)]">
                {metric.value}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {error ? (
        <p className="border-t border-[color:color-mix(in_srgb,var(--danger)_32%,transparent)] bg-[color:color-mix(in_srgb,var(--danger)_9%,transparent)] px-5 py-3 text-xs leading-5 text-[#ffc4bd]" role="status">
          Analytics are temporarily unavailable. {error}
        </p>
      ) : null}
    </section>
  );
}
