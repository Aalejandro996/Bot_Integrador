"""Inicio de sesión con contraseña + verificación en dos pasos (TOTP) obligatoria, activación por enlace de un solo uso y
recuperación de contraseña por correo (la clave nueva solo se aplica tras confirmar el código MFA)."""
from datetime import timedelta
from urllib.parse import unquote, urlsplit

import segno
from flask import Blueprint, abort, current_app, flash, g, redirect, render_template, request, session, url_for

from .. import notificaciones as N, rls, totp
from ..extensions import db, limiter
from ..models import Usuario, ahora
from ..seguridad import (BLOQUEO_MIN, ESPERA_RECUPERACION_S, MAX_INTENTOS, VIGENCIA_RECUPERACION_MIN, anular_recuperacion, hashear,
                         iniciar_sesion, nuevo_enlace_recuperacion, politica_clave, registrar, requiere, usuario_por_recuperacion,
                         usuario_por_token, verificar_clave)

bp = Blueprint("auth", __name__)


def _vista(vista, **kw):
    return render_template("auth.html", vista=vista, **kw)


def _destino(u):
    if u.rol == "Maestro": return url_for("web.maestro")
    return url_for("web.inicio") if u.guia_vista else url_for("web.guia")      # la primera vez: la guía paso a paso


@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10/minute", methods=["POST"])
def login():
    if g.usuario is not None: return redirect(_destino(g.usuario))
    if request.method == "POST":
        nombre = (request.form.get("usuario") or "").strip().lower()[:254]
        rls.ver_usuario(rls.usuario_por_nombre(nombre))      # RLS: solo la cuenta que intenta entrar queda visible
        u = db.session.query(Usuario).filter((db.func.lower(Usuario.username) == nombre) | (db.func.lower(Usuario.email) == nombre)).first()
        ok = verificar_clave(u.password_hash if u else None, request.form.get("clave") or "")
        if u is not None and u.bloqueado_hasta and u.bloqueado_hasta > ahora():
            flash(f"Cuenta bloqueada temporalmente por intentos fallidos. Intente en {BLOQUEO_MIN} minutos."); return _vista("login")
        if not (u and ok and u.activo):
            if u is not None:
                registrar("LOGIN_FALLIDO", "", empresa_id=u.empresa_id, usuario=u.username)
                u.intentos_fallidos += 1
                if u.intentos_fallidos >= MAX_INTENTOS:
                    u.bloqueado_hasta, u.intentos_fallidos = ahora() + timedelta(minutes=BLOQUEO_MIN), 0
                    registrar("BLOQUEO_LOGIN", "", empresa_id=u.empresa_id, usuario=u.username); N.seguridad(u, "bloqueo")
                db.session.commit()
            flash("Usuario o contraseña incorrectos."); return _vista("login")
        session.clear(); session.update(pre=u.id, pre_t=ahora().timestamp())
        return redirect(url_for("auth.mfa" if u.mfa_activo else "auth.mfa_configurar"))
    return _vista("login")


def _pre() -> Usuario:
    if ahora().timestamp() - session.get("pre_t", 0) > 300:
        session.clear(); abort(401)
    rls.ver_usuario(session.get("pre"))
    u = db.session.get(Usuario, session.get("pre") or 0)
    if u is None or not u.activo: abort(401)
    # El bloqueo de cuenta debe cubrir también el segundo factor, no solo la contraseña.
    if u.bloqueado_hasta and u.bloqueado_hasta > ahora():
        session.clear()
        abort(429)
    return u


def _destino_seguro(valor: str, u: Usuario) -> str:
    """Acepta solo rutas locales limpias para evitar redirecciones fuera del sitio."""
    destino = unquote(valor or "")
    partes = urlsplit(destino)
    if (not destino.startswith("/") or destino.startswith("//") or "\\" in destino
            or partes.scheme or partes.netloc or any(ord(c) < 32 for c in destino)
            or len(destino) >= 200):
        return _destino(u)
    return destino


@bp.route("/mfa", methods=["GET", "POST"])
@limiter.limit("10/minute", methods=["POST"])
def mfa():
    u = _pre()
    if not u.mfa_activo: return redirect(url_for("auth.mfa_configurar"))
    if request.method == "POST":
        sec = current_app.extensions["cifrador"].descifrar(u.mfa_secreto_cifrado)
        c = totp.verificar(sec, (request.form.get("codigo") or "").strip(), u.mfa_ultimo_contador)
        if c is None:
            u.intentos_fallidos += 1
            if u.intentos_fallidos >= MAX_INTENTOS:
                u.bloqueado_hasta, u.intentos_fallidos = ahora() + timedelta(minutes=BLOQUEO_MIN), 0; N.seguridad(u, "bloqueo")
            db.session.commit(); flash("Código incorrecto o ya usado."); return _vista("mfa")
        u.mfa_ultimo_contador = c
        iniciar_sesion(u); g.usuario = u
        registrar("LOGIN", "", empresa_id=u.empresa_id); db.session.commit(); N.seguridad(u, "login")
        return redirect(_destino_seguro(request.args.get("siguiente", ""), u))
    return _vista("mfa")


@bp.route("/mfa/configurar", methods=["GET", "POST"])
def mfa_configurar():
    u = _pre()
    if u.mfa_activo: return redirect(url_for("auth.mfa"))
    cif = current_app.extensions["cifrador"]
    if not session.get("mfa_nuevo"):
        session["mfa_nuevo"] = cif.cifrar(totp.nuevo_secreto())
    sec = cif.descifrar(session["mfa_nuevo"])
    if request.method == "POST":
        c = totp.verificar(sec, (request.form.get("codigo") or "").strip())
        if c is None:
            flash("Código incorrecto. Revise la hora del teléfono e intente de nuevo.")
        else:
            u.mfa_secreto_cifrado, u.mfa_activo, u.mfa_ultimo_contador = cif.cifrar(sec), True, c
            session.pop("mfa_nuevo", None)
            iniciar_sesion(u); g.usuario = u
            registrar("MFA_ACTIVADO", "", empresa_id=u.empresa_id); db.session.commit(); N.seguridad(u, "mfa")
            if not u.guia_vista and u.rol != "Maestro": N.bienvenida(u)
            return redirect(_destino(u))
    qr = segno.make(totp.uri_otpauth(sec, u.username), error="m").svg_data_uri(scale=5, border=2)
    return _vista("mfa_configurar", qr=qr, secreto=sec)


@bp.route("/activar/<token>", methods=["GET", "POST"])
@limiter.limit("10/minute", methods=["POST"])
def activar(token):
    u = usuario_por_token(token)
    if u is None: return _vista("enlace_invalido")
    if request.method == "POST":
        nueva = request.form.get("nueva") or ""
        motivo = politica_clave(nueva, u.username, u.email) or (None if nueva == request.form.get("repetir") else "Las contraseñas no coinciden.")
        if motivo:
            flash(motivo); return _vista("activar", usuario=u.username)
        u.password_hash, u.token_activacion_hash, u.token_activacion_vence = hashear(nueva), None, None
        u.mfa_activo, u.mfa_secreto_cifrado, u.sesion_version = False, None, u.sesion_version + 1
        anular_recuperacion(u)
        registrar("CUENTA_ACTIVADA", "", empresa_id=u.empresa_id, usuario=u.username); db.session.commit()
        session.clear(); session.update(pre=u.id, pre_t=ahora().timestamp())
        return redirect(url_for("auth.mfa_configurar"))
    return _vista("activar", usuario=u.username)


@bp.route("/salir", methods=["POST"])
def salir():
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/cuenta/clave", methods=["GET", "POST"])
@requiere()
def cambiar_clave():
    u = g.usuario
    if request.method == "POST":
        nueva = request.form.get("nueva") or ""
        if not verificar_clave(u.password_hash, request.form.get("actual") or ""): flash("La contraseña actual no es correcta.")
        elif (m := politica_clave(nueva, u.username, u.email)): flash(m)
        else:
            u.password_hash, u.sesion_version = hashear(nueva), u.sesion_version + 1
            anular_recuperacion(u)
            registrar("CLAVE_CAMBIADA", ""); db.session.commit()
            iniciar_sesion(u); db.session.commit(); N.seguridad(u, "clave")
            flash("Contraseña actualizada. Se cerraron sus otras sesiones."); return redirect(_destino(u))
    return _vista("cambiar_clave")


# ====================================================================== olvidé mi contraseña
# 1) /recuperar: usuario o correo → enlace de un solo uso (30 min) al correo de la cuenta. La respuesta es SIEMPRE la misma,
#    exista o no la cuenta (no revela quién está registrado).
# 2) /recuperar/<token>: la persona escribe la contraseña nueva; queda PENDIENTE (hash en la base), no se aplica todavía.
# 3) /recuperar/confirmar: código MFA de la cuenta. Solo entonces se aplica la clave, se anula el enlace y se cierran las
#    demás sesiones. Errores de código cuentan para el bloqueo de la cuenta. Sin MFA activo no hay recuperación por correo.
RESPUESTA_RECUPERAR = ("Si la cuenta existe y tiene un correo registrado, le enviamos un enlace para elegir una contraseña nueva "
                       f"(vence en {VIGENCIA_RECUPERACION_MIN} minutos). Revise también la carpeta de correo no deseado.")


@bp.route("/recuperar", methods=["GET", "POST"])
@limiter.limit("5/minute;20/hour", methods=["POST"])
def recuperar():
    if g.usuario is not None: return redirect(_destino(g.usuario))
    if request.method == "POST":
        nombre = (request.form.get("usuario") or "").strip().lower()[:254]
        rls.ver_usuario(rls.usuario_por_nombre(nombre))      # RLS: solo la cuenta que intenta entrar queda visible
        u = db.session.query(Usuario).filter((db.func.lower(Usuario.username) == nombre) | (db.func.lower(Usuario.email) == nombre)).first() if nombre else None
        reciente = (u is not None and u.token_recuperacion_vence is not None
                    and u.token_recuperacion_vence - timedelta(minutes=VIGENCIA_RECUPERACION_MIN) > ahora() - timedelta(seconds=ESPERA_RECUPERACION_S))
        if u is not None and u.activo and u.email and u.password_hash and u.mfa_activo and not reciente:
            enlace = nuevo_enlace_recuperacion(u)
            registrar("RECUPERACION_SOLICITADA", "", empresa_id=u.empresa_id, usuario=u.username); db.session.commit()
            N.recuperacion(u, enlace)
        elif u is not None:
            registrar("RECUPERACION_IGNORADA", "reciente" if reciente else "sin correo o sin MFA", empresa_id=u.empresa_id, usuario=u.username)
            db.session.commit()
        return _vista("recuperar_enviado", mensaje=RESPUESTA_RECUPERAR)
    return _vista("recuperar")


@bp.route("/recuperar/<token>", methods=["GET", "POST"])
@limiter.limit("10/minute", methods=["POST"])
def recuperar_clave(token):
    u = usuario_por_recuperacion(token)
    if u is None: return _vista("enlace_invalido", recuperacion=True)
    if request.method == "POST":
        nueva = request.form.get("nueva") or ""
        motivo = politica_clave(nueva, u.username, u.email) or (None if nueva == request.form.get("repetir") else "Las contraseñas no coinciden.")
        if not motivo and verificar_clave(u.password_hash, nueva): motivo = "Elija una contraseña distinta de la anterior."
        if motivo:
            flash(motivo); return _vista("recuperar_clave", usuario=u.username)
        u.clave_pendiente_hash = hashear(nueva)
        db.session.commit()
        session.clear(); session.update(rec=u.id, rec_t=ahora().timestamp(), rec_tok=u.token_recuperacion_hash)
        return redirect(url_for("auth.recuperar_confirmar"))
    return _vista("recuperar_clave", usuario=u.username)


@bp.route("/recuperar/confirmar", methods=["GET", "POST"])
@limiter.limit("10/minute", methods=["POST"])
def recuperar_confirmar():
    rls.ver_usuario(session.get("rec"))
    u = db.session.get(Usuario, session.get("rec") or 0)
    vigente = (u is not None and u.activo and u.mfa_activo and u.clave_pendiente_hash and session.get("rec_tok") == u.token_recuperacion_hash
               and u.token_recuperacion_vence is not None and u.token_recuperacion_vence > ahora() and ahora().timestamp() - session.get("rec_t", 0) < 600)
    if not vigente:
        session.clear(); return _vista("enlace_invalido", recuperacion=True)
    if u.bloqueado_hasta and u.bloqueado_hasta > ahora():
        session.clear(); abort(429)
    if request.method == "POST":
        sec = current_app.extensions["cifrador"].descifrar(u.mfa_secreto_cifrado)
        c = totp.verificar(sec, (request.form.get("codigo") or "").strip(), u.mfa_ultimo_contador)
        if c is None:
            u.intentos_fallidos += 1
            registrar("RECUPERACION_MFA_FALLIDO", "", empresa_id=u.empresa_id, usuario=u.username)
            if u.intentos_fallidos >= MAX_INTENTOS:
                u.bloqueado_hasta, u.intentos_fallidos = ahora() + timedelta(minutes=BLOQUEO_MIN), 0
                anular_recuperacion(u); N.seguridad(u, "bloqueo"); db.session.commit(); session.clear(); abort(429)
            db.session.commit(); flash("Código incorrecto o ya usado. La contraseña aún no cambió."); return _vista("recuperar_confirmar")
        u.password_hash, u.mfa_ultimo_contador, u.sesion_version = u.clave_pendiente_hash, c, u.sesion_version + 1
        anular_recuperacion(u)
        iniciar_sesion(u); g.usuario = u                          # cierra las demás sesiones y entra con esta
        registrar("CLAVE_RECUPERADA", "", empresa_id=u.empresa_id); db.session.commit(); N.seguridad(u, "clave")
        flash("Listo: su contraseña cambió y se cerraron las demás sesiones abiertas.")
        return redirect(_destino(u))
    return _vista("recuperar_confirmar")
