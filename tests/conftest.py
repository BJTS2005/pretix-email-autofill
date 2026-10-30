import datetime
from decimal import Decimal

import pytest
from django.utils.timezone import now
from django_scopes import scopes_disabled

from pretix.base.models import (
    CartPosition, Event, Item, Order, OrderPosition, Organizer, Question,
    QuestionAnswer, Quota, Team, User,
)
from pretix.testutils.sessions import get_cart_session_key

VICTIM = "Victima@Example.com"


class Env:
    """An organizer with one event, five questions and a previous paid order by VICTIM."""

    def add_to_cart(self, n=1):
        with scopes_disabled():
            return [
                CartPosition.objects.create(event=self.event, cart_id=self.cart_id, item=self.item, price=10,
                                            expires=now() + datetime.timedelta(minutes=30))
                for _ in range(n)
            ]

    def login_admin(self, client):
        with scopes_disabled():
            user = User.objects.create_user("admin@localhost", "admin")
            team = Team.objects.create(organizer=self.orga, all_events=True, all_event_permissions=True,
                                       all_organizer_permissions=True)
            team.members.add(user)
        client.force_login(user)
        return user

    def configure(self, idents=("CEDULA", "TELEFONO", "CIUDAD", "PROMO", "LLEGADA"), attendee=False,
                  enabled=True, event=None):
        ev = event or self.event
        ev.settings.set("email_autofill_enabled", enabled)
        ev.settings.set("email_autofill_questions", list(idents))
        ev.settings.set("email_autofill_attendee_data", attendee)


@pytest.fixture
def env(db, client):
    e = Env()
    with scopes_disabled():
        e.orga = Organizer.objects.create(name="Org", slug="org", plugins="pretix.plugins.banktransfer")
        e.event = ev = Event.objects.create(
            organizer=e.orga, name="Karting", slug="kart", date_from=now() + datetime.timedelta(days=30),
            plugins="pretix.plugins.banktransfer,pretix_email_autofill", live=True,
        )
        ev.settings.set("timezone", "America/Guayaquil")
        ev.settings.set("attendee_names_asked", False)
        ev.settings.set("payment_banktransfer__enabled", True)
        ev.settings.set("region", "EC")
        tax_rule = ev.tax_rules.create(rate=0, default=True)
        quota = Quota.objects.create(event=ev, name="Q", size=100)
        e.item = Item.objects.create(event=ev, name="Entrada", default_price=10, admission=True,
                                     personalized=True, tax_rule=tax_rule)
        quota.items.add(e.item)

        e.q_cedula = Question.objects.create(event=ev, question="Cédula", type=Question.TYPE_STRING,
                                             required=True, position=1, identifier="CEDULA")
        e.q_phone = Question.objects.create(event=ev, question="Teléfono", type=Question.TYPE_PHONENUMBER,
                                            required=False, position=2, identifier="TELEFONO")
        e.q_city = Question.objects.create(event=ev, question="Ciudad", type=Question.TYPE_CHOICE,
                                           required=False, position=3, identifier="CIUDAD")
        e.opt_quito = e.q_city.options.create(answer="Quito", identifier="QUITO")
        e.opt_ibarra = e.q_city.options.create(answer="Ibarra", identifier="IBARRA")
        e.q_promo = Question.objects.create(event=ev, question="¿Acepta publicidad?",
                                            type=Question.TYPE_BOOLEAN, required=False, position=4,
                                            identifier="PROMO")
        e.q_arrival = Question.objects.create(event=ev, question="Llegada", type=Question.TYPE_DATETIME,
                                              required=False, position=5, identifier="LLEGADA")
        e.questions = [e.q_cedula, e.q_phone, e.q_city, e.q_promo, e.q_arrival]
        e.item.questions.add(*e.questions)

        e.old_order = Order.objects.create(
            status=Order.STATUS_PAID, event=ev, email=VICTIM,
            datetime=now() - datetime.timedelta(days=10), expires=now() + datetime.timedelta(days=1),
            total=Decimal("10"), sales_channel=e.orga.sales_channels.get(identifier="web"), locale="es",
        )
        e.old_pos = OrderPosition.objects.create(order=e.old_order, item=e.item, price=Decimal("10"))
        QuestionAnswer.objects.create(orderposition=e.old_pos, question=e.q_cedula, answer="1712345678")
        QuestionAnswer.objects.create(orderposition=e.old_pos, question=e.q_phone, answer="+593991234567")
        city = QuestionAnswer.objects.create(orderposition=e.old_pos, question=e.q_city, answer="Ibarra")
        city.options.add(e.opt_ibarra)
        QuestionAnswer.objects.create(orderposition=e.old_pos, question=e.q_promo, answer="False")
        QuestionAnswer.objects.create(orderposition=e.old_pos, question=e.q_arrival,
                                      answer="2026-05-01T10:30:00-05:00")

    e.client = client
    client.get("/org/kart/")
    e.cart_id = get_cart_session_key(client, e.event)
    return e
