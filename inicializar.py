"""Crea o actualiza las tablas y, la primera vez, el Maestro de Gigadevs (variables MAESTRO_USUARIO y MAESTRO_EMAIL).
Mientras el Maestro no active su cuenta, cada despliegue escribe en el log un enlace nuevo de activación (72 h, un uso).
Uso: python inicializar.py   (Render lo ejecuta antes de arrancar)"""
import sys

sys.path.insert(0, ".")


def main():
    from app import create_app
    from app.extensions import db
    from app.models import Parametros, ProfitPlataforma, Usuario
    from app.seguridad import nuevo_enlace_activacion
    app = create_app()
    with app.app_context(), app.test_request_context(base_url=app.config["APP_BASE_URL"]):
        db.create_all()
        if db.session.get(Parametros, 1) is None: db.session.add(Parametros(id=1, iva_general=16, iva_reducida=8, iva_adicional=15))
        if db.session.get(ProfitPlataforma, 1) is None: db.session.add(ProfitPlataforma(id=1, activa=True, intervalo_min=15))
        nombre = (app.config.get("MAESTRO_USUARIO") or "").strip().lower()
        if nombre:
            u = db.session.query(Usuario).filter_by(username=nombre).one_or_none()
            if u is None:
                u = Usuario(username=nombre, email=app.config.get("MAESTRO_EMAIL") or None, rol="Maestro"); db.session.add(u); db.session.flush()
            if not u.password_hash:
                print(f"[inicializar] Active el Maestro «{u.username}» con este enlace (72 h, un uso): {nuevo_enlace_activacion(u)}", flush=True)
        db.session.commit()
        print("[inicializar] Base de datos lista.", flush=True)


if __name__ == "__main__":
    main()
