import re

from django import forms
from django.utils.translation import gettext_lazy as _

from pretix.base.forms import SettingsForm

from .autofill import selectable_questions


class AutofillSettingsForm(SettingsForm):
    email_autofill_enabled = forms.BooleanField(
        label=_("Activar autocompletado"),
        help_text=_(
            "Antes del paso de preguntas, el comprador puede pedir un código a su correo. "
            "Si ya compró antes en cualquier evento de este organizador, al ingresar el código "
            "se rellenan las preguntas marcadas abajo. No crea cuentas."
        ),
        required=False,
    )
    email_autofill_questions = forms.MultipleChoiceField(
        label=_("Preguntas que se rellenan"),
        help_text=_(
            "Entre eventos, las preguntas se reconocen por su identificador interno (pestaña "
            "«Avanzado» de cada pregunta). Usa el mismo identificador en todos tus eventos, por "
            "ejemplo CEDULA o TELEFONO. Los eventos clonados ya lo conservan. Las preguntas de "
            "archivo y las casillas obligatorias (p. ej. aceptar términos) nunca se rellenan."
        ),
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )
    email_autofill_attendee_data = forms.BooleanField(
        label=_("Rellenar también los datos del asistente"),
        help_text=_("Nombre, email, empresa y dirección del asistente, si este evento los pide."),
        required=False,
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        questions = list(selectable_questions(self.obj))
        self.fields["email_autofill_questions"].choices = [
            (q.identifier, f"{q.question} ({q.get_type_display()}) · {q.identifier}") for q in questions
        ]
        # identifiers are matched case-insensitively
        stored = {str(i).upper() for i in (self.initial.get("email_autofill_questions") or [])}
        self.initial["email_autofill_questions"] = [q.identifier for q in questions if q.identifier.upper() in stored]
        self.has_questions = bool(questions)


class EmailForm(forms.Form):
    email = forms.EmailField(
        label=_("Correo con el que compraste antes"),
        max_length=190,
        widget=forms.EmailInput(attrs={"autocomplete": "email"}),
    )


class CodeForm(forms.Form):
    code = forms.CharField(
        label=_("Código de 6 dígitos"),
        max_length=20,
        widget=forms.TextInput(attrs={
            "autocomplete": "one-time-code",
            "inputmode": "numeric",
            "pattern": "[0-9 ]*",
        }),
    )

    def clean_code(self):
        code = re.sub(r"\s+", "", self.cleaned_data["code"])
        if not re.fullmatch(r"\d{6}", code):
            raise forms.ValidationError(_("El código tiene 6 dígitos."))
        return code
