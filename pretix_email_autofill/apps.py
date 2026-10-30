from django.utils.translation import gettext_lazy as _

from . import __version__

try:
    from pretix.base.plugins import PLUGIN_LEVEL_EVENT, PluginConfig
except ImportError:
    raise RuntimeError("Please use pretix 2025.7 or above to run this plugin!") from None


# Django >= 4.1 ignores ``default_app_config``: the AppConfig has to live in ``apps.py``
# (with ``default = True``), otherwise pretix never sees PretixPluginMeta and the plugin
# is silently loaded as a plain Django app.
class PluginApp(PluginConfig):
    default = True
    name = "pretix_email_autofill"
    verbose_name = _("Autocompletado por correo")

    class PretixPluginMeta:
        name = _("Autocompletado por correo")
        author = "Jesus Tandayamo"
        version = __version__
        category = "FEATURE"
        description = _(
            "Los compradores que ya compraron antes reciben un código en su correo y, al "
            "ingresarlo, sus respuestas anteriores se rellenan solas. No crea cuentas."
        )
        compatibility = "pretix>=2025.7.0"
        level = PLUGIN_LEVEL_EVENT
        visible = True
        settings_links = [
            ((_("Ajustes"), _("Autocompletado")), "plugins:pretix_email_autofill:settings", {}),
        ]
        navigation_links = []

    def ready(self):
        from . import signals  # NOQA
