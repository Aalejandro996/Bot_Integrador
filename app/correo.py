"""Envío de correo (enlaces de activación, alertas de seguridad y avisos de operación), igual que en Contax.

MAIL_BACKEND=brevo (API transaccional de Brevo, BREVO_API_KEY), smtp (STARTTLS o SSL) o console (solo desarrollo: el correo se
escribe en el log y queda en ENVIADOS). Los enlaces se construyen con APP_BASE_URL, NUNCA con la cabecera Host de la petición
(evita el envenenamiento de enlaces). Los avisos no críticos se envían en segundo plano para no demorar el bot ni el inicio de sesión."""
import logging
import re
import smtplib
import ssl
import threading
from email.message import EmailMessage

from flask import current_app

from . import brevo, plantilla_correo

log = logging.getLogger("bot.correo")
ENVIADOS = []  # backend "console": últimos mensajes (lo usan las pruebas)
_VALIDO = re.compile(r"[^@\s<>,;]+@[^@\s<>,;]+\.[^@\s<>,;]+")


def valido(destino) -> bool:
    return bool(destino) and len(destino) <= 254 and bool(_VALIDO.fullmatch(destino))


def real() -> bool:
    """True si el correo sale de verdad (Brevo o SMTP): entonces los enlaces de un solo uso NO se muestran en pantalla."""
    cfg = current_app.config
    return cfg.get("MAIL_BACKEND") == "brevo" and bool(cfg.get("BREVO_API_KEY")) or cfg.get("MAIL_BACKEND") == "smtp" and bool(cfg.get("SMTP_HOST"))


def _mensaje(cfg, destino, asunto, cuerpo, boton=None, titulo=None, destacado=None, filas=(), nota=None, aviso=None, html=None, texto=None):
    """html/texto: correo ya armado con otra plantilla (p. ej. plantilla_correo.documento_html)."""
    parrafos = [p.strip() for p in cuerpo.split("\n\n") if p.strip()]
    titulo = titulo or asunto
    txt = (texto or plantilla_correo.texto(titulo, parrafos, boton, destacado, filas, nota, cfg["APP_NOMBRE"])) + "\n" + plantilla_correo.PIE
    htm = html or plantilla_correo.html(titulo, parrafos, cfg["APP_BASE_URL"], cfg["APP_NOMBRE"], cfg.get("APP_LEMA", ""), boton, destacado, filas, nota, aviso=aviso)
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = cfg["MAIL_FROM"], destino, f"{asunto} · {cfg['APP_NOMBRE']}"
    msg.set_content(txt)
    return msg, txt, htm


def _entregar(cfg, destino, msg, txt, htm):
    if cfg["MAIL_BACKEND"] == "console":
        ENVIADOS.append(msg); del ENVIADOS[:-50]
        log.info("CORREO (console) para %s: %s", destino, msg["Subject"])
        return
    if cfg["MAIL_BACKEND"] == "brevo":
        try:
            brevo.enviar(cfg["BREVO_API_KEY"], cfg["MAIL_FROM"], destino, msg["Subject"], txt, html=htm, etiquetas=("surtia",))
        except brevo.ErrorCorreo as e:
            raise OSError(str(e)) from e
        return
    msg.add_alternative(htm, subtype="html")
    ctx = ssl.create_default_context()
    if cfg["SMTP_SSL"]:
        with smtplib.SMTP_SSL(cfg["SMTP_HOST"], cfg["SMTP_PORT"], context=ctx, timeout=15) as s:
            if cfg["SMTP_USER"]: s.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            s.send_message(msg)
    else:
        with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15) as s:
            s.starttls(context=ctx)  # nunca credenciales en claro
            if cfg["SMTP_USER"]: s.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
            s.send_message(msg)


def enviar(destino: str, asunto: str, cuerpo: str, **kw):
    """cuerpo: párrafos separados por una línea en blanco. kw: boton=(texto, url), titulo, destacado=(etiqueta, valor),
    filas=[(etiqueta, valor)], nota, aviso=(tono, texto). Lanza OSError si no se pudo enviar."""
    if not valido(destino): raise OSError("Destinatario inválido")
    cfg = current_app.config
    msg, txt, htm = _mensaje(cfg, destino, asunto, cuerpo, **kw)
    _entregar(cfg, destino, msg, txt, htm)


def enviar_seguro(destino, asunto, cuerpo, **kw) -> bool:
    """Para avisos no críticos: un fallo del correo nunca tumba la petición."""
    try:
        enviar(destino, asunto, cuerpo, **kw); return True
    except (OSError, smtplib.SMTPException):
        log.exception("No se pudo enviar el correo «%s» a %s", asunto, destino); return False


def en_segundo_plano(destinos, asunto, cuerpo, **kw):
    """Avisos de operación (pedidos, WhatsApp, alertas): se arman ahora y se envían en un hilo aparte, sin demorar al bot.
    Con el backend console se envían en el acto (pruebas y desarrollo)."""
    cfg = current_app.config
    destinos = sorted({d.strip().lower() for d in destinos if d and valido(d.strip())})
    if not destinos: return 0
    lote = [(d, *_mensaje(cfg, d, asunto, cuerpo, **kw)) for d in destinos]
    datos = {k: cfg.get(k) for k in ("MAIL_BACKEND", "BREVO_API_KEY", "MAIL_FROM", "SMTP_HOST", "SMTP_PORT", "SMTP_USER", "SMTP_PASSWORD", "SMTP_SSL")}

    def trabajo():
        for d, msg, txt, htm in lote:
            try:
                _entregar(datos, d, msg, txt, htm)
            except (OSError, smtplib.SMTPException):
                log.exception("No se pudo enviar el correo «%s» a %s", asunto, d)

    if datos["MAIL_BACKEND"] == "console": trabajo()
    else: threading.Thread(target=trabajo, name="correo", daemon=True).start()
    return len(lote)
