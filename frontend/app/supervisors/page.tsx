import type { Metadata } from "next";

import { SupervisorsWorkspace } from "@/components/supervisors-workspace";

export const metadata: Metadata = {
  title: "Supervisors",
  description: "Create and select durable order supervisor configurations.",
};

export default function SupervisorsPage() {
  return <SupervisorsWorkspace />;
}
