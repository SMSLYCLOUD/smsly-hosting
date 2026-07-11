from django.core.management.base import BaseCommand
from django.db import connection


class Command(BaseCommand):
    help = 'Automatically sync all database sequences to the maximum ID of their respective tables'

    def handle(self, *args, **options):
        vendor = connection.vendor
        if vendor != 'postgresql':
            self.stdout.write(self.style.WARNING(f"Sequence repair is only supported/needed on PostgreSQL (current vendor: {vendor})"))
            return

        sql = """
        DO $$
        DECLARE
            seq_record RECORD;
            max_val BIGINT;
        BEGIN
            FOR seq_record IN 
                SELECT
                    c.relname AS seq_name,
                    t.relname AS table_name,
                    a.attname AS col_name
                FROM pg_class c
                JOIN pg_depend d ON d.objid = c.oid AND d.classid = 'pg_class'::regclass AND d.refclassid = 'pg_class'::regclass
                JOIN pg_class t ON t.oid = d.refobjid
                JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = d.refobjsubid
                WHERE c.relkind = 'S'
            LOOP
                EXECUTE format('SELECT max(%I) FROM %I', seq_record.col_name, seq_record.table_name) INTO max_val;
                IF max_val IS NOT NULL THEN
                    EXECUTE format('SELECT setval(%L, %s)', seq_record.seq_name, max_val);
                ELSE
                    EXECUTE format('SELECT setval(%L, 1, false)', seq_record.seq_name);
                END IF;
            END LOOP;
        END $$;
        """
        with connection.cursor() as cursor:
            cursor.execute(sql)
        self.stdout.write(self.style.SUCCESS("Successfully synchronized all PostgreSQL sequences."))
