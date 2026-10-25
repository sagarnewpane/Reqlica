import * as Primitive from '@radix-ui/react-switch';
import type { ComponentProps } from 'react';
import styles from './Switch.module.css';
export default function Switch(props: ComponentProps<typeof Primitive.Root>) {
  return (
    <Primitive.Root {...props} className={styles.root}>
      <Primitive.Thumb className={styles.thumb} />
    </Primitive.Root>
  );
}
