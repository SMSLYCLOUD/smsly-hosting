# fix_sequences — Debug / Manual Use Only

> **STATUS: REMOVED FROM AUTOMATED UPDATE FLOW**
> This command is no longer run automatically during `install.sh --update` /
> fresh install. It caused the update to hang silently in production because
> the sequence-sync loop can block on table/sequence locks. It is kept here as
> a manual recovery tool for specific operational events.

## What it does

Resyncs PostgreSQL auto-increment **sequences** to the actual maximum ID in
each owning table. PostgreSQL tracks the "next ID to assign" in a separate
sequence object (like `AUTO_INCREMENT`). If a sequence drifts out of sync, the
next `INSERT` fails with:

```
duplicate key value violates unique constraint "..."

```

The command:
1. Queries `pg_class` / `pg_depend` to find every sequence and its `(table, column)`
2. For each: `SELECT max(column) FROM table`
3. If the table has rows → `setval(seq, max_val)` (sync counter to current max)
4. If empty → `setval(seq, 1, false)` (reset to 1, marked "not yet used")

It is **idempotent and read-mostly** — it only writes `setval()`, never user data.

## When you ACTUALLY need it

Run this manually ONLY after one of these events:

| Event | Why sequences drift |
|-------|---------------------|
| **Database restore / backup restore** | Restores often reset sequences to 1 |
| **Bulk data import** (raw `INSERT`, `COPY`, csv load) | Rows inserted without advancing the sequence |
| **Replica promotion / failover** | Old primary's sequence state lost |
| **Manual SQL schema changes** outside migrations | Hand-run inserts skip the sequence |
| **pg_dump / pg_restore** with `--data-only` | Data restored but sequences not bumped |

## When you do NOT need it

- Healthy production flow with no manual data ops — sequences stay in sync
  automatically via normal `INSERT`s
- Every routine update — running it on a healthy DB is redundant work
- After a normal `manage.py migrate` — migrations that create tables also
  create correctly-initialized sequences

## How to run it

The file is a Django management command. To use it, copy it back into the
commands directory, then run it:

```bash
# 1. Restore the command into the Django app
cp debug/fix_sequences.py \
   backend/apps/deployments/management/commands/fix_sequences.py

# 2. Run it (with a safety timeout)
timeout 120 docker compose -f /opt/smsly-hosting/docker-compose.yml \
  exec -T --user root backend python manage.py fix_sequences

# 3. Remove it again after use (so it is never auto-discovered)
rm backend/apps/deployments/management/commands/fix_sequences.py
```

### Standalone SQL alternative (no Django needed)

If you cannot easily restore the command, run the equivalent SQL directly
against the database (e.g. via `psql` or `docker exec ... psql`):

```sql
DO $$
DECLARE
    r RECORD;
    seq_max BIGINT;
BEGIN
    SET LOCAL statement_timeout = '30s';
    SET LOCAL lock_timeout = '10s';
    FOR r IN
        SELECT
            c.relname AS seq_name,
            t.relname AS table_name,
            a.attname AS col_name
        FROM pg_class c
        JOIN pg_depend d
          ON d.objid = c.oid AND d.classid = 'pg_class'::regclass
         AND d.refclassid = 'pg_class'::regclass
        JOIN pg_class t ON t.oid = d.refobjid
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
        WHERE c.relkind = 'S'
    LOOP
        BEGIN
            EXECUTE format('SELECT max(%I) FROM %I', r.col_name, r.table_name)
              INTO seq_max;
            IF seq_max IS NOT NULL THEN
                EXECUTE format('SELECT setval(%L, %s)', r.seq_name, seq_max);
            ELSE
                EXECUTE format('SELECT setval(%L, 1, false)', r.seq_name);
            END IF;
        EXCEPTION WHEN OTHERS THEN
            RAISE WARNING 'Skipped sequence % on table %: %',
              r.seq_name, r.table_name, SQLERRM;
        END;
    END LOOP;
END $$;
```

> **Note:** The SQL above adds `lock_timeout = '10s'` so a `setval()` that
> cannot acquire the sequence lock within 10s is skipped instead of blocking
> the whole operation. If even this hangs, kill it — it means a long-running
> transaction holds a lock on a sequence; fix that transaction first.

## Known issues

- **Can hang the update flow.** The loop iterates ALL sequences and calls
  `setval()` (which takes a lock). Under concurrent load (health checks,
  migrations, active writes) it can block indefinitely. `statement_timeout`
  mitigates single statements but the initial catalog scan + many small locks
  can still stall. → This is why it was removed from the automated flow.
- Only supports PostgreSQL. On other databases it prints a warning and exits.
