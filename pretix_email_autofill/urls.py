from django.urls import re_path

from .views import AutofillSettingsView

urlpatterns = [
    re_path(
        r"^control/event/(?P<organizer>[^/]+)/(?P<event>[^/]+)/email-autofill/$",
        AutofillSettingsView.as_view(),
        name="settings",
    ),
]
