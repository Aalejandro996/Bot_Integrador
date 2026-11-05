"""Licencia de Surtia emitida y firmada por la Consola Maestra de Gigadevs (servidor de licencias de Contax).

El token es `payload.firma` (Ed25519, base64url), como en CriteriSeg, Enlace MIT y Quincena VE. Cada empresa tiene su licencia:
producto «surtia», sujeto = RIF de la empresa. La clave pública se toma de PRODUCT_LICENSE_PUBLIC_KEY o se consulta al servidor
(PRODUCT_LICENSE_SERVER_URL, por defecto https://erp-licencias.onrender.com) y se guarda 5 minutos. La clave privada nunca sale
del servidor de licencias.

El control es opcional: SURTIA_LICENSE_ENFORCE=1 lo activa. Apagado (por defecto), la licencia se muestra pero no bloquea nada,
para no interrumpir a las empresas que ya trabajan con Surtia."""
import base64
import json
import os
import threading
import time
import urllib.request

PRODUCTO = "surtia"
_CLAVE = {"valor": "", "hasta": 0.0}
_CANDADO = threading.Lock()
MOTIVOS = {
    "sin_licencia": "La empresa no tiene licencia cargada",
    "formato": "El archivo de licencia no es válido",
    "firma": "La firma de la licencia no es válida",
    "producto": "La licencia no es de Surtia",
    "vencida": "La licencia venció o no está activa",
    "sujeto": "La licencia es de otra empresa (RIF distinto)",
    "verificacion": "No se pudo verificar la licencia en este momento",
}


def exigida() -> bool:
    return os.getenv("SURTIA_LICENSE_ENFORCE", "0").strip().lower() in {"1", "true", "si", "sí", "yes"}


def _b64(v: str) -> bytes:
    return base64.urlsafe_b64decode(v + "=" * (-len(v) % 4))


def clave_publica() -> str:
    clave = os.getenv("PRODUCT_LICENSE_PUBLIC_KEY", "").strip()
    if clave: return clave
    with _CANDADO:
        if _CLAVE["hasta"] > time.monotonic(): return _CLAVE["valor"]
        servidor = os.getenv("PRODUCT_LICENSE_SERVER_URL", "https://erp-licencias.onrender.com").strip().rstrip("/")
        if not servidor.startswith("https://"): raise ValueError("PRODUCT_LICENSE_SERVER_URL debe usar https")
        with urllib.request.urlopen(servidor + "/v1/clave-publica", timeout=5) as r:   # noqa: S310 (https fijo)
            clave = str(json.loads(r.read(4096)).get("ed25519", "")).strip()
        if not clave: raise ValueError("El servidor de licencias no publicó su clave")
        _CLAVE.update(valor=clave, hasta=time.monotonic() + 300)
        return clave


def verificar(token: str | None, rif: str, clave: str | None = None, reloj=time.time) -> tuple[dict | None, str | None]:
    """(claims, None) si la licencia vale para esta empresa; (None, motivo) si no."""
    if not token: return None, "sin_licencia"
    try:
        datos, firma = token.strip().split(".", 1)
        carga = _b64(datos)
        claims = json.loads(carga)
    except (ValueError, TypeError):
        return None, "formato"
    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        try:
            Ed25519PublicKey.from_public_bytes(_b64(clave or clave_publica())).verify(_b64(firma), carga)
        except InvalidSignature:
            return None, "firma"
    except (OSError, ValueError):
        return None, "verificacion"
    if claims.get("producto") != PRODUCTO or claims.get("tipo_sujeto") != "organizacion": return None, "producto"
    if claims.get("estado") != "activa" or int(claims.get("exp", 0)) <= int(reloj()): return None, "vencida"
    norm = lambda v: str(v or "").strip().upper().replace("-", "")
    if norm(claims.get("sujeto")) != norm(rif): return None, "sujeto"
    return claims, None


def de_empresa(e) -> tuple[dict | None, str | None]:
    return verificar(getattr(e, "licencia_token", None), e.rif)


def permite(e) -> bool:
    """¿La empresa puede trabajar? Con el control apagado, siempre."""
    if not exigida(): return True
    claims, motivo = de_empresa(e)
    return claims is not None or motivo == "verificacion"     # si el servidor no responde, no se corta el servicio
