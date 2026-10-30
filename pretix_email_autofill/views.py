from django.urls import reverse

from pretix.base.models import Event
from pretix.control.views.event import EventSettingsFormView, EventSettingsViewMixin

from .compat import settings_permission
from .forms import AutofillSettingsForm


class AutofillSettingsView(EventSettingsViewMixin, EventSettingsFormView):
    """Saves through pretix's own settings view: validation, audit log and success message included."""
    model = Event
    form_class = AutofillSettingsForm
    template_name = "pretix_email_autofill/settings.html"
    permission = settings_permission()

    def get_success_url(self):
        return reverse(
            "plugins:pretix_email_autofill:settings",
            kwargs={"organizer": self.request.event.organizer.slug, "event": self.request.event.slug},
        )
