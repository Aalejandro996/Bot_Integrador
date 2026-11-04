"""Configuración por variables de entorno (Render las fija; en local, un archivo .env)."""
import os

from dotenv import load_dotenv

load_dotenv()


def _db_url():
    u = os.environ.get("DATABASE_URL", "")
    if u.startswith("postgres://"): u = "postgresql://" + u[len("postgres://"):]
    if u.startswith("postgresql://"): u = "postgresql+psycopg://" + u[len("postgresql://"):]
    return u


class Config:
    SECRET_KEY = os.environ.get("SECRET_KEY", "")
    SQLALCHEMY_DATABASE_URI = _db_url()
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True, "pool_recycle": 280}
    FIELD_ENCRYPTION_KEY = os.environ.get("FIELD_ENCRYPTION_KEY", "")
    APP_BASE_URL = (os.environ.get("APP_BASE_URL") or os.environ.get("RENDER_EXTERNAL_URL") or "http://127.0.0.1:5000").rstrip("/")
    APP_NOMBRE = os.environ.get("APP_NOMBRE", "Contax Bot")
    APP_LEMA = "Bot de WhatsApp conectado a Profit Plus"
    # Pasarela OpenWA propia de este bot: si el Maestro no la configuró en la consola, se usa la de estas variables
    OPENWA_URL = os.environ.get("OPENWA_URL", "")
    if OPENWA_URL and "://" not in OPENWA_URL: OPENWA_URL = "http://" + OPENWA_URL      # Render da «host:puerto» de la red privada
    OPENWA_API_KEY = os.environ.get("OPENWA_API_KEY", "")
    MAESTRO_USUARIO = os.environ.get("MAESTRO_USUARIO", "")
    MAESTRO_EMAIL = os.environ.get("MAESTRO_EMAIL", "")
    TASAS_AUTOMATICAS = os.environ.get("TASAS_AUTOMATICAS", "1") == "1"
    PRODUCCION = os.environ.get("FLASK_ENV", "production") == "production"
    SESSION_COOKIE_NAME = "__Host-bot" if PRODUCCION else "bot"
    SESSION_COOKIE_SECURE = PRODUCCION
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Strict"
    PERMANENT_SESSION_LIFETIME = 8 * 3600
    INACTIVIDAD_S = 30 * 60
    MAX_CONTENT_LENGTH = 10 * 1024 * 1024
    WTF_CSRF_TIME_LIMIT = None
    RATELIMIT_ENABLED = True
