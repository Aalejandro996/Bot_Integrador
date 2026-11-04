from decimal import Decimal

import pytest

from app import bot as B

CAT = {"A001": ("Harina PAN 1kg", Decimal("45.50"), Decimal("10")), "B002": ("Aceite 1L", Decimal("120"), Decimal("0"))}


def test_contacto_nuevo_elige_si_es_cliente_o_proveedor():
    r, est, ev = B.responder("hola", "nuevo", {}, CAT, "Bodega Uno")
    assert "cliente" in r[0] and ev == []
    r, est, ev = B.responder("2", "nuevo", {}, CAT, "Bodega Uno")
    assert ev == [{"tipo_contacto": "proveedor"}] and "proveedores" in r[0]


def test_cliente_arma_y_confirma_su_pedido():
    r, est, _ = B.responder("1", "cliente", {}, CAT, "Bodega")
    assert "A001" in r[0] and "agotado" in r[0]
    r, est, _ = B.responder("a001 x3", "cliente", est, CAT, "Bodega")
    assert est["carrito"] == {"A001": "3"}
    r, est2, _ = B.responder("B002 1", "cliente", est, CAT, "Bodega")
    assert "Solo tenemos 0" in r[0] and est2 == est                              # sin existencia no se agrega
    r, _, _ = B.responder("2", "cliente", est, CAT, "Bodega")
    assert "136,50" in r[0]
    r, est, ev = B.responder("3", "cliente", est, CAT, "Bodega")
    assert est == {} and ev[0]["crear_pedido"]["tipo"] == "venta" and ev[0]["crear_pedido"]["total"] == "136.50"
    assert ev[0]["crear_pedido"]["lineas"] == [{"codigo": "A001", "descripcion": "Harina PAN 1kg", "cantidad": "3", "precio": "45.50"}]
    r, _, ev = B.responder("3", "cliente", {}, CAT, "Bodega")
    assert "vacío" in r[0] and ev == []


def test_proveedor_confirma_rechaza_y_oferta():
    ocs = [(12, "Enviada", Decimal("100"), "USD"), (13, "Recibida", Decimal("5"), "VES")]
    r, _, ev = B.responder("1", "proveedor", {}, CAT, "Bodega", ordenes=ocs)
    assert "N° 12" in r[0] and "N° 13" not in r[0]
    assert B.responder("CONFIRMAR 12", "proveedor", {}, CAT, "Bodega", ordenes=ocs)[2] == [{"oc_confirmada": 12}]
    _, _, ev = B.responder("rechazar 12 sin existencia", "proveedor", {}, CAT, "Bodega", ordenes=ocs)
    assert ev == [{"oc_rechazada": 12, "motivo": "sin existencia"}]
    assert "ya está recibida" in B.responder("confirmar 13", "proveedor", {}, CAT, "Bodega", ordenes=ocs)[0][0]
    assert "No encontramos" in B.responder("confirmar 99", "proveedor", {}, CAT, "Bodega", ordenes=ocs)[0][0]   # no ve órdenes ajenas
    _, est, _ = B.responder("2", "proveedor", {}, CAT, "Bodega")
    _, est, _ = B.responder("A001 50 40,10\nentrega el lunes", "proveedor", est, CAT, "Bodega")
    _, est, ev = B.responder("FIN", "proveedor", est, CAT, "Bodega")
    p = ev[0]["crear_pedido"]
    assert est == {} and p["tipo"] == "compra" and p["lineas"][0] == {"codigo": "A001", "descripcion": "Harina PAN 1kg", "cantidad": "50", "precio": "40.10"}
    assert p["nota"] == "entrega el lunes"


def test_presupuesto_por_el_bot():
    r, est, ev = B.responder("6", "cliente", {}, CAT, "Bodega")
    assert ev == [] and "agregue los productos" in r[0]
    _, est, _ = B.responder("A001 4", "cliente", {}, CAT, "Bodega")
    r, est, ev = B.responder("presupuesto", "cliente", est, CAT, "Bodega")
    assert ev == [{"crear_presupuesto": {"lineas": [{"codigo": "A001", "descripcion": "Harina PAN 1kg", "cantidad": "4"}]}}] and "carrito" not in est
    assert B.responder("ACEPTO 12", "cliente", {}, CAT, "Bodega")[2] == [{"presupuesto_aceptado": 12}]
