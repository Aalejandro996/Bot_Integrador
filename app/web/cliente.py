"""Pantallas de la empresa cliente: panel, WhatsApp (QR y opciones), Profit Plus, pedidos, conversaciones, presupuestos y usuarios."""
import re
from datetime import timedelta
from decimal import Decimal
from pathlib import Path

from flask import Response, abort, current_app, flash, g, redirect, render_template, request, url_for
from sqlalchemy import func, select

from .. import notificaciones as N
from .. import openwa as OW
from .. import servicios as SV
from ..extensions import db
from ..models import (ESTADOS_PEDIDO, ESTADOS_PRESUPUESTO, Contacto, Empresa, Mensaje, Pedido, Presupuesto, ProfitArticulo, ProfitSincronizacion,
                      Usuario, ahora)
from ..seguridad import eid_actual, nuevo_enlace_activacion, registrar, requiere
from ..telefono import ErrorWhatsapp
from . import F, bp, campo, hora, intentar, oculto, pagina

CLIENTE = ("Administrador", "Operador")
CARPETA_CONECTOR = Path(__file__).resolve().parents[2] / "conector_profit"
# Sin extensión en la URL (.py, .sql, .ini): así ningún filtro de rastreos las confunde con un escaneo.
DESCARGAS = {"conector": ("surtia_conector.py", "text/x-python"), "usuario-sql": ("crear_usuario_lectura.sql", "text/plain"),
             "instrucciones": ("LEAME.md", "text/markdown"), "requisitos": ("requirements.txt", "text/plain")}


def atraso(ts, intervalo):
    if ts is None: return "Nunca"
    minutos = int((ahora() - ts).total_seconds() // 60)
    return ("hace un momento" if minutos < 1 else "hace " + (f"{minutos} min" if minutos < 120 else f"{minutos // 60} h")) + (" ⚠️ atrasado" if minutos > 2 * intervalo + 5 else "")


@bp.route("/")
@requiere()
def inicio():
    if g.usuario.rol == "Maestro": return redirect(url_for("web.maestro"))
    eid = eid_actual()
    cfg, con, plat = SV.config_wa(eid), SV.profit_conexion(eid), SV.profit_plataforma()
    desde = ahora() - timedelta(hours=24)
    cuenta = lambda q: db.session.execute(q).scalar() or 0
    nuevos = cuenta(select(func.count()).select_from(Pedido).where(Pedido.empresa_id == eid, Pedido.estado == "Nuevo"))
    msgs = cuenta(select(func.count()).select_from(Mensaje).where(Mensaje.empresa_id == eid, Mensaje.ts > desde))
    atencion = db.session.execute(select(Contacto).where(Contacto.empresa_id == eid, Contacto.atencion.is_(True)).order_by(Contacto.ultimo_mensaje.desc()).limit(10)).scalars().all()
    pasos = [("Vincular el WhatsApp de la empresa (código QR)", "hecho" if cfg and cfg.sesion_estado == "ready" else "pendiente"),
             ("Activar la integración con Profit Plus", "hecho" if con else "pendiente"),
             ("Instalar el conector en el servidor de Profit (primera foto recibida)", "hecho" if con and con.ultima_sync else "pendiente")]
    pedidos_hoy = cuenta(select(func.count()).select_from(Pedido).where(Pedido.empresa_id == eid, Pedido.creado_en > desde))
    secs = [dict(kpis=[("WhatsApp", OW.ESTADOS.get(cfg.sesion_estado, "Sin vincular") if cfg else "Sin vincular",
                        ("+" + cfg.numero_visible) if cfg and cfg.numero_visible else "Número de la empresa"),
                       ("Profit: última foto", atraso(con.ultima_sync, plat.intervalo_min if plat else 15) if con else "Sin conectar",
                        f"{con.n_articulos} artículos" if con and con.ultima_sync else "Catálogo e inventario"),
                       ("Pedidos nuevos", nuevos, f"{pedidos_hoy} en las últimas 24 h"), ("Mensajes 24 h", msgs, "Recibidos y enviados"),
                       ("Piden una persona", len(atencion), "Esperan respuesta" if atencion else "Nadie en espera")])]
    if any(e != "hecho" for _, e in pasos): secs.append(dict(titulo="Puesta en marcha", pasos=pasos))
    secs.append(dict(titulo="Clientes que pidieron hablar con una persona", tabla=dict(cols=["Contacto", "Teléfono", "Último mensaje"], filas=[
        dict(url=url_for("web.conversacion", cid=c.id), cells=[c.nombre or "—", "+" + c.telefono, hora(c.ultimo_mensaje)]) for c in atencion])))
    return pagina(f"Hola, {(g.usuario.nombre or g.usuario.username).split()[0].capitalize()} 👋", secs,
                  subtitulo=f"Así va hoy el asistente de WhatsApp de {g.empresa.razon_social}")


# ====================================================================== WhatsApp
def _url_webhook(clave):
    return current_app.config["APP_BASE_URL"] + url_for("web.aviso_whatsapp", clave=clave)


@bp.route("/whatsapp", methods=["GET", "POST"])
@requiere("Administrador")
def whatsapp():
    eid = eid_actual()
    cfg = SV.config_wa(eid)
    if request.method == "POST":
        a = F.get("accion")
        def hacer():
            nonlocal cfg
            if a == "conectar":
                cfg = SV.conectar_wa(eid, _url_webhook); registrar("WHATSAPP_VINCULANDO", "")
            elif a == "desconectar":
                if cfg is None or not cfg.sesion_id: raise ValueError("No hay un WhatsApp vinculado")
                ow = SV.pasarela()
                if ow is not None: ow.cerrar(cfg.sesion_id)
                cfg.sesion_id, cfg.sesion_estado, cfg.numero_visible = None, "disconnected", None
                registrar("WHATSAPP_DESVINCULADO", "")
            elif a == "guardar":
                if cfg is None: raise ValueError("Primero vincule el WhatsApp de la empresa")
                cfg.activo = F.get("activo", "1") == "1"
                cfg.bienvenida = (F.get("bienvenida") or "").strip()[:500] or None
                cfg.instrucciones_pago = (F.get("instrucciones_pago") or "").strip()[:800] or None
                if F.get("presupuesto_modo") in ("automatico", "manual"): cfg.presupuesto_modo = F["presupuesto_modo"]
                registrar("BOT_CONFIGURADO", f"activo={cfg.activo}")
            elif a == "prueba":
                SV.enviar_wa(eid, F["telefono"], "Mensaje de prueba del bot de WhatsApp ✅")
            else: raise ValueError("Acción inválida")
            db.session.commit()
        intentar(hacer, {"conectar": "Escanee el código QR con el WhatsApp de la empresa", "desconectar": "WhatsApp desvinculado",
                         "guardar": "Configuración guardada", "prueba": "Mensaje de prueba enviado"}.get(a, "Hecho"))
        return redirect(request.url)
    ow, estado, qr = SV.pasarela(), None, None
    if cfg is not None and cfg.sesion_id and ow is not None:
        try:
            est = ow.estado(cfg.sesion_id)
            estado = SV.guardar_estado(cfg, est["estado"], est.get("telefono"))
            if estado == "qr_ready": qr = ow.qr(cfg.sesion_id)
        except OW.SesionInexistente:
            cfg.sesion_id, cfg.sesion_estado = None, "disconnected"; db.session.commit()
        except ErrorWhatsapp as e:
            flash(str(e)); estado = cfg.sesion_estado
    secs = [dict(pares=[("WhatsApp", OW.ESTADOS.get(estado, "Sin vincular")), ("Número", ("+" + cfg.numero_visible) if cfg and cfg.numero_visible else "—"),
                        ("Bot", ("Activo" if cfg.activo else "Pausado") if cfg else "—")])]
    if ow is None:
        secs.append(dict(titulo="Conectar WhatsApp", nota="La pasarela de WhatsApp todavía no está configurada. Contacte a soporte de Gigadevs."))
    elif estado == "ready":
        secs.append(dict(titulo="WhatsApp vinculado", abierto=True, nota="El bot ya atiende en este número. Para cambiar de número, desvincule y escanee de nuevo.",
                         form=dict(action=request.path, boton="Desvincular WhatsApp", boton_clase="sec", confirmar="¿Desvincular? El bot deja de responder hasta que vuelva a escanear.",
                                   campos=[oculto("desconectar")])))
    elif estado in ("qr_ready", "initializing", "authenticating", "created"):
        secs.append(dict(titulo="Escanee el código QR", qr_wa=dict(src=qr, estado_url=url_for("web.whatsapp_estado")),
                         nota="1) Abra WhatsApp en el teléfono del número de la empresa. 2) Menú (⋮) o Ajustes → Dispositivos vinculados → Vincular un dispositivo. "
                              "3) Apunte la cámara a este código. La página se actualiza sola al quedar vinculado."))
    else:
        secs.append(dict(titulo="Conectar el WhatsApp de la empresa", abierto=True,
                         nota="Vincule el WhatsApp del negocio como en WhatsApp Web: pulse el botón y escanee el código QR con el teléfono. "
                              "Use el bot para atender, no para enviar publicidad masiva: WhatsApp puede limitar el número.",
                         form=dict(action=request.path, boton="Conectar con código QR", campos=[oculto("conectar")])))
    if cfg is not None:
        secs.append(dict(titulo="Opciones del bot", abierto=True, form=dict(action=request.path, boton="Guardar", campos=[oculto("guardar"),
            campo("activo", "Bot", "select", opciones=[("1", "Activo"), ("0", "Pausado (solo atiende el personal)")], valor="1" if cfg.activo else "0"),
            campo("presupuesto_modo", "Cuando un cliente pida un presupuesto", "select", opciones=[("automatico", "El bot lo envía al instante"),
                  ("manual", "Queda en borrador para revisión")], valor=cfg.presupuesto_modo),
            campo("bienvenida", "Mensaje de bienvenida", "textarea", req=False, valor=cfg.bienvenida, ph="¡Hola! Gracias por escribir a …"),
            campo("instrucciones_pago", "Cómo pagar o retirar (va con cada orden de venta)", "textarea", req=False, valor=cfg.instrucciones_pago,
                  ph="Pago móvil 0102 J-12345678-9 0414-0000000 · Retiro en Av. … de 8 a 5")])))
        if estado == "ready":
            secs.append(dict(titulo="Enviar mensaje de prueba", form=dict(action=request.path, boton="Enviar", campos=[oculto("prueba"), campo("telefono", "WhatsApp destino (58412…)")])))
    return pagina("WhatsApp", secs)


@bp.route("/whatsapp/estado")
@requiere("Administrador")
def whatsapp_estado():
    eid = eid_actual()
    cfg, ow = SV.config_wa(eid), SV.pasarela()
    if cfg is None or not cfg.sesion_id or ow is None: return {"estado": "sin_vincular"}
    try:
        est = ow.estado(cfg.sesion_id)
        estado = SV.guardar_estado(cfg, est["estado"], est.get("telefono"))
        return {"estado": estado, "texto": OW.ESTADOS.get(estado, estado), "qr": ow.qr(cfg.sesion_id) if estado == "qr_ready" else None}
    except ErrorWhatsapp:
        return {"estado": cfg.sesion_estado or "desconocido", "texto": "Sin respuesta de la pasarela; reintentando…", "qr": None}


# ====================================================================== Profit Plus
@bp.route("/profit", methods=["GET", "POST"])
@requiere("Administrador")
def profit():
    eid = eid_actual()
    c, plat = SV.profit_conexion(eid), SV.profit_plataforma()
    if request.method == "POST":
        a = F.get("accion")
        def hacer():
            nonlocal c
            if plat is None or not plat.activa: raise ValueError("La integración con Profit Plus no está habilitada. Contacte a Gigadevs.")
            if a == "activar":
                c = SV.crear_conexion_profit(eid); c.activo = True; registrar("PROFIT_ACTIVADO", c.conector_id)
            elif c is None: raise ValueError("Primero active la integración")
            elif a == "guardar":
                elegidos = [x.strip()[:10] for x in request.form.getlist("almacenes") if x.strip()]
                elegidos += [x.strip()[:10] for x in (F.get("almacenes_txt") or "").replace(";", ",").split(",") if x.strip()]
                c.almacenes = sorted(set(elegidos))[:50]
                lp = (F.get("lista_precio") or "").strip()[:10]
                if not lp: raise ValueError("Indique la lista de precios de Profit (co_precio, p. ej. 01)")
                c.lista_precio, c.restar_comprometido = lp, F.get("restar_comprometido", "1") == "1"
                c.solo_con_existencia, c.activo = F.get("solo_con_existencia") == "1", F.get("activo", "1") == "1"
                registrar("PROFIT_CONFIGURADO", f"almacenes={','.join(c.almacenes) or 'todos'} lista={c.lista_precio} activo={c.activo}")
            elif a == "rotar":
                c.secreto_cifrado = SV.cifrador().cifrar(__import__("secrets").token_urlsafe(32)); registrar("PROFIT_CLAVE_ROTADA", "")
            else: raise ValueError("Acción inválida")
            db.session.commit()
        intentar(hacer, {"activar": "Integración activada: descargue la configuración del conector", "guardar": "Configuración guardada: el conector la aplicará en su próxima lectura",
                         "rotar": "Clave renovada: descargue de nuevo surtia_conector.ini y reemplácelo en el servidor de Profit"}.get(a, "Hecho"))
        return redirect(request.url)
    if plat is None or not plat.activa:
        return pagina("Profit Plus", [dict(titulo="Integración con Profit Plus", nota="La integración todavía no está habilitada. Contacte a soporte de Gigadevs.")])
    if c is None:
        return pagina("Profit Plus", [dict(titulo="Conectar Profit Plus (solo lectura)", abierto=True,
            nota="El bot usará el catálogo de Profit Plus: artículos, existencias por almacén, precios de venta y clientes, actualizados cada "
                 f"{plat.intervalo_min} minutos. Nada se escribe ni se envía a Profit Plus; la factura fiscal se sigue emitiendo en Profit. "
                 "Se instala un conector pequeño en el servidor de Profit: solo hace consultas de lectura y envía los datos por HTTPS. "
                 "No hay que abrir puertos ni exponer SQL Server a Internet.",
            form=dict(action=request.path, boton="Activar integración", campos=[oculto("activar")]))])
    cuenta = lambda *w: db.session.execute(select(func.count()).select_from(ProfitArticulo).where(ProfitArticulo.empresa_id == eid, *w)).scalar() or 0
    secs = [dict(kpis=[("Conector", ("Activo" if c.activo else "Pausado") + (" · con error" if c.ultimo_estado == "error" else "")),
                       ("Última foto", atraso(c.ultima_sync, plat.intervalo_min)), ("Artículos", c.n_articulos),
                       ("Con existencia", cuenta(ProfitArticulo.existencia > 0)), ("Clientes", c.n_clientes)]),
            dict(pares=[("Último contacto del conector", hora(c.ultimo_contacto)), ("Último error", c.ultimo_error or "—"),
                        ("Equipo / versión", f"{c.agente_equipo or '—'} · {c.agente_version or '—'}"), ("ID del conector", c.conector_id),
                        ("Artículos con precio en divisas", f"{cuenta(ProfitArticulo.moneda != 'VES')} (en Bs a la tasa BCV del día)")])]
    disp = c.disponibles or {}
    alms = [(x["codigo"], f"{x['codigo']} · {x['nombre']}") for x in disp.get("almacenes") or []]
    listas = [(x["codigo"], f"{x['codigo']} · {x['nombre']}") for x in disp.get("listas_precio") or []]
    campos = [oculto("guardar"), campo("activo", "Conector", "select", opciones=[("1", "Activo"), ("0", "Pausado: el bot no ofrece productos")], valor="1" if c.activo else "0")]
    campos.append(campo("almacenes", "Almacenes que cuentan para la existencia (ninguno marcado = todos los de venta)", "checks", req=False, opciones=alms, valor=c.almacenes) if alms
                  else campo("almacenes_txt", "Almacenes (co_alma separados por coma; vacío = todos los de venta)", req=False, valor=", ".join(c.almacenes or [])))
    campos += [campo("lista_precio", "Lista de precios de venta", "select", opciones=listas, valor=c.lista_precio) if listas else campo("lista_precio", "Lista de precios (co_precio)", valor=c.lista_precio),
               campo("restar_comprometido", "Existencia que ve el cliente", "select", opciones=[("1", "Actual menos lo comprometido"), ("0", "Existencia actual")], valor="1" if c.restar_comprometido else "0"),
               campo("solo_con_existencia", "Artículos que ofrece el bot", "select", opciones=[("0", "Todos los que tienen precio (los agotados se marcan)"), ("1", "Solo los que tienen existencia")],
                     valor="1" if c.solo_con_existencia else "0")]
    secs.append(dict(titulo="Qué toma el bot de Profit", abierto=True, nota="Los almacenes y listas aparecen aquí cuando el conector se conecta por primera vez.",
                     form=dict(action=request.path, boton="Guardar", campos=campos)))
    enlaces = [dict(url=url_for("web.profit_descarga", archivo="configuracion"), texto="⬇ surtia_conector.ini (configuración con su clave)")]
    enlaces += [dict(url=url_for("web.profit_descarga", archivo=k), texto="⬇ " + v[0]) for k, v in DESCARGAS.items()]
    if plat.url_instalador: enlaces.insert(0, dict(url=plat.url_instalador, texto="⬇ Instalador surtia_conector.exe (Windows)"))
    secs.append(dict(titulo="Instalar el conector en el servidor de Profit Plus", abierto=True, enlaces=enlaces,
                     nota="1) En SQL Server ejecute crear_usuario_lectura.sql (usuario que solo puede LEER). 2) Copie surtia_conector.exe y surtia_conector.ini en "
                          "C:\\Surtia. 3) Complete la sección [sqlserver] del .ini. 4) Ejecute «surtia_conector --probar». 5) «surtia_conector --instalar-tarea» "
                          f"(arranca con Windows y lee cada {plat.intervalo_min} minutos). Detalles en LEAME.md."))
    secs.append(dict(titulo="Renovar la clave del conector", nota="Úsela si cree que surtia_conector.ini se filtró. El conector deja de funcionar hasta que reemplace el archivo.",
                     form=dict(action=request.path, boton="Renovar clave", boton_clase="sec", confirmar="¿Renovar la clave? El conector actual dejará de sincronizar.", campos=[oculto("rotar")])))
    arts = db.session.execute(select(ProfitArticulo).where(ProfitArticulo.empresa_id == eid).order_by(ProfitArticulo.codigo).limit(3000)).scalars().all()
    if arts:
        secs.append(dict(titulo="Catálogo recibido de Profit Plus", tabla=dict(cols=["Código", "Descripción", "Precio", "Existencia", "Por almacén", "IVA"], num=[2, 3], filas=[
            dict(cells=[a.codigo, a.nombre, f"{a.moneda} {a.precio:,.2f}", f"{a.existencia.normalize():f}",
                        " · ".join(f"{k}: {Decimal(v).normalize():f}" for k, v in (a.por_almacen or {}).items()), a.alicuota]) for a in arts])))
    sincs = db.session.execute(select(ProfitSincronizacion).where(ProfitSincronizacion.empresa_id == eid).order_by(ProfitSincronizacion.id.desc()).limit(15)).scalars()
    secs.append(dict(titulo="Sincronizaciones recientes", tabla=dict(cols=["Fecha", "Resultado", "Artículos", "Clientes", "Duración", "Detalle"], filas=[
        dict(cells=[hora(s.ts), s.resultado, s.articulos, s.clientes, f"{s.duracion_ms} ms" if s.duracion_ms is not None else "—", s.detalle or ""]) for s in sincs])))
    return pagina("Profit Plus", secs)


@bp.route("/profit/descargar/<archivo>")
@requiere("Administrador")
def profit_descarga(archivo):
    eid = eid_actual()
    c = SV.profit_conexion(eid)
    if c is None: abort(404)
    if archivo == "configuracion":
        registrar("PROFIT_INI_DESCARGADO", ""); db.session.commit()
        return Response(SV.conector_ini(c), mimetype="text/plain; charset=utf-8",
                        headers={"Content-Disposition": "attachment; filename=surtia_conector.ini", "Cache-Control": "no-store"})
    if archivo not in DESCARGAS: abort(404)
    nombre, tipo = DESCARGAS[archivo]
    return Response((CARPETA_CONECTOR / nombre).read_bytes(), mimetype=tipo + "; charset=utf-8", headers={"Content-Disposition": f"attachment; filename={nombre}"})


# ====================================================================== pedidos
@bp.route("/pedidos", methods=["GET", "POST"])
@requiere(*CLIENTE)
def pedidos():
    eid = eid_actual()
    if request.method == "POST":
        def hacer():
            p = db.session.execute(select(Pedido).where(Pedido.empresa_id == eid, Pedido.id == int(F["pid"])).with_for_update()).scalar_one_or_none()
            if p is None: raise ValueError("Pedido inexistente")
            if F["estado"] not in ESTADOS_PEDIDO: raise ValueError("Estado inválido")
            p.estado = F["estado"]
            registrar("PEDIDO_ESTADO", f"N°{p.numero} → {p.estado}")
            db.session.commit()
            if F.get("avisar") == "1" and p.telefono:
                frase = {"Confirmado": f"✅ ¡Buenas noticias! Su pedido N° {p.numero} ya está *confirmado* y lo estamos preparando.",
                         "Despachado": f"🚚 ¡Su pedido N° {p.numero} va en camino! Ya fue *despachado*.",
                         "Facturado en Profit": f"🧾 Listo: su pedido N° {p.numero} ya está *facturado*. ¡Gracias por su compra!",
                         "Rechazado": f"Lamentamos informarle que no podemos atender su pedido N° {p.numero} en este momento. Si quiere, escríbanos y le ayudamos a buscar alternativas.",
                         }.get(p.estado, f"📦 Su pedido N° {p.numero} está *{p.estado.lower()}*.")
                texto = frase + (f"\n\n{F['mensaje'].strip()[:500]}" if (F.get("mensaje") or "").strip() else "")
                SV.enviar_wa(eid, p.telefono, texto); db.session.commit()
        intentar(hacer, "Pedido actualizado")
        return redirect(request.url)
    filtro = request.args.get("estado") or ""
    q = select(Pedido).where(Pedido.empresa_id == eid)
    if filtro in ESTADOS_PEDIDO: q = q.where(Pedido.estado == filtro)
    ps = db.session.execute(q.order_by(Pedido.id.desc()).limit(3000)).scalars().all()
    cuenta = lambda e: db.session.execute(select(func.count()).select_from(Pedido).where(Pedido.empresa_id == eid, Pedido.estado == e)).scalar() or 0
    sub_ = {"Nuevo": "Por revisar", "Confirmado": "Listos para despachar", "Despachado": "Falta facturar en Profit", "Facturado en Profit": "Cerrados"}
    secs = [dict(kpis=[(e, cuenta(e), sub_[e]) for e in ("Nuevo", "Confirmado", "Despachado", "Facturado en Profit")]),
            dict(titulo="Pedidos por WhatsApp", nota="Cargue cada pedido en Profit Plus con el código de cliente y emita allí la factura; luego márquelo «Facturado en Profit».",
                 tabla=dict(cols=["N°", "Fecha", "Cliente", "Cód. Profit", "Productos", "Total ref. Bs", "Estado", "Origen"], num=[5], filas=[
                     dict(url=url_for("web.pedido", pid=p.id), cells=[p.numero, hora(p.creado_en), p.nombre or ("+" + (p.telefono or "")), p.codigo_cliente or "—",
                          "; ".join(f"{l['codigo']} ×{l['cantidad']}" for l in p.lineas)[:120] or "—", SV.bs(p.total), p.estado,
                          "PDF" if p.origen == "whatsapp_pdf" else "Chat"]) for p in ps]))]
    if ps:
        secs.append(dict(titulo="Atender pedido", form=dict(action=request.path, boton="Actualizar", campos=[
            campo("pid", "Pedido", "select", opciones=[(p.id, f"N° {p.numero} · {p.nombre or p.telefono}") for p in ps if p.estado not in ("Facturado en Profit", "Rechazado")][:100]),
            campo("estado", "Nuevo estado", "select", opciones=[(e, e) for e in ESTADOS_PEDIDO]),
            campo("avisar", "¿Avisar por WhatsApp?", "select", opciones=[("1", "Sí"), ("0", "No")]), campo("mensaje", "Mensaje adicional", req=False)])))
    return pagina("Pedidos", secs, subtitulo="Lo que sus clientes pidieron por WhatsApp, listo para cargar en Profit Plus")


@bp.route("/pedidos/<int:pid>")
@requiere(*CLIENTE)
def pedido(pid):
    p = db.session.get(Pedido, pid)
    if p is None or p.empresa_id != eid_actual(): abort(404)
    return render_template("documento.html", d=SV.documento_pedido(p), e=g.empresa, interno=True, volver=url_for("web.pedidos"))


@bp.route("/pedidos/exportar")
@requiere(*CLIENTE)
def pedidos_exportar():
    """CSV de los pedidos abiertos, una línea por producto, para cargarlos en Profit Plus."""
    import csv
    import io
    eid = eid_actual()
    out = io.StringIO(); escritor = csv.writer(out, delimiter=";")
    def fila(*valores):          # sin fórmulas al abrirlo en Excel: un texto que empiece por = + - @ va como texto
        escritor.writerow([("'" + str(v)) if str(v)[:1] in ("=", "+", "-", "@", "\t", "\r") else v for v in valores])
    fila("pedido", "fecha", "co_cli", "cliente", "rif", "co_art", "descripcion", "cantidad", "precio_bs", "estado")
    for p in db.session.execute(select(Pedido).where(Pedido.empresa_id == eid, Pedido.tipo == "venta", Pedido.estado.in_(("Nuevo", "Confirmado", "Despachado")))
                                .order_by(Pedido.numero)).scalars():
        for l in p.lineas:
            fila(p.numero, p.creado_en.date().isoformat(), p.codigo_cliente or "", p.nombre or "", p.rif or "", l["codigo"], l["descripcion"],
                 l["cantidad"], l["precio"], p.estado)
    return Response("\ufeff" + out.getvalue(), mimetype="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=pedidos_para_profit.csv"})


# ====================================================================== conversaciones
@bp.route("/conversaciones")
@requiere(*CLIENTE)
def conversaciones():
    eid = eid_actual()
    cs = db.session.execute(select(Contacto).where(Contacto.empresa_id == eid).order_by(Contacto.ultimo_mensaje.desc().nullslast()).limit(3000)).scalars().all()
    return pagina("Conversaciones", [dict(tabla=dict(cols=["Contacto", "Teléfono", "Tipo", "Cód. Profit", "Último mensaje", "Atención"], filas=[
        dict(url=url_for("web.conversacion", cid=c.id), cells=[c.nombre or "—", "+" + c.telefono, c.tipo.capitalize(), c.codigo_profit or "—",
                                                                  hora(c.ultimo_mensaje), "🙋 Pidió una persona" if c.atencion else ""]) for c in cs]))])


@bp.route("/conversaciones/<int:cid>", methods=["GET", "POST"])
@requiere(*CLIENTE)
def conversacion(cid):
    eid = eid_actual()
    c = db.session.get(Contacto, cid)
    if c is None or c.empresa_id != eid: abort(404)
    if request.method == "POST":
        a = F.get("accion")
        def hacer():
            if a == "responder":
                try: SV.enviar_wa(eid, c.telefono, F["texto"].strip()[:4000], c)
                finally: db.session.commit()
            elif a == "ficha":
                if F["tipo"] not in ("nuevo", "cliente", "proveedor", "bloqueado"): raise ValueError("Tipo inválido")
                c.tipo, c.nombre, c.estado = F["tipo"], (F.get("nombre") or "").strip()[:120] or c.nombre, {}
                registrar("CONTACTO", f"…{c.telefono[-4:]} tipo={c.tipo}"); db.session.commit()
            elif a == "atendido":
                c.atencion = False; db.session.commit()
            else: raise ValueError("Acción inválida")
        intentar(hacer, {"responder": "Mensaje enviado", "ficha": "Contacto actualizado", "atendido": "Marcado como atendido"}.get(a, "Hecho"))
        return redirect(request.url)
    ms = db.session.execute(select(Mensaje).where(Mensaje.empresa_id == eid, Mensaje.contacto_id == cid).order_by(Mensaje.id.desc()).limit(500)).scalars().all()
    secs = [dict(pares=[("Teléfono", "+" + c.telefono), ("Nombre", c.nombre or "—"), ("Tipo", c.tipo.capitalize()), ("RIF", c.rif or "—"), ("Cliente en Profit", c.codigo_profit or "—")]),
            dict(titulo="Conversación (más reciente primero)", tabla=dict(cols=["Fecha", "De", "Mensaje", "Envío"], filas=[
                dict(cells=[hora(m.ts), "Contacto" if m.direccion == "in" else "Empresa", m.texto[:600], "" if m.direccion == "in" else (m.resultado or "")]) for m in ms])),
            dict(titulo="Responder", abierto=True, form=dict(action=request.path, boton="Enviar", campos=[oculto("responder"), campo("texto", "Mensaje", "textarea")]))]
    if c.atencion:
        secs.insert(1, dict(titulo="🙋 Este contacto pidió hablar con una persona", form=dict(action=request.path, boton="Marcar como atendido", campos=[oculto("atendido")])))
    secs.append(dict(titulo="Ficha del contacto", nota="«Bloqueado» hace que el bot lo ignore. «Proveedor» le da el menú de ofertas.",
                     form=dict(action=request.path, boton="Guardar", campos=[oculto("ficha"), campo("tipo", "Tipo", "select", opciones=[("cliente", "Cliente"), ("proveedor", "Proveedor"),
                               ("nuevo", "Sin clasificar"), ("bloqueado", "Bloqueado")], valor=c.tipo), campo("nombre", "Nombre", req=False, valor=c.nombre)])))
    return pagina(f"Conversación · {c.nombre or '+' + c.telefono}", secs, acciones=[dict(url=url_for("web.conversaciones"), texto="← Conversaciones")])


# ====================================================================== presupuestos
@bp.route("/presupuestos", methods=["GET", "POST"])
@requiere(*CLIENTE)
def presupuestos():
    eid = eid_actual()
    if request.method == "POST":
        def hacer():
            pv = db.session.execute(select(Presupuesto).where(Presupuesto.empresa_id == eid, Presupuesto.id == int(F["pid"])).with_for_update()).scalar_one_or_none()
            if pv is None: raise ValueError("Presupuesto inexistente")
            a = F.get("accion")
            if a == "enviar":
                if not pv.cliente_telefono: raise ValueError("El presupuesto no tiene teléfono")
                pv.estado = "Enviado"; db.session.commit()
                SV.enviar_wa(eid, pv.cliente_telefono, SV.resumen_presupuesto(pv))
            elif a in ("Aceptado", "Rechazado"): pv.estado = a
            else: raise ValueError("Acción inválida")
            registrar("PRESUPUESTO", f"N°{pv.numero} {a}"); db.session.commit()
        intentar(hacer, "Presupuesto actualizado")
        return redirect(request.url)
    ps = db.session.execute(select(Presupuesto).where(Presupuesto.empresa_id == eid).order_by(Presupuesto.id.desc()).limit(3000)).scalars().all()
    filas = [dict(url=url_for("web.presupuesto", pid=p.id), cells=[p.numero, p.fecha.strftime("%Y-%m-%d"), p.cliente_nombre, SV.bs(p.total), p.estado]) for p in ps]
    secs = [dict(titulo="Presupuestos", tabla=dict(cols=["N°", "Fecha", "Cliente", "Total Bs", "Estado"], num=[3], filas=filas))]
    pend = [p for p in ps if p.estado in ("Borrador", "Enviado")]
    if pend:
        secs.append(dict(titulo="Atender presupuesto", form=dict(action=request.path, boton="Aplicar", campos=[
            campo("pid", "Presupuesto", "select", opciones=[(p.id, f"N° {p.numero} · {p.cliente_nombre} · {p.estado}") for p in pend][:100]),
            campo("accion", "Acción", "select", opciones=[("enviar", "Enviar por WhatsApp"), ("Aceptado", "Marcar aceptado"), ("Rechazado", "Marcar rechazado")])])))
    return pagina("Presupuestos", secs)


@bp.route("/presupuestos/<int:pid>")
@requiere(*CLIENTE)
def presupuesto(pid):
    pv = db.session.get(Presupuesto, pid)
    if pv is None or pv.empresa_id != eid_actual(): abort(404)
    return render_template("documento.html", d=SV.documento_presupuesto(pv), e=g.empresa, interno=True, volver=url_for("web.presupuestos"))


# ====================================================================== documento público (enlace firmado que recibe el cliente)
@bp.route("/d/<token>")
def documento_publico(token):
    datos = SV.leer_enlace(token)
    if not datos: abort(404)
    tipo, eid, oid = datos
    e = db.session.get(Empresa, eid)
    obj = db.session.get(Pedido if tipo == "pedido" else Presupuesto, oid)
    if e is None or obj is None or obj.empresa_id != eid: abort(404)
    d = SV.documento_pedido(obj) if tipo == "pedido" else SV.documento_presupuesto(obj)
    return render_template("documento.html", d=d, e=e, interno=False)


# ====================================================================== usuarios de la empresa
@bp.route("/usuarios", methods=["GET", "POST"])
@requiere("Administrador")
def usuarios():
    eid = eid_actual()
    if request.method == "POST":
        a = F.get("accion")
        def hacer():
            if a == "invitar":
                nombre = (F.get("username") or "").strip().lower()
                if not re.fullmatch(r"[a-z0-9._-]{3,40}", nombre): raise ValueError("Usuario: 3 a 40 letras, números, punto, guion")
                if db.session.query(Usuario).filter(func.lower(Usuario.username) == nombre).first(): raise ValueError("Ese usuario ya existe")
                u = Usuario(username=nombre, email=(F.get("email") or "").strip()[:254] or None, nombre=(F.get("nombre") or "").strip()[:120] or None,
                            rol=F.get("rol") if F.get("rol") in ("Administrador", "Operador") else "Operador", empresa_id=eid)
                db.session.add(u); db.session.flush()
                enlace = nuevo_enlace_activacion(u)
                registrar("USUARIO_INVITADO", f"{u.username} rol={u.rol}"); db.session.commit()
                flash(N.enlace_activacion(u, enlace, empresa=g.empresa)[1])
            elif a in ("desactivar", "reactivar", "reenlace"):
                u = db.session.get(Usuario, int(F["uid"]))
                if u is None or u.empresa_id != eid or u.id == g.usuario.id: raise ValueError("Usuario inválido")
                if a == "reenlace":
                    enlace = nuevo_enlace_activacion(u); u.sesion_version += 1
                else: u.activo = a == "reactivar"; u.sesion_version += 1
                registrar("USUARIO_" + a.upper(), u.username); db.session.commit()
                if a == "reenlace": flash(N.enlace_activacion(u, enlace, "reenlace", empresa=g.empresa)[1])
            else: raise ValueError("Acción inválida")
        intentar(hacer, "Listo")
        return redirect(request.url)
    us = db.session.execute(select(Usuario).where(Usuario.empresa_id == eid).order_by(Usuario.username)).scalars().all()
    return pagina("Usuarios", [
        dict(tabla=dict(cols=["Usuario", "Nombre", "Rol", "Estado", "Último acceso"], filas=[
            dict(cells=[u.username, u.nombre or "—", u.rol, ("Activo" if u.password_hash else "Sin activar") if u.activo else "Desactivado", hora(u.ultimo_acceso)]) for u in us])),
        dict(titulo="Invitar usuario", nota="Se genera un enlace de un solo uso: la persona crea su contraseña y su verificación en dos pasos. Nadie más conoce su clave.",
             form=dict(action=request.path, boton="Crear enlace", campos=[oculto("invitar"), campo("username", "Usuario"), campo("nombre", "Nombre", req=False),
                       campo("email", "Correo", "email", req=False), campo("rol", "Rol", "select", opciones=[("Operador", "Operador: pedidos y conversaciones"),
                                                                                                             ("Administrador", "Administrador: todo")])])),
        dict(titulo="Desactivar, reactivar o nuevo enlace", form=dict(action=request.path, boton="Aplicar", campos=[
            campo("uid", "Usuario", "select", opciones=[(u.id, u.username) for u in us if u.id != g.usuario.id]),
            campo("accion", "Acción", "select", opciones=[("desactivar", "Desactivar"), ("reactivar", "Reactivar"), ("reenlace", "Nuevo enlace de activación")])]))])
