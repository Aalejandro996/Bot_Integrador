"""Recorrido completo contra PostgreSQL REAL (se omite sin base de pruebas; ¡la base se BORRA!):

  TEST_DATABASE_URL=postgresql+psycopg://postgres@localhost:5432/bot_pruebas pytest tests/test_integracion.py
Maestro → empresa y administrador por enlace → MFA → pasarela OpenWA propia → QR → Profit (conector firmado) → el bot atiende
con el catálogo de Profit (búsqueda, pedido, presupuesto, PDF) → pedidos, CSV y documento → monitoreo → aislamiento."""
import gzip
import hashlib
import hmac
import io
import json
import os
import re
import sys
import time
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import pytest

URL = os.environ.get("TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="Defina TEST_DATABASE_URL para la prueba de integración")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "conector_profit"))
CLAVE = "Clave-Segura#2026"
SID = "8f3c2b1a-9d4e-4c7a-8b2f-1e6d5a4c3b2a"


@pytest.fixture(scope="module")
def app():
    from cryptography.fernet import Fernet
    from sqlalchemy import create_engine, text
    with create_engine(URL, isolation_level="AUTOCOMMIT").connect() as c:
        c.execute(text("DROP SCHEMA public CASCADE")); c.execute(text("CREATE SCHEMA public"))
    from config import Config

    class Cfg(Config):
        SECRET_KEY = "x" * 48; FIELD_ENCRYPTION_KEY = Fernet.generate_key().decode(); SQLALCHEMY_DATABASE_URI = URL
        APP_BASE_URL = "https://bot.test"; WTF_CSRF_ENABLED = False; RATELIMIT_ENABLED = False; TASAS_AUTOMATICAS = False
        PRODUCCION = False; SESSION_COOKIE_SECURE = False; SESSION_COOKIE_NAME = "s"; MAESTRO_USUARIO = "gigadevs"
    from app import create_app
    a = create_app(Cfg)
    from app.extensions import db
    from app.models import Parametros, ProfitPlataforma
    with a.app_context():
        db.create_all(); db.session.add(Parametros(id=1, tasa_usd=Decimal("36.5"), tasa_fecha=datetime.now().date())); db.session.add(ProfitPlataforma(id=1)); db.session.commit()
    return a


def _uno(app, sql, **p):
    from app.extensions import db
    with app.app_context():
        return db.session.execute(db.text(sql), p).scalar()


def _activar(app, enlace):
    """Sigue el enlace de activación: crea la clave y configura el MFA leyendo el secreto de la página."""
    from app import totp
    c = app.test_client()
    ruta = enlace.replace("https://bot.test", "")
    assert "Active su cuenta" in c.get(ruta).get_data(as_text=True)
    assert "no coinciden" in c.post(ruta, data={"nueva": CLAVE, "repetir": "otra"}, follow_redirects=True).get_data(as_text=True)
    assert "al menos 12" in c.post(ruta, data={"nueva": "corta", "repetir": "corta"}, follow_redirects=True).get_data(as_text=True)
    r = c.post(ruta, data={"nueva": CLAVE, "repetir": CLAVE})
    assert r.headers["Location"].endswith("/mfa/configurar")
    sec = re.search(r"<code>([A-Z2-7]{20,})</code>", c.get("/mfa/configurar").get_data(as_text=True)).group(1)
    assert c.post("/mfa/configurar", data={"codigo": totp.codigo(sec)}).status_code == 302
    assert "Enlace no válido" in app.test_client().get(ruta).get_data(as_text=True)          # un solo uso
    return c, sec


def _enlace(r):
    return re.search(r"https://bot\.test/activar/[\w-]+", r.get_data(as_text=True)).group(0)


def test_recorrido_completo(app):
    from app import totp
    import surtia_conector as CP
    # --- el primer Maestro: inicializar escribe su enlace en el log
    from app.extensions import db
    from app.models import Usuario
    from app.seguridad import nuevo_enlace_activacion
    with app.app_context(), app.test_request_context(base_url="https://bot.test"):
        u = Usuario(username="gigadevs", email="soporte@gigadevs.test", rol="Maestro"); db.session.add(u); db.session.flush(); enlace = nuevo_enlace_activacion(u); db.session.commit()
    root, sec_root = _activar(app, enlace)
    from app import correo
    asuntos = lambda para: [m["Subject"] for m in correo.ENVIADOS if m["To"] == para]
    texto_de = lambda para: [m.get_content() for m in correo.ENVIADOS if m["To"] == para][-1]
    assert "Verificación en dos pasos activada · Surtia" in asuntos("soporte@gigadevs.test")      # alerta de seguridad
    assert "Monitoreo del bot" in root.get("/maestro").get_data(as_text=True)
    anon = app.test_client()
    assert anon.get("/maestro").status_code == 302 and anon.get("/pedidos").status_code == 302
    # login con clave + MFA; bloqueo tras 5 fallos
    for _ in range(5): anon.post("/login", data={"usuario": "gigadevs", "clave": "mala"})
    assert "bloqueada" in anon.post("/login", data={"usuario": "gigadevs", "clave": CLAVE}).get_data(as_text=True)
    assert "Cuenta bloqueada por intentos fallidos · Surtia" in asuntos("soporte@gigadevs.test")
    with app.app_context():
        db.session.execute(db.text("UPDATE usuarios SET bloqueado_hasta = NULL")); db.session.commit()
    # --- empresa y su administrador
    r = root.post("/maestro/empresas", data={"accion": "crear", "razon_social": "General de Suministros Orituco, C.A.", "rif": "j123456780",
                                             "direccion": "Altagracia de Orituco", "username": "orituco", "email": "admin@orituco.test",
                                             "correo": "ventas@orituco.test"}, follow_redirects=True)
    assert "Empresa creada" in r.get_data(as_text=True) and "Sistema de General de Suministros Orituco" in r.get_data(as_text=True)   # sigue: su sistema
    assert "Active su cuenta · Surtia" in asuntos("admin@orituco.test") and _enlace(r) in texto_de("admin@orituco.test")   # el enlace llega por correo
    assert "General de Suministros Orituco" in texto_de("admin@orituco.test")
    adm, sec_adm = _activar(app, _enlace(r))
    t = adm.get("/guia").get_data(as_text=True)                                    # guía paso a paso del administrador
    assert "Vincule el WhatsApp de la empresa" in t and "Así conversará con sus clientes" in t and "wa-msj" in t
    assert "Bienvenido a Surtia, General de Suministros Orituco, C.A. · Surtia" in asuntos("admin@orituco.test")
    assert adm.post("/guia").headers["Location"].endswith("/")
    eid = _uno(app, "SELECT id FROM empresas WHERE rif='J-12345678-0'")
    assert "Vincular el WhatsApp de la empresa" in adm.get("/").get_data(as_text=True)
    assert adm.get("/maestro").status_code == 403                                        # la consola es solo de Gigadevs
    # --- licencia de Surtia (Consola Maestra de Gigadevs): el Maestro la carga; con el control activo, sin licencia no se entra
    import base64, json as _json, time as _t
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    priv = Ed25519PrivateKey.generate()
    os.environ["PRODUCT_LICENSE_PUBLIC_KEY"] = base64.urlsafe_b64encode(priv.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).decode().rstrip("=")
    def lic(**kw):
        c = _json.dumps({"producto": "surtia", "tipo_sujeto": "organizacion", "sujeto": "J-12345678-0", "estado": "activa",
                         "exp": int(_t.time()) + 86400, "suscripcion": 7, **kw}, sort_keys=True, separators=(",", ":")).encode()
        b = lambda x: base64.urlsafe_b64encode(x).rstrip(b"=").decode()
        return f"{b(c)}.{b(priv.sign(c))}"
    try:
        assert "Sin licencia" in root.get("/maestro/empresas").get_data(as_text=True)
        os.environ["SURTIA_LICENSE_ENFORCE"] = "1"
        r = adm.get("/")
        assert r.status_code == 402 and "La licencia de Surtia no está activa" in r.get_data(as_text=True)
        r = root.post("/maestro/empresas", data={"accion": "licencia", "eid": eid, "token": lic(sujeto="J-99999999-9")}, follow_redirects=True)
        assert "otra empresa" in r.get_data(as_text=True)
        r = root.post("/maestro/empresas", data={"accion": "licencia", "eid": eid, "token": lic()}, follow_redirects=True)
        assert "cargada: vence el" in r.get_data(as_text=True) and "Vigente hasta" in r.get_data(as_text=True)
        assert adm.get("/").status_code == 200
    finally:
        os.environ.pop("SURTIA_LICENSE_ENFORCE", None)
    assert "no está configurada" in adm.get("/whatsapp").get_data(as_text=True)
    # --- pasarela OpenWA propia (simulada)
    pas = {"estado": "created", "telefono": None, "webhook": None, "enviados": [], "n": 0}
    def ow_t(metodo, url, cuerpo, cab):
        assert cab.get("X-API-Key") == "owa_propia"
        ruta, d = url.split("/api", 1)[1], (json.loads(cuerpo) if cuerpo else None)
        if (metodo, ruta) == ("GET", "/sessions"):
            return 200, json.dumps([{"id": SID, "name": f"surtia-e{eid}-abcd", "status": pas["estado"], "phone": pas["telefono"]}]).encode()
        if (metodo, ruta) == ("POST", "/sessions"): return 201, json.dumps({"id": SID}).encode()
        if ruta == f"/sessions/{SID}/webhooks": pas["webhook"] = d; return 201, b'{"id":"w1"}'
        if ruta == f"/sessions/{SID}/start": pas["estado"] = "qr_ready"; return 200, b"{}"
        if (metodo, ruta) == ("GET", f"/sessions/{SID}"): return 200, json.dumps({"status": pas["estado"], "phone": pas["telefono"]}).encode()
        if ruta == f"/sessions/{SID}/qr": return 200, b'{"qrCode":"data:image/png;base64,iVBORw0KGgo=","status":"qr_ready"}'
        if ruta == f"/sessions/{SID}/messages/send-text":
            pas["enviados"].append(d); pas["n"] += 1; return 201, json.dumps({"messageId": f"out{pas['n']}"}).encode()
        if ruta == f"/sessions/{SID}/logout": pas["estado"] = "disconnected"; return 200, b"{}"
        if metodo == "DELETE": return 204, b""
        return 404, b'{"message":"no"}'
    app.extensions["openwa_transporte"] = ow_t
    r = root.post("/maestro/whatsapp", data={"url": "https://wa.bot.test", "api_key": "owa_propia", "activa": "1"}, follow_redirects=True)
    assert "Pasarela verificada y guardada" in r.get_data(as_text=True)
    assert "owa_propia" not in _uno(app, "SELECT api_key_cifrada FROM whatsapp_pasarela")
    t = adm.post("/whatsapp", data={"accion": "conectar"}, follow_redirects=True).get_data(as_text=True)
    assert "Escanee el código QR" in t and "iVBORw0KGgo=" in t
    clave = _uno(app, "SELECT clave_webhook FROM whatsapp_config WHERE empresa_id=:e", e=eid)
    assert pas["webhook"]["url"] == f"https://bot.test/wa/{clave}"
    secreto = pas["webhook"]["secret"]
    def aviso(evento, datos, firma=None):
        cuerpo = json.dumps({"event": evento, "sessionId": SID, "data": datos}).encode()
        return anon.post(f"/wa/{clave}", data=cuerpo, content_type="application/json",
                         headers={"X-OpenWA-Signature": firma or "sha256=" + hmac.new(secreto.encode(), cuerpo, hashlib.sha256).hexdigest()})
    assert aviso("session.authenticated", {"phone": "584247770000"}, firma="sha256=" + "0" * 64).status_code == 403
    pas["estado"], pas["telefono"] = "ready", "584247770000"
    aviso("session.authenticated", {"sessionId": SID, "phone": "584247770000"})
    assert adm.get("/whatsapp/estado").get_json()["estado"] == "ready"
    assert "Desvincular WhatsApp" in adm.get("/whatsapp").get_data(as_text=True)
    # --- Profit Plus: lo registró y lo configura el Maestro; la empresa no instala ni configura nada
    t = adm.get("/catalogo").get_data(as_text=True)
    assert "lo administra Surtia" in t.lower() or "Lo administra Surtia" in t
    assert adm.get(f"/maestro/empresas/{eid}").status_code == 403 and adm.get(f"/maestro/empresas/{eid}/descargar/configuracion").status_code == 403
    assert adm.post("/profit", data={"accion": "activar"}).status_code == 405                    # ya no hay activación del lado del cliente
    t = root.get(f"/maestro/empresas/{eid}").get_data(as_text=True)
    assert "Profit Plus" in t and "surtia_conector.ini" in t and "Opciones de Profit Plus" in t
    import configparser
    ini = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
    ini.read_string(root.get(f"/maestro/empresas/{eid}/descargar/configuracion").get_data(as_text=True))
    cid, cl = ini["surtia"]["conector"], ini["surtia"]["clave"]
    assert ini["surtia"]["url"] == "https://bot.test" and b"saStockAlmacen" in root.get(f"/maestro/empresas/{eid}/descargar/usuario-sql").data
    def conector(ruta, datos, clave_=None, fecha=None):
        cuerpo = gzip.compress(json.dumps(datos, default=str).encode()); f = fecha or int(time.time())
        return anon.post("/api/profit/v1/" + ruta, data=cuerpo, content_type="application/json",
                         headers={"X-Surtia-Conector": cid, "Content-Encoding": "gzip", "X-Surtia-Fecha": str(f), "X-Surtia-Firma": CP.firmar(clave_ or cl, f, cuerpo)})
    assert conector("ping", {}, clave_="otra").status_code == 401 and conector("ping", {}, fecha=int(time.time()) - 900).status_code == 401
    cfg = conector("ping", {"version": "1.0.0", "equipo": "SRV-PROFIT"}).get_json()["config"]
    foto = CP.construir_foto([("01", "PRINCIPAL", False), ("09", "MERMAS", True)], [("01", "DETAL")],
                             [("HAR-01", "HARINA PAN 1KG", "VIV", "1"), ("LEC-01", "LECHE COMPLETA 1L", "VIV", "7"), ("ACE-01", "ACEITE VATEL 1L", "VIV", "1")],
                             [("HAR-01", "01", "ACT", 50), ("HAR-01", "01", "COM", 10), ("HAR-01", "09", "ACT", 99), ("LEC-01", "01", "ACT", 5), ("ACE-01", "01", "ACT", 20)],
                             [("HAR-01", "", Decimal("45.5"), "BS", datetime(2026, 1, 1)), ("LEC-01", "", Decimal("60"), "BS", datetime(2026, 1, 1)),
                              ("ACE-01", "", Decimal("3.10"), "US$", datetime(2026, 1, 1))],
                             [("C001", "COMERCIAL ORITUCO CA", "J111111111", "Av. Principal, Altagracia", "0238-1234567 / 0414-7778899", None)],
                             config=cfg, alicuotas={"1": "G", "7": "E"})
    r = conector("sync", foto)
    assert (r.get_json()["articulos"], r.get_json()["clientes"]) == (3, 1)
    assert conector("sync", {"articulos": "x", "clientes": []}).status_code == 400 and _uno(app, "SELECT count(*) FROM profit_articulos") == 3
    assert "HARINA PAN 1KG" in adm.get("/catalogo").get_data(as_text=True)
    t = root.get(f"/maestro/empresas/{eid}").get_data(as_text=True)
    assert "1.0.0" in t and "01 · PRINCIPAL" in t and "HARINA" not in t                        # el Maestro ve estados, no el catálogo
    root.post(f"/maestro/empresas/{eid}", data={"accion": "opciones", "activo": "1", "almacenes": "01", "lista_precio": "01", "restar_comprometido": "1",
                                                 "solo_con_existencia": "0"})
    assert conector("ping", {}).get_json()["config"]["almacenes"] == ["01"]
    # --- el bot atiende con el catálogo de Profit
    msg = lambda i, txt, **extra: aviso("message.received", {"id": f"true_584147778899@c.us_M{i}", "from": "584147778899@c.us", "chatId": "584147778899@c.us",
                                                            "type": extra.pop("tipo", "text"), "body": txt, "kind": "individual", **extra})
    ultimo = lambda: pas["enviados"][-1]["text"]
    msg(1, "harina"); assert "HAR-01" in ultimo() and "45,50" in ultimo()
    assert _uno(app, "SELECT nombre || '|' || tipo || '|' || codigo_profit FROM contactos WHERE telefono='584147778899'") == "COMERCIAL ORITUCO CA|cliente|C001"
    msg(2, "aceite"); assert "113,15" in ultimo()                                             # 3,10 USD × 36,5
    msg(3, "HAR-01 60"); assert "Solo tenemos 40" in ultimo()
    msg(4, "HAR-01 3"); msg(5, "LEC-01 2"); msg(6, "3"); assert "Pedido recibido" in ultimo()
    n = len(pas["enviados"]); msg(6, "3"); assert len(pas["enviados"]) == n                   # aviso repetido: no responde dos veces
    assert _uno(app, "SELECT total FROM pedidos WHERE empresa_id=:e", e=eid) == Decimal("256.50")
    msg(7, "LEC-01 2"); msg(8, "6"); assert "Presupuesto N° 1" in ultimo() and "120,00" in ultimo()
    msg(9, "ACEPTO 1"); assert "aceptado" in ultimo()
    msg(10, "5"); assert _uno(app, "SELECT atencion FROM contactos WHERE telefono='584147778899'") is True
    # avisos por correo a la empresa (correo de la empresa + administradores/operadores), con enlace al panel
    for para in ("ventas@orituco.test", "admin@orituco.test"):
        a = asuntos(para)
        assert "Pedido N° 1 por WhatsApp · Surtia" in a and "Presupuesto N° 1 aceptado · Surtia" in a and "Un cliente pide hablar con una persona · Surtia" in a, a
    assert "TOTAL: Bs 278,34" in [m.get_content() for m in correo.ENVIADOS if "Pedido N° 1" in m["Subject"]][0]
    assert not [m for m in correo.ENVIADOS if "Pedido" in m["Subject"] and m["To"] == "soporte@gigadevs.test"]   # el Maestro no ve pedidos
    # orden de compra en PDF → orden de venta con lo disponible
    from app import servicios as SV
    pdf = _pdf(["ORDEN DE COMPRA N° 9001", "COMERCIAL ORITUCO CA RIF J-11111111-1", "HAR-01 HARINA PAN 1KG 100", "LEC-01 1"])
    app.extensions["whatsapp_descarga"] = lambda mid: pdf
    msg(11, "", tipo="document", media={"mimetype": "application/pdf", "filename": "oc.pdf"})
    resp = ultimo()
    assert "Orden de venta N° 2" in resp and "pidió 100" in resp and "factura fiscal se emitirá" in resp, resp
    doc = anon.get(re.search(r"https://bot\.test(/d/\S+)", resp).group(1)).get_data(as_text=True)
    assert "ORDEN DE VENTA" in doc and "COMERCIAL ORITUCO CA" in doc and "Av. Principal, Altagracia" in doc and "C001" in doc
    # --- pantallas de la empresa
    t = adm.get("/pedidos").get_data(as_text=True); assert "C001" in t and "256,50" in t
    pid = _uno(app, "SELECT id FROM pedidos WHERE numero=1 AND empresa_id=:e", e=eid)
    adm.post("/pedidos", data={"pid": pid, "estado": "Confirmado", "avisar": "1", "mensaje": "Pase mañana"})
    assert "confirmado" in ultimo() and "Pase mañana" in ultimo()
    csv = adm.get("/pedidos/exportar").get_data(as_text=True); assert "C001;COMERCIAL ORITUCO CA" in csv and "HAR-01" in csv
    assert "Pidió una persona" in adm.get("/conversaciones").get_data(as_text=True)
    assert "Aceptado" in adm.get("/presupuestos").get_data(as_text=True)
    # --- monitoreo del Maestro: estados y conteos, nunca catálogo ni conversaciones
    t = root.get("/maestro").get_data(as_text=True)
    assert "General de Suministros Orituco" in t and "Conectado" in t and "HARINA" not in t and "COMERCIAL ORITUCO" not in t
    assert "General de Suministros Orituco" in root.get("/maestro/whatsapp").get_data(as_text=True)
    # --- aislamiento: otra empresa no ve nada de la primera
    r = root.post("/maestro/empresas", data={"accion": "crear", "razon_social": "Otra CA", "rif": "J-22222222-2", "username": "otra"}, follow_redirects=True)
    otra, _ = _activar(app, _enlace(r))
    assert "Otra CA" in root.get("/maestro").get_data(as_text=True)               # empresa sin WhatsApp: el monitoreo no se cae
    # --- otra empresa con su catálogo en Excel/CSV: lo sube el Maestro (vista previa → importar)
    e2 = _uno(app, "SELECT id FROM empresas WHERE rif='J-22222222-2'")
    t = root.post(f"/maestro/empresas/{e2}", data={"accion": "sistema", "sistema": "archivo"}, follow_redirects=True).get_data(as_text=True)
    assert "Sistema registrado: Archivo Excel o CSV" in t and "Subir el catálogo" in t
    root.post(f"/maestro/empresas/{e2}", data={"accion": "opciones", "activo": "1", "solo_con_existencia": "0", "moneda": "USD", "alicuota": "G"})
    hoja = "Código;Descripción;Precio;Stock\nTOR-01;TORNILLO 1/4;0,50;1.200\nmal codigo;SIN CODIGO;1;1\n".encode()
    t = root.post(f"/maestro/empresas/{e2}", data={"accion": "importar", "modo": "vista", "archivo": (io.BytesIO(hoja), "lista.csv")},
                  content_type="multipart/form-data").get_data(as_text=True)
    assert "Vista previa de lista.csv" in t and "TORNILLO 1/4" in t and _uno(app, "SELECT count(*) FROM profit_articulos WHERE empresa_id=:e", e=e2) == 0
    t = root.post(f"/maestro/empresas/{e2}", data={"accion": "importar", "modo": "importar", "archivo": (io.BytesIO(hoja), "lista.csv")},
                  content_type="multipart/form-data", follow_redirects=True).get_data(as_text=True)
    assert "Catálogo importado: 1 artículo(s)" in t and "1 fila(s) sin código válido" in t
    assert _uno(app, "SELECT existencia FROM profit_articulos WHERE empresa_id=:e AND codigo='TOR-01'", e=e2) == Decimal("1200")
    t = otra.get("/catalogo").get_data(as_text=True); assert "TORNILLO 1/4" in t and "HARINA" not in t
    # --- y luego WooCommerce (nube): el Maestro guarda las claves cifradas y prueba la lectura
    def woo(metodo, url, cuerpo=None, cab=None):
        assert cab["Authorization"].startswith("Basic ")
        if "/products?" in url and "page=1" in url:
            return 200, json.dumps([{"id": 7, "sku": "CAM-01", "name": "CAMISA AZUL", "price": "12.5", "manage_stock": True, "stock_quantity": 4, "tax_status": "taxable"}]).encode()
        if "/customers?" in url and "page=1" in url:
            return 200, json.dumps([{"id": 3, "email": "ana@test.com", "first_name": "Ana", "last_name": "Ruiz", "billing": {"phone": "04141234567"}}]).encode()
        return 200, b"[]"
    app.extensions["fuente_transporte"] = woo
    root.post(f"/maestro/empresas/{e2}", data={"accion": "sistema", "sistema": "woocommerce"})
    assert _uno(app, "SELECT count(*) FROM profit_articulos WHERE empresa_id=:e", e=e2) == 0            # al cambiar de sistema se borra el anterior
    import socket
    real = socket.getaddrinfo
    socket.getaddrinfo = lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))]
    try:
        t = root.post(f"/maestro/empresas/{e2}", data={"accion": "opciones", "url": "https://tienda.test", "ck": "ck_secreta", "cs": "cs_secreto", "moneda": "USD",
                                                       "activo": "1", "solo_con_existencia": "0"}, follow_redirects=True).get_data(as_text=True)
        assert "Opciones guardadas" in t and "guardada" in t and "ck_secreta" not in t
        socket.getaddrinfo = lambda *a, **k: [(2, 1, 6, "", ("10.0.0.5", 443))]
        t = root.post(f"/maestro/empresas/{e2}", data={"accion": "opciones", "url": "https://interno.test", "activo": "1"}, follow_redirects=True).get_data(as_text=True)
        assert "red interna" in t
    finally:
        socket.getaddrinfo = real
    assert "ck_secreta" not in (_uno(app, "SELECT credenciales_cifradas FROM profit_conexion WHERE empresa_id=:e", e=e2) or "")
    t = root.post(f"/maestro/empresas/{e2}", data={"accion": "sincronizar"}, follow_redirects=True).get_data(as_text=True)
    assert "Lectura correcta: 1 artículo(s) y 1 cliente(s)" in t, t
    assert _uno(app, "SELECT whatsapp FROM profit_clientes WHERE empresa_id=:e", e=e2) == "584141234567"
    t = root.get("/maestro/empresas").get_data(as_text=True); assert "WooCommerce" in t and "Profit Plus" in t
    assert otra.get(f"/pedidos/{pid}").status_code == 404 and "COMERCIAL" not in otra.get("/pedidos").get_data(as_text=True)
    cid1 = _uno(app, "SELECT id FROM contactos WHERE empresa_id=:e", e=eid)
    assert otra.get(f"/conversaciones/{cid1}").status_code == 404
    # operador: atiende pero no configura
    r = adm.post("/usuarios", data={"accion": "invitar", "username": "vendedor1", "rol": "Operador"}, follow_redirects=True)
    op, _ = _activar(app, _enlace(r))
    assert op.get("/pedidos").status_code == 200 and op.get("/catalogo").status_code == 200 and op.get("/whatsapp").status_code == 403
    assert op.get(f"/maestro/empresas/{eid}").status_code == 403
    # el WhatsApp se cae: avisan a la empresa y al Maestro
    aviso("session.status", {"sessionId": SID, "status": "disconnected"})
    assert "El WhatsApp del bot se desconectó · Surtia" in asuntos("admin@orituco.test") and "El WhatsApp del bot se desconectó · Surtia" in asuntos("soporte@gigadevs.test")
    # suspender la empresa: nadie entra y el bot no atiende
    root.post("/maestro/empresas", data={"accion": "suspender", "eid": eid})
    assert adm.get("/pedidos").status_code == 302 and aviso("message.received", {"id": "x9", "from": "584147778899@c.us", "body": "hola"}).get_json()["ignorado"] == "empresa"
    assert anon.get("/salud").get_json() == {"ok": True}
    # redirección abierta tras el MFA: solo rutas internas
    c = app.test_client()
    c.post("/login", data={"usuario": "gigadevs", "clave": CLAVE})
    assert c.post("/mfa?siguiente=/%5Cevil.com", data={"codigo": totp.codigo(sec_root, t=time.time() + 30)}).headers["Location"] == "/maestro"
    # --- olvidé mi contraseña: enlace por correo → clave nueva → código MFA → recién ahí cambia
    from app.extensions import limiter
    r = app.test_client()
    assert "¿Olvidó su contraseña?" in r.get("/login").get_data(as_text=True)
    resp_ok = r.post("/recuperar", data={"usuario": "SOPORTE@gigadevs.test"}).get_data(as_text=True)
    resp_no = r.post("/recuperar", data={"usuario": "nadie@x.test"}).get_data(as_text=True)
    assert "Si la cuenta existe" in resp_ok and resp_ok == resp_no                        # no revela si la cuenta existe
    assert "Recupere su contraseña · Surtia" in asuntos("soporte@gigadevs.test")
    n = len(asuntos("soporte@gigadevs.test")); r.post("/recuperar", data={"usuario": "gigadevs"})
    assert len(asuntos("soporte@gigadevs.test")) == n                                     # no reenvía antes de 2 minutos
    enl = re.search(r"https://bot\.test(/recuperar/[\w-]+)", texto_de("soporte@gigadevs.test")).group(1)
    nueva = "Otra-Clave#Segura2026"
    assert "Elija una contraseña nueva" in r.get(enl).get_data(as_text=True)
    assert "distinta de la anterior" in r.post(enl, data={"nueva": CLAVE, "repetir": CLAVE}).get_data(as_text=True)
    assert r.post(enl, data={"nueva": nueva, "repetir": nueva}).headers["Location"].endswith("/recuperar/confirmar")
    assert "anterior" not in r.get("/recuperar/confirmar").get_data(as_text=True)
    assert app.test_client().post("/login", data={"usuario": "gigadevs", "clave": nueva}).status_code == 200   # aún NO cambió
    t = r.post("/recuperar/confirmar", data={"codigo": "000000"}).get_data(as_text=True)
    assert "La contraseña aún no cambió" in t
    with app.app_context():                                     # el código anterior ya se usó en esta ventana de 30 s
        db.session.execute(db.text("UPDATE usuarios SET mfa_ultimo_contador = 0 WHERE username='gigadevs'")); db.session.commit()
    assert r.post("/recuperar/confirmar", data={"codigo": totp.codigo(sec_root)}).headers["Location"] == "/maestro"
    assert "Su contraseña cambió · Surtia" in asuntos("soporte@gigadevs.test")
    assert root.get("/maestro").status_code == 302                                         # se cerraron las demás sesiones
    assert "Enlace no válido" in r.get(enl).get_data(as_text=True)                         # el enlace era de un solo uso
    c2 = app.test_client()
    assert c2.post("/login", data={"usuario": "gigadevs", "clave": nueva}).headers["Location"].endswith("/mfa")
    assert "Enlace no válido" in app.test_client().get("/recuperar/" + "x" * 43).get_data(as_text=True)


def _pdf(lineas):
    from pypdf import PdfWriter
    try:
        from reportlab.pdfgen import canvas
    except ImportError:
        pytest.skip("reportlab no instalado")
    b = io.BytesIO(); cv = canvas.Canvas(b); y = 800
    for l in lineas: cv.drawString(50, y, l); y -= 20
    cv.save(); assert PdfWriter
    return b.getvalue()
