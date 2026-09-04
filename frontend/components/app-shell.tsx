import type { ReactNode } from "react";

import { NavLinks } from "@/components/nav-links";

export function AppShell({ children }: { children: ReactNode }) {
  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">
        Skip to main content
      </a>
      <aside className="app-rail r-navigation-rail" aria-label="Primary navigation">
        <div className="app-brand r-brand">
          <span>Order</span>
          <span>Supervisor</span>
        </div>
        <NavLinks />
        <p className="environment-note r-environment-note">
          <span>Local POC</span>
          <span>No authentication</span>
        </p>
      </aside>
      <main className="app-stage" id="main-content" tabIndex={-1}>{children}</main>
    </div>
  );
}
