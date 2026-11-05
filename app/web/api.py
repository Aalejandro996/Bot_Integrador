"""Entradas sin usuario: avisos firmados de la pasarela de WhatsApp y API firmada de catálogo (conector de Profit Plus u otro
sistema, o la integración propia de la empresa). Las rutas /api/profit/v1/* quedan por compatibilidad con los conectores instalados."""
import json
import time

from flask import abort, current_app, request
from sqlalchemy import select

from .. import notificaciones as N
from .. import openwa as OW
from .. import profit as PF
from .. import servicios as SV
from .. import sistemas as SIS
from ..extensions import csrf, db, limiter
from ..models import Empresa, WhatsappConfig, ahora
from . import bp


@bp.route("/salud")
def salud():
    db.session.execute(db.text("SELECT 1"))
    return {"ok": True}


# ====================================================================== avisos de la pasarela OpenWA
@bp.route("/wa/<clave>", methods=["GET", "POST"])
@csrf.exempt
@limiter.limit("300/minute")
def aviso_whatsapp(clave):
    if len(clave) > 64: abort(404)
    cfg = db.session.execute(select(WhatsappConfig).where(WhatsappConfig.clave_webhook == clave)).scalar_one_or_none()
    if cfg is None: abort(404)
    e = db.session.get(Empresa, cfg.empresa_id)
    if e is None or not e.activa: return {"ok": True, "ignorado": "empresa"}
    if request.method == "GET": return {"ok": True}
    cuerpo = request.get_data(cache=True)
    if not OW.firma_valida(SV.cifrador().descifrar(cfg.secreto_cifrado), cuerpo, request.headers.get("X-OpenWA-Signature", "")): abort(403)
    try:
        aviso = json.loads(cuerpo or b"{}")
    except ValueError:
        return {"ok": True}
    if str(aviso.get("sessionId") or "") != (cfg.sesion_id or ""): return {"ok": True, "ignorado": "sesion"}   # sesión anterior ya desvinculada
    cambio = OW.estado_del_aviso(aviso)
    if cambio:
        SV.guardar_estado(cfg, cambio[0], cambio[1]); return {"ok": True}
    if not cfg.activo: return {"ok": True, "ignorado": "pausado"}
    eid = cfg.empresa_id
    for tel, nombre, texto, wamid, adjunto in OW.mensajes_entrantes(aviso):
        try:
            SV.procesar_mensaje(eid, tel, nombre, texto, wamid, adjunto=adjunto); db.session.commit(); N.despachar()
        except Exception:  # noqa: BLE001 — un mensaje defectuoso no debe frenar a los demás ni provocar reintentos en bucle
            db.session.rollback(); N.descartar(); current_app.logger.exception("Mensaje de WhatsApp no procesado")
    return {"ok": True}


# ====================================================================== conector de Profit Plus
def _conector():
    cid = request.headers.get("X-Surtia-Conector", "")
    if not cid or len(cid) > 64: abort(401)
    from ..models import ProfitConexion
    c = db.session.execute(select(ProfitConexion).where(ProfitConexion.conector_id == cid)).scalar_one_or_none()
    cuerpo = request.get_data(cache=True)
    if c is None or SIS.SISTEMAS.get(c.sistema, {}).get("modo") not in ("agente", "api") or not PF.firma_valida(SV.cifrador().descifrar(c.secreto_cifrado), request.headers.get("X-Surtia-Fecha", ""), cuerpo,
                                         request.headers.get("X-Surtia-Firma", "")):
        abort(401)
    e, plat = db.session.get(Empresa, c.empresa_id), SV.profit_plataforma()
    if e is None or not e.activa: abort(402)
    if plat is None or not plat.activa: abort(503)
    return c.empresa_id, c, plat, cuerpo


def _respuesta(c, plat, **extra):
    return {"ok": True, "config": PF.config_para_conector(c, plat.intervalo_min), **extra}


@bp.route("/api/profit/v1/ping", methods=["POST"])
@bp.route("/api/fuente/v1/ping", methods=["POST"], endpoint="fuente_ping")
@csrf.exempt
@limiter.limit("30/minute")
def profit_ping():
    eid, c, plat, cuerpo = _conector()
    try:
        d = PF.descomprimir(cuerpo, request.headers.get("Content-Encoding", ""))
    except PF.ErrorProfit as e:
        return {"ok": False, "error": str(e)}, 400
    c.ultimo_contacto = ahora()
    c.agente_version = str(d.get("version") or c.agente_version or "")[:20] or None
    c.agente_equipo = str(d.get("equipo") or c.agente_equipo or "")[:60] or None
    if d.get("error"): SV.error_conector(eid, c, f"Conector: {str(d['error'])[:400]}", ip=request.remote_addr)
    else: SV.anotar_sync(eid, "ping", ip=request.remote_addr)
    db.session.commit()
    return _respuesta(c, plat)


@bp.route("/api/profit/v1/sync", methods=["POST"])
@bp.route("/api/fuente/v1/sync", methods=["POST"], endpoint="fuente_sync")
@csrf.exempt
@limiter.limit("12/minute")
def profit_sync():
    t0 = time.monotonic()
    eid, c, plat, cuerpo = _conector()
    if not c.activo:
        c.ultimo_contacto = ahora(); db.session.commit()
        return _respuesta(c, plat, ignorado="pausado")
    try:
        foto = PF.validar_foto(PF.descomprimir(cuerpo, request.headers.get("Content-Encoding", "")), plat.max_articulos, plat.max_clientes)
    except PF.ErrorProfit as e:
        db.session.rollback()
        c = SV.profit_conexion(eid); SV.error_conector(eid, c, str(e), ip=request.remote_addr); db.session.commit()
        return {"ok": False, "error": str(e)}, 400
    SV.aplicar_foto(eid, c, foto, ip=request.remote_addr, duracion_ms=int((time.monotonic() - t0) * 1000))
    db.session.commit()
    return _respuesta(c, plat, articulos=c.n_articulos, clientes=c.n_clientes)
