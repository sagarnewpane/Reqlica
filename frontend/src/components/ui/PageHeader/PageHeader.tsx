import type { ReactNode } from 'react';
import styles from './PageHeader.module.css';

type Props = {
  title: ReactNode;
  metadata?: ReactNode;
  actions?: ReactNode;
  description?: ReactNode;
  id?: string;
  className?: string;
};

export default function PageHeader({
  title,
  metadata,
  actions,
  description,
  id,
  className = '',
}: Props) {
  return (
    <header className={`${styles.header} ${className}`}>
      <div className={styles.identity}>
        <div className={styles.titleRow}>
          <h1 id={id} className={styles.title}>
            {title}
          </h1>
          {metadata && <div className={styles.metadata}>{metadata}</div>}
        </div>
        {description && <p className={styles.description}>{description}</p>}
      </div>
      {actions && <div className={styles.actions}>{actions}</div>}
    </header>
  );
}
