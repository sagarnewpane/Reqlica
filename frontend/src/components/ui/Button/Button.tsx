import type { ButtonHTMLAttributes } from 'react';
import { Button as ShadcnButton } from '@/components/ui/shadcn/button';
import styles from './Button.module.css';
type ButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  variant?: 'primary' | 'secondary' | 'ghost' | 'danger';
  size?: 'default' | 'icon';
  asChild?: boolean;
};
export default function Button({
  children,
  className,
  variant = 'primary',
  size = 'default',
  asChild,
  ...props
}: ButtonProps) {
  return (
    <ShadcnButton
      asChild={asChild}
      variant={
        variant === 'primary'
          ? 'default'
          : variant === 'secondary'
            ? 'outline'
            : variant === 'danger'
              ? 'destructive'
              : 'ghost'
      }
      size={size}
      className={`${styles.button} ${styles[variant]} ${size === 'icon' ? styles.icon : ''} ${className || ''}`}
      {...props}
    >
      {children}
    </ShadcnButton>
  );
}
