import type { InputHTMLAttributes } from 'react';
import { Input as ShadcnInput } from '@/components/ui/shadcn/input';
import styles from './Input.module.css';
export default function Input({
  className,
  ...props
}: InputHTMLAttributes<HTMLInputElement>) {
  return (
    <ShadcnInput className={`${styles.input} ${className || ''}`} {...props} />
  );
}
