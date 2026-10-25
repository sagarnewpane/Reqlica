import type { HTMLAttributes } from 'react';
import { Badge as ShadcnBadge } from '@/components/ui/shadcn/badge';
import styles from './Badge.module.css';

type BadgeVariant = 'success' | 'warning' | 'danger';

type BadgeProps = HTMLAttributes<HTMLSpanElement> & {
  variant?: BadgeVariant;
};

export default function Badge({
  children,
  variant = 'success',
  className,
  ...props
}: BadgeProps) {
  return (
    <ShadcnBadge
      variant="outline"
      className={`${styles.badge} ${styles[variant]} ${className || ''}`}
      {...props}
    >
      {children}
    </ShadcnBadge>
  );
}
