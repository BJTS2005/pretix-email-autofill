from django.dispatch import receiver
from django.urls import resolve, reverse
from django.utils.translation import gettext_lazy as _

from pretix.base.settings import settings_hierarkey
from pretix.control.signals import nav_event_settings
from pretix.presale.signals import (
    checkout_flow_steps, contact_form_fields_overrides,
)

from .compat import settings_permission

# Registered defaults give the settings their proper types (bool / list) everywhere.
settings_hierarkey.add_default("email_autofill_enabled", "False", bool)
settings_hierarkey.add_default("email_autofill_questions", "[]", list)
settings_hierarkey.add_default("email_autofill_attendee_data", "False", bool)


@receiver(checkout_flow_steps, dispatch_uid="email_autofill_checkout_step")
def register_checkout_step(sender, **kwargs):
    from .checkoutflow import EmailAutofillStep

    return EmailAutofillStep


@receiver(contact_form_fields_overrides, dispatch_uid="email_autofill_contact_email")
def prefill_contact_email(sender, request, order=None, **kwargs):
    """After a successful verification, suggest the verified address as the order email."""
    if order is not None:
        return {}
    from .checkoutflow import STATE_KEY
    from pretix.presale.views.cart import cart_session

    cs = cart_session(request)
    state = (cs or {}).get(STATE_KEY) or {}
    if not state.get("verified") or cs.get("email") or (cs.get("contact_form_data") or {}).get("email"):
        return {}  # never replace an address the buyer already entered
    # pretix also feeds these overrides to the invoice address form, which reads v["disabled"]
    # whenever "initial" is present, so "disabled" must always be included.
    return {
        "email": {"initial": state["email"], "disabled": False},
        "email_repeat": {"initial": state["email"], "disabled": False},
    }


@receiver(nav_event_settings, dispatch_uid="email_autofill_nav")
def navbar_settings(sender, request, **kwargs):
    if not request.user.has_event_permission(
        request.organizer, request.event, settings_permission(), request=request
    ):
        return []
    url = resolve(request.path_info)
    return [
        {
            "label": _("Autocompletado"),
            "url": reverse(
                "plugins:pretix_email_autofill:settings",
                kwargs={"event": request.event.slug, "organizer": request.organizer.slug},
            ),
            "active": url.namespace == "plugins:pretix_email_autofill" and url.url_name == "settings",
        }
    ]
