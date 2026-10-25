import type { TableHTMLAttributes } from 'react';
import styles from './Table.module.css';

type TableProps = TableHTMLAttributes<HTMLTableElement>;

export default function Table({ children, ...props }: TableProps) {
  return (
    <div className={styles.tableWrapper}>
      <table className={styles.table} {...props}>
        {children}
      </table>
    </div>
  );
}
