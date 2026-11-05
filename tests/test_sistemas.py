"""Sistemas que Surtia puede leer (app/sistemas.py): tablas Excel/CSV, Odoo, WooCommerce, hoja en línea y protecciones."""
import io
import json
import socket
from decimal import Decimal

import pytest

from app import profit as PF
from app import sistemas as SIS


def test_catalogo_de_sistemas_completo():
    for k, v in SIS.SISTEMAS.items():
        assert v["modo"] in SIS.MODOS and v["nombre"] and v["resumen"], k
    assert set(SIS.LECTORES) == {k for k, v in SIS.SISTEMAS.items() if v["modo"] == "nube"}
    assert SIS.nombre("profit") == "Profit Plus" and SIS.nombre("archivo") == "su sistema" and SIS.nombre("api", {"nombre_sistema": "Saint"}) == "Saint"


def test_excel_con_columnas_detectadas_solas():
    import openpyxl
    libro = openpyxl.Workbook(); h = libro.active
    h.append(["Lista de precios"]); h.append(["SKU", "Producto", "PVP", "Existencia", "IVA"])
    h.append([1001, "ARROZ 1KG", 1.5, 30, "exento"]); h.append(["ACE-01", "ACEITE 1L", "3,10", 0, 16]); h.append([None, None, None, None, None])
    b = io.BytesIO(); libro.save(b)
    enc, filas = SIS.leer_tabla(b.getvalue(), "lista.xlsx")
    assert enc[:3] == ["SKU", "Producto", "PVP"] and len(filas) == 2
    foto = PF.validar_foto(SIS.foto_de_tabla(enc, filas, {"moneda": "USD"}))
    a = {x["codigo"]: x for x in foto["articulos"]}
    assert set(a) == {"1001", "ACE-01"} and a["1001"]["alicuota"] == "E" and a["ACE-01"]["alicuota"] == "G"
    assert a["ACE-01"]["precio"] == Decimal("3.1") and a["1001"]["moneda"] == "USD" and a["1001"]["existencia"] == Decimal(30)


def test_csv_con_mapeo_del_maestro_y_errores_claros():
    csv = "cod_interno;desc;costo;venta\nA1;UNO;1;2\n".encode("latin-1")
    enc, filas = SIS.leer_tabla(csv, "x.csv")
    with pytest.raises(SIS.ErrorFuente, match="columna"):
        SIS.foto_de_tabla(enc, filas, {})                                   # no hay «código» ni «precio» reconocibles
    foto = SIS.foto_de_tabla(enc, filas, {"col_codigo": "cod_interno", "col_nombre": "desc", "col_precio": "venta"})
    assert foto["articulos"][0]["precio"] == "2" and foto["articulos"][0]["existencia"] == "0"
    with pytest.raises(SIS.ErrorFuente, match="No hay una columna"):
        SIS.foto_de_tabla(enc, filas, {"col_precio": "precio_final"})
    with pytest.raises(SIS.ErrorFuente, match="antiguo"):
        SIS.leer_tabla(b"\xd0\xcf\x11\xe0resto", "viejo.xls")


def test_odoo_solo_lee_y_normaliza():
    llamadas = []

    def odoo(metodo, url, cuerpo, cab):
        d = json.loads(cuerpo)["params"]; llamadas.append((d["service"], d["method"], d["args"][3] if len(d["args"]) > 3 else None, d["args"][4] if len(d["args"]) > 4 else None))
        if d["method"] == "authenticate": return 200, json.dumps({"result": 9 if d["args"][2] == "buena" else False}).encode()
        if d["args"][3] == "product.product":
            return 200, json.dumps({"result": [{"id": 1, "default_code": "HAR-01", "name": "HARINA", "list_price": 1.2, "qty_available": 5, "taxes_id": [1], "categ_id": [2, "Víveres"]},
                                               {"id": 2, "default_code": False, "name": "SIN CODIGO", "list_price": 3, "qty_available": -2, "taxes_id": [], "categ_id": False}]}).encode()
        return 200, json.dumps({"result": [{"id": 4, "ref": "C9", "name": "CLIENTE", "vat": "J-12345678-9", "mobile": "0414-1112233", "phone": False, "email": False, "street": "Calle 1"}]}).encode()
    op = {"url": "https://x.odoo.com", "base_datos": "x", "usuario": "u@x.com", "almacen": "3"}
    with pytest.raises(SIS.ErrorFuente, match="rechazó"):
        SIS.foto_odoo(op, {"clave": "mala"}, odoo)
    foto = PF.validar_foto(SIS.foto_odoo(op, {"clave": "buena"}, odoo))
    assert {a["codigo"] for a in foto["articulos"]} == {"HAR-01", "OD2"}
    od2 = next(a for a in foto["articulos"] if a["codigo"] == "OD2")
    assert od2["existencia"] == 0 and od2["alicuota"] == "E" and foto["clientes"][0]["whatsapp"] == "584141112233"
    assert {m for _, m, *_ in llamadas} == {"authenticate", "execute_kw"} and all(x[3] in (None, "search_read") for x in llamadas)


def test_hoja_en_linea_rechaza_paginas_html():
    with pytest.raises(SIS.ErrorFuente, match="no un CSV"):
        SIS.foto_hoja({}, {"enlace": "https://docs.google.com/x"}, lambda *a: (200, b"<!DOCTYPE html><html>"))
    foto = SIS.foto_hoja({}, {"enlace": "https://docs.google.com/x"}, lambda *a: (200, b"codigo,descripcion,precio,existencia\nP1,PAPEL,1.50,10\n"))
    assert foto["articulos"][0]["precio"] == "1.50"


def test_numeros_venezolanos_y_de_otros_paises():
    assert SIS.numero("1.234,56") == "1234.56" and SIS.numero("1,234.56") == "1234.56" and SIS.numero("Bs. 45,5") == "45.5"
    assert SIS.numero("1.200", coma=True) == "1200" and SIS.numero("1,200", coma=False) == "1200" and SIS.numero("") == "0"


def test_solo_https_publico(monkeypatch):
    with pytest.raises(SIS.ErrorFuente, match="https"):
        SIS.url_publica("http://tienda.com")
    with pytest.raises(SIS.ErrorFuente, match="usuario"):
        SIS.url_publica("https://a:b@tienda.com")
    for ip in ("127.0.0.1", "10.1.2.3", "169.254.169.254", "192.168.0.10", "::1"):
        monkeypatch.setattr(socket, "getaddrinfo", lambda *a, ip=ip, **k: [(2, 1, 6, "", (ip, 443))])
        with pytest.raises(SIS.ErrorFuente, match="red interna"):
            SIS.url_publica("https://tienda.com")
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", ("93.184.216.34", 443))])
    assert SIS.url_publica("https://tienda.com/") == "https://tienda.com/"


def test_consultas_sql_solo_lectura():
    assert SIS.consultas_ini({"q_articulos": "SELECT co_art, art_des, co_lin, tipo_imp\n FROM articulos"}) == \
        "articulos = SELECT co_art, art_des, co_lin, tipo_imp FROM articulos"
    for mala in ("DELETE FROM x", "SELECT 1; DROP TABLE x", "EXEC sp_algo", "UPDATE x SET a=1"):
        with pytest.raises(SIS.ErrorFuente, match="solo de lectura"):
            SIS.consultas_ini({"q_stock": mala})
