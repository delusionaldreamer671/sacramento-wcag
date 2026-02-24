import type { Metadata } from "next";
import { Inter } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

export const metadata: Metadata = {
  title: "WCAG Remediation Dashboard - Sacramento County",
  description:
    "Human-in-the-loop review dashboard for WCAG 2.1 AA PDF remediation. Review and approve AI-generated accessibility fixes for Sacramento County documents.",
  robots: { index: false, follow: false },
};

const navLinks = [
  { href: "/", label: "Queue" },
  { href: "/upload", label: "Upload" },
  { href: "/issues", label: "Issues" },
  { href: "/admin/rules", label: "Rules" },
];

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={inter.variable}>
      <body className="min-h-screen bg-background font-sans antialiased">
        {/* Skip-to-content link — keyboard navigation WCAG 2.4.1 */}
        <a href="#main-content" className="skip-link">
          Skip to main content
        </a>

        <div className="relative flex min-h-screen flex-col">
          {/* Gold accent bar at the very top */}
          <div className="h-1 w-full bg-sac-gold" aria-hidden="true" />

          {/* Site-wide header */}
          <header
            role="banner"
            className="sticky top-0 z-40 w-full border-b border-sac-navy/10 bg-sac-navy shadow-sac-md"
          >
            <div className="container mx-auto flex h-16 max-w-screen-xl items-center justify-between px-4 sm:px-6">
              {/* Brand mark */}
              <div className="flex items-center gap-3">
                {/* Sacramento County seal */}
                <div
                  aria-hidden="true"
                  className="flex h-9 w-9 items-center justify-center rounded-full border-2 border-sac-gold bg-sac-gold/10 text-xs font-bold text-sac-gold"
                >
                  SC
                </div>
                <div className="flex flex-col leading-tight">
                  <span className="text-sm font-bold tracking-wide text-white">
                    Sacramento County
                  </span>
                  <span className="text-[11px] font-medium text-sac-blue">
                    WCAG Document Remediation
                  </span>
                </div>
              </div>

              {/* Primary navigation */}
              <nav aria-label="Primary navigation" className="flex items-center gap-1">
                {navLinks.map(({ href, label }) => (
                  <Link
                    key={href}
                    href={href}
                    className="rounded-md px-3 py-2 text-sm font-medium text-white/70 transition-colors hover:bg-white/10 hover:text-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-sac-gold focus-visible:ring-offset-2 focus-visible:ring-offset-sac-navy"
                  >
                    {label}
                  </Link>
                ))}
              </nav>
            </div>
          </header>

          {/* Main content area */}
          <main
            id="main-content"
            role="main"
            className="flex-1"
            tabIndex={-1}
          >
            {children}
          </main>

          {/* Footer */}
          <footer
            role="contentinfo"
            className="border-t border-border bg-sac-navy py-6"
          >
            <div className="container mx-auto max-w-screen-xl px-4 sm:px-6">
              <div className="flex flex-col items-center justify-between gap-2 sm:flex-row">
                <div className="flex items-center gap-2">
                  <div
                    aria-hidden="true"
                    className="flex h-6 w-6 items-center justify-center rounded-full border border-sac-gold/50 text-[8px] font-bold text-sac-gold"
                  >
                    SC
                  </div>
                  <span className="text-xs font-medium text-white/80">
                    Sacramento County
                  </span>
                </div>
                <p className="text-xs text-white/50">
                  WCAG 2.1 AA Document Remediation Pipeline &middot; Powered by BridgeAI
                </p>
              </div>
            </div>
          </footer>
        </div>
      </body>
    </html>
  );
}
