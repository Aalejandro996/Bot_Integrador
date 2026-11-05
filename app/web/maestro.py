"""Consola Maestro (Gigadevs): monitoreo del bot, API de catálogo, empresas y su sistema, pasarela de WhatsApp y parámetros.
El Maestro ve conteos y estados técnicos; no ve conversaciones, catálogos ni clientes de las empresas."""
import re
from datetime import timedelta
from decimal import Decimal, InvalidOperation

from flask import abort, current_app, flash, g, redirect, request, url_for
from sqlalchemy import func, select

from .. import notificaciones as N
from .. import openwa as OW
from .. import servicios as SV
from .. import sistemas as SIS
from ..extensions import db
from ..models import Bitacora, Contacto, Empresa, Mensaje, Pedido, ProfitConexion, ProfitPlataforma, Usuario, WhatsappConfig, WhatsappPasarela, ahora
from ..seguridad import nuevo_enlace_activacion, registrar, requiere
from ..telefono import ErrorWhatsapp
from . import F, bp, campo, hora, intentar, oculto, pagina
from .cliente import atraso


def _monitor():
    """Una fila por empresa: estado del WhatsApp y del conector, y conteos de las últimas 24 h."""
    desde = ahora() - timedelta(hours=24)
    cnt = lambda *w: db.session.execute(select(func.count()).select_from(Mensaje).where(*w)).scalar() or 0
    filas = []
    for e in db.session.execute(select(Empresa).order_by(Empresa.razon_social)).scalars():
        w, p = db.session.get(WhatsappConfig, e.id), db.session.get(ProfitConexion, e.id)
        filas.append(dict(e=e, w=w, p=p,
                          recibidos=cnt(Mensaje.empresa_id == e.id, Mensaje.direccion == "in", Mensaje.ts > desde),
                          enviados=cnt(Mensaje.empresa_id == e.id, Mensaje.direccion == "out", Mensaje.ts > desde, Mensaje.resultado == "ok"),
                          fallidos=cnt(Mensaje.empresa_id == e.id, Mensaje.direccion == "out", Mensaje.ts > desde, Mensaje.resultado != "ok"),
                          ultimo=db.session.execute(select(func.max(Mensaje.ts)).where(Mensaje.empresa_id == e.id)).scalar(),
                          pedidos=db.session.execute(select(func.count()).select_from(Pedido).where(Pedido.empresa_id == e.id, Pedido.creado_en > desde)).scalar() or 0,
                          atencion=db.session.execute(select(func.count()).select_from(Contacto).where(Contacto.empresa_id == e.id, Contacto.atencion.is_(True))).scalar() or 0))
    return filas


@bp.route("/maestro", methods=["GET", "POST"])
@requiere("Maestro")
def maestro():
    p = SV.profit_plataforma()
    if request.method == "POST":
        def guardar():
            nonlocal p
            if F.get("accion") != "guardar": raise ValueError("Acción inválida")
            url, inst = (F.get("url_publica") or "").strip().rstrip("/"), (F.get("url_instalador") or "").strip()
            for u in (url, inst):
                if u and not u.startswith("https://"): raise ValueError("Las URL deben empezar por https://")
            intervalo, arts, clis = int(F.get("intervalo_min") or 15), int(F.get("max_articulos") or 20000), int(F.get("max_clientes") or 50000)
            if not 5 <= intervalo <= 1440: raise ValueError("El intervalo debe estar entre 5 y 1440 minutos")
            if not (100 <= arts <= 200000 and 100 <= clis <= 500000): raise ValueError("Límites fuera de rango")
            if p is None: p = ProfitPlataforma(id=1); db.session.add(p)
            p.activa, p.url_publica, p.url_instalador = F.get("activa", "1") == "1", url or None, inst or None
            p.intervalo_min, p.max_articulos, p.max_clientes, p.actualizado_por = intervalo, arts, clis, g.usuario.username
            registrar("PROFIT_API", f"activa={p.activa} intervalo={intervalo}"); db.session.commit()
        intentar(guardar, "API de catálogo guardada")
        return redirect(request.url)
    intervalo = p.intervalo_min if p else 15
    filas = _monitor()
    tarde = lambda f: f["p"] and f["p"].activo and (f["p"].ultima_sync is None or ahora() - f["p"].ultima_sync > timedelta(minutes=2 * intervalo + 5))
    alertas = [dict(cells=[f["e"].razon_social, "WhatsApp " + (OW.ESTADOS.get(f["w"].sesion_estado, "sin vincular") if f["w"] else "sin vincular").lower()]) for f in filas
               if f["e"].activa and (f["w"] is None or f["w"].sesion_estado != "ready")]
    alertas += [dict(cells=[f["e"].razon_social, "Catálogo: " + ("sin foto aún" if f["p"].ultima_sync is None else "foto " + atraso(f["p"].ultima_sync, intervalo))
                            + (f" · {f['p'].ultimo_error[:150]}" if f["p"].ultimo_estado == "error" and f["p"].ultimo_error else "")]) for f in filas if tarde(f)]
    alertas += [dict(cells=[f["e"].razon_social, f"{f['fallidos']} respuesta(s) del bot sin enviar en 24 h"]) for f in filas if f["fallidos"]]
    ow_txt = "Sin configurar"
    if SV.pasarela() is not None:
        try: SV.pasarela().salud(); ow_txt = "En línea"
        except ErrorWhatsapp: ow_txt = "Sin respuesta"
    return pagina("Monitoreo del bot", [
        dict(kpis=[("Pasarela WhatsApp", ow_txt, "OpenWA propia"), ("API de catálogo", ("Activa" if p.activa else "Pausada") if p else "Sin configurar",
                    f"Cada {intervalo} min"), ("Empresas", len(filas), f"{sum(1 for f in filas if f['e'].activa)} activas"),
                   ("WhatsApp conectados", sum(1 for f in filas if f["w"] and f["w"].sesion_estado == "ready"), f"de {len(filas)} empresas"),
                   ("Mensajes 24 h", sum(f["recibidos"] + f["enviados"] for f in filas), f"{sum(f['fallidos'] for f in filas)} fallidos"),
                   ("Alertas", len(alertas), "Todo en orden" if not alertas else "Requieren atención")]),
        dict(titulo="Alertas", tabla=dict(cols=["Empresa", "Qué pasa"], filas=alertas or [dict(cells=["—", "Todo en orden ✅"])])),
        dict(titulo="Monitoreo por empresa", tabla=dict(ancha=True, cols=["Empresa", "WhatsApp", "Bot", "Recibidos 24 h", "Enviados 24 h", "Fallidos 24 h", "Último mensaje",
                                                                         "Pedidos 24 h", "Piden persona", "Sistema", "Último catálogo", "Artículos", "Conector"], num=[3, 4, 5, 7, 8, 11], filas=[
            dict(cells=[f["e"].razon_social + ("" if f["e"].activa else " (suspendida)"), OW.ESTADOS.get(f["w"].sesion_estado, "Sin vincular") if f["w"] else "—",
                        ("Activo" if f["w"].activo else "Pausado") if f["w"] else "—", f["recibidos"], f["enviados"], f["fallidos"], hora(f["ultimo"]), f["pedidos"], f["atencion"],
                        SIS.SISTEMAS.get(f["p"].sistema, {}).get("nombre", "—") if f["p"] else "Sin registrar",
                        ((atraso(f["p"].ultima_sync, intervalo) if f["p"].activo else "Pausado") + (" · error" if f["p"].ultimo_estado == "error" else "")) if f["p"] else "—",
                        f["p"].n_articulos if f["p"] else "—", f"{f['p'].agente_equipo or '—'} · {f['p'].agente_version or '—'}" if f["p"] else "—"]) for f in filas])),
        dict(titulo="API de catálogo (conectores e integraciones)", abierto=True,
             nota="Los conectores (Profit Plus u otro SQL Server) y las integraciones propias llaman a esta URL por HTTPS (solo salida; el cliente no abre puertos). "
                  "Los sistemas en la nube los lee Surtia con el mismo intervalo. El ID y la clave de cada empresa se descargan en Empresas → Sistema. "
                  "Pausar la API detiene todas las lecturas: los bots siguen con el último catálogo.",
             form=dict(action=request.path, boton="Guardar", campos=[oculto("guardar"),
                       campo("activa", "API", "select", opciones=[("1", "Activa"), ("0", "Pausada")], valor="1" if (p is None or p.activa) else "0"),
                       campo("url_publica", "URL pública que usan los conectores", req=False, valor=p.url_publica if p else None, ph=current_app.config["APP_BASE_URL"]),
                       campo("intervalo_min", "Cada cuántos minutos se lee el catálogo", "number", valor=intervalo),
                       campo("max_articulos", "Máximo de artículos por empresa", "number", valor=p.max_articulos if p else 20000),
                       campo("max_clientes", "Máximo de clientes por empresa", "number", valor=p.max_clientes if p else 50000),
                       campo("url_instalador", "Descarga del instalador surtia_conector.exe (https://…)", req=False, valor=p.url_instalador if p else None)]))])


def _sistema_txt(eid):
    c = SV.profit_conexion(eid)
    return SIS.SISTEMAS.get(c.sistema, {}).get("nombre", "—") if c else "Sin registrar"


def _catalogo_txt(eid):
    c = SV.profit_conexion(eid)
    if c is None: return "—"
    if not c.activo: return "Pausado"
    if c.ultimo_estado == "error": return "error"
    return f"{c.n_articulos} artículo{'' if c.n_articulos == 1 else 's'}" if c.ultima_sync else "Sin catálogo aún"


@bp.route("/maestro/empresas", methods=["GET", "POST"])
@requiere("Maestro")
def maestro_empresas():
    if request.method == "POST":
        a = F.get("accion")
        def hacer():
            if a == "crear":
                rif = (F.get("rif") or "").strip().upper()
                m = re.fullmatch(r"([VEJPGC])-?(\d{8})-?(\d)", rif)
                if not m: raise ValueError("RIF inválido (J-12345678-9)")
                rif = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
                if db.session.query(Empresa).filter_by(rif=rif).first(): raise ValueError("Ya existe una empresa con ese RIF")
                usuario = (F.get("username") or "").strip().lower()
                if not re.fullmatch(r"[a-z0-9._-]{3,40}", usuario): raise ValueError("Usuario del administrador: 3 a 40 letras, números, punto, guion")
                if db.session.query(Usuario).filter(func.lower(Usuario.username) == usuario).first(): raise ValueError("Ese usuario ya existe")
                sistema = F.get("sistema") or "profit"
                if sistema not in SIS.SISTEMAS: raise ValueError("Sistema desconocido")
                e = Empresa(razon_social=F["razon_social"].strip()[:200], rif=rif, direccion=(F.get("direccion") or "").strip()[:300] or None,
                            telefono=(F.get("telefono") or "").strip()[:40] or None, correo=(F.get("correo") or "").strip()[:120] or None)
                db.session.add(e); db.session.flush()
                u = Usuario(username=usuario, email=(F.get("email") or "").strip()[:254] or None, rol="Administrador", empresa_id=e.id)
                db.session.add(u); db.session.flush()
                SV.crear_conexion_profit(e.id, sistema)
                enlace = nuevo_enlace_activacion(u)
                registrar("EMPRESA_CREADA", f"{e.razon_social} {e.rif} admin={u.username} sistema={sistema}", empresa_id=e.id); db.session.commit()
                flash("Empresa creada. " + N.enlace_activacion(u, enlace, empresa=e)[1] + f" Siguiente paso: configure su sistema ({SIS.SISTEMAS[sistema]['nombre']}).")
                return e.id
            elif a in ("suspender", "reactivar", "reenlace"):
                e = db.session.get(Empresa, int(F["eid"]))
                if e is None: raise ValueError("Empresa inexistente")
                if a == "reenlace":
                    u = db.session.execute(select(Usuario).where(Usuario.empresa_id == e.id, Usuario.rol == "Administrador").order_by(Usuario.id)).scalars().first()
                    if u is None: raise ValueError("La empresa no tiene administrador")
                    enlace = nuevo_enlace_activacion(u); u.sesion_version += 1
                else:
                    e.activa = a == "reactivar"
                registrar("EMPRESA_" + a.upper(), e.razon_social, empresa_id=e.id); db.session.commit()
                if a == "reenlace": flash(N.enlace_activacion(u, enlace, "reenlace", empresa=e)[1] + " Reemplaza su clave y su MFA.")
            else: raise ValueError("Acción inválida")
        nuevo = []
        intentar(lambda: nuevo.append(hacer()), "Listo")
        if nuevo and nuevo[0]: return redirect(url_for("web.maestro_sistema", eid=nuevo[0]))
        return redirect(request.url)
    es = db.session.execute(select(Empresa).order_by(Empresa.razon_social)).scalars().all()
    from .conexiones import OPCIONES_SISTEMA
    n_us = lambda eid: db.session.execute(select(func.count()).select_from(Usuario).where(Usuario.empresa_id == eid)).scalar()
    return pagina("Empresas", [
        dict(nota="Abra una empresa para registrar o cambiar su sistema administrativo y configurarlo. La empresa no configura conexiones.",
             tabla=dict(cols=["Empresa", "RIF", "Sistema", "Catálogo", "Usuarios", "Estado", "Alta"], num=[4], filas=[
            dict(url=url_for("web.maestro_sistema", eid=e.id), cells=[e.razon_social, e.rif, _sistema_txt(e.id), _catalogo_txt(e.id), n_us(e.id),
                                                                       "Activa" if e.activa else "Suspendida", hora(e.creada)]) for e in es])),
        dict(titulo="Nueva empresa", nota="Se crea la empresa y su administrador; recibirá un enlace de un solo uso para crear su contraseña y su verificación en dos pasos.",
             form=dict(action=request.path, boton="Crear empresa", campos=[oculto("crear"), campo("razon_social", "Razón social"), campo("rif", "RIF", ph="J-12345678-9"),
                       campo("direccion", "Dirección", req=False), campo("telefono", "Teléfono", req=False), campo("correo", "Correo de la empresa", "email", req=False),
                       campo("username", "Usuario del administrador"), campo("email", "Correo del administrador", "email", req=False),
                       campo("sistema", "Sistema administrativo a conectar", "select", opciones=OPCIONES_SISTEMA, valor="profit",
                             ayuda="Después de crearla verá las opciones de ese sistema")])),
        dict(titulo="Suspender, reactivar o nuevo enlace del administrador", form=dict(action=request.path, boton="Aplicar", campos=[
            campo("eid", "Empresa", "select", opciones=[(e.id, e.razon_social) for e in es]),
            campo("accion", "Acción", "select", opciones=[("suspender", "Suspender (nadie entra, el bot no atiende)"), ("reactivar", "Reactivar"),
                                                          ("reenlace", "Nuevo enlace para el administrador")])]))])


@bp.route("/maestro/whatsapp", methods=["GET", "POST"])
@requiere("Maestro")
def maestro_whatsapp():
    """Pasarela OpenWA propia del bot: aquí (y solo aquí) van la URL y la clave de API."""
    p, cif = db.session.get(WhatsappPasarela, 1), SV.cifrador()
    transporte = current_app.extensions.get("openwa_transporte")
    if request.method == "POST":
        try:
            url, clave = OW.validar_url(F["url"]), (F.get("api_key") or "").strip()
            if p is None and not clave: raise ValueError("Indique la clave de API de la pasarela")
            n = OW.Pasarela(url, clave or cif.descifrar(p.api_key_cifrada), transporte).salud()      # se verifica ANTES de guardar
            if p is None:
                p = WhatsappPasarela(id=1, url=url, api_key_cifrada=cif.cifrar(clave)); db.session.add(p)
            p.url, p.activa, p.actualizado_por = url, F.get("activa", "1") == "1", g.usuario.username
            if clave: p.api_key_cifrada = cif.cifrar(clave)
            registrar("PASARELA", f"url={url} activa={p.activa} clave_nueva={bool(clave)}"); db.session.commit()
            flash(f"Pasarela verificada y guardada ({n} sesión(es) en ella).")
        except (ErrorWhatsapp, ValueError) as e:
            db.session.rollback(); flash(str(e))
        return redirect(request.url)
    sesiones, ow = [], SV.pasarela()
    if ow is not None:
        try: sesiones = ow.sesiones()
        except ErrorWhatsapp as e: flash(f"No se pudo leer la pasarela: {e}")
    nombres = {e.id: e.razon_social for e in db.session.execute(select(Empresa)).scalars()}
    empresa = lambda n: nombres.get(int(m.group(1)), f"Empresa {m.group(1)}") if (m := re.match(r"(?:surtia|pedyra)-e(\d+)-", str(n or ""))) else "— (creada fuera del bot)"
    origen = "consola" if p else ("variables OPENWA_URL / OPENWA_API_KEY" if ow else "sin configurar")
    return pagina("Pasarela de WhatsApp", [
        dict(kpis=[("Pasarela", ("Activa" if (p.activa if p else True) else "Pausada") if ow or p else "Sin configurar"), ("Origen", origen), ("Sesiones", len(sesiones)),
                   ("Conectadas", sum(x.get("status") == "ready" for x in sesiones))]),
        dict(titulo="Pasarela OpenWA del bot", abierto=True,
             nota="Pasarela propia de este bot (no la de Surtia): cada empresa tiene su sesión y vincula SU número con el QR. La clave de administrador la "
                  "genera OpenWA en su primer arranque (aparece en su log y en /app/data/.api-key). Se guarda cifrada; déjela vacía para conservar la actual. "
                  "Antes de guardar se comprueba que la URL y la clave funcionen.",
             form=dict(action=request.path, boton="Verificar y guardar", campos=[campo("url", "URL de la pasarela", valor=p.url if p else current_app.config.get("OPENWA_URL")),
                       campo("api_key", "Clave de API (X-API-Key)", "password", req=p is None),
                       campo("activa", "Estado", "select", opciones=[("1", "Activa"), ("0", "Pausada: nadie puede vincular ni enviar")], valor="1" if (p is None or p.activa) else "0")])),
        dict(titulo="Sesiones en la pasarela", tabla=dict(cols=["Empresa", "Estado", "Número", "Nombre en WhatsApp", "Última actividad"], filas=[
            dict(cells=[empresa(x.get("name")), OW.ESTADOS.get(x.get("status"), x.get("status") or "—"), ("+" + x["phone"]) if x.get("phone") else "—",
                        x.get("pushName") or "—", str(x.get("lastActive") or "—")[:16].replace("T", " ")]) for x in sesiones]))])


@bp.route("/maestro/parametros", methods=["GET", "POST"])
@requiere("Maestro")
def maestro_parametros():
    pr = SV.parametros()
    if request.method == "POST":
        def guardar():
            a = F.get("accion")
            num = lambda k: Decimal((F.get(k) or "").replace(",", "."))
            try:
                if a == "iva":
                    vals = [num(k) for k in ("iva_general", "iva_reducida", "iva_adicional")]
                    if any(not (0 <= v <= 50) for v in vals): raise ValueError("Alícuotas fuera de rango")
                    pr.iva_general, pr.iva_reducida, pr.iva_adicional = vals
                elif a == "tasa":
                    usd = num("tasa_usd")
                    if not (Decimal("0.0001") < usd < Decimal("1e8")): raise ValueError("Tasa fuera de rango")
                    pr.tasa_usd, pr.tasa_eur = usd, (num("tasa_eur") if F.get("tasa_eur") else pr.tasa_eur)
                    from ..tiempo import hoy_ve
                    pr.tasa_fecha, pr.tasa_fuente, pr.tasa_manual = hoy_ve(), f"Manual ({g.usuario.username})", True
                elif a == "auto":
                    if not SV.actualizar_tasa(forzar=True): raise ValueError("DolarAPI no respondió; cargue la tasa a mano")
                else: raise ValueError("Acción inválida")
            except InvalidOperation as e:
                raise ValueError("Número inválido") from e
            registrar("PARAMETROS", a); db.session.commit()
        intentar(guardar, "Parámetros guardados")
        return redirect(request.url)
    return pagina("Parámetros", [
        dict(pares=[("Tasa BCV USD", f"{pr.tasa_usd:,.4f}" if pr.tasa_usd else "—"), ("Tasa BCV EUR", f"{pr.tasa_eur:,.4f}" if pr.tasa_eur else "—"),
                    ("Vigencia", str(pr.tasa_fecha or "—")), ("Fuente", pr.tasa_fuente or "—")]),
        dict(titulo="Tasa BCV", nota="Se toma sola de DolarAPI (BCV) una vez al día. Una tasa cargada a mano tiene prioridad ese día. Los precios en divisas se muestran en Bs con ella.",
             form=dict(action=request.path, boton="Guardar tasa manual", campos=[oculto("tasa"), campo("tasa_usd", "Bs por USD"), campo("tasa_eur", "Bs por EUR", req=False)])),
        dict(titulo="Actualizar desde el BCV ahora", form=dict(action=request.path, boton="Consultar", boton_clase="sec", campos=[oculto("auto")])),
        dict(titulo="Alícuotas de IVA", nota="Para presupuestos y órdenes de venta. Adicional = la de lujo, que se suma a la general.",
             form=dict(action=request.path, boton="Guardar", campos=[oculto("iva"), campo("iva_general", "General %", valor=pr.iva_general),
                       campo("iva_reducida", "Reducida %", valor=pr.iva_reducida), campo("iva_adicional", "Adicional (lujo) %", valor=pr.iva_adicional)]))])


@bp.route("/maestro/bitacora")
@requiere("Maestro")
def maestro_bitacora():
    nombres = {e.id: e.razon_social for e in db.session.execute(select(Empresa)).scalars()}
    bs = db.session.execute(select(Bitacora).order_by(Bitacora.id.desc()).limit(3000)).scalars()
    return pagina("Bitácora", [dict(tabla=dict(cols=["Fecha", "Empresa", "Usuario", "Acción", "Detalle", "IP"], filas=[
        dict(cells=[hora(b.ts), nombres.get(b.empresa_id, "Plataforma"), b.usuario, b.accion, (b.detalle or "")[:200], b.ip or ""]) for b in bs]))])


@bp.route("/maestro/usuarios", methods=["GET", "POST"])
@requiere("Maestro")
def maestro_usuarios():
    """Otros Maestros de Gigadevs (soporte)."""
    if request.method == "POST":
        def hacer():
            nombre = (F.get("username") or "").strip().lower()
            if not re.fullmatch(r"[a-z0-9._-]{3,40}", nombre): raise ValueError("Usuario: 3 a 40 letras, números, punto, guion")
            if db.session.query(Usuario).filter(func.lower(Usuario.username) == nombre).first(): raise ValueError("Ese usuario ya existe")
            u = Usuario(username=nombre, email=(F.get("email") or "").strip()[:254] or None, rol="Maestro"); db.session.add(u); db.session.flush()
            enlace = nuevo_enlace_activacion(u); registrar("MAESTRO_INVITADO", nombre); db.session.commit()
            flash(N.enlace_activacion(u, enlace)[1])
        intentar(hacer, "Listo")
        return redirect(request.url)
    us = db.session.execute(select(Usuario).where(Usuario.rol == "Maestro").order_by(Usuario.username)).scalars()
    return pagina("Maestros", [dict(tabla=dict(cols=["Usuario", "Correo", "Estado", "Último acceso"], filas=[
        dict(cells=[u.username, u.email or "—", "Activo" if u.password_hash else "Sin activar", hora(u.ultimo_acceso)]) for u in us])),
        dict(titulo="Invitar Maestro", form=dict(action=request.path, boton="Crear enlace", campos=[campo("username", "Usuario"), campo("email", "Correo", "email", req=False)]))])


# ====================================================================== correo: estado, plantillas y envío de prueba
def _muestras():
    """Plantillas de correo con datos de ejemplo (no tocan la base de datos)."""
    from datetime import date
    from types import SimpleNamespace
    from .. import plantilla_correo as PC
    cfg = current_app.config
    base, nombre, lema = cfg["APP_BASE_URL"], cfg["APP_NOMBRE"], cfg.get("APP_LEMA", "")
    em = SimpleNamespace(razon_social="General de Suministros Orituco, C.A.", rif="J-12345678-0", direccion="Altagracia de Orituco", telefono="0238-1234567")
    doc = dict(titulo="Orden de venta", numero="OV-000001", fecha=date.today(), estado="Nuevo", nota="Su orden de compra: 9001.",
               cliente=dict(nombre="COMERCIAL ORITUCO CA", rif="J-11111111-1", codigo="C001", direccion="Av. Principal, Altagracia", telefono="584147778899"),
               lineas=[dict(codigo="HAR-01", descripcion="HARINA PAN 1KG", cantidad=3, precio="45.50", monto="136.50", alicuota="G"),
                       dict(codigo="LEC-01", descripcion="LECHE COMPLETA 1L", cantidad=2, precio="60.00", monto="120.00", alicuota="E")],
               exento="120.00", base="136.50", iva="21.84", total="278.34")
    h = lambda *a, **k: PC.html(*a, base, nombre, lema, **k)
    return {
        "activacion": ("Activación de cuenta", h("Active su cuenta", ["Hola:", "Se creó su cuenta de General de Suministros Orituco, C.A. en Surtia.",
                       "Con el botón creará su contraseña y activará la verificación en dos pasos."], boton=("Crear mi contraseña", base + "/activar/ejemplo"),
                       filas=[("Usuario", "orituco"), ("Rol", "Administrador"), ("Vigencia", "72 horas, un solo uso")], aviso=("info", "Activación de cuenta"),
                       nota="Si no esperaba este correo, ignórelo. Surtia nunca le pedirá su contraseña ni sus códigos.")),
        "recuperacion": ("Recuperar contraseña", h("Recupere su contraseña", ["Recibimos una solicitud para cambiar la contraseña de su cuenta en Surtia.",
                         "Con el botón elegirá una contraseña nueva. Para confirmar que es usted, después le pediremos el código de 6 dígitos de su aplicación "
                         "autenticadora: sin ese código la contraseña no cambia."], boton=("Elegir una contraseña nueva", base + "/recuperar/ejemplo"),
                         filas=[("Usuario", "orituco"), ("Vigencia", "30 minutos, un solo uso"), ("Dirección IP", "190.202.0.10")], aviso=("alerta", "Recuperación de contraseña"),
                         nota="Si no lo pidió usted, ignore este correo: su contraseña sigue igual.")),
        "seguridad": ("Nuevo inicio de sesión", h("Nuevo inicio de sesión", ["Se inició sesión en su cuenta.", "Si fue usted, no tiene que hacer nada."],
                      filas=[("Usuario", "orituco"), ("Fecha", "05/11/2026 09:14 am"), ("Dirección IP", "190.202.0.10")], aviso=("info", "Seguridad de la cuenta"),
                      boton=("Abrir Surtia", base + "/login"))),
        "pedido": ("Pedido nuevo (documento)", PC.documento_html(doc, em, base, nombre, lema, intro="COMERCIAL ORITUCO CA hizo un pedido por WhatsApp. Revíselo y cárguelo en Profit Plus.",
                   boton=("Abrir el pedido en Surtia", base + "/pedidos/1"), aviso=("info", "Pedido nuevo"))),
        "presupuesto": ("Presupuesto aceptado (documento)", PC.documento_html(dict(doc, titulo="Presupuesto", numero="P-000001", estado="Aceptado", nota=None,
                        valido_hasta=date.today()), em, base, nombre, lema, intro="COMERCIAL ORITUCO CA aceptó el presupuesto por WhatsApp.",
                        boton=("Abrir el presupuesto en Surtia", base + "/presupuestos/1"), aviso=("info", "Presupuesto aceptado"))),
        "atencion": ("Cliente pide una persona", h("Un cliente pide hablar con una persona", ["COMERCIAL ORITUCO CA pidió atención de una persona en el WhatsApp de la empresa."],
                     filas=[("Cliente", "COMERCIAL ORITUCO CA"), ("WhatsApp", "+584147778899")], boton=("Responder la conversación", base + "/conversaciones"),
                     aviso=("alerta", "Atención humana"))),
        "desconectado": ("WhatsApp desconectado", h("El WhatsApp del bot se desconectó", ["El número de la empresa dejó de estar vinculado: el bot NO está respondiendo.",
                         "Entre a Surtia → WhatsApp y vuelva a escanear el código QR."], filas=[("Empresa", em.razon_social), ("Estado", "disconnected")],
                         boton=("Volver a vincular", base + "/whatsapp"), aviso=("peligro", "Bot sin conexión"))),
    }


@bp.route("/maestro/correo", methods=["GET", "POST"])
@requiere("Maestro")
def maestro_correo():
    from .. import correo
    cfg = current_app.config
    if request.method == "POST":
        destino = (F.get("destino") or "").strip()
        if not correo.valido(destino): flash("Correo de destino inválido.")
        elif correo.enviar_seguro(destino, "Correo de prueba", "Si recibe este mensaje, el envío de correos de Surtia está bien configurado.",
                                  filas=[("Método", cfg["MAIL_BACKEND"]), ("Remitente", cfg["MAIL_FROM"])], aviso=("info", "Prueba")):
            registrar("CORREO_PRUEBA", destino); db.session.commit()
            flash(f"Correo de prueba enviado a {destino}." if correo.real() else "Modo consola: el correo se escribió en el log (configure BREVO_API_KEY para enviarlo de verdad).")
        else: flash("No se pudo enviar: revise BREVO_API_KEY, el remitente verificado en Brevo o los datos SMTP.")
        return redirect(request.url)
    modo = {"brevo": "Brevo (API)", "smtp": "SMTP", "console": "Consola (no sale)"}.get(cfg["MAIL_BACKEND"], cfg["MAIL_BACKEND"])
    return pagina("Correo", [
        dict(kpis=[("Envío", modo), ("Estado", "Activo" if correo.real() else "Sin configurar"), ("Remitente", cfg["MAIL_FROM"].split("<")[-1].rstrip(">"))]),
        dict(titulo="Plantillas", nota="Así se ven los correos que envía Surtia (datos de ejemplo). Se abren en una pestaña nueva.",
             enlaces=[dict(url=url_for("web.maestro_correo_vista", nombre=k), texto=v[0], icono="correo", nueva=True) for k, v in _muestras().items()]),
        dict(titulo="Enviar un correo de prueba", abierto=True, form=dict(action=request.path, boton="Enviar prueba", campos=[
            campo("destino", "Correo de destino", "email", valor=g.usuario.email or cfg.get("MAESTRO_EMAIL") or "")]))])


@bp.route("/maestro/correo/vista/<nombre>")
@requiere("Maestro")
def maestro_correo_vista(nombre):
    m = _muestras().get(nombre)
    if m is None: abort(404)
    r = current_app.response_class(m[1], mimetype="text/html")
    # el correo lleva estilos en línea (así lo exigen los clientes de correo): solo esta vista los permite, sin scripts
    r.headers["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
    return r
