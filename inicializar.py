"""Crea o actualiza las tablas y, la primera vez, el Maestro de Gigadevs (variables MAESTRO_USUARIO y MAESTRO_EMAIL).
Mientras el Maestro no active su cuenta, cada despliegue genera un enlace nuevo de activación (72 h, un uso): se envía a
MAESTRO_EMAIL si el correo está configurado (Brevo o SMTP) y, si no, se escribe en el log.
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
        # columnas nuevas en tablas que ya existían (create_all no altera tablas)
        from app.models import ESTADOS_PEDIDO
        for sql in ("ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS guia_vista BOOLEAN NOT NULL DEFAULT false",
                    "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS token_recuperacion_hash TEXT UNIQUE",
                    "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS token_recuperacion_vence TIMESTAMPTZ",
                    "ALTER TABLE usuarios ADD COLUMN IF NOT EXISTS clave_pendiente_hash TEXT",
                    "ALTER TABLE profit_conexion ADD COLUMN IF NOT EXISTS sistema TEXT NOT NULL DEFAULT 'profit'",
                    "ALTER TABLE profit_conexion ADD COLUMN IF NOT EXISTS opciones JSON NOT NULL DEFAULT '{}'",
                    "ALTER TABLE profit_conexion ADD COLUMN IF NOT EXISTS credenciales_cifradas TEXT",
                    "ALTER TABLE profit_conexion ADD COLUMN IF NOT EXISTS ultimo_intento TIMESTAMPTZ",
                    # «Facturado en Profit» → «Facturado» (Surtia ya no es solo para Profit Plus)
                    "ALTER TABLE pedidos DROP CONSTRAINT IF EXISTS ck_pedido_estado",
                    "UPDATE pedidos SET estado = 'Facturado' WHERE estado = 'Facturado en Profit'",
                    f"ALTER TABLE pedidos ADD CONSTRAINT ck_pedido_estado CHECK (estado IN {ESTADOS_PEDIDO})"):
            db.session.execute(db.text(sql))
        if db.session.get(Parametros, 1) is None: db.session.add(Parametros(id=1, iva_general=16, iva_reducida=8, iva_adicional=15))
        if db.session.get(ProfitPlataforma, 1) is None: db.session.add(ProfitPlataforma(id=1, activa=True, intervalo_min=15))
        nombre = (app.config.get("MAESTRO_USUARIO") or "").strip().lower()
        if nombre:
            u = db.session.query(Usuario).filter_by(username=nombre).one_or_none()
            if u is None:
                u = Usuario(username=nombre, email=app.config.get("MAESTRO_EMAIL") or None, rol="Maestro"); db.session.add(u); db.session.flush()
            if not u.email and app.config.get("MAESTRO_EMAIL"): u.email = app.config["MAESTRO_EMAIL"].strip()[:254]
            if not u.password_hash:
                enlace = nuevo_enlace_activacion(u); db.session.commit()
                from app import notificaciones as N
                enviado, texto = N.enlace_activacion(u, enlace)
                print(f"[inicializar] Maestro «{u.username}»: " + (texto if enviado and texto.startswith("Enviamos") else
                      f"active la cuenta con este enlace (72 h, un uso): {enlace}"), flush=True)
        db.session.commit()
        print("[inicializar] Base de datos lista.", flush=True)


if __name__ == "__main__":
    main()
