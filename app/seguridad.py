"""Contraseñas (Argon2id), política de claves, enlaces de activación, sesión y permisos por rol."""
import hashlib
import secrets
from datetime import timedelta
from functools import wraps

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from flask import abort, current_app, g, redirect, request, session, url_for

from .extensions import db
from .models import Bitacora, Usuario, ahora

_ph = PasswordHasher()
_FALSO = _ph.hash("clave-que-nadie-usa-#1")        # comparación de tiempo constante con usuarios inexistentes
COMUNES = {"contraseña123", "password1234", "123456789012", "qwertyuiop12", "venezuela2026", "admin1234567"}
MAX_INTENTOS, BLOQUEO_MIN = 5, 15
VIGENCIA_ACTIVACION_H = 72


def hashear(clave: str) -> str:
    return _ph.hash(clave)


def verificar_clave(hash_, clave: str) -> bool:
    try:
        return _ph.verify(hash_ or _FALSO, clave) and bool(hash_)
    except (VerificationError, InvalidHashError):
        return False


def politica_clave(clave: str, usuario: str = "", email: str = "") -> str | None:
    """Devuelve el motivo del rechazo o None si la clave sirve."""
    if len(clave) < 12: return "La contraseña debe tener al menos 12 caracteres."
    if len(clave) > 128: return "La contraseña es demasiado larga."
    clases = sum((any(c.islower() for c in clave), any(c.isupper() for c in clave), any(c.isdigit() for c in clave),
                  any(not c.isalnum() for c in clave)))
    if clases < 3: return "Use al menos 3 de: minúsculas, mayúsculas, números y símbolos."
    baja = clave.lower()
    if baja in COMUNES: return "Esa contraseña es demasiado común."
    for x in (usuario, (email or "").split("@")[0]):
        if x and len(x) >= 3 and x.lower() in baja: return "La contraseña no puede contener su usuario o correo."
    return None


def _h(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def nuevo_enlace_activacion(u: Usuario) -> str:
    """Enlace de un solo uso (72 h) para que el usuario cree su contraseña y su MFA. Solo se guarda su SHA-256."""
    token = secrets.token_urlsafe(32)
    u.token_activacion_hash, u.token_activacion_vence = _h(token), ahora() + timedelta(hours=VIGENCIA_ACTIVACION_H)
    return current_app.config["APP_BASE_URL"] + url_for("auth.activar", token=token)


VIGENCIA_RECUPERACION_MIN = 30
ESPERA_RECUPERACION_S = 120       # entre dos correos de recuperación para la misma cuenta


def nuevo_enlace_recuperacion(u: Usuario) -> str:
    """Enlace de un solo uso (30 min) para elegir una contraseña nueva. Solo se guarda su SHA-256; no toca el MFA."""
    token = secrets.token_urlsafe(32)
    u.token_recuperacion_hash, u.token_recuperacion_vence = _h(token), ahora() + timedelta(minutes=VIGENCIA_RECUPERACION_MIN)
    u.clave_pendiente_hash = None
    return current_app.config["APP_BASE_URL"] + url_for("auth.recuperar_clave", token=token)


def usuario_por_recuperacion(token: str) -> Usuario | None:
    if not token or len(token) > 100: return None
    u = db.session.query(Usuario).filter_by(token_recuperacion_hash=_h(token)).one_or_none()
    if u is None or not u.activo or not u.mfa_activo or u.token_recuperacion_vence is None or u.token_recuperacion_vence < ahora(): return None
    return u


def anular_recuperacion(u: Usuario):
    u.token_recuperacion_hash = u.token_recuperacion_vence = u.clave_pendiente_hash = None


def usuario_por_token(token: str) -> Usuario | None:
    if not token or len(token) > 100: return None
    u = db.session.query(Usuario).filter_by(token_activacion_hash=_h(token)).one_or_none()
    if u is None or not u.activo or u.token_activacion_vence is None or u.token_activacion_vence < ahora(): return None
    return u


def registrar(accion: str, detalle: str = "", empresa_id=None, usuario=None):
    u = getattr(g, "usuario", None)
    db.session.add(Bitacora(empresa_id=empresa_id if empresa_id is not None else (u.empresa_id if u else None),
                            usuario=usuario or (u.username if u else "sistema"), accion=accion, detalle=(detalle or "")[:1000],
                            ip=(request.remote_addr if request else None)))


# ====================================================================== sesión
def iniciar_sesion(u: Usuario):
    session.clear()
    session.permanent = True
    session.update(uid=u.id, ver=u.sesion_version, t=ahora().timestamp(), inicio=ahora().timestamp())
    u.ultimo_acceso, u.intentos_fallidos, u.bloqueado_hasta = ahora(), 0, None


def cargar_usuario():
    """before_request: valida la sesión (versión, inactividad, duración máxima) y deja g.usuario y g.empresa."""
    g.usuario = g.empresa = None
    uid = session.get("uid")
    if not uid: return
    t = ahora().timestamp()
    u = db.session.get(Usuario, uid)
    if (u is None or not u.activo or session.get("ver") != u.sesion_version or t - session.get("t", 0) > current_app.config["INACTIVIDAD_S"]
            or t - session.get("inicio", 0) > current_app.config["PERMANENT_SESSION_LIFETIME"]):
        session.clear(); return
    if u.empresa_id is not None:
        from .models import Empresa
        e = db.session.get(Empresa, u.empresa_id)
        if e is None or not e.activa:
            session.clear(); return
        g.empresa = e
    session["t"] = t
    g.usuario = u


def requiere(*roles):
    """Ruta autenticada; con roles, solo para esos roles (403 a los demás)."""
    def deco(f):
        @wraps(f)
        def interior(*a, **kw):
            if g.usuario is None: return redirect(url_for("auth.login", siguiente=request.path))
            if roles and g.usuario.rol not in roles: abort(403)
            return f(*a, **kw)
        return interior
    return deco


def eid_actual() -> int:
    """Empresa del usuario cliente. Las rutas de cliente no aceptan otra empresa: lo ajeno no existe para ellas."""
    if g.empresa is None: abort(403)
    return g.empresa.id
