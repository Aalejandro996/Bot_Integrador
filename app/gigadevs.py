"""Conexión con la Consola Maestra de Gigadevs (servidor de licencias de Contax).

- Cada empresa que se crea, suspende o reactiva aquí se envía allá como cliente de Surtia, para que Gigadevs lleve la parte
  administrativa: planes, órdenes y pagos, suscripción, presupuestos, contratos y facturas.
- La licencia firmada de cada empresa se trae de allá (cuando su suscripción está vigente).

Cada petición va firmada con HMAC-SHA256 y un secreto propio de Surtia (GIGADEVS_SYNC_SECRET, el mismo que SYNC_SECRET_SURTIA en el
servidor de licencias), con marca de tiempo y un nonce de un solo uso. Sin secreto configurado, no se envía nada."""
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
import urllib.error
import urllib.request

log = logging.getLogger("bot.gigadevs")
PRODUCTO = "surtia"


class ErrorGigadevs(Exception):
    pass


def configurado() -> bool:
    return len(os.getenv("GIGADEVS_SYNC_SECRET", "")) >= 32


def _url() -> str:
    url = os.getenv("GIGADEVS_SYNC_URL", "https://erp-licencias.onrender.com").strip().rstrip("/")
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1"): raise ErrorGigadevs("GIGADEVS_SYNC_URL debe usar https")
    return url


def firma(secreto: str, metodo: str, ruta: str, cuerpo: bytes, t=None) -> str:
    t, n = str(int(t if t is not None else time.time())), secrets.token_hex(16)
    base = "\n".join([t, n, metodo.upper(), ruta, hashlib.sha256(cuerpo).hexdigest()])
    return f"t={t},n={n},f={hmac.new(secreto.encode(), base.encode(), hashlib.sha256).hexdigest()}"


def _peticion(metodo, ruta, datos=None, transporte=None):
    if not configurado(): raise ErrorGigadevs("La conexión con la Consola Maestra de Gigadevs no está configurada")
    cuerpo = json.dumps(datos).encode() if datos is not None else b""
    cab = {"X-Gigadevs-Firma": firma(os.environ["GIGADEVS_SYNC_SECRET"], metodo, ruta, cuerpo), "Accept": "application/json"}
    if datos is not None: cab["Content-Type"] = "application/json"
    if transporte: estado, resp = transporte(metodo, ruta, cuerpo, cab)
    else:
        req = urllib.request.Request(_url() + ruta, cuerpo or None, cab, method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=15) as r: estado, resp = r.status, r.read()   # noqa: S310 (https fijo)
        except urllib.error.HTTPError as e: estado, resp = e.code, e.read()
        except (urllib.error.URLError, OSError) as e: raise ErrorGigadevs(f"No se pudo contactar la consola de Gigadevs: {e}") from e
    try: d = json.loads(resp or b"{}")
    except ValueError: d = {}
    return estado, d


def enviar_empresa(e, transporte=None) -> dict:
    """Crea o actualiza la empresa como cliente de Surtia en la consola de Gigadevs."""
    estado, d = _peticion("POST", f"/sync/v1/{PRODUCTO}/clientes", {
        "referencia": e.rif, "nombre": e.razon_social, "correo": e.correo, "telefono": e.telefono, "direccion": e.direccion,
        "externo_id": str(e.id), "estado": "activo" if e.activa else "suspendido"}, transporte)
    if estado not in (200, 201): raise ErrorGigadevs(d.get("message") or f"La consola de Gigadevs respondió {estado}")
    return d


def enviar_seguro(e, transporte=None) -> str | None:
    """Envío que nunca interrumpe la operación de Surtia: devuelve el error (para avisarlo) o None."""
    if not configurado(): return None
    try:
        enviar_empresa(e, transporte); return None
    except ErrorGigadevs as ex:
        log.warning("No se envió %s a Gigadevs: %s", e.rif, ex); return str(ex)


def traer_licencia(e, transporte=None) -> str | None:
    """Licencia firmada de la suscripción vigente de la empresa, o None si aún no tiene."""
    from urllib.parse import quote
    estado, d = _peticion("GET", f"/sync/v1/{PRODUCTO}/licencia?referencia={quote(e.rif)}", None, transporte)
    if estado == 404: return None
    if estado != 200 or not d.get("token"): raise ErrorGigadevs(d.get("message") or f"La consola de Gigadevs respondió {estado}")
    return d["token"]
