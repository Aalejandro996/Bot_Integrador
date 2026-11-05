"""Avisos por correo, como en Contax: enlaces de activación, alertas de seguridad de la cuenta y avisos de operación
(pedido nuevo, cliente que pide una persona, presupuesto aceptado, WhatsApp desconectado).

Los avisos de operación se ENCOLAN durante la petición y solo salen después de guardar en la base de datos (despachar),
para no avisar de algo que luego se deshizo. Nunca llevan conversaciones completas ni datos del sistema de la empresa: solo lo necesario."""
from flask import current_app, g, has_request_context, request, url_for
from sqlalchemy import select

from . import correo
from .extensions import db
from .models import Empresa, Usuario
from .tiempo import ahora_ve

NOTA_SEGURIDAD = "Surtia nunca le pedirá su contraseña ni sus códigos de verificación por correo, WhatsApp ni teléfono."


def _url(endpoint, **kw) -> str:
    return current_app.config["APP_BASE_URL"].rstrip("/") + url_for(endpoint, **kw)


def _hora() -> str:
    return ahora_ve().strftime("%d/%m/%Y %I:%M %p").lower() + " (hora de Venezuela)"


def _ip() -> str:
    return (request.remote_addr or "—") if has_request_context() else "—"


# ====================================================================== cola: se envía después del commit
def encolar(destinos, asunto, cuerpo, **kw):
    if not has_request_context():
        correo.en_segundo_plano(destinos, asunto, cuerpo, **kw); return
    g.setdefault("_avisos", []).append((list(destinos), asunto, cuerpo, kw))


def despachar():
    for destinos, asunto, cuerpo, kw in g.pop("_avisos", []):
        correo.en_segundo_plano(destinos, asunto, cuerpo, **kw)


def descartar():
    g.pop("_avisos", None)


def correos_empresa(eid, roles=("Administrador",)) -> list[str]:
    """Correo de la empresa + correos de sus usuarios activos con esos roles."""
    e = db.session.get(Empresa, eid)
    us = db.session.execute(select(Usuario.email).where(Usuario.empresa_id == eid, Usuario.activo.is_(True), Usuario.rol.in_(roles),
                                                        Usuario.email.is_not(None))).scalars().all()
    return [x for x in [e.correo if e else None, *us] if x]


def correos_maestro() -> list[str]:
    us = db.session.execute(select(Usuario.email).where(Usuario.rol == "Maestro", Usuario.activo.is_(True), Usuario.email.is_not(None))).scalars().all()
    return [*us, current_app.config.get("MAESTRO_EMAIL") or ""]


# ====================================================================== enlaces de activación
def enlace_activacion(u: Usuario, enlace: str, motivo="invitacion", empresa: Empresa | None = None) -> tuple[bool, str]:
    """Envía el enlace de un solo uso al correo del usuario. Devuelve (enviado, texto para la pantalla).
    Si el correo sale de verdad, el enlace NO se muestra en pantalla (solo lo recibe su dueño)."""
    nuevo = motivo == "reenlace"
    org = f" de {empresa.razon_social}" if empresa else (" de la Consola Maestro" if u.rol == "Maestro" else "")
    asunto = "Nuevo enlace para su cuenta" if nuevo else "Active su cuenta"
    cuerpo = (f"Hola{(' ' + u.nombre) if u.nombre else ''}:\n\n"
              + (f"Se generó un nuevo enlace para su cuenta{org}. Al usarlo se reemplazan su contraseña y su verificación en dos pasos, "
                 "y se cierran sus sesiones abiertas." if nuevo else
                 f"Se creó su cuenta{org} en Surtia, el asistente de pedidos por WhatsApp conectado a su sistema administrativo.")
              + "\n\nCon el botón creará su contraseña y activará la verificación en dos pasos (Google Authenticator, Microsoft Authenticator u otra).")
    filas = [("Usuario", u.username), ("Rol", u.rol), ("Vigencia", "72 horas, un solo uso")]
    enviado = False
    if u.email:
        enviado = correo.enviar_seguro(u.email, asunto, cuerpo, boton=("Crear mi contraseña", enlace), filas=filas,
                                       nota="Si no esperaba este correo, ignórelo: sin el enlace nadie puede activar la cuenta. " + NOTA_SEGURIDAD,
                                       aviso=("info", "Activación de cuenta"))
    if enviado and correo.real():
        return True, f"Enviamos el enlace de activación de {u.username} a {u.email} (72 horas, un solo uso)."
    falla = " No se pudo enviar por correo:" if u.email and not enviado else ""
    return enviado, f"{falla} Envíe este enlace a {u.username} (72 horas, un solo uso): {enlace}".strip()


# ====================================================================== seguridad de la cuenta
_SEGURIDAD = {
    "login": ("Nuevo inicio de sesión", "Se inició sesión en su cuenta.", "info"),
    "bloqueo": ("Cuenta bloqueada por intentos fallidos", "Su cuenta se bloqueó 15 minutos por varios intentos fallidos de contraseña o de código.", "peligro"),
    "clave": ("Su contraseña cambió", "La contraseña de su cuenta se cambió y se cerraron sus otras sesiones.", "alerta"),
    "mfa": ("Verificación en dos pasos activada", "Se activó la verificación en dos pasos de su cuenta en una aplicación autenticadora.", "info"),
}


def seguridad(u: Usuario, evento: str):
    if not u or not u.email or evento not in _SEGURIDAD: return
    asunto, texto, tono = _SEGURIDAD[evento]
    filas = [("Usuario", u.username), ("Fecha", _hora()), ("Dirección IP", _ip())]
    if has_request_context(): filas.append(("Navegador", (request.user_agent.string or "—")[:90]))
    encolar([u.email], asunto, texto + "\n\nSi fue usted, no tiene que hacer nada. Si no lo reconoce, cambie su contraseña de inmediato "
            "y avise a su administrador (o a Gigadevs).", filas=filas, nota=NOTA_SEGURIDAD, aviso=(tono, "Seguridad de la cuenta"),
            boton=("Abrir Surtia", _url("auth.login")) if evento != "bloqueo" else None)


# ====================================================================== operación del bot
def _bs(v) -> str:
    return f"{float(v or 0):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _documento(d, eid, intro, boton, aviso):
    """Correo con el documento completo (plantilla documento_html) para el personal de la empresa."""
    from . import plantilla_correo as PC
    cfg, em = current_app.config, db.session.get(Empresa, eid)
    return dict(html=PC.documento_html(d, em, cfg["APP_BASE_URL"], cfg["APP_NOMBRE"], cfg.get("APP_LEMA", ""), intro=intro, boton=boton, aviso=aviso),
                texto=PC.documento_texto(d, em, intro, boton, cfg["APP_NOMBRE"]))


def _sistema(eid) -> str:
    from .servicios import nombre_sistema
    return nombre_sistema(eid)


def pedido_nuevo(p):
    tipo = "Orden de venta (PDF)" if p.origen == "whatsapp_pdf" else ("Pedido" if p.tipo == "venta" else "Solicitud de oferta")
    filas = [("Número", f"N° {p.numero}"), ("Tipo", tipo), ("Cliente", p.nombre or "—"), ("WhatsApp", f"+{p.telefono}"),
             ("RIF", p.rif or "—"), ("Productos", len(p.lineas or [])), ("Total", f"Bs {_bs(p.total)}")]
    if p.referencia_cliente: filas.append(("Orden de compra del cliente", p.referencia_cliente))
    from .servicios import documento_pedido
    intro = (f"{p.nombre or 'Un cliente'} hizo un {tipo.lower()} por WhatsApp (+{p.telefono}). Revíselo, confirme la existencia "
             f"y cárguelo en {_sistema(p.empresa_id)} para emitir la factura.")
    boton = ("Abrir el pedido en Surtia", _url("web.pedido", pid=p.id))
    encolar(correos_empresa(p.empresa_id, ("Administrador", "Operador")), f"{tipo} N° {p.numero} por WhatsApp", intro,
            filas=filas, boton=boton, **_documento(documento_pedido(p), p.empresa_id, intro, boton, ("info", "Pedido nuevo")))


def atencion(c):
    encolar(correos_empresa(c.empresa_id, ("Administrador", "Operador")), "Un cliente pide hablar con una persona",
            f"{c.nombre or 'Un cliente'} pidió atención de una persona en el WhatsApp de la empresa. El bot dejó de insistir: respóndale desde Conversaciones.",
            filas=[("Cliente", c.nombre or "—"), ("WhatsApp", f"+{c.telefono}"), ("Fecha", _hora())],
            boton=("Responder la conversación", _url("web.conversacion", cid=c.id)), aviso=("alerta", "Atención humana"))


def presupuesto_aceptado(pv):
    from .servicios import documento_presupuesto
    intro = f"{pv.cliente_nombre or 'El cliente'} aceptó el presupuesto por WhatsApp. Ya puede facturarlo en {_sistema(pv.empresa_id)} y coordinar la entrega."
    boton = ("Abrir el presupuesto en Surtia", _url("web.presupuesto", pid=pv.id))
    encolar(correos_empresa(pv.empresa_id, ("Administrador", "Operador")), f"Presupuesto N° {pv.numero} aceptado", intro, boton=boton,
            **_documento(documento_presupuesto(pv), pv.empresa_id, intro, boton, ("info", "Presupuesto aceptado")))


ESTADOS_CAIDA = ("disconnected", "failed", "action_required", "logged_out", "expired")


def whatsapp_cambio(cfg, anterior, nuevo):
    """Solo avisa cuando el número deja de estar vinculado o vuelve a estarlo (no en cada paso del QR)."""
    e = db.session.get(Empresa, cfg.empresa_id)
    if anterior == "ready" and nuevo in ESTADOS_CAIDA:
        encolar(correos_empresa(cfg.empresa_id) + correos_maestro(), "El WhatsApp del bot se desconectó",
                f"El número de WhatsApp de {e.razon_social if e else 'la empresa'} dejó de estar vinculado: el bot NO está respondiendo a los clientes.\n\n"
                "Entre a Surtia → WhatsApp y vuelva a escanear el código QR con el teléfono de la empresa.",
                filas=[("Empresa", e.razon_social if e else "—"), ("Número", f"+{cfg.numero_visible}" if cfg.numero_visible else "—"),
                       ("Estado", nuevo), ("Fecha", _hora())],
                boton=("Volver a vincular", _url("web.whatsapp")), aviso=("peligro", "Bot sin conexión"))
    elif nuevo == "ready" and anterior in ESTADOS_CAIDA:
        encolar(correos_empresa(cfg.empresa_id), "El WhatsApp del bot está conectado",
                f"El número de WhatsApp de {e.razon_social if e else 'la empresa'} volvió a quedar vinculado. El bot ya responde a los clientes.",
                filas=[("Número", f"+{cfg.numero_visible}" if cfg.numero_visible else "—"), ("Fecha", _hora())], aviso=("info", "Bot en línea"))


def bienvenida(u: Usuario):
    """Al activar su cuenta, el administrador (u operador) recibe los primeros pasos y el enlace a la guía."""
    e = db.session.get(Empresa, u.empresa_id) if u.empresa_id else None
    if not u.email or e is None: return
    if u.rol == "Administrador":
        pasos = "1) Vincule el WhatsApp de la empresa con el código QR.\n\n2) Personalice el saludo del bot y la información de pago.\n\n" \
                f"3) Invite a su equipo y haga una prueba escribiéndole al número.\n\nLa conexión con {_sistema(e.id)} la hace el equipo de Surtia: usted no tiene que instalar nada."
    else:
        pasos = "En Pedidos verá lo que piden los clientes por WhatsApp; en Conversaciones, a quienes piden hablar con una persona.\n\n" \
                "Cambie el estado de cada pedido (Confirmado, Despachado, Facturado) y el cliente recibe el aviso por WhatsApp."
    encolar([u.email], f"Bienvenido a Surtia, {e.razon_social}",
            f"Su cuenta ya está activa. Surtia será el asistente de WhatsApp de {e.razon_social}: atiende a sus clientes a cualquier hora "
            f"con el catálogo, las existencias y los precios de {_sistema(e.id)}, y le deja los pedidos listos para facturar.\n\n" + pasos,
            boton=("Abrir la guía paso a paso", _url("web.guia")), aviso=("info", "Primeros pasos"),
            nota="La guía también está en el menú «Guía de inicio» cuando la necesite.")
