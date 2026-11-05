"""Surtia™ · Profit Plus — bot de WhatsApp conectado (solo lectura) a Profit Plus. Gigadevs Corp."""
import logging
import secrets

from flask import Flask, g, request


def create_app(config=None):
    from config import Config
    from .cifrado import CifradorCampos
    from .extensions import csrf, db, limiter
    from .seguridad import cargar_usuario

    app = Flask(__name__)
    app.config.from_object(config or Config)
    if not app.config["SECRET_KEY"] or not app.config["FIELD_ENCRYPTION_KEY"] or not app.config["SQLALCHEMY_DATABASE_URI"]:
        raise RuntimeError("Faltan SECRET_KEY, FIELD_ENCRYPTION_KEY o DATABASE_URL")
    logging.basicConfig(level=logging.INFO)
    db.init_app(app); csrf.init_app(app); limiter.init_app(app)
    app.extensions["cifrador"] = CifradorCampos(app.config["FIELD_ENCRYPTION_KEY"])
    from . import models  # noqa: F401  (registra tablas)
    from . import interfaz
    interfaz.registrar(app)
    from .web import auth, bp
    app.register_blueprint(auth.bp); app.register_blueprint(bp)
    if app.config.get("PRODUCCION"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    @app.before_request
    def _usuario():
        g.nonce = secrets.token_urlsafe(12)
        if request.endpoint in ("static", "web.salud"): return
        cargar_usuario()

    @app.after_request
    def _cabeceras(r):
        from . import notificaciones
        if r.status_code < 400: notificaciones.despachar()       # avisos por correo: solo si la operación se guardó
        else: notificaciones.descartar()
        r.headers.setdefault("Content-Security-Policy", "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; "
                                                         "connect-src 'self'; frame-ancestors 'none'; form-action 'self'; base-uri 'none'; object-src 'none'")
        r.headers["X-Content-Type-Options"] = "nosniff"
        r.headers["X-Frame-Options"] = "DENY"
        r.headers["Referrer-Policy"] = "same-origin"
        r.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if app.config.get("PRODUCCION"): r.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.endpoint != "static": r.headers.setdefault("Cache-Control", "no-store")
        return r

    from . import errores
    errores.registrar(app)
    return app
