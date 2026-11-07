"""Documentos legales de Surtia aprobados en la Consola Maestra de Gigadevs (abogada y directores). Al ingresar, el administrador de
cada empresa acepta el contrato, los términos y la privacidad; los operadores, los términos y la privacidad. La aceptación se
registra en la consola de Gigadevs y queda copia local."""
import time

from flask import current_app, flash, g, redirect, render_template, request, session, url_for

from .. import gigadevs as GD
from ..extensions import db
from ..models import AceptacionLegal
from ..seguridad import registrar, requiere
from . import bp

REVISAR_CADA_S = 900
LIBRES = ("web.legal_aceptar", "web.salud", "static")


def _perfil(u):
    return "responsable" if u.rol == "Administrador" else "usuario"


def compuerta():
    u = g.get("usuario")
    if u is None or g.get("empresa") is None or not GD.configurado(): return None
    ep = request.endpoint or ""
    if ep in LIBRES or ep.startswith("auth.") or request.path.startswith(("/api/", "/wa/")): return None
    if session.get("legal_ok_hasta", 0) > time.time() and session.get("legal_ok_usuario") == u.id: return None
    try:
        pend = GD.legal_pendientes(u.username, _perfil(u))
    except GD.ErrorGigadevs as e:                       # la consola no responde: no se corta el trabajo
        current_app.logger.warning("Legal: no se pudo consultar documentos pendientes: %s", e)
        session["legal_ok_hasta"], session["legal_ok_usuario"] = time.time() + 300, u.id
        return None
    if pend: return redirect(url_for("web.legal_aceptar"))
    session["legal_ok_hasta"], session["legal_ok_usuario"] = time.time() + REVISAR_CADA_S, u.id
    return None


@bp.route("/legal/aceptar", methods=["GET", "POST"])
@requiere()
def legal_aceptar():
    u, e = g.usuario, g.get("empresa")
    if e is None: return redirect("/")
    try:
        pend = GD.legal_pendientes(u.username, _perfil(u))
    except GD.ErrorGigadevs as ex:
        flash(f"No pudimos cargar los documentos legales: {ex}"); return redirect("/")
    if not pend:
        session["legal_ok_hasta"], session["legal_ok_usuario"] = time.time() + REVISAR_CADA_S, u.id
        return redirect("/")
    if request.method == "POST":
        if {x for x in request.form.getlist("acepto")} != {str(p["id"]) for p in pend}:
            flash("Marque la casilla de cada documento para aceptarlo.")
        else:
            ip = (request.headers.get("X-Forwarded-For") or request.remote_addr or "").split(",")[0].strip()[:64]
            ua = (request.user_agent.string or "")[:300]
            try:
                GD.legal_aceptar(documentos=[p["id"] for p in pend], usuario=u.username, nombre=u.nombre or u.username, sujeto=e.rif,
                                 organizacion=e.razon_social, rol=u.rol, ip=ip, agente=ua)
            except GD.ErrorGigadevs as ex:
                flash(f"No se pudo registrar la aceptación: {ex}"); return redirect(request.path)
            for p in pend:
                db.session.add(AceptacionLegal(usuario_id=u.id, empresa_id=e.id, documento_id=p["id"], tipo=p["tipo"], version=p["version"],
                                               huella=p["huella"], ip=ip, agente=ua))
            registrar("DOCUMENTOS_LEGALES_ACEPTADOS", ", ".join(f"{p['tipo']} v{p['version']}" for p in pend), empresa_id=e.id)
            db.session.commit()
            session["legal_ok_hasta"], session["legal_ok_usuario"] = time.time() + REVISAR_CADA_S, u.id
            flash("Gracias. Su aceptación quedó registrada.")
            return redirect("/")
    return render_template("legal_aceptar.html", titulo="Documentos legales", empresa=e, pendientes=pend)
