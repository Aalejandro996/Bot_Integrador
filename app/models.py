"""Modelo de datos del bot. Cada fila de negocio cuelga de una empresa; la app filtra SIEMPRE por la empresa del usuario."""
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, UniqueConstraint

from .extensions import db

M = db.Numeric(18, 2)
Q = db.Numeric(18, 4)
ROLES = ("Maestro", "Administrador", "Operador")
ESTADOS_PEDIDO = ("Nuevo", "Confirmado", "Despachado", "Facturado en Profit", "Rechazado")
ESTADOS_PRESUPUESTO = ("Borrador", "Enviado", "Aceptado", "Rechazado")


def ahora():
    return datetime.now(timezone.utc)


# ====================================================================== empresas y usuarios
class Empresa(db.Model):
    __tablename__ = "empresas"
    id = db.Column(db.Integer, primary_key=True)
    razon_social = db.Column(db.Text, nullable=False)
    rif = db.Column(db.Text, nullable=False, unique=True)
    direccion = db.Column(db.Text)
    telefono = db.Column(db.Text)
    correo = db.Column(db.Text)
    activa = db.Column(db.Boolean, nullable=False, default=True)
    creada = db.Column(db.DateTime(timezone=True), default=ahora, nullable=False)


class Usuario(db.Model):
    __tablename__ = "usuarios"
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.Text, nullable=False, unique=True)
    email = db.Column(db.Text)
    nombre = db.Column(db.Text)
    rol = db.Column(db.Text, nullable=False)
    empresa_id = db.Column(db.ForeignKey("empresas.id"))          # NULL solo para el Maestro
    password_hash = db.Column(db.Text)                           # NULL hasta que active su cuenta
    mfa_secreto_cifrado = db.Column(db.Text)
    mfa_activo = db.Column(db.Boolean, nullable=False, default=False)
    mfa_ultimo_contador = db.Column(db.BigInteger, nullable=False, default=0)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    intentos_fallidos = db.Column(db.Integer, nullable=False, default=0)
    bloqueado_hasta = db.Column(db.DateTime(timezone=True))
    sesion_version = db.Column(db.Integer, nullable=False, default=1)
    token_activacion_hash = db.Column(db.Text, unique=True)       # SHA-256 del enlace de activación (un uso)
    token_activacion_vence = db.Column(db.DateTime(timezone=True))
    ultimo_acceso = db.Column(db.DateTime(timezone=True))
    guia_vista = db.Column(db.Boolean, nullable=False, default=False, server_default="false")   # terminó la guía de inicio
    creado = db.Column(db.DateTime(timezone=True), default=ahora, nullable=False)
    __table_args__ = (CheckConstraint(f"rol IN {ROLES}", name="ck_usuario_rol"),
                      CheckConstraint("(rol = 'Maestro') = (empresa_id IS NULL)", name="ck_usuario_empresa"))


class Bitacora(db.Model):
    __tablename__ = "bitacora"
    id = db.Column(db.BigInteger, primary_key=True)
    ts = db.Column(db.DateTime(timezone=True), default=ahora, nullable=False)
    empresa_id = db.Column(db.Integer, index=True)
    usuario = db.Column(db.Text, nullable=False)
    accion = db.Column(db.Text, nullable=False)
    detalle = db.Column(db.Text)
    ip = db.Column(db.Text)


class Parametros(db.Model):
    """Parámetros de la plataforma (una fila): alícuotas de IVA y tasa BCV vigente. Solo los edita el Maestro."""
    __tablename__ = "parametros"
    id = db.Column(db.Integer, primary_key=True, default=1)
    iva_general = db.Column(M, nullable=False, default=16)
    iva_reducida = db.Column(M, nullable=False, default=8)
    iva_adicional = db.Column(M, nullable=False, default=15)
    tasa_usd = db.Column(db.Numeric(18, 6))
    tasa_eur = db.Column(db.Numeric(18, 6))
    tasa_fecha = db.Column(db.Date)
    tasa_fuente = db.Column(db.Text)
    tasa_manual = db.Column(db.Boolean, nullable=False, default=False)
    __table_args__ = (CheckConstraint("id = 1", name="ck_parametros_unica"),)


# ====================================================================== WhatsApp
class WhatsappPasarela(db.Model):
    """Pasarela OpenWA PROPIA de este bot (una fila). La clave de API va cifrada (Fernet)."""
    __tablename__ = "whatsapp_pasarela"
    id = db.Column(db.Integer, primary_key=True, default=1)
    url = db.Column(db.Text, nullable=False)
    api_key_cifrada = db.Column(db.Text, nullable=False)
    activa = db.Column(db.Boolean, nullable=False, default=True)
    actualizado = db.Column(db.DateTime(timezone=True), default=ahora, onupdate=ahora, nullable=False)
    actualizado_por = db.Column(db.Text)
    __table_args__ = (CheckConstraint("id = 1", name="ck_pasarela_unica"),)


class WhatsappConfig(db.Model):
    __tablename__ = "whatsapp_config"
    empresa_id = db.Column(db.ForeignKey("empresas.id"), primary_key=True)
    sesion_id = db.Column(db.Text)
    sesion_estado = db.Column(db.Text)
    sesion_actualizada = db.Column(db.DateTime(timezone=True))
    numero_visible = db.Column(db.Text)
    secreto_cifrado = db.Column(db.Text, nullable=False)           # firma de los avisos de la pasarela
    clave_webhook = db.Column(db.Text, nullable=False, unique=True)  # parte secreta de la URL del aviso
    bienvenida = db.Column(db.Text)
    instrucciones_pago = db.Column(db.Text)
    presupuesto_modo = db.Column(db.Text, nullable=False, default="automatico")
    activo = db.Column(db.Boolean, nullable=False, default=True)


class Contacto(db.Model):
    __tablename__ = "contactos"
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.ForeignKey("empresas.id"), nullable=False, index=True)
    telefono = db.Column(db.Text, nullable=False)
    nombre = db.Column(db.Text)
    tipo = db.Column(db.Text, nullable=False, default="nuevo")
    rif = db.Column(db.Text)
    codigo_profit = db.Column(db.Text)                          # co_cli si se reconoció en Profit
    estado = db.Column(db.JSON, nullable=False, default=dict)    # conversación en curso (paso, carrito, oferta)
    atencion = db.Column(db.Boolean, nullable=False, default=False)   # pidió hablar con una persona
    ultimo_mensaje = db.Column(db.DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("empresa_id", "telefono", name="uq_contacto"),
                      CheckConstraint("tipo IN ('nuevo','cliente','proveedor','bloqueado')", name="ck_contacto_tipo"))


class Mensaje(db.Model):
    __tablename__ = "mensajes"
    id = db.Column(db.BigInteger, primary_key=True)
    empresa_id = db.Column(db.ForeignKey("empresas.id"), nullable=False, index=True)
    contacto_id = db.Column(db.ForeignKey("contactos.id"), index=True)
    telefono = db.Column(db.Text, nullable=False)
    direccion = db.Column(db.String(3), nullable=False)        # in | out
    texto = db.Column(db.Text, nullable=False)
    wamid = db.Column(db.Text, unique=True)                    # un aviso repetido se procesa una sola vez
    resultado = db.Column(db.Text)                             # envío: ok / motivo del error
    ts = db.Column(db.DateTime(timezone=True), default=ahora, nullable=False, index=True)


class Pedido(db.Model):
    """Pedido de un cliente (orden de venta con lo disponible) u oferta de un proveedor. La factura se emite en Profit."""
    __tablename__ = "pedidos"
    id = db.Column(db.BigInteger, primary_key=True)
    empresa_id = db.Column(db.ForeignKey("empresas.id"), nullable=False, index=True)
    numero = db.Column(db.Integer, nullable=False)
    tipo = db.Column(db.Text, nullable=False, default="venta")      # venta | compra
    origen = db.Column(db.Text, nullable=False, default="whatsapp")  # whatsapp | whatsapp_pdf
    contacto_id = db.Column(db.ForeignKey("contactos.id"))
    telefono = db.Column(db.Text)
    nombre = db.Column(db.Text)
    rif = db.Column(db.Text)
    codigo_cliente = db.Column(db.Text)                        # co_cli de Profit, para cargarlo allá
    lineas = db.Column(db.JSON, nullable=False, default=list)   # [{codigo, descripcion, cantidad, precio, alicuota}]
    total = db.Column(M, nullable=False, default=0)            # referencial en Bs, sin IVA
    nota = db.Column(db.Text)
    referencia_cliente = db.Column(db.Text)                    # N° de la orden de compra que envió el cliente
    estado = db.Column(db.Text, nullable=False, default="Nuevo")
    creado_en = db.Column(db.DateTime(timezone=True), default=ahora, nullable=False, index=True)
    __table_args__ = (UniqueConstraint("empresa_id", "numero", name="uq_pedido_numero"),
                      CheckConstraint("tipo IN ('venta','compra')", name="ck_pedido_tipo"),
                      CheckConstraint(f"estado IN {ESTADOS_PEDIDO}", name="ck_pedido_estado"))


class Presupuesto(db.Model):
    __tablename__ = "presupuestos"
    id = db.Column(db.BigInteger, primary_key=True)
    empresa_id = db.Column(db.ForeignKey("empresas.id"), nullable=False, index=True)
    numero = db.Column(db.Integer, nullable=False)
    fecha = db.Column(db.Date, nullable=False)
    valido_hasta = db.Column(db.Date, nullable=False)
    contacto_id = db.Column(db.ForeignKey("contactos.id"))
    cliente_nombre = db.Column(db.Text, nullable=False)
    cliente_rif = db.Column(db.Text)
    cliente_telefono = db.Column(db.Text)
    lineas = db.Column(db.JSON, nullable=False, default=list)
    exento = db.Column(M, nullable=False, default=0)
    base = db.Column(M, nullable=False, default=0)
    iva = db.Column(M, nullable=False, default=0)
    total = db.Column(M, nullable=False, default=0)
    estado = db.Column(db.Text, nullable=False, default="Borrador")
    creado_en = db.Column(db.DateTime(timezone=True), default=ahora, nullable=False)
    __table_args__ = (UniqueConstraint("empresa_id", "numero", name="uq_presupuesto_numero"),
                      CheckConstraint(f"estado IN {ESTADOS_PRESUPUESTO}", name="ck_presupuesto_estado"))


# ====================================================================== Profit Plus (solo lectura)
class ProfitPlataforma(db.Model):
    __tablename__ = "profit_plataforma"
    id = db.Column(db.Integer, primary_key=True, default=1)
    activa = db.Column(db.Boolean, nullable=False, default=True)
    url_publica = db.Column(db.Text)
    url_instalador = db.Column(db.Text)
    intervalo_min = db.Column(db.Integer, nullable=False, default=15)
    max_articulos = db.Column(db.Integer, nullable=False, default=20000)
    max_clientes = db.Column(db.Integer, nullable=False, default=50000)
    actualizado = db.Column(db.DateTime(timezone=True), default=ahora, onupdate=ahora, nullable=False)
    actualizado_por = db.Column(db.Text)
    __table_args__ = (CheckConstraint("id = 1", name="ck_profit_plataforma_unica"),
                      CheckConstraint("intervalo_min BETWEEN 5 AND 1440", name="ck_profit_intervalo"))


class ProfitConexion(db.Model):
    __tablename__ = "profit_conexion"
    empresa_id = db.Column(db.ForeignKey("empresas.id"), primary_key=True)
    conector_id = db.Column(db.Text, nullable=False, unique=True)
    secreto_cifrado = db.Column(db.Text, nullable=False)
    activo = db.Column(db.Boolean, nullable=False, default=True)
    almacenes = db.Column(db.JSON, nullable=False, default=list)
    lista_precio = db.Column(db.Text, nullable=False, default="01")
    restar_comprometido = db.Column(db.Boolean, nullable=False, default=True)
    solo_con_existencia = db.Column(db.Boolean, nullable=False, default=False)
    disponibles = db.Column(db.JSON, nullable=False, default=dict)
    ultima_sync = db.Column(db.DateTime(timezone=True))
    ultimo_contacto = db.Column(db.DateTime(timezone=True))
    ultimo_estado = db.Column(db.Text)
    ultimo_error = db.Column(db.Text)
    agente_version = db.Column(db.Text)
    agente_equipo = db.Column(db.Text)
    n_articulos = db.Column(db.Integer, nullable=False, default=0)
    n_clientes = db.Column(db.Integer, nullable=False, default=0)
    creado = db.Column(db.DateTime(timezone=True), default=ahora, nullable=False)


class ProfitArticulo(db.Model):
    __tablename__ = "profit_articulos"
    empresa_id = db.Column(db.ForeignKey("empresas.id"), primary_key=True)
    codigo = db.Column(db.Text, primary_key=True)
    nombre = db.Column(db.Text, nullable=False)
    linea = db.Column(db.Text)
    alicuota = db.Column(db.String(1), nullable=False, default="G")
    precio = db.Column(Q, nullable=False, default=0)
    moneda = db.Column(db.String(3), nullable=False, default="VES")
    existencia = db.Column(Q, nullable=False, default=0)
    por_almacen = db.Column(db.JSON, nullable=False, default=dict)
    __table_args__ = (CheckConstraint("alicuota IN ('G','R','A','E') AND moneda IN ('VES','USD','EUR')", name="ck_profit_art"),)


class ProfitCliente(db.Model):
    __tablename__ = "profit_clientes"
    empresa_id = db.Column(db.ForeignKey("empresas.id"), primary_key=True)
    codigo = db.Column(db.Text, primary_key=True)
    nombre = db.Column(db.Text, nullable=False)
    rif = db.Column(db.Text, index=True)
    direccion = db.Column(db.Text)
    telefonos = db.Column(db.Text)
    whatsapp = db.Column(db.Text, index=True)
    email = db.Column(db.Text)


class ProfitSincronizacion(db.Model):
    __tablename__ = "profit_sincronizaciones"
    id = db.Column(db.BigInteger, primary_key=True)
    empresa_id = db.Column(db.ForeignKey("empresas.id"), nullable=False, index=True)
    ts = db.Column(db.DateTime(timezone=True), default=ahora, nullable=False)
    resultado = db.Column(db.Text, nullable=False)
    articulos = db.Column(db.Integer, nullable=False, default=0)
    clientes = db.Column(db.Integer, nullable=False, default=0)
    duracion_ms = db.Column(db.Integer)
    detalle = db.Column(db.Text)
    ip = db.Column(db.Text)
