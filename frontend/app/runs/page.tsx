import type { Metadata } from "next";

import { RunsWorkspace } from "@/components/runs-workspace";

export const metadata: Metadata = {
  title: "Runs",
  description: "Start and inspect durable order supervision runs.",
};

export default function RunsPage() {
  return <RunsWorkspace />;
}
