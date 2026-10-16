import type { HTMLAttributes } from "react";
import styles from "./Card.module.css";

type CardProps = HTMLAttributes<HTMLDivElement>;

export default function Card({ children, ...props }: CardProps) {
  return (
    <div className={styles.card} {...props}>
      {children}
    </div>
  );
}
