"""Guía de inicio paso a paso. Al activar su cuenta, el administrador ve qué hará el bot y cómo dejarlo funcionando;
cada paso se marca solo según el estado real (WhatsApp vinculado, catálogo conectado por Surtia, saludo personalizado, equipo invitado).
El operador ve una versión corta centrada en atender pedidos y conversaciones."""
from decimal import Decimal

from flask import flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select

from .. import bot as BOT
from .. import servicios as SV
from .. import sistemas as SIS
from ..extensions import db
from ..models import Usuario
from ..seguridad import eid_actual, registrar, requiere
from . import bp

DEMO = {"HAR-01": ("HARINA PAN 1KG", Decimal("45.50"), Decimal("120")), "HAR-02": ("HARINA JUANA 1KG", Decimal("42.00"), Decimal("80")),
        "ACE-01": ("ACEITE VATEL 1L", Decimal("113.15"), Decimal("40")), "ARR-01": ("ARROZ MARY 1KG", Decimal("38.00"), Decimal("0"))}


def conversacion_demo(empresa: str, catalogo: dict) -> list[tuple[str, str]]:
    """Conversación real del bot (bot.py, sin base de datos) con el catálogo de la empresa, o uno de ejemplo si aún no hay."""
    cat = {k: v for k, v in list((catalogo or {}).items())[:400]} or DEMO
    cod, (nombre, _, _) = next(((c, v) for c, v in cat.items() if v[2] is None or v[2] > 0), next(iter(cat.items())))
    palabra = next((p for p in nombre.split() if len(p) >= 4 and p.isalpha()), nombre.split()[0]).lower()
    guion = [("Buenas tardes", "nuevo"), ("1", "nuevo"), (f"tienen {palabra}?", "cliente"), (f"{cod} 2", "cliente"), ("eso es todo", "cliente"), ("gracias", "cliente")]
    chat, est = [], {}
    for texto, tipo in guion:
        respuestas, est, _ = BOT.responder(texto, tipo, est, cat, empresa, nombre="Carlos Pérez")
        chat.append(("cliente", texto))
        chat += [("bot", r) for r in respuestas]
    return chat


def _pasos(eid):
    cfg, con = SV.config_wa(eid), SV.profit_conexion(eid)
    nombre = SIS.nombre(con.sistema if con else None, con.opciones if con else None)
    modo = SIS.SISTEMAS.get(con.sistema if con else "profit", SIS.SISTEMAS["profit"])["modo"]
    equipo = db.session.execute(select(func.count()).select_from(Usuario).where(Usuario.empresa_id == eid)).scalar() or 0
    wa = bool(cfg and cfg.sesion_estado == "ready")
    return [
        dict(id="conozca", icono="chat", titulo="Conozca a su asistente", hecho=True, quien="todos",
             texto="Surtia es el asistente virtual de su empresa en WhatsApp. Atiende a sus clientes a cualquier hora, con el catálogo, las existencias "
                   f"y los precios de {nombre}, siempre actualizados.",
             hace=["Saluda por el nombre y pregunta si le escribe un cliente o un proveedor.",
                   "Muestra el catálogo y busca productos por nombre: «tienen harina?».",
                   "Arma el pedido con la existencia real y avisa si algo no alcanza.",
                   "Prepara presupuestos y lee órdenes de compra en PDF.",
                   "Le avisa a usted por correo de cada pedido nuevo.",
                   "Pasa la conversación a una persona cuando el cliente lo pide."],
             no_hace=[f"No escribe ni modifica nada en {nombre} (solo lectura).", f"No cobra ni emite la factura fiscal: eso sigue en {nombre}."]),
        dict(id="whatsapp", icono="whatsapp", titulo="Vincule el WhatsApp de la empresa", hecho=wa, quien="admin", url=url_for("web.whatsapp"), boton="Ir a WhatsApp",
             texto="El bot responde desde el número de su empresa. Se vincula una sola vez, como WhatsApp Web.",
             hace=["Entre a WhatsApp y pulse «Conectar».", "En el teléfono de la empresa: WhatsApp → Dispositivos vinculados → Vincular un dispositivo.",
                   "Escanee el código QR de la pantalla. Listo: el estado cambia a «Conectado»."],
             consejo="Use un número dedicado a ventas y deje ese teléfono con Internet. Si se desvincula, Surtia le avisa por correo."),
        dict(id="sistema", icono="base", titulo=f"Su catálogo de {nombre}: lo conectamos nosotros", hecho=bool(con and con.ultima_sync), quien="todos",
             url=url_for("web.catalogo"), boton="Ver el catálogo",
             texto=f"El equipo de Surtia conecta {nombre} por usted: no tiene que instalar ni configurar nada. Surtia solo lee artículos, existencias, "
                   "precios y clientes; nunca escribe ni modifica nada.",
             hace=(["Coordinamos con usted o su técnico la instalación del conector de solo lectura (sin abrir puertos)."] if modo == "agente" else
                   ["Nos conectamos a su sistema en la nube con un acceso de solo lectura."] if modo == "nube" else
                   ["Usted nos envía su lista de productos en Excel y nosotros la cargamos."] if modo == "archivo" else
                   ["Entregamos a su proveedor los datos de la API para que envíe el catálogo."]) +
                  ["Elegimos con usted los almacenes, la lista de precios y qué productos ofrece el bot.",
                   "Cuando esté listo, verá sus productos en «Catálogo» y este paso se marcará solo."],
             consejo="¿Quiere cambiar algo del catálogo (almacenes, precios, productos)? Escríbanos por WhatsApp y lo ajustamos."),
        dict(id="voz", icono="chat", titulo="Personalice cómo habla el bot", hecho=bool(cfg and (cfg.bienvenida or cfg.instrucciones_pago)), quien="admin",
             url=url_for("web.whatsapp"), boton="Personalizar",
             texto="Ajuste el saludo y la información que el bot da al cerrar cada pedido.",
             hace=["Mensaje de bienvenida: «¡Hola! Gracias por escribir a …».", "Cómo pagar o retirar: cuentas, pago móvil, horario y dirección.",
                   "Presupuestos: que el bot los envíe al instante o que un asesor los revise primero."]),
        dict(id="equipo", icono="usuarios", titulo="Invite a su equipo", hecho=equipo > 1, quien="admin", url=url_for("web.usuarios"), boton="Invitar usuarios",
             texto="Cada persona entra con su propio usuario y verificación en dos pasos.",
             hace=["Operador: atiende pedidos, conversaciones y presupuestos.", "Administrador: además configura el WhatsApp, el saludo del bot y los usuarios.",
                   "Cada invitado recibe un enlace de un solo uso para crear su clave."]),
        dict(id="demo", icono="actividad", titulo="Así conversará con sus clientes", hecho=True, quien="todos", demo=True,
             texto="Esta conversación la generó el bot de verdad con " + ("el catálogo de su empresa." if con and con.ultima_sync else "un catálogo de ejemplo (el suyo aparecerá cuando lo conectemos).")),
        dict(id="atender", icono="recibo", titulo="Atienda los pedidos", hecho=True, quien="todos", url=url_for("web.pedidos"), boton="Ver pedidos",
             texto=f"Cada pedido llega a Pedidos (y a su correo). Ustedes lo revisan y lo cargan en {nombre} para facturar.",
             hace=["Nuevo → revíselo y márquelo «Confirmado»: el cliente recibe el aviso por WhatsApp.",
                   "Despachado → cuando sale la mercancía.", f"Facturado → cuando emitió la factura en {nombre}.",
                   "En Conversaciones verá quién pidió hablar con una persona y podrá responderle desde aquí.",
                   "Exporte los pedidos a Excel (CSV) con el código de cliente para cargarlos en su sistema."]),
        dict(id="prueba", icono="ok", titulo="Haga una prueba", hecho=False, quien="admin",
             texto="Desde otro teléfono escríbale «Hola» al número de la empresa y pida un producto. Verá el pedido aparecer en Pedidos.",
             hace=["Pruebe buscar por nombre, agregar con «código cantidad» y confirmar con «eso es todo».",
                   "Escriba «quiero hablar con una persona» para ver cómo le avisa a su equipo."]),
    ]


@bp.route("/guia", methods=["GET", "POST"])
@requiere("Administrador", "Operador")
def guia():
    eid, u = eid_actual(), g.usuario
    if request.method == "POST":
        u.guia_vista = True; registrar("GUIA_COMPLETADA", ""); db.session.commit()
        flash("¡Listo! Puede volver a la guía cuando quiera desde el menú «Guía de inicio».")
        return redirect(url_for("web.inicio"))
    pasos = [p for p in _pasos(eid) if p["quien"] == "todos" or u.rol == "Administrador"]
    for i, p in enumerate(pasos, 1): p["n"] = i
    hechos = sum(1 for p in pasos if p["hecho"])
    return render_template("guia.html", titulo="Guía de inicio", pasos=pasos, hechos=hechos, total=len(pasos),
                           chat=conversacion_demo(g.empresa.razon_social, SV.catalogo(eid)))
