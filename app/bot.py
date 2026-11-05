"""Conversación del bot de WhatsApp de la empresa. Lógica PURA (sin BD ni red): recibe el texto, el tipo de contacto,
su estado y el catálogo; devuelve las respuestas, el estado nuevo y los EVENTOS que la capa web persiste
(crear pedido, confirmar/rechazar una orden de compra, clasificar al contacto).

CLIENTES: ver catálogo, armar un pedido («COD 3»), revisarlo, confirmarlo y consultar el estado de sus pedidos.
PROVEEDORES: ver las órdenes de compra que la empresa les emitió, confirmarlas o rechazarlas, y enviar ofertas o
cotizaciones (líneas «COD cantidad precio» o texto libre) que llegan al ERP como pedido de compra."""
import re
import unicodedata
from decimal import Decimal, InvalidOperation

MAX_CARRITO = 30
MAX_CATALOGO = 40
MAX_BUSQUEDA = 15
COD = r"[a-z0-9.\-_/#]{1,40}"


def _n(t) -> str:
    t = unicodedata.normalize("NFKD", str(t or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", t).strip().lower()


def _dec(v):
    try:
        d = Decimal(str(v).replace(",", "."))
        return d if d.is_finite() and d > 0 else None
    except (InvalidOperation, ValueError):
        return None


def bs(v) -> str:
    return f"{Decimal(str(v)):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


SALUDOS = ("hola", "holaa", "buenas", "buen dia", "buenos dias", "buenas tardes", "buenas noches", "saludos", "epa", "hey", "que tal")
GRACIAS = ("gracias", "muchas gracias", "mil gracias", "ok gracias", "listo gracias", "grs", "thanks", "perfecto gracias", "excelente gracias")
DESPEDIDAS = ("chao", "chau", "adios", "hasta luego", "nos vemos", "bye", "hasta manana")
INTENCIONES = {   # frases naturales → opción del menú de clientes
    "1": ("catalogo", "ver catalogo", "productos", "que tienen", "que venden", "lista", "lista de precios", "precios", "menu de productos", "inventario"),
    "2": ("mi pedido", "ver mi pedido", "ver pedido", "carrito", "que llevo", "que tengo", "resumen"),
    "3": ("confirmar", "confirmar pedido", "confirmo", "listo", "eso es todo", "es todo", "seria todo", "si confirmo", "enviar pedido", "procesar"),
    "4": ("mis pedidos", "estado", "estado de mi pedido", "como va mi pedido", "donde esta mi pedido", "ya esta mi pedido"),
    "5": ("persona", "una persona", "asesor", "un asesor", "humano", "alguien", "vendedor", "hablar con alguien", "hablar con una persona",
          "atencion", "atencion al cliente", "agente"),
    "6": ("presupuesto", "cotizar", "cotizacion", "pedir presupuesto", "cotizame", "proforma"),
}


def _intencion(t: str) -> str | None:
    for op, frases in INTENCIONES.items():
        if t in frases: return op
        if op in ("4", "5") and any(t.startswith(f + " ") or t.endswith(" " + f) or (" " + f + " ") in t for f in frases if len(f) > 6): return op
    return None


def _hola(nombre) -> str:
    n = (str(nombre or "").strip().split() or [""])[0].capitalize()
    return f"¡Hola, {n}!" if n and not n.isdigit() and not n.startswith("+") else "¡Hola!"


def menu(tipo, empresa, nombre=None, saludo=True) -> str:
    cab = (_hola(nombre) + " ") if saludo else ""
    if tipo == "proveedor":
        return (f"{cab}Está hablando con el asistente de proveedores de *{empresa}*. ¿En qué le ayudo?\n\n"
                "1️⃣ Ver mis órdenes de compra pendientes\n2️⃣ Enviar una oferta o cotización\n3️⃣ Hablar con alguien de compras\n\n"
                "Para responder una orden escríbame, por ejemplo, *CONFIRMAR 12* o *RECHAZAR 12 y el motivo*.")
    return (f"{cab}Soy el asistente virtual de *{empresa}* 🤖. Puedo tomarle el pedido ahora mismo. ¿Qué necesita?\n\n"
            "1️⃣ Ver el catálogo\n2️⃣ Ver lo que llevo\n3️⃣ Confirmar mi pedido\n4️⃣ ¿Cómo van mis pedidos?\n5️⃣ Hablar con una persona\n"
            "6️⃣ Pedir un presupuesto\n\n"
            "También puede escribirme como le resulte natural: *«harina»* para buscar, *«quiero 3 harina pan»* o *«A001 3»* para agregar, "
            "*«quitar A001»* para sacar algo y *«vaciar»* para empezar de nuevo.")


def _linea(c, v) -> str:
    return f"• *{c}* · {v[0]} — Bs {bs(v[1])}" + ("" if v[2] is None else ("" if Decimal(str(v[2])) > 0 else " _(agotado por ahora)_"))


def buscar(catalogo, consulta, n=MAX_BUSQUEDA):
    """Artículos cuyo nombre (o código) contiene TODAS las palabras buscadas, sin importar acentos ni mayúsculas."""
    palabras = [p for p in _n(consulta).split() if len(p) >= 2][:6]
    if not palabras: return []
    out = []
    for c, v in catalogo.items():
        nom = _n(f"{c} {v[0]}")
        if all(p in nom for p in palabras):
            out.append((c, v))
            if len(out) >= n: break
    return out


def _carrito_texto(carrito, catalogo):
    if not carrito: return "Todavía no ha agregado nada: su pedido está vacío. ¿Le muestro el catálogo? Escriba *1* o el nombre de lo que busca.", Decimal(0)
    lineas, total = [], Decimal(0)
    for cod, cant in carrito.items():
        nom, precio, _ = catalogo.get(cod, (cod, Decimal(0), None))
        sub = Decimal(str(precio)) * Decimal(str(cant)); total += sub
        lineas.append(f"• {cod} {nom} × {cant} = Bs {bs(sub)}")
    return ("🛒 *Esto es lo que lleva hasta ahora:*\n" + "\n".join(lineas) + f"\n\n*Total referencial: Bs {bs(total)}* (sin IVA)"
            "\n¿Lo confirmo? Escriba *3*, o siga agregando productos."), total


def responder(texto, tipo, estado, catalogo, empresa, pedidos=(), ordenes=(), bienvenida=None, nombre=None):
    """texto: mensaje recibido. tipo: nuevo|cliente|proveedor. estado: dict persistido del contacto.
    catalogo: {codigo: (nombre, precio_bs, existencia)}. pedidos: [(numero, estado, total)] del contacto.
    ordenes: [(numero, estado, total, moneda)] de órdenes de compra dirigidas a este proveedor.
    Devuelve (respuestas: list[str], estado: dict, eventos: list[dict])."""
    t, est, ev = _n(texto), dict(estado or {}), []
    carrito = {k: v for k, v in (est.get("carrito") or {}).items()}

    if tipo == "bloqueado": return [], est, ev
    if tipo == "nuevo":
        if t in ("1", "cliente", "soy cliente", "comprar", "quiero comprar", "para comprar"):
            ev.append({"tipo_contacto": "cliente"}); return [menu("cliente", empresa, nombre, saludo=False)], {}, ev
        if t in ("2", "proveedor", "soy proveedor"):
            ev.append({"tipo_contacto": "proveedor"}); return [menu("proveedor", empresa, nombre, saludo=False)], {}, ev
        saludo = bienvenida or f"{_hola(nombre)} Gracias por escribirle a *{empresa}* 😊"
        return [saludo + "\n\nPara atenderle como se merece, cuénteme: ¿nos escribe como *cliente* o como *proveedor*?\n"
                "1️⃣ Soy cliente\n2️⃣ Soy proveedor"], est, ev

    if t in SALUDOS or t in ("menu", "inicio", "0", "ayuda", "opciones") or any(t.startswith(x + " ") for x in SALUDOS[:8]) and len(t) <= 30:
        est.pop("paso", None)
        return [menu(tipo, empresa, nombre, saludo=t not in ("menu", "inicio", "0", "ayuda", "opciones"))], est, ev
    if t in GRACIAS or t.startswith("gracias"):
        return ["¡Con mucho gusto! 🙌 Aquí estoy si necesita algo más."], est, ev
    if t in DESPEDIDAS:
        return ["¡Que tenga un excelente día! Cuando quiera hacer otro pedido, solo escríbame. 👋"], est, ev

    # ------------------------------------------------------------ proveedores
    if tipo == "proveedor":
        if est.get("paso") == "oferta":
            if t in ("fin", "enviar", "listo"):
                lineas, notas = [], []
                for ln in est.get("oferta", []):
                    m = re.fullmatch(r"([A-Za-z0-9.\-_]{1,40})\s+(\d+(?:[.,]\d+)?)\s+(\d+(?:[.,]\d+)?)", ln.strip())
                    if m and _dec(m.group(2)) and _dec(m.group(3)):
                        cod = m.group(1).upper()
                        lineas.append({"codigo": cod, "descripcion": catalogo.get(cod, (cod,))[0], "cantidad": str(_dec(m.group(2))), "precio": str(_dec(m.group(3)))})
                    else: notas.append(ln)
                if not lineas and not notas: return ["Todavía no me ha enviado ninguna línea. Escriba su oferta o *CANCELAR* si prefiere dejarlo para después."], est, ev
                ev.append({"crear_pedido": {"tipo": "compra", "lineas": lineas, "nota": "\n".join(notas)[:2000]}})
                return ["✅ ¡Listo, recibimos su oferta! El equipo de compras la va a revisar y le responde por aquí mismo."], {}, ev
            if t == "cancelar": return ["Sin problema, cancelé la oferta.", menu("proveedor", empresa, nombre, saludo=False)], {}, ev
            of = (est.get("oferta") or []) + [ln for ln in str(texto).splitlines() if ln.strip()][:50]
            return ["Anotado 👍 Siga escribiendo y, cuando termine, envíe *FIN* para remitirla."], {**est, "oferta": of[:200]}, ev
        m = re.fullmatch(r"(confirmar|aceptar|rechazar)\s*(?:oc)?\s*#?\s*(\d{1,9})\s*(.*)", t)
        if m:
            num, mias = int(m.group(2)), {o[0]: o for o in ordenes}
            if num not in mias: return [f"No encontramos la orden de compra N° {num} a su nombre. ¿Puede revisar el número?"], est, ev
            if mias[num][1] not in ("Emitida", "Enviada"): return [f"La orden N° {num} ya está {mias[num][1].lower()}, así que no hace falta hacer nada más."], est, ev
            if m.group(1) == "rechazar":
                ev.append({"oc_rechazada": num, "motivo": str(texto).split(None, 2)[2][:300] if len(str(texto).split(None, 2)) > 2 else "Sin motivo"})
                return [f"Entendido, dejamos registrado que no puede atender la orden N° {num}. Gracias por avisarnos a tiempo."], est, ev
            ev.append({"oc_confirmada": num})
            return [f"✅ ¡Perfecto! La orden de compra N° {num} quedó confirmada. Lo esperamos según las condiciones de entrega."], est, ev
        if t == "1":
            pend = [o for o in ordenes if o[1] in ("Emitida", "Enviada", "Confirmada")]
            if not pend: return ["Por ahora no tiene órdenes de compra pendientes con nosotros. 👌"], est, ev
            return ["📄 *Sus órdenes de compra pendientes:*\n" + "\n".join(f"• N° {o[0]} · {o[1]} · {o[3]} {bs(o[2])}" for o in pend[:20])
                    + "\n\nPara responder escriba *CONFIRMAR* y el número, o *RECHAZAR*, el número y el motivo."], est, ev
        if t == "2":
            return ["¡Claro! Escríbame su oferta, una línea por producto con *código, cantidad y precio* (por ejemplo: A001 50 2,35), "
                    "o como texto libre si le resulta más fácil. Cuando termine, envíe *FIN*."], {"paso": "oferta", "oferta": []}, ev
        if t == "3" or _intencion(t) == "5":
            ev.append({"atencion": True}); return ["Ya le aviso a alguien del equipo de compras; le escribe por este mismo chat en breve. 🙋"], est, ev
        return ["Disculpe, no le entendí bien 😅. Escriba *MENÚ* y le muestro en qué le puedo ayudar."], est, ev

    # ------------------------------------------------------------ clientes
    t = _intencion(t) or t
    if t == "1":
        if not catalogo: return ["En este momento no tengo productos publicados 😕. Si quiere, escriba *5* y le paso con una persona."], est, ev
        lin = [_linea(c, v) for c, v in list(catalogo.items())[:MAX_CATALOGO]]
        mas = (f"\n\nMostrando {MAX_CATALOGO} de {len(catalogo)} productos. Si busca algo en particular, escríbame el nombre (por ejemplo *harina*)."
               if len(catalogo) > MAX_CATALOGO else "")
        return ["📦 *Esto es lo que tenemos disponible:*\n" + "\n".join(lin) + mas
                + "\n\nPara agregar, escríbame el código y la cantidad, por ejemplo *" + next(iter(catalogo)) + " 2*."], est, ev
    if t == "2":
        return [_carrito_texto(carrito, catalogo)[0]], est, ev
    if t in ("3", "confirmar", "confirmar pedido"):
        if not carrito: return ["Su pedido todavía está vacío. Escriba *1* para ver el catálogo o el nombre del producto que busca."], est, ev
        txt, total = _carrito_texto(carrito, catalogo)
        lineas = [{"codigo": c, "descripcion": catalogo.get(c, (c,))[0], "cantidad": str(q), "precio": str(catalogo.get(c, (c, 0))[1])} for c, q in carrito.items()]
        ev.append({"crear_pedido": {"tipo": "venta", "lineas": lineas, "total": str(total)}})
        return ["✅ ¡Pedido recibido, muchas gracias! Ya se lo pasé al equipo; le aviso por aquí apenas lo confirmen.\n\n"
                + txt.split("\n¿Lo confirmo?")[0].replace("Esto es lo que lleva hasta ahora:", "Su pedido:")], {}, ev
    if t == "4":
        if not pedidos: return ["Todavía no tiene pedidos con nosotros. ¿Hacemos el primero? Escriba *1* para ver el catálogo. 😊"], est, ev
        return ["📋 *Así van sus pedidos:*\n" + "\n".join(f"• N° {p[0]} · {p[1]} · Bs {bs(p[2])}" for p in pedidos[:10])], est, ev
    if t == "5":
        ev.append({"atencion": True})
        return ["¡Claro! Ya le avisé a alguien del equipo y le escribe por este mismo chat en breve. 🙋 Mientras tanto, aquí sigo si necesita algo."], est, ev
    if t == "6":
        if not carrito:
            return ["Con gusto le preparo un presupuesto. Primero agregue los productos (por ejemplo *" + (next(iter(catalogo)) if catalogo else "A001")
                    + " 3*) y luego escriba *6*."], est, ev
        lineas = [{"codigo": c, "descripcion": catalogo.get(c, (c,))[0], "cantidad": str(q)} for c, q in carrito.items()]
        ev.append({"crear_presupuesto": {"lineas": lineas}})
        return ["📑 Perfecto, le estoy preparando el presupuesto…"], {k: v for k, v in est.items() if k != "carrito"}, ev
    m = re.fullmatch(r"(?:acepto|aceptar|aprobado)\s*(?:presupuesto)?\s*#?\s*(\d{1,9})", t)
    if m:
        ev.append({"presupuesto_aceptado": int(m.group(1))})
        return ["¡Gracias! Dejamos registrada su aceptación."], est, ev
    if t in ("vaciar", "borrar todo", "empezar de nuevo", "cancelar", "cancelar pedido"):
        return ["Listo, vacié su pedido. Cuando quiera empezamos de nuevo. 👍"], {k: v for k, v in est.items() if k != "carrito"}, ev
    m = re.fullmatch(r"(?:quitar|eliminar|borrar)\s+(" + COD + ")", t)
    if m:
        cod = next((c for c in carrito if c.lower() == m.group(1)), None)
        if cod is None: return [f"Mmm, {m.group(1).upper()} no está en su pedido."], est, ev
        carrito.pop(cod)
        return [f"Listo, saqué {cod}.\n\n" + _carrito_texto(carrito, catalogo)[0]], {**est, "carrito": carrito}, ev
    PIDE = r"(?:(?:quiero|quisiera|dame|deme|necesito|me das|me da|me puede dar|agrega|agregame|agregue|pon|ponme|anota|anotame|mandame|envieme|pedir)\s+)?"
    m = re.fullmatch(PIDE + r"(" + COD + r")\s*(?:x|\*|×)?\s*(\d+(?:[.,]\d+)?)(?:\s*(?:unidades|und|unid|u|bultos|cajas))?", t)
    m2 = None if m else re.fullmatch(PIDE + r"(\d+(?:[.,]\d+)?)\s*(?:x|\*|×)?\s*(?:unidades de |und de |de |del |la |el |los |las )?(.{2,60})", t)
    if m or m2:
        cant = _dec(m.group(2) if m else m2.group(1))
        cod = next((c for c in catalogo if c.lower() == (m.group(1) if m else m2.group(2).strip())), None)
        if cod is None and m2:                                   # «quiero 3 harina pan»: se busca por nombre
            hall = buscar(catalogo, m2.group(2))
            if len(hall) > 1:
                return [f"Tengo varias opciones para «{m2.group(2)}» 👇\n" + "\n".join(_linea(c, v) for c, v in hall[:8])
                        + f"\n\n¿Cuál prefiere? Escríbame el código y la cantidad, por ejemplo *{hall[0][0]} {m2.group(1)}*."], est, ev
            cod = hall[0][0] if hall else None
        if cod is None:
            q = (m.group(1) if m else m2.group(2)).upper()
            return [f"No encontré «{q}» en el catálogo 🤔. Escriba *1* para verlo o pruebe con otra palabra."], est, ev
        if cant is None: return ["¿Cuántas unidades necesita? La cantidad debe ser mayor que cero."], est, ev
        exist = catalogo[cod][2]
        if exist is not None and Decimal(str(exist)) < cant:
            hay = Decimal(str(exist)).normalize()
            return [f"Solo tenemos {hay:f} unidades de {cod} ({catalogo[cod][0]}) en este momento."
                    + (f" ¿Le agrego las {hay:f}? Escriba *{cod} {hay:f}*." if hay > 0 else " Le aviso apenas llegue más.")], est, ev
        if cod not in carrito and len(carrito) >= MAX_CARRITO: return ["Su pedido llegó al máximo de productos que puedo tomar por aquí. Confírmelo con *3* y hacemos otro."], est, ev
        carrito[cod] = format(cant.normalize(), "f")
        sub = Decimal(str(catalogo[cod][1])) * cant
        return [f"✅ Anotado: {carrito[cod]} × {catalogo[cod][0]} ({cod}) — Bs {bs(sub)}.\n¿Algo más? Cuando termine escriba *3* para confirmar o *2* para ver su pedido."],\
            {**est, "carrito": carrito}, ev
    m = re.fullmatch(r"(?:buscar|busco|tienen|tiene|hay|venden|precio(?: de)?|cuanto cuesta(?: el| la)?|cuanto sale(?: el| la)?|que precio tiene(?: el| la)?)\s+(.{2,60}?)\??", t)
    consulta = m.group(1) if m else (t if 3 <= len(t) <= 60 and re.search(r"[a-z]{3}", t) else None)
    if consulta:
        hall = buscar(catalogo, consulta)
        if hall:
            return [("¡Sí, tenemos! 👇\n" if len(hall) > 0 else "") + "\n".join(_linea(c, v) for c, v in hall)
                    + ("\n(Hay más opciones: agregue otra palabra para afinar la búsqueda.)" if len(hall) >= MAX_BUSQUEDA else "")
                    + f"\n\nPara agregarlo escríbame el código y la cantidad, por ejemplo *{hall[0][0]} 2*."], est, ev
        if m: return [f"No encontré nada con «{consulta}» 🤔. Pruebe con otra palabra o escriba *1* para ver el catálogo."], est, ev
    return ["Disculpe, no le entendí bien 😅. Puede escribirme el nombre de un producto (por ejemplo *harina*), "
            "o *MENÚ* para ver todo lo que puedo hacer."], est, ev
