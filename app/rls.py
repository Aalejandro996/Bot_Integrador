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

_SQL = text("SELECT set_config('surtia.empresa_id', :e, true), set_config('surtia.maestro', :m, true)")


def _valores() -> dict:
    v = getattr(g, "_rls", None) if has_app_context() else None
    e, m = v or (None, False)
    return {"e": str(int(e)) if e else "", "m": "1" if m else ""}


def instalar():
    if event.contains(Session, "after_begin", _fijar): return
    event.listen(Session, "after_begin", _fijar)


def _fijar(session, transaction, connection):  # noqa: ARG001
    if connection.dialect.name == "postgresql":
        connection.execute(_SQL, _valores())


def fijar(empresa_id=None, maestro=False):
    """Contexto de esta petición o tarea; se aplica a la transacción en curso y a las siguientes."""
    from .extensions import db
    g._rls = (empresa_id, bool(maestro))
    if db.engine.dialect.name == "postgresql" and db.session().in_transaction():
        db.session.execute(_SQL, _valores())


def de_usuario(u):
    fijar(empresa_id=u.empresa_id, maestro=(u.rol == "Maestro"))


@contextmanager
def como(empresa_id=None, maestro=False):
    anterior = getattr(g, "_rls", None)
    fijar(empresa_id, maestro)
    try: yield
    finally:
        e, m = anterior or (None, False); fijar(e, m)


def empresa_por(funcion: str, valor: str):
    """Empresa dueña de un secreto (clave del webhook, id del conector) sin abrir la tabla: lo resuelve una función
    SECURITY DEFINER que solo devuelve el número de empresa."""
    from .extensions import db
    if db.engine.dialect.name != "postgresql": return None
    if funcion not in ("surtia_empresa_por_webhook", "surtia_empresa_por_conector"): raise ValueError(funcion)
    return db.session.execute(text(f"SELECT {funcion}(:v)"), {"v": valor}).scalar()


def rol_limitado(engine, rol):
    if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", rol): raise RuntimeError("DB_ROL_APLICACION inválido")

    @event.listens_for(engine, "connect")
    def _set_role(dbapi_con, _registro):
        cur = dbapi_con.cursor(); cur.execute(f"SET ROLE {rol}"); cur.close(); dbapi_con.commit()
