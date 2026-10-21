import Link from "next/link";
import { useRouter } from "next/router";
import type { ReactNode } from "react";
import styles from "./DashboardLayout.module.css";

type DashboardLayoutProps = {
  children: ReactNode;
  title: string;
};

const menuItems = [
  { label: "Projects", href: "/" },
  { label: "Generate API", href: "/generate" },
  { label: "Draft Review", href: "/draft-review" },
  { label: "Endpoints", href: "/endpoints" },
  { label: "Request Tester", href: "/request-tester" },
  { label: "Sample Data", href: "/sample-data" },
  { label: "Request Logs", href: "/request-logs" },
  { label: "Settings", href: "/settings" },
];

export default function DashboardLayout({
  children,
  title,
}: DashboardLayoutProps) {
  const router = useRouter();

  return (
    <div className={styles.dashboard}>
      <aside className={styles.sidebar}>
        <h2 className={styles.logo}>Reqlica</h2>

        <p className={styles.menuTitle}>WORKSPACE</p>

        <nav className={styles.navigation}>
          {menuItems.map((item) => (
            <Link
              key={item.href}
              href={item.href}
              className={
                router.pathname === item.href ? styles.active : styles.navLink
              }
            >
              {item.label}
            </Link>
          ))}
        </nav>
      </aside>

      <main className={styles.main}>
        <header className={styles.header}>
          <span>Workspace / {title}</span>
          <span>Reqlica Console</span>
        </header>

        {children}
      </main>
    </div>
  );
}
