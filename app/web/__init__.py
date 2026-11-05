"""Pantallas: auth (login, MFA, activación), cliente (WhatsApp, Profit, pedidos, conversaciones, presupuestos, usuarios),
maestro (empresas, pasarela, parámetros, API Profit y monitoreo) y api (avisos de la pasarela y conector de Profit)."""
from flask import Blueprint, current_app, flash, g, render_template, request
from sqlalchemy.exc import SQLAlchemyError
from werkzeug.local import LocalProxy

from ..extensions import db
from ..telefono import ErrorWhatsapp

bp = Blueprint("web", __name__)
F = LocalProxy(lambda: request.form)


def campo(n, l, t="text", req=True, **kw):
    return dict(n=n, l=l, t=t, req=req, **kw)


def oculto(v, n="accion"):
    return campo(n, "", "hidden", valor=v)


def pagina(titulo, secciones, acciones=None, pestanas=None, subtitulo=None):
    return render_template("pagina.html", titulo=titulo, empresa=g.get("empresa"), secciones=secciones, acciones=acciones or [], pestanas=pestanas or [],
                           subtitulo=subtitulo)


def intentar(fn, ok):
    """Ejecuta una operación; los errores de negocio se muestran, los de BD nunca filtran detalles técnicos."""
    try:
        res = fn(); flash(ok(res) if callable(ok) else ok); return True
    except (ValueError, ErrorWhatsapp) as e:
        db.session.rollback(); flash(str(e))
    except SQLAlchemyError as e:
        db.session.rollback(); current_app.logger.warning("Operación rechazada por la BD: %s", str(getattr(e, "orig", e)).splitlines()[0][:300])
        flash("La operación fue rechazada por las reglas de integridad de la base de datos.")
    return False


def hora(ts):
    from ..tiempo import a_hora_ve
    return a_hora_ve(ts).strftime("%Y-%m-%d %H:%M") if ts else "—"


from . import api, auth, cliente, guia, maestro  # noqa: E402,F401  (registran sus rutas)
