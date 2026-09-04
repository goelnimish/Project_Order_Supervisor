"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const links = [
  { href: "/", label: "Overview", marker: "overview" },
  { href: "/supervisors", label: "Supervisors", marker: "supervisor" },
  { href: "/runs", label: "Runs", marker: "runs" },
] as const;

function isCurrent(pathname: string, href: string) {
  if (href === "/") return pathname === href;
  return pathname === href || pathname.startsWith(`${href}/`);
}

export function NavLinks() {
  const pathname = usePathname();

  return (
    <nav className="nav-links">
      {links.map((link) => {
        const current = isCurrent(pathname, link.href);
        return (
          <Link
            aria-current={current ? "page" : undefined}
            className={`nav-link ${current ? "nav-link-active" : ""}`}
            href={link.href}
            key={link.href}
          >
            <span className={`nav-marker nav-marker-${link.marker}`} aria-hidden="true">
              {link.marker === "overview" ? (
                <><span /><span /><span /><span /></>
              ) : null}
            </span>
            <span>{link.label}</span>
          </Link>
        );
      })}
    </nav>
  );
}
