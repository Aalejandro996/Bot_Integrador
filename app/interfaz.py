"""Ayudantes de la interfaz: íconos de línea (sprite static/iconos.svg) y estilo de las tarjetas de indicadores."""
from markupsafe import Markup, escape
from flask import url_for

TONOS = ("menta", "azul", "violeta", "ambar", "indigo", "rojo")


def icono(nombre: str, clase: str = "") -> Markup:
    url = url_for("static", filename="iconos.svg")
    return Markup(f'<svg class="i {escape(clase)}" aria-hidden="true" focusable="false"><use href="{url}#{escape(nombre)}"></use></svg>')


def _bueno(v) -> bool | None:
    t = str(v).lower()
    if any(x in t for x in ("desconect", "falló", "error", "sin config", "suspend", "requiere", "sin respuesta", "atrasado")): return False
    if any(x in t for x in ("conectado", "activa", "activo", "hace un momento", "en orden", "hace ", "en línea")): return True
    return None


def kpi_estilo(etiqueta, valor, n=0) -> tuple[str, str]:
    """(ícono, tono) de una tarjeta según lo que muestra; el tono del estado manda (verde bien, rojo mal, ámbar pendiente)."""
    e = str(etiqueta).lower()
    reglas = [("facturado", "recibo", "menta"), ("whatsapp", "whatsapp", None), ("pasarela", "antena", None), ("sesion", "whatsapp", "violeta"), ("profit", "base", "azul"),
              ("conector", "servidor", None), ("foto", "reloj", "azul"), ("api", "servidor", None), ("persona", "persona", "ambar"),
              ("atenci", "persona", "ambar"), ("mensaje", "chat", "violeta"), ("pedidos nuevos", "rayo", "ambar"), ("nuevo", "rayo", "ambar"),
              ("confirmado", "ok", "azul"), ("despachado", "camion", "violeta"), ("facturado", "recibo", "menta"), ("pedido", "paquete", "menta"),
              ("presupuesto", "documento", "indigo"), ("empresa", "edificio", "indigo"), ("alerta", "alerta", None), ("artículo", "paquete", "azul"),
              ("cliente", "usuarios", "indigo"), ("almac", "edificio", "azul"), ("tasa", "tasa", "menta"), ("iva", "recibo", "azul"),
              ("envío", "correo", "azul"), ("correo", "correo", "azul"), ("remitente", "persona", "indigo"), ("estado", "ok", None),
              ("usuario", "usuarios", "indigo"), ("origen", "enlace", "azul"), ("sincron", "sube", "menta")]
    for clave, ico, tono in reglas:
        if clave in e:
            if tono is None:
                b = _bueno(valor)
                if clave == "alerta": b = str(valor).strip() in ("0", "")
                tono = "menta" if b else ("rojo" if b is False else "ambar")
                if clave == "estado" and b is not True: ico = "alerta"
            return ico, tono
    return "actividad", TONOS[n % len(TONOS)]


def formato_whatsapp(texto: str) -> Markup:
    """*negrita*, _cursiva_ y saltos de línea de WhatsApp → HTML (se escapa primero: nunca pasa HTML del texto)."""
    import re
    t = str(escape(texto or ""))
    t = re.sub(r"\*([^*\n]+)\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<![\w/])_([^_\n]+)_(?!\w)", r"<i>\1</i>", t)
    return Markup(t.replace("\n", "<br>"))


def registrar(app):
    app.jinja_env.globals.update(icono=icono, kpi_estilo=kpi_estilo)
    app.jinja_env.filters["whatsapp"] = formato_whatsapp
