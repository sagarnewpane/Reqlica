import Select from '@/components/ui/Select/Select';
import {
  tableRestriction,
  type DatabaseSchema,
  type DatabaseTable,
  type DataMode,
} from '@/lib/sample-data';
import styles from './SampleData.module.css';

function TableDefinition({
  table,
  schema,
}: {
  table: DatabaseTable;
  schema: DatabaseSchema;
}) {
  const restriction = tableRestriction(table, schema);
  return (
    <details className={styles.definition}>
      <summary>
        <code>{table.name}</code> · {table.columns.length} columns
        {restriction ? ' · Unsupported for insertion' : ''}
      </summary>
      {restriction && <p className={styles.warning}>{restriction}</p>}
      <div className={styles.tableWrapper}>
        <table className={styles.table}>
          <caption className={styles.srOnly}>Columns in {table.name}</caption>
          <thead>
            <tr>
              <th scope="col">Column</th>
              <th scope="col">Type</th>
              <th scope="col">Rules</th>
            </tr>
          </thead>
          <tbody>
            {table.columns.map((column) => (
              <tr key={column.name}>
                <td>
                  <code>{column.name}</code>
                </td>
                <td>{column.type || 'Unspecified'}</td>
                <td>
                  {[
                    column.pk ? 'Primary key' : '',
                    column.notnull ? 'Not null' : '',
                    column.hidden ? 'Generated / not writable' : '',
                    column.dflt_value != null
                      ? `Default: ${String(column.dflt_value)}`
                      : '',
                  ]
                    .filter(Boolean)
                    .join(' · ') || '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {table.foreign_keys.length > 0 && (
        <div className={styles.relationships}>
          <h3>Foreign keys</h3>
          <ul>
            {table.foreign_keys.map((key, index) => (
              <li key={index}>
                <code>
                  {key.from} → {key.table}.{key.to || '(primary key)'}
                </code>
              </li>
            ))}
          </ul>
        </div>
      )}
    </details>
  );
}

export default function DatabaseDefinitions({
  schema,
  mode,
  table,
  disabled,
  onTableChange,
}: {
  schema: DatabaseSchema;
  mode: DataMode;
  table: string;
  disabled: boolean;
  onTableChange: (table: string) => void;
}) {
  return (
    <>
      <p className={styles.context}>
        Definitions only; no existing rows are loaded. Automatic integer primary
        keys can usually be omitted. The backend rechecks writable columns and
        application constraints.
      </p>
      {!schema.tables.length && (
        <p className={styles.warning}>
          No existing tables. Use JSON fixtures or complete database setup with
          backend management tools before reloading definitions.
        </p>
      )}
      {mode === 'sqlite' && (
        <div className={styles.field}>
          <label htmlFor="data-table">Destination table</label>
          <Select
            id="data-table"
            value={table}
            disabled={disabled || !schema.tables.length}
            onValueChange={onTableChange}
          >
            <option value="" disabled>
              Select a supported table…
            </option>
            {schema.tables.map((item) => (
              <option
                key={item.name}
                value={item.name}
                disabled={!!tableRestriction(item, schema)}
              >
                {item.name}
                {tableRestriction(item, schema) ? ' · Unsupported' : ''}
              </option>
            ))}
          </Select>
        </div>
      )}
      <div className={styles.definitions}>
        {(mode === 'sqlite'
          ? schema.tables.filter((item) => item.name === table)
          : schema.tables
        ).map((item) => (
          <TableDefinition key={item.name} table={item} schema={schema} />
        ))}
      </div>
      <details className={styles.definition}>
        <summary>Schema fingerprint</summary>
        <code className={styles.digest}>{schema.fingerprint}</code>
      </details>
    </>
  );
}
