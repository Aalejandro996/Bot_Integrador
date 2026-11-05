"""Páginas de error de Surtia: cada código explica qué pasó, qué puede hacer la persona y ofrece la salida adecuada.

Nunca muestran trazas ni detalles técnicos. Un error interno (500) recibe un código de referencia corto que queda en el log
junto con la traza: el cliente se lo dicta a soporte y se encuentra el fallo sin exponer nada. Las rutas de API y de avisos de
la pasarela responden JSON. La página no consulta la base de datos (puede ser justo lo que falló)."""
import logging
import secrets

from flask import g, render_template, request, url_for
from flask_wtf.csrf import CSRFError
from werkzeug.exceptions import HTTPException

log = logging.getLogger("bot.errores")

# codigo: (título, explicación, ícono, tono, acciones)  · acciones: inicio | volver | login | recargar | soporte
CATALOGO = {
    400: ("No pudimos procesar la solicitud", "Algún dato llegó incompleto o con un formato que no esperábamos. Revise lo que escribió e intente de nuevo.",
          "alerta", "ambar", ("volver", "inicio")),
    401: ("Su sesión terminó", "Por seguridad cerramos la sesión tras un tiempo sin actividad o porque el paso de verificación venció. Vuelva a iniciar sesión para continuar.",
          "candado", "azul", ("login",)),
    403: ("No tiene permiso para ver esto", "Su usuario no tiene acceso a esta sección. Si cree que debería tenerlo, pídaselo al administrador de su empresa.",
          "candado", "rojo", ("inicio", "volver")),
    404: ("No encontramos esta página", "Puede que el enlace esté mal escrito, que la página se haya movido o que lo que busca ya no exista.",
          "buscar", "menta", ("inicio", "volver")),
    405: ("Esa acción no está permitida aquí", "La página no acepta esta operación. Vuelva atrás y use los botones de la pantalla.",
          "alerta", "ambar", ("volver", "inicio")),
    413: ("El archivo es demasiado grande", "El límite es de 10 MB. Reduzca el archivo (por ejemplo, exporte el PDF con menor calidad) e intente otra vez.",
          "documento", "ambar", ("volver",)),
    419: ("La página venció", "Por seguridad, los formularios caducan cuando pasa mucho tiempo o se abren en otra pestaña. Recargue la página y envíe de nuevo.",
          "reloj", "azul", ("recargar", "inicio")),
    429: ("Vamos más despacio", "Recibimos demasiadas solicitudes seguidas desde su conexión. Espere un minuto e intente de nuevo.",
          "reloj", "ambar", ("recargar", "inicio")),
    500: ("Algo salió mal de nuestro lado", "No es su culpa: ocurrió un error inesperado y ya quedó registrado. Intente de nuevo en unos minutos; "
          "si se repite, escríbanos con el código de referencia.", "alerta", "rojo", ("recargar", "inicio", "soporte")),
    502: ("Un servicio no respondió", "Uno de los servicios que usamos tardó demasiado o no está disponible. Intente de nuevo en unos minutos.",
          "antena", "rojo", ("recargar", "inicio", "soporte")),
    503: ("Estamos en mantenimiento", "Estamos haciendo mejoras y volvemos en unos minutos. Gracias por su paciencia.",
          "servidor", "azul", ("recargar", "soporte")),
}
SOPORTE = "https://wa.me/584242588321?text="


def pagina(codigo: int, ref: str | None = None, estado: int | None = None):
    titulo, texto, icono, tono, acciones = CATALOGO.get(codigo, CATALOGO[500])
    if request.path.startswith(("/api/", "/wa/")):
        cuerpo = {"ok": False, "error": titulo}
        if ref: cuerpo["referencia"] = ref
        return cuerpo, estado or codigo
    usuario = g.get("usuario")
    msg = f"Hola Gigadevs, necesito ayuda con Surtia (error {codigo}{' · ref ' + ref if ref else ''})."
    try:
        html = render_template("error.html", codigo=codigo, titulo=titulo, texto=texto, icono_err=icono, tono=tono, acciones=acciones, ref=ref,
                               soporte=SOPORTE + msg.replace(" ", "%20"), con_sesion=usuario is not None,
                               url_login=url_for("auth.login", siguiente=request.path if request.method == "GET" else None))
    except Exception:  # noqa: BLE001 — si hasta la plantilla falla, una página mínima (nunca una traza)
        log.exception("No se pudo dibujar la página de error")
        html = f"<!doctype html><meta charset=utf-8><title>Error {codigo}</title><h1>{titulo}</h1><p>{texto}</p><p><a href='/'>Ir al inicio</a></p>"
    return html, estado or codigo


def registrar(app):
    from .extensions import db

    def http(e: HTTPException):
        return pagina(e.code or 500)

    def csrf(e):
        return pagina(419, estado=400)

    def interno(e):
        try: db.session.rollback()
        except Exception: pass  # noqa: BLE001,S110
        ref = secrets.token_hex(3).upper()
        original = getattr(e, "original_exception", None) or e
        log.error("Error interno ref=%s %s %s", ref, request.method, request.path, exc_info=(type(original), original, original.__traceback__))
        return pagina(500, ref=ref)

    for codigo in (400, 401, 403, 404, 405, 413, 429, 502, 503):
        app.register_error_handler(codigo, http)
    app.register_error_handler(CSRFError, csrf)
    app.register_error_handler(500, interno)
    app.register_error_handler(Exception, lambda e: http(e) if isinstance(e, HTTPException) else interno(e))
