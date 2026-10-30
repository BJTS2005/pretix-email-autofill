from django.contrib import messages
from django.utils.functional import cached_property
from django.utils.translation import gettext as _, pgettext_lazy

from pretix.helpers.http import redirect_to_url
from pretix.presale.checkoutflow import TemplateFlowStep
from pretix.presale.views import CartMixin

from . import autofill, otp
from .forms import CodeForm, EmailForm

STATE_KEY = "email_autofill"


class EmailAutofillStep(CartMixin, TemplateFlowStep):
    """
    Optional step right before pretix's questions step:
    email -> 6-digit code by email -> previous answers copied into the cart.
    First-time buyers just click "continue".
    """
    priority = 49  # after add-ons (40), customer (45), membership (47); before questions (50)
    identifier = "autofill"
    template_name = "pretix_email_autofill/checkout_step.html"
    label = pgettext_lazy("checkoutflow", "Autocompletar")
    icon = "magic"

    # ------------------------------------------------------------------ flow integration
    @property
    def state(self):
        cs = self.cart_session
        if not isinstance(cs.get(STATE_KEY), dict):
            cs[STATE_KEY] = {}
        return cs[STATE_KEY]

    @cached_property
    def targets(self):
        return autofill.target_positions(self.request.event, self.positions)

    def is_applicable(self, request):
        self.request = request
        if not request.event.settings.get("email_autofill_enabled", as_type=bool):
            return False
        if self.cart_customer:  # logged-in customers already have pretix's saved profiles
            return False
        return bool(self.targets)

    def is_completed(self, request, warn=False):
        self.request = request
        return bool((self.cart_session.get(STATE_KEY) or {}).get("done"))

    # ------------------------------------------------------------------ views
    def get(self, request):
        self.request = request
        return self.render()

    def post(self, request):
        self.request = request
        handler = {
            "send": self._send,
            "verify": self._verify,
            "skip": self._skip,
            "change": self._change,
            "reapply": self._reapply,
        }.get(request.POST.get("action"))
        return handler() if handler else self.render()

    def _back_here(self):
        return redirect_to_url(self.get_step_url(self.request))

    def _send(self):
        form = EmailForm(data=self.request.POST)
        if not form.is_valid():
            return self.render(email_form=form)
        email = form.cleaned_data["email"].strip()
        deliver = autofill.has_previous_orders(self.request.event, email)
        if not otp.issue_code(self.request, self.state, email, deliver=deliver):
            messages.error(self.request, _(
                "Pediste demasiados códigos. Espera unos minutos o continúa sin autocompletar."
            ))
            return self.render(email_form=form)
        self.state.pop("verified", None)
        return self._back_here()

    def _verify(self):
        if not self.state.get("otp"):
            return self._back_here()
        form = CodeForm(data=self.request.POST)
        if not form.is_valid():
            return self.render(code_form=form)

        result = otp.verify_code(self.state, form.cleaned_data["code"])
        if result == otp.INVALID:
            messages.error(self.request, _("El código no es correcto."))
            return self.render()
        if result != otp.OK:
            self.state.pop("otp", None)
            messages.error(self.request, _(
                "El código venció o hubo demasiados intentos. Pide un código nuevo."
            ))
            return self._back_here()

        email = self.state["email"]
        sends = self.state.get("sends", [])
        self.state.clear()
        self.state.update({"email": email, "verified": True, "done": True, "sends": sends})
        return self._fill_and_continue(email)

    def _reapply(self):
        if not self.state.get("verified"):
            return self._back_here()
        self.state["done"] = True
        return self._fill_and_continue(self.state["email"])

    def _fill_and_continue(self, email):
        filled = autofill.apply_to_cart(self.request.event, self.positions, email)
        if filled:
            messages.success(self.request, _(
                "Listo: rellenamos tus datos con los de tu compra anterior. Revísalos antes de continuar."
            ))
        else:
            messages.info(self.request, _(
                "Tu correo quedó verificado, pero no encontramos datos anteriores para este carrito."
            ))
        return redirect_to_url(self.get_next_url(self.request))

    def _skip(self):
        self.state["done"] = True
        return redirect_to_url(self.get_next_url(self.request))

    def _change(self):
        for key in ("otp", "email", "verified"):
            self.state.pop(key, None)
        return self._back_here()

    # ------------------------------------------------------------------ rendering
    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        state = self.state
        if state.get("verified"):
            stage = "done"
        elif state.get("otp"):
            stage = "code"
        else:
            stage = "email"
        ctx["cart"] = self.get_cart()
        ctx["stage"] = stage
        ctx["pending_email"] = state.get("email", "")
        ctx.setdefault("email_form", EmailForm(initial={"email": state.get("email") or self.cart_session.get("email", "")}))
        ctx.setdefault("code_form", CodeForm())
        asked = {q.pk: q for _pos, questions in self.targets for q in questions}
        ctx["fields"] = [str(q.question) for q in sorted(asked.values(), key=lambda q: (q.position, q.pk))]
        ctx["attendee_data"] = autofill.attendee_data_enabled(self.request.event)
        ctx["minutes"] = otp.CODE_TTL // 60
        return ctx
