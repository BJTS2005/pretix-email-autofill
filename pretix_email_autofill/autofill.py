"""
Finds a buyer's previous answers (in every event of the same organizer) and copies them into
the current cart.

The answers are written as regular cart answers. pretix then renders every question type
natively (choices, yes/no, phone numbers, dates...) and the buyer can still edit everything on
the questions page. Nothing the buyer already typed is ever overwritten.

Matching rules:
- Questions are matched across events by their internal identifier (case-insensitive), so
  cloned events and events that reuse identifiers like ``CEDULA`` work out of the box.
- Ticket N in the cart is filled from ticket N of the buyer's previous orders (most recent
  order first; missing fields are taken from older orders).
"""
import logging
from collections import OrderedDict
from decimal import Decimal, InvalidOperation

from django.db import IntegrityError, transaction
from django.db.models import Prefetch
from django.db.models.functions import Upper

from pretix.base.models import Order, OrderPosition, Question, QuestionAnswer

logger = logging.getLogger(__name__)

EXCLUDED_TYPES = {Question.TYPE_FILE}
TEXT_TYPES = {Question.TYPE_STRING, Question.TYPE_TEXT}
CHOICE_TYPES = {Question.TYPE_CHOICE, Question.TYPE_CHOICE_MULTIPLE}
ELIGIBLE_STATUSES = (Order.STATUS_PAID, Order.STATUS_PENDING)
MAX_ORDERS = 20  # only the buyer's most recent orders are looked at
ADDRESS_FIELDS = ("street", "zipcode", "city", "country", "state")


# --------------------------------------------------------------------------- configuration
def configured_identifiers(event):
    raw = event.settings.get("email_autofill_questions", as_type=list) or []
    return {str(i).strip().upper() for i in raw if str(i).strip()}


def attendee_data_enabled(event):
    return bool(event.settings.get("email_autofill_attendee_data", as_type=bool))


def selectable_questions(event):
    """Questions an organizer may pick in the settings page."""
    qs = event.questions.filter(ask_during_checkin=False, hidden=False).exclude(type__in=EXCLUDED_TYPES)
    if hasattr(Question, "ContainerType"):  # pretix >= 2026.8 also has order-level questions
        qs = qs.filter(container_type=Question.ContainerType.ORDERPOSITION)
    return qs.order_by("position", "pk")


def _fillable(question):
    if question.type in EXCLUDED_TYPES:
        return False
    if question.type == Question.TYPE_BOOLEAN and question.required:
        # Required checkboxes are usually consents ("Acepto el reglamento"): never pre-tick them.
        return False
    return True


# --------------------------------------------------------------------------- cart side
def _asked_questions(position):
    questions = getattr(position.item, "questions_to_ask", None)  # prefetched by pretix's get_cart()
    if questions is None:
        questions = selectable_questions(position.event).filter(items=position.item)
    return questions


def _wants_attendee_data(event, position):
    s = event.settings
    return bool(position.item.ask_attendee_data) and bool(
        s.attendee_names_asked or s.attendee_emails_asked or s.attendee_company_asked
        or s.attendee_addresses_asked
    )


def target_positions(event, positions):
    """Cart positions that ask something we can fill, in the order pretix displays them."""
    identifiers = configured_identifiers(event)
    with_attendee = attendee_data_enabled(event)
    if not identifiers and not with_attendee:
        return []
    targets = []
    for pos in sorted(positions, key=lambda p: p.sort_key):
        questions = [
            q for q in _asked_questions(pos)
            if _fillable(q) and (q.identifier or "").upper() in identifiers
        ]
        if questions or (with_attendee and _wants_attendee_data(event, pos)):
            targets.append((pos, questions))
    return targets


# --------------------------------------------------------------------------- history side
def recent_orders_qs(event, email):
    # On PostgreSQL this is served by the functional index from migration 0001.
    qs = Order.objects.filter(
        event__organizer_id=event.organizer_id,
        email__iexact=(email or "").strip(),
        status__in=ELIGIBLE_STATUSES,
    )
    if not event.testmode:
        qs = qs.filter(testmode=False)
    return qs.order_by("-datetime", "-pk").values_list("pk", flat=True)


def _recent_order_ids(event, email, limit=MAX_ORDERS):
    return list(recent_orders_qs(event, email)[:limit])


def has_previous_orders(event, email):
    return bool(_recent_order_ids(event, email, limit=1))


def _name_filled(parts):
    return any(v for k, v in (parts or {}).items() if not k.startswith("_"))


def _has_attendee_data(pos):
    return bool(
        _name_filled(pos.attendee_name_parts) or pos.attendee_email or pos.company
        or any(getattr(pos, f) for f in ADDRESS_FIELDS)
    )


def previous_positions(event, email):
    """
    Returns ``[[(OrderPosition, {IDENTIFIER: QuestionAnswer}), ...], ...]``: one inner list per
    previous order (most recent first) with its useful positions in ticket order.
    Costs 3-4 queries no matter how many orders or tickets there are.
    """
    identifiers = configured_identifiers(event)
    with_attendee = attendee_data_enabled(event)
    order_ids = _recent_order_ids(event, email)
    if not order_ids:
        return []

    answers = (
        QuestionAnswer.objects.annotate(_autofill_ident=Upper("question__identifier"))
        .filter(_autofill_ident__in=identifiers or {""})
        .exclude(question__type__in=EXCLUDED_TYPES)
        .select_related("question")
        .prefetch_related("options")
    )
    positions = OrderPosition.objects.filter(order_id__in=order_ids).prefetch_related(
        Prefetch("answers", queryset=answers, to_attr="_autofill_answers")
    )

    rank = {pk: i for i, pk in enumerate(order_ids)}
    grouped = OrderedDict((pk, []) for pk in order_ids)
    for pos in sorted(positions, key=lambda p: (rank[p.order_id], p.positionid)):
        found = {
            a.question.identifier.upper(): a
            for a in pos._autofill_answers
            if a.question.identifier and (a.answer or a.options.all())
        }
        if found or (with_attendee and _has_attendee_data(pos)):
            grouped[pos.order_id].append((pos, found))
    return [plist for plist in grouped.values() if plist]


# --------------------------------------------------------------------------- conversion
def _option_texts(option):
    data = getattr(option.answer, "data", option.answer)
    values = data.values() if isinstance(data, dict) else [data]
    return {str(v).strip().casefold() for v in values if v}


def _map_options(source_options, target_options):
    by_ident = {(o.identifier or "").upper(): o for o in target_options}
    by_text = {}
    for o in target_options:
        for t in _option_texts(o):
            by_text.setdefault(t, o)
    mapped = []
    for so in source_options:
        o = by_ident.get((so.identifier or "").upper())
        if o is None:
            o = next((by_text[t] for t in _option_texts(so) if t in by_text), None)
        if o is not None and o not in mapped:
            mapped.append(o)
    return sorted(mapped, key=lambda o: (o.position, o.pk))


def convert_answer(src, question):
    """Returns ``(answer_text, [options])`` ready for ``question`` or ``None`` if not transferable."""
    s_type, t_type = src.question.type, question.type
    if t_type in CHOICE_TYPES:
        if s_type not in CHOICE_TYPES:
            return None
        options = _map_options(list(src.options.all()), list(question.options.all()))
        if not options:
            return None
        if t_type == Question.TYPE_CHOICE:
            return str(options[0].answer), options[:1]
        return ", ".join(str(o) for o in options), options
    if s_type in CHOICE_TYPES or not src.answer:
        return None
    if t_type in TEXT_TYPES:
        return (src.answer, []) if s_type in TEXT_TYPES else None
    if s_type != t_type:
        return None
    if t_type == Question.TYPE_NUMBER:
        try:
            Decimal(src.answer)
        except InvalidOperation:
            return None
    if t_type == Question.TYPE_BOOLEAN and src.answer not in ("True", "False"):
        return None
    return src.answer, []


# --------------------------------------------------------------------------- apply
def _copy_attendee_data(event, pos, candidates):
    s = event.settings
    changed = []
    if s.attendee_names_asked and not _name_filled(pos.attendee_name_parts):
        scheme = s.name_scheme
        src = next((c for c in candidates if _name_filled(c.attendee_name_parts)
                    and (c.attendee_name_parts or {}).get("_scheme") == scheme), None)
        if src:
            pos.attendee_name_parts = dict(src.attendee_name_parts)
            changed.append("attendee_name_parts")
    if s.attendee_emails_asked and not pos.attendee_email:
        src = next((c for c in candidates if c.attendee_email), None)
        if src:
            pos.attendee_email = src.attendee_email
            changed.append("attendee_email")
    if s.attendee_company_asked and not pos.company:
        src = next((c for c in candidates if c.company), None)
        if src:
            pos.company = src.company
            changed.append("company")
    if s.attendee_addresses_asked and not any(getattr(pos, f) for f in ADDRESS_FIELDS):
        src = next((c for c in candidates if any(getattr(c, f) for f in ADDRESS_FIELDS)), None)
        if src:
            for f in ADDRESS_FIELDS:
                setattr(pos, f, getattr(src, f))
            changed += list(ADDRESS_FIELDS)
    if changed:
        pos.save(update_fields=changed)
    return len([c for c in changed if c not in ADDRESS_FIELDS[1:]])


def apply_to_cart(event, positions, email):
    """
    Copies the buyer's previous data into the cart positions.
    Returns how many fields were filled. Existing input is never overwritten.
    """
    targets = target_positions(event, positions)
    if not targets:
        return 0
    history = previous_positions(event, email)
    if not history:
        return 0
    with_attendee = attendee_data_enabled(event)

    existing = set(
        QuestionAnswer.objects.filter(cartposition__in=[p for p, _ in targets])
        .values_list("cartposition_id", "question_id")
    )
    filled = 0
    with transaction.atomic():
        for index, (pos, questions) in enumerate(targets):
            candidates = [plist[index] for plist in history if index < len(plist)]
            if not candidates:
                continue
            for q in questions:
                if (pos.pk, q.pk) in existing:
                    continue
                ident = q.identifier.upper()
                src = next((found[ident] for _, found in candidates if ident in found), None)
                converted = convert_answer(src, q) if src is not None else None
                if converted is None:
                    continue
                text, options = converted
                try:
                    with transaction.atomic():
                        qa = QuestionAnswer(cartposition=pos, question=q, answer=text)
                        qa.save()
                        if options:
                            qa.options.add(*options)
                    filled += 1
                except IntegrityError:  # answered in a parallel request
                    logger.info("Answer for position %s / question %s already exists", pos.pk, q.pk)
            if with_attendee and _wants_attendee_data(event, pos):
                filled += _copy_attendee_data(event, pos, [c for c, _ in candidates])
    return filled
