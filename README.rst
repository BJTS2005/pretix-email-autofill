Autocompletado por correo para pretix
=====================================

Plugin para pretix (self-hosted) que permite a quien ya compró antes recuperar sus
respuestas (cédula, teléfono, institución…) en una nueva compra, **sin crear cuentas ni
contraseñas**.

Cómo funciona
-------------

1. Antes del paso de preguntas aparece un paso opcional, **Autocompletar**.
2. El comprador escribe su correo y pide un código.
3. Si ese correo tiene pedidos pagados o pendientes en **cualquier evento del mismo
   organizador**, le llega un código de 6 dígitos que vence en 10 minutos.
4. Al ingresarlo, las preguntas marcadas aparecen rellenas en el paso siguiente. El
   comprador las revisa y puede cambiarlas antes de continuar.

Quien compra por primera vez solo pulsa **Continuar sin autocompletar**.

¿Por qué un código?
~~~~~~~~~~~~~~~~~~~

Si basta con escribir un correo, cualquiera que conozca el email de otra persona vería su
cédula y su teléfono. El código demuestra que quien compra tiene acceso a ese buzón. La
pantalla es idéntica exista o no una compra anterior, así que tampoco revela quién compró.

Instalación
-----------

En el entorno virtual de pretix:

.. code-block:: bash

    pip install -e /ruta/a/pretix-email-autofill
    python -m pretix migrate      # crea un índice en PostgreSQL (ver "Rendimiento")
    # reinicia pretix (web y celery)

Con Docker, crea tu propia imagen:

.. code-block:: docker

    FROM pretix/standalone:stable
    USER root
    COPY pretix-email-autofill /tmp/pretix-email-autofill
    RUN pip3 install /tmp/pretix-email-autofill
    USER pretixuser
    RUN cd /pretix/src && make production

Configuración
-------------

1. En el evento: **Ajustes → Plugins** y activa *Autocompletado por correo*.
2. **Ajustes → Autocompletado**: activa la opción y marca las preguntas que se rellenan.
3. Opcional: *Rellenar también los datos del asistente* (nombre, email, empresa y
   dirección, si el evento los pide).

**Importante:** entre eventos, las preguntas se reconocen por su **identificador interno**
(pestaña *Avanzado* de cada pregunta). Usa el mismo identificador en todos tus eventos, por
ejemplo ``CEDULA`` o ``TELEFONO``. Los eventos clonados ya lo conservan. Mayúsculas y
minúsculas dan igual.

Reglas
------

- Busca en los últimos 20 pedidos pagados o pendientes de ese correo, en todos los eventos
  del organizador. Si la tienda está en vivo, ignora los pedidos de prueba.
- La entrada 1 del carrito se rellena con la entrada 1 de las compras anteriores, la 2 con la
  2, etc. Manda el pedido más reciente; si le falta un dato, se toma de uno anterior.
- Nunca reemplaza lo que el comprador ya escribió.
- Nunca rellena preguntas de archivo ni casillas obligatorias (por ejemplo, aceptar términos).
- Las preguntas de opciones se emparejan por identificador de la opción o, si no coincide,
  por su texto.
- Si el comprador inició sesión con una cuenta de cliente de pretix, el paso no aparece
  (pretix ya tiene sus propios perfiles guardados).
- No guarda nada nuevo: las respuestas se copian al carrito como respuestas normales de
  pretix. El código solo existe como HMAC en la sesión del comprador.

Rendimiento
-----------

- La búsqueda se hace **una sola vez**, al verificar el código: una consulta indexada de
  pedidos y unas pocas más para las respuestas, sin importar cuántas entradas haya en el
  carrito. La página de preguntas no agrega ninguna consulta.
- La migración ``0001`` crea en PostgreSQL un índice funcional ``UPPER(email)`` sobre los
  pedidos (con ``CONCURRENTLY``, sin bloquear la tabla). Medido con 100 000 pedidos:
  la búsqueda pasa de ~60–70 ms a ~0,05 ms. En otras bases de datos no hace nada.

Seguridad y requisitos
----------------------

- Usa el correo saliente que ya tengas configurado en pretix.
- Límites: 3 códigos por sesión cada 10 minutos y 5 intentos por código. Además, por correo,
  5 códigos y 10 intentos por hora; por IP, 20 códigos por hora. Estos últimos necesitan Redis
  o memcached (pretix los recomienda en producción). Sin ellos, siguen valiendo los de sesión.
- Con Celery activo, el envío del correo no cambia el tiempo de respuesta. Sin Celery, una
  respuesta más lenta podría delatar que ese correo ya compró.

Pruebas
-------

Con pretix instalado en modo desarrollo:

.. code-block:: bash

    pip install -e .
    pytest tests

Compatibilidad
--------------

Probado de punta a punta en pretix 2026.9 con SQLite y PostgreSQL 16. Las APIs que usa
existen desde pretix 2025.7.
