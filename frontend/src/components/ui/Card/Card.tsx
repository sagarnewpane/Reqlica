import type { HTMLAttributes } from 'react';
import { Card as ShadcnCard } from '@/components/ui/shadcn/card';
import styles from './Card.module.css';
export default function Card({
  children,
  className,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <ShadcnCard className={`${styles.card} ${className || ''}`} {...props}>
      {children}
    </ShadcnCard>
  );
}
