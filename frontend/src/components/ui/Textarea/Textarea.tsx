import type { TextareaHTMLAttributes } from "react";
import styles from "./Textarea.module.css";

type TextareaProps = TextareaHTMLAttributes<HTMLTextAreaElement>;

export default function Textarea({ children, ...props }: TextareaProps) {
  return (
    <textarea className={styles.textarea} {...props}>
      {children}
    </textarea>
  );
}
