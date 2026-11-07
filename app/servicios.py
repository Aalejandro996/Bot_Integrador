"""Servicios del bot: pasarela de WhatsApp, catálogo del sistema de la empresa (Profit Plus u otros), conversación, pedidos,
presupuestos y tasa BCV."""
import json
import logging
import secrets
import urllib.request
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

from flask import current_app
from sqlalchemy import delete, func, insert, select

from . import bot as BOT
from . import notificaciones as N
from . import openwa as OW
from . import profit as PF
from . import sistemas as SIS
from .extensions import db
from .models import (Contacto, Empresa, Mensaje, Parametros, Pedido, Presupuesto, ProfitArticulo, ProfitCliente, ProfitConexion,
                     ProfitPlataforma, ProfitSincronizacion, WhatsappConfig, WhatsappPasarela, ahora)
from .seguridad import registrar
from .telefono import ErrorWhatsapp, telefono
from .tiempo import hoy_ve

log = logging.getLogger("bot")
C2 = Decimal("0.01")
GUARDAR_SINC = 200


def cifrador():
    return current_app.extensions["cifrador"]


def _siguiente(eid, Modelo) -> int:
    """Consecutivo por empresa sin saltos ni duplicados: se bloquea la fila de la empresa mientras se numera."""
    db.session.execute(select(Empresa.id).where(Empresa.id == eid).with_for_update())
    return (db.session.execute(select(func.max(Modelo.numero)).where(Modelo.empresa_id == eid)).scalar() or 0) + 1


def bs(v) -> str:
    return f"{Decimal(str(v)):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ====================================================================== parámetros y tasa BCV
def parametros() -> Parametros:
    p = db.session.get(Parametros, 1)
    if p is None:
        p = Parametros(id=1, iva_general=16, iva_reducida=8, iva_adicional=15); db.session.add(p); db.session.flush()
    return p


def pct_iva(alicuota, p=None) -> Decimal:
    p = p or parametros()
    return {"G": Decimal(p.iva_general), "R": Decimal(p.iva_reducida), "A": Decimal(p.iva_general) + Decimal(p.iva_adicional)}.get(alicuota, Decimal(0))


URL_TASAS = {"USD": "https://ve.dolarapi.com/v1/dolares/oficial", "EUR": "https://ve.dolarapi.com/v1/euros/oficial"}
_ultimo_intento = [0.0]


def _get_json(url):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Surtia/1.0"})
    with urllib.request.urlopen(req, timeout=6) as r:  # noqa: S310 (URL fija HTTPS)
        return json.loads(r.read(64 * 1024))


def actualizar_tasa(obtener=None, forzar=False) -> bool:
    """Tasa oficial del BCV (vía DolarAPI), como mucho cada 30 min y solo si falta la de hoy. Nunca pisa una tasa manual."""
    import time
    p = parametros()
    if not forzar and p.tasa_fecha == hoy_ve(): return False
    if not forzar and time.time() - _ultimo_intento[0] < 1800: return False
    _ultimo_intento[0] = time.time()
    try:
        datos = {m: (obtener or _get_json)(u) for m, u in URL_TASAS.items()}
        usd, eur = (Decimal(str(datos[m]["promedio"])) for m in ("USD", "EUR"))
        fecha = date.fromisoformat(str(datos["USD"]["fechaActualizacion"])[:10])
        if not (Decimal("0.0001") < usd < Decimal("1e8")) or fecha > hoy_ve() + timedelta(days=7): raise ValueError("fuera de rango")
    except (OSError, ValueError, KeyError, TypeError, InvalidOperation) as e:
        log.warning("Tasa BCV automática no disponible: %s", e); return False
    if p.tasa_manual and p.tasa_fecha and p.tasa_fecha >= fecha: return False
    p.tasa_usd, p.tasa_eur, p.tasa_fecha, p.tasa_fuente, p.tasa_manual = usd, eur, fecha, "BCV vía DolarAPI", False
    db.session.commit()
    return True


def tasa(moneda) -> Decimal | None:
    if moneda == "VES": return Decimal(1)
    if current_app.config.get("TASAS_AUTOMATICAS"): actualizar_tasa()
    p = parametros()
    v = p.tasa_usd if moneda == "USD" else p.tasa_eur
    return Decimal(v) if v else None


# ====================================================================== pasarela de WhatsApp (OpenWA propia)
def pasarela():
    """Cliente de la pasarela: la configurada por el Maestro o, si no hay, la de OPENWA_URL / OPENWA_API_KEY."""
    p = db.session.get(WhatsappPasarela, 1)
    transporte = current_app.extensions.get("openwa_transporte")
    if p is not None:
        return OW.Pasarela(p.url, cifrador().descifrar(p.api_key_cifrada), transporte) if p.activa else None
    url, clave = current_app.config.get("OPENWA_URL"), current_app.config.get("OPENWA_API_KEY")
    return OW.Pasarela(url, clave, transporte) if url and clave else None


def config_wa(eid) -> WhatsappConfig | None:
    return db.session.get(WhatsappConfig, eid)


def conectar_wa(eid, url_webhook) -> WhatsappConfig:
    """Crea (o reanuda) la sesión de la empresa en la pasarela, registra su aviso firmado y la inicia: queda el QR."""
    ow = pasarela()
    if ow is None: raise ErrorWhatsapp("La pasarela de WhatsApp no está configurada. Contacte a soporte de Gigadevs.")
    cfg = config_wa(eid)
    if cfg is None:
        cfg = WhatsappConfig(empresa_id=eid, clave_webhook=secrets.token_urlsafe(24), secreto_cifrado=cifrador().cifrar(secrets.token_urlsafe(32)), activo=True)
        db.session.add(cfg); db.session.flush()
    if not cfg.sesion_id:
        secreto = secrets.token_urlsafe(32)
        sid = ow.crear_sesion(OW.nombre_sesion(eid, secrets.token_hex(4)))
        ow.registrar_webhook(sid, url_webhook(cfg.clave_webhook), secreto)
        cfg.sesion_id, cfg.secreto_cifrado = sid, cifrador().cifrar(secreto)
    db.session.commit()             # ANTES de iniciar: la pasarela avisa al instante y el aviso debe encontrar la sesión
    try:
        ow.iniciar(cfg.sesion_id)
    except OW.SesionInexistente:
        cfg.sesion_id = None; db.session.commit()
        return conectar_wa(eid, url_webhook)
    guardar_estado(cfg, "initializing", None)
    return cfg


def guardar_estado(cfg, estado, tel):
    anterior = cfg.sesion_estado
    cfg.sesion_estado, cfg.sesion_actualizada = estado, ahora()
    if estado == "ready" and tel: cfg.numero_visible = "".join(c for c in str(tel) if c.isdigit())[:20] or cfg.numero_visible
    db.session.commit()
    if anterior != estado: N.whatsapp_cambio(cfg, anterior, estado)
    return estado


def enviar_wa(eid, destino, texto, contacto=None) -> str:
    """Envía un texto por el número de la empresa y lo deja en la conversación. Lanza ErrorWhatsapp si no se pudo."""
    cfg = config_wa(eid)
    tel = telefono(destino)
    try:
        ow = pasarela()
        if cfg is None or not cfg.activo: raise ErrorWhatsapp("El bot de WhatsApp está pausado")
        if ow is None or not cfg.sesion_id: raise ErrorWhatsapp("El WhatsApp de la empresa no está vinculado")
        wamid, res = "ow." + ow.enviar_texto(cfg.sesion_id, tel, texto), "ok"
    except ErrorWhatsapp as e:
        wamid, res = None, str(e)[:300]
    if contacto is None:
        contacto = db.session.execute(select(Contacto).where(Contacto.empresa_id == eid, Contacto.telefono == tel)).scalar_one_or_none()
    if wamid and db.session.execute(select(Mensaje.id).where(Mensaje.wamid == wamid)).first(): wamid = None   # pasarela que repite ids
    db.session.add(Mensaje(empresa_id=eid, contacto_id=contacto.id if contacto else None, telefono=tel, direccion="out", texto=str(texto)[:4000],
                           wamid=wamid, resultado=res))
    if res != "ok": raise ErrorWhatsapp(res)
    return wamid


# ====================================================================== Profit Plus
def profit_plataforma() -> ProfitPlataforma | None:
    return db.session.get(ProfitPlataforma, 1)


def profit_conexion(eid) -> ProfitConexion | None:
    return db.session.get(ProfitConexion, eid)


def crear_conexion_profit(eid, sistema="profit") -> ProfitConexion:
    c = profit_conexion(eid)
    if c is not None: return c
    if sistema not in SIS.SISTEMAS: raise ValueError("Sistema desconocido")
    c = ProfitConexion(empresa_id=eid, sistema=sistema, opciones={}, conector_id="pp_" + secrets.token_hex(10), secreto_cifrado=cifrador().cifrar(secrets.token_urlsafe(32)),
                       activo=True, almacenes=[], lista_precio="01", restar_comprometido=True, solo_con_existencia=False, disponibles={})
    db.session.add(c); db.session.flush()
    return c


def url_api_profit() -> str:
    p = profit_plataforma()
    return ((p.url_publica if p and p.url_publica else None) or current_app.config["APP_BASE_URL"]).rstrip("/")


def nombre_sistema(eid) -> str:
    c = profit_conexion(eid)
    return SIS.nombre(c.sistema if c else None, c.opciones if c else None)


def credenciales(c: ProfitConexion) -> dict:
    if not c.credenciales_cifradas: return {}
    try: return json.loads(cifrador().descifrar(c.credenciales_cifradas))
    except (ValueError, TypeError): return {}


def guardar_credenciales(c: ProfitConexion, nuevas: dict):
    """Solo reemplaza las claves que llegaron con valor (un campo vacío conserva la clave guardada)."""
    d = credenciales(c); d.update({k: v for k, v in nuevas.items() if v})
    c.credenciales_cifradas = cifrador().cifrar(json.dumps(d)) if d else None


def conector_ini(c: ProfitConexion) -> str:
    consultas = SIS.consultas_ini(c.opciones or {}) if c.sistema == "sql" else ""
    extra = f"\n[consultas]\n; Consultas a la medida del sistema del cliente (todas de solo lectura).\n{consultas}\n" if consultas else ""
    return _ini_base(c) + extra


def _ini_base(c: ProfitConexion) -> str:
    return f"""; Conector Surtia ↔ {SIS.nombre(c.sistema, c.opciones) if c.sistema != 'sql' else 'sistema con SQL Server'} (SOLO LECTURA). Generado el {hoy_ve().isoformat()}.
; Lo instala el equipo de Surtia junto a surtia_conector.exe. No lo comparta: la clave identifica a la empresa.

[surtia]
url = {url_api_profit()}
conector = {c.conector_id}
clave = {cifrador().descifrar(c.secreto_cifrado)}

[sqlserver]
; Servidor y base de datos del sistema. Use un usuario de SOLO LECTURA (vea crear_usuario_lectura.sql).
servidor = localhost\\SQLEXPRESS
base_datos = NOMBRE_DE_LA_EMPRESA_EN_PROFIT
usuario = surtia_lectura
contrasena =
; driver = ODBC Driver 18 for SQL Server
; cifrar = no

[profit]
; Versión: 2k12 (Profit Plus 2K12 / 12.x). Para otra versión ajuste las consultas en la sección [consultas].
esquema = 2k12
stock_actual = ACT
stock_comprometido = COM

[alicuotas]
; tipo_imp de Profit → alícuota (G general, R reducida, A adicional/lujo, E exento)
1 = G
2 = R
3 = A
6 = E
7 = E
"""


def aplicar_foto(eid, c: ProfitConexion, foto: dict, ip=None, duracion_ms=None):
    """Reemplaza artículos y clientes por la foto recibida en UNA transacción (o queda la anterior)."""
    db.session.execute(delete(ProfitArticulo).where(ProfitArticulo.empresa_id == eid))
    db.session.execute(delete(ProfitCliente).where(ProfitCliente.empresa_id == eid))
    if foto["articulos"]: db.session.execute(insert(ProfitArticulo), [dict(empresa_id=eid, **a) for a in foto["articulos"]])
    if foto["clientes"]: db.session.execute(insert(ProfitCliente), [dict(empresa_id=eid, **x) for x in foto["clientes"]])
    c.ultima_sync = c.ultimo_contacto = ahora()
    c.ultimo_estado, c.ultimo_error = "ok", None
    c.n_articulos, c.n_clientes = len(foto["articulos"]), len(foto["clientes"])
    c.agente_version, c.agente_equipo = foto.get("version") or c.agente_version, foto.get("equipo") or c.agente_equipo
    if foto["disponibles"]["almacenes"] or foto["disponibles"]["listas_precio"]: c.disponibles = foto["disponibles"]
    anotar_sync(eid, "ok", articulos=c.n_articulos, clientes=c.n_clientes, duracion_ms=duracion_ms, ip=ip)


def anotar_sync(eid, resultado, articulos=0, clientes=0, duracion_ms=None, detalle=None, ip=None):
    db.session.add(ProfitSincronizacion(empresa_id=eid, resultado=resultado, articulos=articulos, clientes=clientes, duracion_ms=duracion_ms,
                                        detalle=(detalle or "")[:500] or None, ip=(ip or "")[:60] or None))
    db.session.flush()
    corte = db.session.execute(select(ProfitSincronizacion.id).where(ProfitSincronizacion.empresa_id == eid)
                               .order_by(ProfitSincronizacion.id.desc()).offset(GUARDAR_SINC).limit(1)).scalar()
    if corte: db.session.execute(delete(ProfitSincronizacion).where(ProfitSincronizacion.empresa_id == eid, ProfitSincronizacion.id <= corte))


def error_conector(eid, c, mensaje, ip=None):
    c.ultimo_contacto, c.ultimo_estado, c.ultimo_error = ahora(), "error", str(mensaje)[:500]
    anotar_sync(eid, "error", detalle=str(mensaje), ip=ip)


def recibir_foto(eid, c: ProfitConexion, cruda: dict, ip=None, duracion_ms=None) -> dict:
    """Valida y aplica una foto de cualquier sistema. Devuelve {articulos, clientes, omitidos}. Lanza PF.ErrorProfit."""
    plat = profit_plataforma()
    foto = PF.validar_foto(cruda, plat.max_articulos if plat else 20000, plat.max_clientes if plat else 50000)
    omitidos = max(len(cruda.get("articulos") or []) - len(foto["articulos"]), 0)
    aplicar_foto(eid, c, foto, ip=ip, duracion_ms=duracion_ms)
    return dict(articulos=c.n_articulos, clientes=c.n_clientes, omitidos=omitidos)


def sincronizar_fuente(eid, transporte=None) -> dict:
    """Lee AHORA un sistema en la nube (Odoo, WooCommerce, hoja en línea) y reemplaza el catálogo. Errores → ValueError legible."""
    import time
    c = profit_conexion(eid)
    if c is None or c.sistema not in SIS.LECTORES: raise ValueError("Esta empresa no usa un sistema en la nube")
    plat = profit_plataforma()
    c.ultimo_intento, t0 = ahora(), time.monotonic()
    try:
        cruda = SIS.LECTORES[c.sistema](c.opciones or {}, credenciales(c), transporte or current_app.extensions.get("fuente_transporte") or SIS.transporte_http,
                                        max_articulos=plat.max_articulos if plat else 20000, max_clientes=plat.max_clientes if plat else 50000)
        return recibir_foto(eid, c, cruda, duracion_ms=int((time.monotonic() - t0) * 1000))
    except (SIS.ErrorFuente, PF.ErrorProfit) as e:
        error_conector(eid, c, str(e)); db.session.commit()
        raise ValueError(f"No se pudo leer {SIS.SISTEMAS[c.sistema]['nombre']}: {e}") from e


def importar_tabla(eid, contenido: bytes, nombre_archivo: str, solo_vista=False) -> dict:
    """Excel/CSV subido por el Maestro → vista previa o catálogo nuevo (reemplaza el anterior completo)."""
    c = profit_conexion(eid)
    if c is None: raise ValueError("La empresa no tiene sistema registrado")
    try:
        enc, filas = SIS.leer_tabla(contenido, nombre_archivo)
        cruda = SIS.foto_de_tabla(enc, filas, c.opciones or {})
        if solo_vista:
            foto = PF.validar_foto(cruda, 10**7, 10**7)
            return dict(encabezados=enc, articulos=foto["articulos"], omitidos=len(cruda["articulos"]) - len(foto["articulos"]), filas=len(filas))
        return recibir_foto(eid, c, cruda)
    except (SIS.ErrorFuente, PF.ErrorProfit) as e:
        raise ValueError(str(e)) from e


def pendientes_nube() -> list[int]:
    """Empresas activas con sistema en la nube a las que ya les toca leer (según el intervalo de la plataforma)."""
    plat = profit_plataforma()
    if plat is None or not plat.activa: return []
    limite = ahora() - timedelta(minutes=plat.intervalo_min)
    q = (select(ProfitConexion.empresa_id).join(Empresa, Empresa.id == ProfitConexion.empresa_id)
         .where(ProfitConexion.sistema.in_(tuple(SIS.LECTORES)), ProfitConexion.activo.is_(True), Empresa.activa.is_(True),
                (ProfitConexion.ultimo_intento.is_(None)) | (ProfitConexion.ultimo_intento < limite)))
    return list(db.session.execute(q).scalars())


def programador(app, cada_s=60):
    """Hilo que lee los sistemas en la nube. Con varios procesos, un candado de PostgreSQL deja trabajar a uno solo."""
    import threading
    import time

    def bucle():
        while True:
            time.sleep(cada_s)
            with app.app_context():
                from . import rls
                try:
                    rls.fijar(maestro=True)               # tarea interna: lista las empresas pendientes
                    if db.engine.dialect.name == "postgresql" and not db.session.execute(db.text("SELECT pg_try_advisory_xact_lock(472019)")).scalar():
                        db.session.rollback(); continue
                    for eid in pendientes_nube():
                        rls.fijar(empresa_id=eid)         # cada lectura, solo con los datos de esa empresa
                        try: sincronizar_fuente(eid); db.session.commit()
                        except ValueError as e: db.session.rollback(); log.warning("Lectura programada empresa %s: %s", eid, e)
                    db.session.commit()
                except Exception:  # noqa: BLE001 — el hilo nunca debe morir
                    db.session.rollback(); log.exception("Programador de lecturas")
                finally:
                    db.session.remove()

    threading.Thread(target=bucle, name="surtia-lecturas", daemon=True).start()


def precio_bs(a: ProfitArticulo) -> Decimal | None:
    if a.moneda == "VES": return Decimal(a.precio)
    t = tasa(a.moneda)
    return (Decimal(a.precio) * t).quantize(C2, ROUND_HALF_UP) if t else None


def catalogo(eid, todos=False) -> dict:
    """{codigo: (nombre, precio_bs, existencia)} desde la foto del sistema de la empresa. Sin tasa del día, los precios en divisas no se
    ofrecen. `todos` (lectura de PDF): incluye artículos sin precio o sin existencia."""
    c = profit_conexion(eid)
    if c is None or not c.activo: return {}
    q = select(ProfitArticulo).where(ProfitArticulo.empresa_id == eid).order_by(ProfitArticulo.codigo)
    if not todos:
        q = q.where(ProfitArticulo.precio > 0)
        if c.solo_con_existencia: q = q.where(ProfitArticulo.existencia > 0)
    out = {}
    for a in db.session.execute(q).scalars():
        p = precio_bs(a)
        if p is None and not todos: continue
        out[a.codigo] = (a.nombre, p or Decimal(0), Decimal(a.existencia))
    return out


def alicuota_de(eid, codigo) -> str:
    a = db.session.get(ProfitArticulo, (eid, codigo)) if codigo else None
    return a.alicuota if a else "G"


def cliente_por_rif(eid, rif):
    if not rif: return None
    return db.session.execute(select(ProfitCliente).where(ProfitCliente.empresa_id == eid, ProfitCliente.rif == rif).limit(1)).scalar_one_or_none()


def cliente_por_whatsapp(eid, tel):
    return db.session.execute(select(ProfitCliente).where(ProfitCliente.empresa_id == eid, ProfitCliente.whatsapp == tel).limit(1)).scalar_one_or_none()


# ====================================================================== presupuestos y documentos
def totales(lineas, p=None) -> dict:
    p = p or parametros()
    exento = base = iva = Decimal(0)
    for l in lineas:
        monto = Decimal(str(l["monto"]))
        if l["alicuota"] == "E": exento += monto
        else: base += monto; iva += (monto * pct_iva(l["alicuota"], p) / 100).quantize(C2, ROUND_HALF_UP)
    return dict(exento=exento, base=base, iva=iva, total=exento + base + iva)


def lineas_con_monto(eid, lineas, cat) -> list[dict]:
    out = []
    for l in lineas:
        cod = l["codigo"]
        if cod not in cat: continue
        cant = Decimal(str(l["cantidad"]))
        precio = Decimal(str(l.get("precio") or cat[cod][1]))
        out.append(dict(codigo=cod, descripcion=l.get("descripcion") or cat[cod][0], cantidad=format(cant.normalize(), "f"), precio=str(precio),
                        alicuota=l.get("alicuota") or alicuota_de(eid, cod), monto=str((cant * precio).quantize(C2, ROUND_HALF_UP))))
    return out


def crear_presupuesto(eid, c: Contacto, lineas, estado) -> Presupuesto:
    t = totales(lineas)
    hoy = hoy_ve()
    pv = Presupuesto(empresa_id=eid, numero=_siguiente(eid, Presupuesto), fecha=hoy, valido_hasta=hoy + timedelta(days=15), contacto_id=c.id,
                     cliente_nombre=c.nombre or ("+" + c.telefono), cliente_rif=c.rif, cliente_telefono=c.telefono, lineas=lineas, estado=estado, **t)
    db.session.add(pv); db.session.flush()
    registrar("PRESUPUESTO", f"N°{pv.numero} total={pv.total} estado={estado}", empresa_id=eid, usuario="bot")
    return pv


def resumen_presupuesto(pv: Presupuesto) -> str:
    det = "\n".join(f"• {l['codigo']} {l['descripcion']} × {l['cantidad']} = Bs {bs(l['monto'])}" for l in pv.lineas)
    return (f"📑 *Presupuesto N° {pv.numero}* (válido hasta {pv.valido_hasta.strftime('%d/%m/%Y')})\n{det}\n"
            f"Base: Bs {bs(pv.base)} · Exento: Bs {bs(pv.exento)} · IVA: Bs {bs(pv.iva)}\n*Total: Bs {bs(pv.total)}*\n"
            f"Aquí puede verlo: {enlace('presupuesto', pv.empresa_id, pv.id)}\n\nSi le parece bien, respóndame *ACEPTO {pv.numero}* y lo procesamos.")


def enlace(tipo, eid, oid) -> str:
    from itsdangerous import URLSafeTimedSerializer
    token = URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="documento").dumps([tipo, eid, oid])
    return current_app.config["APP_BASE_URL"] + "/d/" + token


def leer_enlace(token):
    from itsdangerous import BadSignature, URLSafeTimedSerializer
    try:
        return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt="documento").loads(token, max_age=60 * 86400)
    except BadSignature:
        return None


def documento_pedido(p: Pedido) -> dict:
    lineas = [dict(l, monto=str((Decimal(str(l["cantidad"])) * Decimal(str(l["precio"]))).quantize(C2, ROUND_HALF_UP)),
                   alicuota=l.get("alicuota") or alicuota_de(p.empresa_id, l.get("codigo"))) for l in p.lineas]
    cp = cliente_por_rif(p.empresa_id, p.rif)
    return dict(titulo="Orden de venta", numero=f"OV-{p.numero:06d}", fecha=p.creado_en.date(), estado=p.estado, lineas=lineas, **totales(lineas),
                cliente=dict(nombre=p.nombre or ("+" + (p.telefono or "")), rif=p.rif, direccion=cp.direccion if cp else None, telefono=p.telefono,
                             codigo=p.codigo_cliente), nota=" ".join(x for x in (f"Su orden de compra: {p.referencia_cliente}." if p.referencia_cliente else "", p.nota or "") if x))


def documento_presupuesto(pv: Presupuesto) -> dict:
    cp = cliente_por_rif(pv.empresa_id, pv.cliente_rif)
    return dict(titulo="Presupuesto", numero=f"P-{pv.numero:06d}", fecha=pv.fecha, estado=pv.estado, lineas=pv.lineas, exento=pv.exento, base=pv.base,
                iva=pv.iva, total=pv.total, valido_hasta=pv.valido_hasta,
                cliente=dict(nombre=pv.cliente_nombre, rif=pv.cliente_rif, direccion=cp.direccion if cp else None, telefono=pv.cliente_telefono, codigo=None), nota=None)


# ====================================================================== conversación
def procesar_mensaje(eid, tel, nombre, texto, wamid, adjunto=None) -> list[str]:
    """Un mensaje entrante: registra, conversa (bot.py), aplica los eventos y responde. Idempotente por wamid."""
    if db.session.execute(select(Mensaje.id).where(Mensaje.wamid == wamid)).first(): return []
    tel = telefono(tel)
    c = db.session.execute(select(Contacto).where(Contacto.empresa_id == eid, Contacto.telefono == tel).with_for_update()).scalar_one_or_none()
    if c is None:
        cp = cliente_por_whatsapp(eid, tel)                       # cliente de Profit con ese celular
        c = Contacto(empresa_id=eid, telefono=tel, nombre=(cp.nombre if cp else nombre) or None, tipo="cliente" if cp else "nuevo",
                     rif=cp.rif if cp else None, codigo_profit=cp.codigo if cp else None, estado={})
        db.session.add(c); db.session.flush()
    elif nombre and not c.nombre: c.nombre = nombre[:120]
    c.ultimo_mensaje = ahora()
    db.session.add(Mensaje(empresa_id=eid, contacto_id=c.id, telefono=tel, direccion="in", texto=texto, wamid=wamid))
    cfg, e = config_wa(eid), db.session.get(Empresa, eid)
    responder = lambda t: _enviar_seguro(eid, tel, t, c)
    if c.tipo == "bloqueado": return []
    if adjunto:
        es_pdf = "pdf" in (adjunto.get("mime") or "") or adjunto.get("nombre", "").lower().endswith(".pdf")
        if es_pdf: return procesar_orden_pdf(eid, c, adjunto)
        responder("¡Gracias, recibí su archivo! 📎 Para tomarle el pedido solo, envíeme la orden de compra en *PDF* o escríbame los productos "
                  "(código y cantidad). Si prefiere, escriba *MENÚ* y le muestro las opciones.")
        return []
    import re
    m_rif = re.fullmatch(r"\s*(?:rif[:\s]*)?([VEJPGCvejpgc])\s*-?\s*(\d{8})\s*-?\s*(\d)\s*", texto or "")
    if m_rif:
        c.rif = f"{m_rif.group(1).upper()}-{m_rif.group(2)}-{m_rif.group(3)}"
        cp = cliente_por_rif(eid, c.rif)
        if cp: c.nombre, c.codigo_profit, c.tipo = cp.nombre, cp.codigo, "cliente" if c.tipo == "nuevo" else c.tipo
        responder(f"¡Gracias! Ya registré su RIF {c.rif}" + (f" a nombre de {cp.nombre}" if cp else "") + ". Con eso podemos emitir su factura. 🧾")
        return []
    peds = [(p.numero, p.estado, p.total) for p in db.session.execute(select(Pedido).where(Pedido.empresa_id == eid, Pedido.contacto_id == c.id)
                                                                      .order_by(Pedido.id.desc()).limit(10)).scalars()]
    cat = catalogo(eid)
    respuestas, estado, eventos = BOT.responder(texto, c.tipo, c.estado or {}, cat, e.razon_social, peds, (), cfg.bienvenida if cfg else None,
                                                nombre=c.nombre)
    c.estado = estado
    for ev in eventos:
        if "tipo_contacto" in ev: c.tipo = ev["tipo_contacto"]
        if ev.get("atencion"):
            if not c.atencion: N.atencion(c)
            c.atencion = True
        if "crear_pedido" in ev:
            d = ev["crear_pedido"]
            lineas = [dict(l, alicuota=alicuota_de(eid, l.get("codigo"))) for l in d["lineas"]]
            total = sum((Decimal(l["cantidad"]) * Decimal(l["precio"]) for l in lineas), Decimal(0)).quantize(C2)
            p = Pedido(empresa_id=eid, numero=_siguiente(eid, Pedido), tipo=d["tipo"], contacto_id=c.id, telefono=tel, nombre=c.nombre, rif=c.rif,
                       codigo_cliente=c.codigo_profit, lineas=lineas, total=total, nota=d.get("nota") or None)
            db.session.add(p); db.session.flush()
            respuestas = [r + f"\n\nSu número de {'pedido' if p.tipo == 'venta' else 'oferta'} es el *{p.numero}*, por si lo necesita." if i == 0 else r for i, r in enumerate(respuestas)]
            registrar("PEDIDO_WHATSAPP", f"{p.tipo} N°{p.numero} tel=…{tel[-4:]} total={total}", empresa_id=eid, usuario="bot"); N.pedido_nuevo(p)
        if "crear_presupuesto" in ev:
            lineas = lineas_con_monto(eid, ev["crear_presupuesto"]["lineas"], cat)
            if not lineas:
                respuestas = ["Uy, los productos que agregó se acaban de agotar 😕. Escriba *1* y le muestro lo que tenemos ahora."]; continue
            auto = (cfg.presupuesto_modo if cfg else "automatico") == "automatico"
            pv = crear_presupuesto(eid, c, lineas, "Enviado" if auto else "Borrador")
            respuestas = [resumen_presupuesto(pv)] if auto else [f"📑 ¡Recibido! Su solicitud de presupuesto es la N° {pv.numero}. Un asesor lo revisa y se lo envía por aquí mismo."]
        if "presupuesto_aceptado" in ev:
            pv = db.session.execute(select(Presupuesto).where(Presupuesto.empresa_id == eid, Presupuesto.numero == ev["presupuesto_aceptado"],
                                                              Presupuesto.contacto_id == c.id).with_for_update()).scalar_one_or_none()
            if pv is None or pv.estado not in ("Enviado", "Borrador"):
                respuestas = [f"No encuentro un presupuesto N° {ev['presupuesto_aceptado']} pendiente a su nombre. ¿Puede revisar el número?"]
            else:
                pv.estado = "Aceptado"; registrar("PRESUPUESTO_ACEPTADO", f"N°{pv.numero}", empresa_id=eid, usuario="bot"); N.presupuesto_aceptado(pv)
                respuestas = [f"✅ ¡Excelente! Presupuesto N° {pv.numero} aceptado. Le confirmamos la factura y la entrega por aquí mismo."]
    return [r for r in respuestas if responder(r)]


def _enviar_seguro(eid, tel, texto, c) -> bool:
    try:
        enviar_wa(eid, tel, texto, c); return True
    except ErrorWhatsapp as err:
        log.warning("El bot no pudo responder a …%s: %s", tel[-4:], err); return False


def procesar_orden_pdf(eid, c: Contacto, adjunto) -> list[str]:
    """PDF de orden de compra → lo pedido → ajustado a la existencia de Profit → ORDEN DE VENTA (la factura se emite en Profit)."""
    from . import lector_pedidos as LP
    cfg, e = config_wa(eid), db.session.get(Empresa, eid)
    responder = lambda t: _enviar_seguro(eid, c.telefono, t, c)
    descarga = current_app.extensions.get("whatsapp_descarga")
    try:
        datos = descarga(adjunto["id"]) if descarga else (adjunto.get("datos") or pasarela().descargar_media(cfg.sesion_id, adjunto["chat"], adjunto["id"]))
        cat = catalogo(eid, todos=True)
        leido = LP.interpretar(LP.texto_pdf(datos), cat)
    except (LP.ErrorLectura, ErrorWhatsapp, AttributeError) as err:
        responder(f"No pudimos leer su archivo: {err}."); return []
    rif = leido["rif"] or c.rif
    cp = cliente_por_rif(eid, rif)
    if rif and not c.rif: c.rif = rif
    nombre = (cp.nombre if cp else None) or c.nombre or ("+" + c.telefono)
    if not leido["lineas"]:
        p = Pedido(empresa_id=eid, numero=_siguiente(eid, Pedido), tipo="venta", origen="whatsapp_pdf", contacto_id=c.id, telefono=c.telefono,
                   nombre=nombre, rif=rif, codigo_cliente=cp.codigo if cp else None, lineas=[], total=0, referencia_cliente=leido["referencia"],
                   nota=("No identificado: " + "; ".join(leido["no_identificadas"]))[:2000] or "PDF sin productos reconocidos")
        db.session.add(p); db.session.flush(); N.pedido_nuevo(p)
        responder("Recibí su orden de compra, pero no logré reconocer los productos de nuestro catálogo. Un asesor la revisa y le escribe en breve. "
                  "Si prefiere, escriba *1* para ver el catálogo con los códigos.")
        return []
    lineas, faltan = LP.despachable(leido["lineas"], cat)
    for l in lineas: l["alicuota"] = alicuota_de(eid, l["codigo"])
    total = sum((Decimal(l["cantidad"]) * Decimal(l["precio"]) for l in lineas), Decimal(0)).quantize(C2)
    nota = "; ".join(([f"Sin existencia suficiente: {', '.join(faltan)}"] if faltan else []) + ([f"No identificado: {'; '.join(leido['no_identificadas'][:8])}"] if leido["no_identificadas"] else []))
    p = Pedido(empresa_id=eid, numero=_siguiente(eid, Pedido), tipo="venta", origen="whatsapp_pdf", contacto_id=c.id, telefono=c.telefono, nombre=nombre, rif=rif,
               codigo_cliente=cp.codigo if cp else None, lineas=lineas, total=total, nota=nota[:2000] or None, referencia_cliente=leido["referencia"])
    db.session.add(p); db.session.flush()
    registrar("ORDEN_PDF", f"pedido N°{p.numero} lineas={len(lineas)} faltantes={len(faltan)} ref={leido['referencia'] or ''}", empresa_id=eid, usuario="bot"); N.pedido_nuevo(p)
    doc = documento_pedido(p)
    detalle = "\n".join(f"• {l['codigo']} {l['descripcion']} × {l['cantidad']}" + (f" (pidió {l['solicitado']})" if l["solicitado"] != l["cantidad"] else "") for l in lineas)
    partes = [f"✅ ¡Recibí su orden de compra{(' N° ' + leido['referencia']) if leido['referencia'] else ''}, gracias!"]
    if lineas:
        partes += [f"📄 *Orden de venta N° {p.numero}*\n{detalle}", f"*Total: Bs {bs(doc['total'])}* (IVA incluido)"]
    if faltan: partes.append("⚠️ Hay algunos productos que no tenemos completos en este momento: " + "; ".join(faltan) + ".")
    if leido["no_identificadas"]: partes.append("❓ No logré identificar: " + "; ".join(leido["no_identificadas"][:5]) + ". Un asesor se lo confirma.")
    if lineas:
        partes.append(f"Aquí puede ver el documento: {enlace('pedido', eid, p.id)}")
        partes.append(cfg.instrucciones_pago or f"Puede pagar y retirar en nuestra oficina: {e.direccion or e.razon_social}" + (f" · Tel. {e.telefono}" if e.telefono else "") + ".")
        partes.append("Su factura fiscal se emitirá al confirmar el despacho." if rif else "Para emitir su factura fiscal, respóndame con su *RIF* (por ejemplo J-12345678-9).")
    responder("\n\n".join(partes))
    return partes
