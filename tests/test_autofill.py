"""End-to-end tests: checkout flow, privacy rules, data mapping, performance and settings page."""
import datetime
import re
from decimal import Decimal

import pytest
from bs4 import BeautifulSoup
from django.core import mail as djmail
from django.db import connection
from django.test.utils import CaptureQueriesContext
from django.utils.timezone import now
from django_scopes import scopes_disabled

from pretix.base.models import (
    Event, Item, Order, OrderPosition, Organizer, Question, QuestionAnswer,
)

VICTIM = "Victima@Example.com"  # see conftest.py
STEP = "/org/kart/checkout/autofill/"
QUESTIONS = "/org/kart/checkout/questions/"


def request_code(client, email, step=STEP):
    djmail.outbox = []
    r = client.post(step, {"action": "send", "email": email})
    assert r.status_code == 302, r.content.decode()[:500]
    if not djmail.outbox:
        return None
    m = re.search(r"^\s*(\d{6})\s*$", djmail.outbox[-1].body, re.M)
    return m.group(1)


def doc_of(r):
    return BeautifulSoup(r.content.decode(), "lxml")


def value(doc, name):
    el = doc.select_one(f'[name="{name}"]')
    if el is None:
        return None
    if el.name == "select":
        opt = el.select_one("option[selected]")
        return opt.get("value") if opt else None
    if el.name == "textarea":
        return el.text
    return el.get("value")


def questions(client, url=QUESTIONS):
    r = client.get(url, follow=True)
    assert r.status_code == 200
    return doc_of(r)


# ----------------------------------------------------------------------------- flow placement
def test_checkout_goes_through_autofill_step_first(env):
    env.configure()
    env.add_to_cart()
    r = env.client.get("/org/kart/checkout/start", follow=True)
    assert "/checkout/autofill/" in r.redirect_chain[-1][0]
    assert "Enviarme un código" in r.content.decode()


def test_step_hidden_when_disabled(env):
    env.configure(enabled=False)
    env.add_to_cart()
    r = env.client.get("/org/kart/checkout/start", follow=True)
    assert "/checkout/questions/" in r.redirect_chain[-1][0]


def test_step_hidden_when_cart_asks_nothing_configured(env):
    env.configure(idents=["NO_EXISTE"])
    env.add_to_cart()
    r = env.client.get("/org/kart/checkout/start", follow=True)
    assert "/checkout/questions/" in r.redirect_chain[-1][0]


def test_skip_goes_to_questions_without_filling(env):
    env.configure()
    pos = env.add_to_cart()[0]
    r = env.client.post(STEP, {"action": "skip"}, follow=True)
    assert "/checkout/questions/" in r.redirect_chain[-1][0]
    assert value(doc_of(r), f"{pos.id}-question_{env.q_cedula.id}") in (None, "")


# ----------------------------------------------------------------------------- happy path
def test_full_flow_fills_every_type(env):
    env.configure()
    pos = env.add_to_cart()[0]
    code = request_code(env.client, "victima@example.com")  # different case than the stored email
    assert code, "a code must be mailed to a returning buyer"
    assert djmail.outbox[-1].to == ["victima@example.com"]

    r = env.client.post(STEP, {"action": "verify", "code": code})
    assert r.status_code == 302 and "/checkout/questions/" in r["Location"]

    doc = questions(env.client)
    p = f"{pos.id}-question_"
    assert value(doc, f"{p}{env.q_cedula.id}") == "1712345678"
    phone = (value(doc, f"{p}{env.q_phone.id}_0") or "") + (value(doc, f"{p}{env.q_phone.id}_1") or "")
    assert re.sub(r"\D", "", phone).endswith("991234567"), phone
    assert value(doc, f"{p}{env.q_city.id}") == "IBARRA"
    assert not doc.select_one(f'[name="{p}{env.q_promo.id}"]').has_attr("checked")  # previous answer was "No"
    assert value(doc, f"{p}{env.q_arrival.id}_0")  # date part rendered, no crash
    assert value(doc, "email") == "victima@example.com"


def test_buyer_can_finish_checkout_after_autofill(env):
    env.configure()
    pos = env.add_to_cart()[0]
    code = request_code(env.client, VICTIM)
    env.client.post(STEP, {"action": "verify", "code": code})
    doc = questions(env.client)
    data = {
        "email": value(doc, "email"),
        "transmission_type": "email",
    }
    for el in doc.select(f'[name^="{pos.id}-question_"]'):
        n = el.get("name")
        if el.name == "select":
            data[n] = value(doc, n) or ""
        elif el.get("type") == "checkbox":
            if el.has_attr("checked"):
                data[n] = "on"
        else:
            data[n] = el.get("value") or ""
    r = env.client.post(QUESTIONS, data)
    assert r.status_code == 302 and "/checkout/payment/" in r["Location"], doc_of(r).select(".has-error")
    with scopes_disabled():
        answers = {a.question.identifier: a.answer for a in QuestionAnswer.objects.filter(cartposition=pos)}
    assert answers["CEDULA"] == "1712345678"
    assert answers["CIUDAD"] == "Ibarra"


def test_reapply_fills_tickets_added_after_verification(env):
    env.configure(idents=["CEDULA"])
    first = env.add_to_cart()[0]
    code = request_code(env.client, VICTIM)
    env.client.post(STEP, {"action": "verify", "code": code})
    with scopes_disabled():
        first.delete()  # buyer swaps the ticket
    second = env.add_to_cart()[0]
    assert "Volver a rellenar" in env.client.get(STEP).content.decode()
    r = env.client.post(STEP, {"action": "reapply"})
    assert "/checkout/questions/" in r["Location"]
    assert value(questions(env.client), f"{second.id}-question_{env.q_cedula.id}") == "1712345678"


def test_change_email_returns_to_first_stage(env):
    env.configure()
    env.add_to_cart()
    request_code(env.client, VICTIM)
    env.client.post(STEP, {"action": "change"})
    page = env.client.get(STEP).content.decode()
    assert "Enviarme un código" in page and "Rellenar mis datos" not in page


def test_contact_email_typed_by_buyer_wins(env):
    env.configure(idents=["CEDULA"])
    env.add_to_cart()
    session = env.client.session
    session["carts"][env.cart_id]["email"] = "otro@correo.ec"
    session.save()
    code = request_code(env.client, VICTIM)
    env.client.post(STEP, {"action": "verify", "code": code})
    assert value(questions(env.client), "email") == "otro@correo.ec"


# ----------------------------------------------------------------------------- privacy / abuse
def test_unknown_email_sees_same_screen_and_cannot_verify(env):
    env.configure()
    env.add_to_cart()
    assert request_code(env.client, "nunca@compro.ec") is None  # nothing mailed
    page = env.client.get(STEP).content.decode()
    assert "te enviamos un código" in page  # same wording as for returning buyers
    r = env.client.post(STEP, {"action": "verify", "code": "123456"})
    assert "El código no es correcto" in r.content.decode()


def test_code_is_not_stored_in_clear(env):
    env.configure()
    env.add_to_cart()
    code = request_code(env.client, VICTIM)
    state = env.client.session["carts"][env.cart_id]["email_autofill"]
    assert code not in repr(state)


def test_wrong_codes_lock_after_five_tries(env):
    env.configure()
    pos = env.add_to_cart()[0]
    code = request_code(env.client, VICTIM)
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(4):
        r = env.client.post(STEP, {"action": "verify", "code": wrong})
        assert "El código no es correcto" in r.content.decode()
    r = env.client.post(STEP, {"action": "verify", "code": wrong}, follow=True)
    assert "demasiados intentos" in r.content.decode()
    r = env.client.post(STEP, {"action": "verify", "code": code})  # even the right one is dead now
    doc = questions(env.client)
    assert value(doc, f"{pos.id}-question_{env.q_cedula.id}") in (None, "")


def test_session_send_limit(env):
    env.configure()
    env.add_to_cart()
    for _ in range(3):
        assert request_code(env.client, VICTIM)
    r = env.client.post(STEP, {"action": "send", "email": VICTIM})
    assert r.status_code == 200 and "demasiados códigos" in r.content.decode()


# ----------------------------------------------------------------------------- data rules
def test_never_overwrites_what_the_buyer_typed(env):
    env.configure()
    pos = env.add_to_cart()[0]
    with scopes_disabled():
        QuestionAnswer.objects.create(cartposition=pos, question=env.q_cedula, answer="0999999999")
    code = request_code(env.client, VICTIM)
    env.client.post(STEP, {"action": "verify", "code": code})
    doc = questions(env.client)
    assert value(doc, f"{pos.id}-question_{env.q_cedula.id}") == "0999999999"
    assert value(doc, f"{pos.id}-question_{env.q_city.id}") == "IBARRA"


def test_required_checkbox_is_never_pre_ticked(env):
    with scopes_disabled():
        q = Question.objects.create(event=env.event, question="Acepto el reglamento", identifier="TERMINOS",
                                    type=Question.TYPE_BOOLEAN, required=True, position=9)
        env.item.questions.add(q)
        QuestionAnswer.objects.create(orderposition=env.old_pos, question=q, answer="True")
    env.configure(idents=["CEDULA", "TERMINOS"])
    pos = env.add_to_cart()[0]
    code = request_code(env.client, VICTIM)
    env.client.post(STEP, {"action": "verify", "code": code})
    doc = questions(env.client)
    assert not doc.select_one(f'[name="{pos.id}-question_{q.id}"]').has_attr("checked")


def test_each_ticket_gets_its_own_person(env):
    with scopes_disabled():
        p2 = OrderPosition.objects.create(order=env.old_order, item=env.item, price=Decimal("10"))
        QuestionAnswer.objects.create(orderposition=p2, question=env.q_cedula, answer="0400000000")
    env.configure(idents=["CEDULA"])
    a, b = env.add_to_cart(n=2)
    code = request_code(env.client, VICTIM)
    env.client.post(STEP, {"action": "verify", "code": code})
    doc = questions(env.client)
    got = sorted([value(doc, f"{a.id}-question_{env.q_cedula.id}"), value(doc, f"{b.id}-question_{env.q_cedula.id}")])
    assert got == ["0400000000", "1712345678"]


def test_missing_fields_come_from_older_orders(env):
    with scopes_disabled():
        newer = Order.objects.create(
            status=Order.STATUS_PAID, event=env.event, email=VICTIM, datetime=now() - datetime.timedelta(days=1),
            expires=now(), total=Decimal("10"), sales_channel=env.orga.sales_channels.get(identifier="web"),
        )
        np = OrderPosition.objects.create(order=newer, item=env.item, price=Decimal("10"))
        QuestionAnswer.objects.create(orderposition=np, question=env.q_cedula, answer="1799999999")
    env.configure(idents=["CEDULA", "CIUDAD"])
    pos = env.add_to_cart()[0]
    code = request_code(env.client, VICTIM)
    env.client.post(STEP, {"action": "verify", "code": code})
    doc = questions(env.client)
    assert value(doc, f"{pos.id}-question_{env.q_cedula.id}") == "1799999999"  # newest wins
    assert value(doc, f"{pos.id}-question_{env.q_city.id}") == "IBARRA"      # older order fills the gap


@pytest.fixture
def other_event(env):
    """A separate event of the SAME organizer whose questions reuse the identifiers (any case)."""
    with scopes_disabled():
        ev2 = Event.objects.create(
            organizer=env.orga, name="Taller", slug="taller", date_from=now() + datetime.timedelta(days=40),
            plugins="pretix.plugins.banktransfer,pretix_email_autofill", live=True,
        )
        ev2.settings.set("attendee_names_asked", False)
        ev2.settings.set("payment_banktransfer__enabled", True)
        tr = ev2.tax_rules.create(rate=0, default=True)
        item = Item.objects.create(event=ev2, name="Cupo", default_price=5, admission=True, personalized=True,
                                   tax_rule=tr)
        ev2.quotas.create(name="Q", size=10).items.add(item)
        q_ced = Question.objects.create(event=ev2, question="Cédula", type=Question.TYPE_STRING, required=True,
                                        identifier="cedula")
        q_city = Question.objects.create(event=ev2, question="Ciudad", type=Question.TYPE_CHOICE, required=False,
                                         identifier="CIUDAD")
        q_city.options.create(answer="Ibarra", identifier="IBA2")  # different option identifier, same text
        q_city.options.create(answer="Quito", identifier="UIO2")
        item.questions.add(q_ced, q_city)
    env.configure(idents=["cedula", "CIUDAD"], event=ev2)
    return ev2, item, q_ced, q_city


def test_lookup_spans_all_events_of_the_organizer(env, client, other_event):
    from pretix.base.models import CartPosition
    from pretix.testutils.sessions import get_cart_session_key
    ev2, item, q_ced, q_city = other_event
    client.get("/org/taller/")
    cart_id = get_cart_session_key(client, ev2)
    with scopes_disabled():
        pos = CartPosition.objects.create(event=ev2, cart_id=cart_id, item=item, price=5,
                                          expires=now() + datetime.timedelta(minutes=30))
    code = request_code(client, VICTIM, step="/org/taller/checkout/autofill/")
    client.post("/org/taller/checkout/autofill/", {"action": "verify", "code": code})
    doc = questions(client, "/org/taller/checkout/questions/")
    assert value(doc, f"{pos.id}-question_{q_ced.id}") == "1712345678"
    assert value(doc, f"{pos.id}-question_{q_city.id}") == "IBA2"  # mapped by option text


def test_other_organizers_are_never_used(env):
    with scopes_disabled():
        other = Organizer.objects.create(name="Otro", slug="otro")
        oev = Event.objects.create(organizer=other, name="X", slug="x", date_from=now())
        oitem = Item.objects.create(event=oev, name="X", default_price=1)
        oq = Question.objects.create(event=oev, question="Cédula", type=Question.TYPE_STRING, identifier="CEDULA")
        oorder = Order.objects.create(status=Order.STATUS_PAID, event=oev, email="solo@otro.ec", datetime=now(),
                                      expires=now(), total=Decimal("1"),
                                      sales_channel=other.sales_channels.get(identifier="web"))
        op = OrderPosition.objects.create(order=oorder, item=oitem, price=Decimal("1"))
        QuestionAnswer.objects.create(orderposition=op, question=oq, answer="SECRETO")
    env.configure()
    env.add_to_cart()
    assert request_code(env.client, "solo@otro.ec") is None  # no order with this organizer -> no mail


def test_attendee_name_is_copied_when_enabled(env):
    with scopes_disabled():
        env.old_pos.attendee_name_parts = {"_scheme": "full", "full_name": "María Pérez"}
        env.old_pos.save()
    env.event.settings.set("attendee_names_asked", True)
    env.event.settings.set("name_scheme", "full")
    env.configure(idents=["CEDULA"], attendee=True)
    pos = env.add_to_cart()[0]
    code = request_code(env.client, VICTIM)
    env.client.post(STEP, {"action": "verify", "code": code})
    doc = questions(env.client)
    assert value(doc, f"{pos.id}-attendee_name_parts_0") == "María Pérez"


# ----------------------------------------------------------------------------- performance
def _order_email_lookups(ctx):
    return [q["sql"] for q in ctx.captured_queries
            if 'FROM "pretixbase_order"' in q["sql"] and ('"pretixbase_order"."email" LIKE' in q["sql"] or 'UPPER("pretixbase_order"."email"' in q["sql"])]


@pytest.mark.parametrize("tickets", [1, 5])
def test_lookup_cost_is_constant(env, tickets):
    env.configure()
    env.add_to_cart(n=tickets)
    code = request_code(env.client, VICTIM)
    with CaptureQueriesContext(connection) as ctx:
        env.client.post(STEP, {"action": "verify", "code": code})
    lookups = _order_email_lookups(ctx)
    answers = [q for q in ctx.captured_queries if 'FROM "pretixbase_questionanswer"' in q["sql"]]
    print(f"{tickets} tickets -> {len(lookups)} order lookup(s), {len(answers)} answer queries, "
          f"{len(ctx.captured_queries)} queries in the verify request")
    assert len(lookups) == 1
    with CaptureQueriesContext(connection) as ctx:
        questions(env.client)
    assert _order_email_lookups(ctx) == []  # the questions page itself does no lookups at all


# ----------------------------------------------------------------------------- settings page
def test_settings_page_lists_and_saves_identifiers(env, client):
    with scopes_disabled():
        Question.objects.create(event=env.event, question="Foto", type=Question.TYPE_FILE, identifier="FOTO")
    env.login_admin(client)
    r = client.get("/control/event/org/kart/email-autofill/")
    assert r.status_code == 200
    doc = doc_of(r)
    offered = {i.get("value") for i in doc.select('input[name="email_autofill_questions"]')}
    assert offered == {"CEDULA", "TELEFONO", "CIUDAD", "PROMO", "LLEGADA"}  # file question excluded

    r = client.post("/control/event/org/kart/email-autofill/", {
        "email_autofill_enabled": "on",
        "email_autofill_questions": ["CEDULA", "CIUDAD"],
    })
    assert r.status_code == 302
    env.event.settings.flush()
    assert env.event.settings.get("email_autofill_enabled", as_type=bool) is True
    assert env.event.settings.get("email_autofill_questions", as_type=list) == ["CEDULA", "CIUDAD"]
    with scopes_disabled():
        assert not [k for k in env.event.settings._cache() if k.startswith("q_")]
        assert env.event.all_logentries().filter(action_type="pretix.event.settings").exists()

    doc = doc_of(client.get("/control/event/org/kart/email-autofill/"))
    checked = {i.get("value") for i in doc.select('input[name="email_autofill_questions"][checked]')}
    assert checked == {"CEDULA", "CIUDAD"}
