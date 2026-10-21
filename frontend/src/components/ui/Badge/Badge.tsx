import type { HTMLAttributes } from "react";
import styles from "./Badge.module.css";

type BadgeVariant = "success" | "warning" | "danger";

type BadgeProps = HTMLAttributes<HTMLSpanElement> & {
  variant?: BadgeVariant;
};

export default function Badge({
  children,
  variant = "success",
  ...props
}: BadgeProps) {
  return (
    <span className={`${styles.badge} ${styles[variant]}`} {...props}>
      {children}
    </span>
  );
}
