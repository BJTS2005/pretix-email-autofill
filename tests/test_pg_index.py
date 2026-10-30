"""PostgreSQL only: the migration's functional index must match the SQL Django generates for the lookup."""
import datetime
import importlib

import pytest
from django.db import connection
from django.utils.timezone import now
from django_scopes import scopes_disabled


@pytest.mark.django_db(transaction=True)
def test_index_is_used_by_lookup():
    if connection.vendor != "postgresql":
        pytest.skip("PostgreSQL only")
    from pretix.base.models import Event, Organizer
    from pretix_email_autofill.autofill import recent_orders_qs
    mig = importlib.import_module("pretix_email_autofill.migrations.0001_order_email_index")

    with scopes_disabled():
        orga = Organizer.objects.create(name="O", slug="o")
        ev = Event.objects.create(organizer=orga, name="E", slug="e", date_from=now() + datetime.timedelta(days=1))
        sql, params = recent_orders_qs(ev, "Alguien@Example.com")[:20].query.sql_with_params()

    with connection.schema_editor(atomic=False) as se:
        mig.create_index(None, se)
        mig.create_index(None, se)  # idempotent
    try:
        with connection.cursor() as c:
            c.execute("SET enable_seqscan = off")
            c.execute("EXPLAIN " + sql, params)
            plan = "\n".join(r[0] for r in c.fetchall())
        print(sql % tuple(repr(p) for p in params))
        print(plan)
        assert "email_autofill_order_upper_email" in plan
    finally:
        with connection.schema_editor(atomic=False) as se:
            mig.drop_index(None, se)
