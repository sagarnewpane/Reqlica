import type { SelectHTMLAttributes } from "react";
import styles from "./Select.module.css";

type SelectProps = SelectHTMLAttributes<HTMLSelectElement>;

export default function Select({ children, ...props }: SelectProps) {
  return (
    <select className={styles.select} {...props}>
      {children}
    </select>
  );
}
