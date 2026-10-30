"""Small shims so the plugin runs on every pretix from 2025.7 up to current releases."""
import importlib.util
import inspect


def settings_permission() -> str:
    # pretix 2026.3 introduced fine-grained permissions ("event.settings.general:write").
    # Older releases only know the legacy name.
    if importlib.util.find_spec("pretix.base.permissions") is not None:
        return "event.settings.general:write"
    return "can_change_event_settings"


def mail_supports(param: str) -> bool:
    from pretix.base.services.mail import mail

    return param in inspect.signature(mail).parameters
