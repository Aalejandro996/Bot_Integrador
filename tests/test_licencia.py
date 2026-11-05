"""Licencia de Surtia firmada por la Consola Maestra de Gigadevs (mismo formato que el servidor de licencias de Contax)."""
import base64
import json
import time

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from app import licencia as L

PRIV = Ed25519PrivateKey.generate()
PUB = base64.urlsafe_b64encode(PRIV.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode().rstrip("=")


def _token(**kw):
    claims = {"producto": "surtia", "tipo_sujeto": "organizacion", "sujeto": "J-12345678-0", "estado": "activa",
              "exp": int(time.time()) + 3600, "plan": "empresa", "modulos": ["bot_pedidos"], **kw}
    payload = json.dumps(claims, sort_keys=True, separators=(",", ":")).encode()
    b = lambda x: base64.urlsafe_b64encode(x).rstrip(b"=").decode()
    return f"{b(payload)}.{b(PRIV.sign(payload))}"


def test_licencia_valida_y_cada_motivo_de_rechazo():
    assert L.verificar(_token(), "J-12345678-0", PUB)[0]["plan"] == "empresa"
    assert L.verificar(_token(sujeto="j123456780"), "J-12345678-0", PUB)[1] is None              # el RIF se compara normalizado
    assert L.verificar(None, "J-12345678-0", PUB) == (None, "sin_licencia")
    assert L.verificar("no-es-un-token", "J-12345678-0", PUB) == (None, "formato")
    assert L.verificar(_token(producto="criteriseg"), "J-12345678-0", PUB) == (None, "producto")
    assert L.verificar(_token(estado="suspendida"), "J-12345678-0", PUB) == (None, "vencida")
    assert L.verificar(_token(exp=int(time.time()) - 1), "J-12345678-0", PUB) == (None, "vencida")
    assert L.verificar(_token(), "J-99999999-9", PUB) == (None, "sujeto")
    otra = base64.urlsafe_b64encode(Ed25519PrivateKey.generate().public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode().rstrip("=")
    assert L.verificar(_token(), "J-12345678-0", otra) == (None, "firma")                          # firmada por otro: no vale


def test_el_control_esta_apagado_por_defecto(monkeypatch):
    from types import SimpleNamespace
    e = SimpleNamespace(rif="J-12345678-0", licencia_token=None)
    monkeypatch.delenv("SURTIA_LICENSE_ENFORCE", raising=False)
    assert L.permite(e)                                          # nadie se queda sin servicio al actualizar
    monkeypatch.setenv("SURTIA_LICENSE_ENFORCE", "1"); monkeypatch.setenv("PRODUCT_LICENSE_PUBLIC_KEY", PUB)
    assert not L.permite(e)
    e.licencia_token = _token()
    assert L.permite(e)
