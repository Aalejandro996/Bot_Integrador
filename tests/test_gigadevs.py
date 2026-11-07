"""Conexión con la Consola Maestra de Gigadevs: la firma que envía Surtia la acepta el servidor de licencias de Contax."""
import hashlib
import hmac
import json
from types import SimpleNamespace

from app import gigadevs as GD

SECRETO = "s" * 40


def _verificar(secreto, cab, metodo, ruta, cuerpo):
    """Misma verificación que licensing_server/comercial.firma_valida (servidor de licencias)."""
    p = dict(x.split("=", 1) for x in cab.split(","))
    base = "\n".join([p["t"], p["n"], metodo, ruta, hashlib.sha256(cuerpo).hexdigest()])
    return hmac.compare_digest(hmac.new(secreto.encode(), base.encode(), hashlib.sha256).hexdigest(), p["f"])


def test_envia_la_empresa_firmada_y_trae_la_licencia(monkeypatch):
    monkeypatch.setenv("GIGADEVS_SYNC_SECRET", SECRETO)
    vistos = []
    def transporte(metodo, ruta, cuerpo, cab):
        assert _verificar(SECRETO, cab["X-Gigadevs-Firma"], metodo, ruta, cuerpo)
        vistos.append((metodo, ruta, json.loads(cuerpo) if cuerpo else None))
        if ruta.startswith("/sync/v1/surtia/licencia"): return 200, b'{"token": "abc.def"}'
        return 201, b'{"cliente": {"id": 1}, "creado": true}'
    e = SimpleNamespace(id=7, rif="J-40000000-1", razon_social="Ferretería Los Llanos", correo="a@b.test", telefono=None, direccion=None, activa=False)
    GD.enviar_empresa(e, transporte)
    assert vistos[0][1] == "/sync/v1/surtia/clientes" and vistos[0][2]["estado"] == "suspendido" and vistos[0][2]["externo_id"] == "7"
    assert GD.traer_licencia(e, transporte) == "abc.def" and vistos[1][1] == "/sync/v1/surtia/licencia?referencia=J-40000000-1"


def test_sin_secreto_no_se_envia_nada_y_un_fallo_no_interrumpe(monkeypatch):
    monkeypatch.delenv("GIGADEVS_SYNC_SECRET", raising=False)
    e = SimpleNamespace(id=1, rif="J-1", razon_social="X", correo=None, telefono=None, direccion=None, activa=True)
    assert GD.enviar_seguro(e) is None
    monkeypatch.setenv("GIGADEVS_SYNC_SECRET", SECRETO)
    assert "500" in GD.enviar_seguro(e, lambda *a: (500, b'{}'))


def _firmado(estado, cuerpo, cab, alterar=False):
    """Como la consola: firma la respuesta ligada al nonce de la petición."""
    import time
    n = dict(x.split("=", 1) for x in cab["X-Gigadevs-Firma"].split(","))["n"]; t = str(int(time.time()))
    f = hmac.new(SECRETO.encode(), "\n".join([t, n, str(estado), hashlib.sha256(cuerpo).hexdigest()]).encode(), hashlib.sha256).hexdigest()
    return estado, (cuerpo + b" " if alterar else cuerpo), {"X-Gigadevs-Respuesta": f"t={t},f={f}"}


def test_respuesta_firmada_se_acepta_y_alterada_o_sin_firma_se_rechaza(monkeypatch):
    import pytest
    monkeypatch.setenv("GIGADEVS_SYNC_SECRET", SECRETO)
    e = SimpleNamespace(id=7, rif="J-40000000-1", razon_social="X", correo=None, telefono=None, direccion=None, activa=True)
    assert GD.traer_licencia(e, lambda m, r, c, h: _firmado(200, b'{"token": "abc.def"}', h)) == "abc.def"
    for t in (lambda m, r, c, h: _firmado(200, b'{"token": "abc.def"}', h, alterar=True),
              lambda m, r, c, h: (200, b'{"token": "abc.def"}', {}),
              lambda m, r, c, h: (200, b'{"token": "abc.def"}', {"X-Gigadevs-Respuesta": "t=1,f=" + "0" * 64})):
        with pytest.raises(GD.ErrorGigadevs, match="firma"):
            GD.traer_licencia(e, t)
