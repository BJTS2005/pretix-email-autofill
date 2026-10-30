Cambios
=======

2.0.0
-----

Reescritura completa. La 1.0.0 no llegaba a funcionar en ninguna versión de pretix.

Corregido:

- El plugin no cargaba: la AppConfig estaba en ``__init__.py`` con ``default_app_config``,
  que Django ignora desde la 4.1. Ahora vive en ``apps.py``.
- La página de ajustes no mostraba preguntas (``{% regroup ... by field.field.item %}``) y
  guardaba un ajuste basura ``q_<id>`` por pregunta. Ahora es una lista de casillas y se guarda
  junto con el registro de cambios de pretix.
- ``QuestionAnswer.objects.filter(order=...)``: error 500 en pretix 2026.4–2026.7 y sin
  resultados para preguntas por entrada desde 2026.8.
- ``cart_session(request, create=False)`` lanzaba TypeError (silenciado) en pretix ≤ 2026.3.
- El email se pedía en la misma página que las preguntas, así que nunca se rellenaba nada en
  la primera visita.
- Tipos: las opciones no se seleccionaban, un "No" aparecía marcado, el teléfono no se
  rellenaba y una pregunta de fecha y hora tumbaba la página.
- Fuga de datos: cualquiera que escribiera el correo de otra persona veía sus respuestas.
  Ahora se exige un código enviado a ese correo.
- Los IDs de preguntas se perdían al clonar eventos: ahora se guardan identificadores.
- ``pretixplugin.toml`` no era TOML válido.

Nuevo:

- Paso "Autocompletar" con código de 6 dígitos y límites de intentos.
- Búsqueda en todos los eventos del organizador, emparejando preguntas por identificador.
- Cada entrada del carrito se rellena con la entrada equivalente de compras anteriores.
- Opción para rellenar nombre, email, empresa y dirección del asistente.
- Índice ``UPPER(email)`` en PostgreSQL y búsqueda única por verificación (antes se repetía
  por cada entrada en cada carga de página).
- Pruebas automáticas en ``tests/``.
