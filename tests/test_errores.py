"""Páginas de error y su comportamiento (sin PostgreSQL: las páginas de error no consultan la base)."""
import pytest
from cryptography.fernet import Fernet


@pytest.fixture()
def cliente():
    from config import Config

    class Cfg(Config):
        SECRET_KEY = "x" * 48; FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode(); SQLALCHEMY_DATABASE_URI = "sqlite://"
        APP_BASE_URL = "https://bot.test"; RATELIMIT_ENABLED = False; PRODUCCION = False; SESSION_COOKIE_SECURE = False; SESSION_COOKIE_NAME = "s"
        WTF_CSRF_ENABLED = True; PROPAGATE_EXCEPTIONS = False; TESTING = False
    from app import create_app
    app = create_app(Cfg)

    @app.route("/explota")
    def explota():
        raise RuntimeError("detalle interno secreto")
    return app.test_client()


def test_404_con_salidas(cliente):
    r = cliente.get("/no-existe")
    t = r.get_data(as_text=True)
    assert r.status_code == 404 and "No encontramos esta página" in t and "Volver atrás" in t and "Ir al inicio" in t
    assert 'data-volver' in t and "app.js" in t


def test_500_sin_trazas_y_con_referencia(cliente, caplog):
    r = cliente.get("/explota")
    t = r.get_data(as_text=True)
    assert r.status_code == 500 and "Algo salió mal" in t and "secreto" not in t and "Traceback" not in t
    ref = t.split("<code>")[1].split("</code>")[0]
    assert len(ref) == 6 and any(ref in m for m in caplog.messages)                    # la referencia queda en el log


def test_formulario_vencido_csrf(cliente):
    r = cliente.post("/login", data={"usuario": "x", "clave": "y"})
    assert r.status_code == 400 and "La página venció" in r.get_data(as_text=True)


def test_api_responde_json(cliente):
    r = cliente.get("/api/no-existe")
    assert r.status_code == 404 and r.get_json() == {"ok": False, "error": "No encontramos esta página"}
