"""Aislamiento entre empresas en PostgreSQL (Row-Level Security), igual que Contax.

Cada transacción fija dos valores (set_config local): `surtia.empresa_id` (la empresa del usuario, del webhook de WhatsApp o del
conector que llama) y `surtia.maestro` ('1' solo para la consola Maestro y las tareas internas). Las políticas RLS solo dejan
leer y escribir filas de esa empresa: aunque una consulta olvidara su filtro, la base de datos no entrega datos de otra empresa.
Sin contexto no se ve nada. RLS no aplica al dueño de las tablas: la app hace SET ROLE a un rol limitado (DB_ROL_APLICACION)."""
import re
from contextlib import contextmanager

from flask import g, has_app_context
from sqlalchemy import event, text
from sqlalchemy.orm import Session

_SQL = text("SELECT set_config('surtia.empresa_id', :e, true), set_config('surtia.maestro', :m, true), set_config('surtia.usuario_id', :u, true)")


def _valores() -> dict:
    v = getattr(g, "_rls", None) if has_app_context() else None
    e, m, u = v or (None, False, None)
    return {"e": str(int(e)) if e else "", "m": "1" if m else "", "u": str(int(u)) if u else ""}


def instalar():
    if event.contains(Session, "after_begin", _fijar): return
    event.listen(Session, "after_begin", _fijar)


def _fijar(session, transaction, connection):  # noqa: ARG001
    if connection.dialect.name == "postgresql":
        connection.execute(_SQL, _valores())


def fijar(empresa_id=None, maestro=False, usuario_id=None):
    """Contexto de esta petición o tarea; se aplica a la transacción en curso y a las siguientes."""
    from .extensions import db
    g._rls = (empresa_id, bool(maestro), usuario_id)
    if db.engine.dialect.name == "postgresql" and db.session().in_transaction():
        db.session.execute(_SQL, _valores())


def de_usuario(u):
    fijar(empresa_id=u.empresa_id, maestro=(u.rol == "Maestro"), usuario_id=u.id)


def ver_usuario(uid):
    """La persona que intenta entrar (login, MFA, enlaces): solo su propia fila de usuario queda visible."""
    if uid: fijar(usuario_id=int(uid))


@contextmanager
def como(empresa_id=None, maestro=False):
    anterior = getattr(g, "_rls", None)
    fijar(empresa_id, maestro)
    try: yield
    finally:
        e, m, u = anterior or (None, False, None); fijar(e, m, u)


def empresa_por(funcion: str, valor: str):
    """Empresa dueña de un secreto (clave del webhook, id del conector) sin abrir la tabla: lo resuelve una función
    SECURITY DEFINER que solo devuelve el número de empresa."""
    from .extensions import db
    if db.engine.dialect.name != "postgresql": return None
    if funcion not in ("surtia_empresa_por_webhook", "surtia_empresa_por_conector"): raise ValueError(funcion)
    return db.session.execute(text(f"SELECT {funcion}(:v)"), {"v": valor}).scalar()


def _buscar(sql, **p):
    from .extensions import db
    if db.engine.dialect.name != "postgresql": return None
    return db.session.execute(text(sql), p).scalar()


def usuario_por_nombre(valor): return _buscar("SELECT surtia_usuario_por_nombre(:v)", v=valor)
def usuario_por_enlace(tipo, hash_): return _buscar("SELECT surtia_usuario_por_enlace(:t, :h)", t=tipo, h=hash_)
def existe_usuario(valor) -> bool: return bool(_buscar("SELECT surtia_existe_usuario(:v)", v=valor))


def rol_limitado(engine, rol):
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", rol): raise RuntimeError("DB_ROL_APLICACION inválido")

    @event.listens_for(engine, "connect")
    def _set_role(dbapi_con, _registro):
        cur = dbapi_con.cursor(); cur.execute(f"SET ROLE {rol}"); cur.close(); dbapi_con.commit()
