"""
Speeds up the "previous orders of this email" lookup on PostgreSQL.

Django turns ``email__iexact`` into ``UPPER("email"::text) = UPPER(%s)``, which a plain index
cannot serve, so without this every lookup scans all orders. This functional index turns it
into an index lookup. It is created CONCURRENTLY, so ``python -m pretix migrate`` does not lock
the orders table. Other databases (e.g. SQLite in development) are left untouched.
"""
from django.db import migrations

INDEX_NAME = "email_autofill_order_upper_email"


def create_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        f"CREATE INDEX CONCURRENTLY IF NOT EXISTS {INDEX_NAME} "
        f"ON pretixbase_order (UPPER(email::text))"
    )


def drop_index(apps, schema_editor):
    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {INDEX_NAME}")


class Migration(migrations.Migration):
    atomic = False  # CREATE INDEX CONCURRENTLY cannot run inside a transaction

    dependencies = [
        ("pretixbase", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_index, drop_index),
    ]
