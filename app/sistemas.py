"""Sistemas administrativos que Surtia puede leer (SOLO LECTURA) y la «foto» normalizada que entrega cada uno.

La conexión la registra y configura el equipo de Surtia (Maestro) por empresa: el cliente no instala ni configura nada.
Cada sistema tiene un modo de lectura:
  agente  → conector de Windows en la red del cliente que envía la foto firmada (Profit Plus u otro ERP con SQL Server)
  nube    → el servidor de Surtia consulta la API del sistema cada N minutos (Odoo, WooCommerce, hoja en línea)
  archivo → el equipo de Surtia sube un Excel/CSV con el catálogo
  api     → el sistema del cliente (o su proveedor) envía la foto firmada a la API de Surtia
Todos terminan en el mismo formato que valida profit.validar_foto, así el bot no cambia.

Lógica PURA: la red entra por un `transporte` inyectable (pruebas) y nunca toca la base de datos."""
import base64
import csv
import io
import ipaddress
import json
import re
import socket
import urllib.error
import urllib.parse
import urllib.request

# ====================================================================== catálogo de sistemas
# campos: (nombre, etiqueta, tipo, opciones|None, ayuda|None)   tipo: text | number | select | textarea | password
MONEDAS = [("VES", "Bolívares (Bs)"), ("USD", "Dólares (USD, se muestran en Bs a la tasa BCV)"), ("EUR", "Euros (EUR, en Bs a la tasa BCV)")]
ALICUOTAS = [("G", "General"), ("R", "Reducida"), ("A", "Adicional (lujo)"), ("E", "Exento")]
_MAPEO = [("col_codigo", "Columna del código", "text", None, "Vacío = se detecta sola (código, sku, referencia…)"),
          ("col_nombre", "Columna de la descripción", "text", None, "Vacío = se detecta sola (descripción, nombre, producto…)"),
          ("col_precio", "Columna del precio", "text", None, "Vacío = se detecta sola (precio, pvp, monto…)"),
          ("col_existencia", "Columna de la existencia", "text", None, "Vacío = se detecta sola (existencia, stock, cantidad…)"),
          ("col_alicuota", "Columna del IVA (G/R/A/E, 16, 8, exento…)", "text", None, "Opcional"),
          ("col_moneda", "Columna de la moneda", "text", None, "Opcional"),
          ("moneda", "Moneda de los precios (si no hay columna)", "select", MONEDAS, None),
          ("alicuota", "IVA por defecto (si no hay columna)", "select", ALICUOTAS, None)]

SISTEMAS = {
    "profit": dict(nombre="Profit Plus", modo="agente", estado="Probado",
                   resumen="Profit Plus 2K12 sobre SQL Server. Se instala el conector de solo lectura en el servidor de Profit del cliente.",
                   credenciales=[], opciones=[]),
    "sql": dict(nombre="Otro sistema con SQL Server", modo="agente", estado="Beta",
                resumen="Cualquier sistema administrativo con base de datos SQL Server: el mismo conector, con consultas a la medida de sus tablas.",
                credenciales=[], opciones=[
                    ("q_articulos", "Consulta de artículos → co_art, art_des, co_lin, tipo_imp", "textarea", None, None),
                    ("q_stock", "Consulta de existencias → co_art, co_alma, tipo (ACT/COM), stock", "textarea", None, None),
                    ("q_precios", "Consulta de precios (? = lista) → co_art, co_alma, monto, co_mone, desde", "textarea", None, None),
                    ("q_clientes", "Consulta de clientes → co_cli, cli_des, rif, direc1, telefonos, email", "textarea", None, None),
                    ("q_almacenes", "Consulta de almacenes → co_alma, des_alma, noventa (1 = no es de venta)", "textarea", None, None),
                    ("q_listas", "Consulta de listas de precio → co_precio, des_precio", "textarea", None, None)]),
    "odoo": dict(nombre="Odoo", modo="nube", estado="Beta",
                 resumen="Odoo (en línea o propio) por su API JSON-RPC: productos con existencia y precio de venta, y clientes.",
                 credenciales=[("clave", "Clave API del usuario de Odoo (Preferencias → Seguridad → Claves API)", "password", None, None)],
                 opciones=[("url", "Dirección de Odoo (https://empresa.odoo.com)", "text", None, None), ("base_datos", "Base de datos", "text", None, None),
                           ("usuario", "Usuario (correo) de solo lectura", "text", None, None),
                           ("almacen", "ID del almacén para la existencia", "text", None, "Vacío = todos los almacenes"),
                           ("moneda", "Moneda de los precios en Odoo", "select", MONEDAS, None)]),
    "woocommerce": dict(nombre="WooCommerce", modo="nube", estado="Beta",
                        resumen="Tienda WooCommerce (WordPress) por su API REST: productos publicados con precio y existencia, y clientes.",
                        credenciales=[("ck", "Clave de cliente (ck_…), permiso de LECTURA", "password", None, None), ("cs", "Secreto de cliente (cs_…)", "password", None, None)],
                        opciones=[("url", "Dirección de la tienda (https://tienda.com)", "text", None, None),
                                  ("moneda", "Moneda de la tienda", "select", MONEDAS, None),
                                  ("sin_control", "Existencia de productos sin control de inventario", "number", None, "Si «En existencia» pero sin cantidad: este número (0 = no ofrecer)")]),
    "hoja": dict(nombre="Hoja de cálculo en línea (Google Sheets / CSV)", modo="nube", estado="Beta",
                 resumen="Una hoja de Google publicada como CSV (Archivo → Compartir → Publicar en la Web → CSV) o cualquier enlace a un CSV.",
                 credenciales=[("enlace", "Enlace del CSV publicado (https://…)", "password", None, "Se guarda cifrado: quien tenga el enlace ve la hoja")],
                 opciones=_MAPEO),
    "archivo": dict(nombre="Archivo Excel o CSV", modo="archivo", estado="Beta",
                    resumen="Para cualquier sistema que exporte a Excel: el equipo de Surtia sube el catálogo y se reemplaza completo.",
                    credenciales=[], opciones=_MAPEO),
    "api": dict(nombre="API de Surtia (integración propia)", modo="api", estado="Beta",
                resumen="El sistema del cliente o su proveedor envía la foto firmada a la API de Surtia. Se entrega el ID y la clave al integrador.",
                credenciales=[], opciones=[("nombre_sistema", "Nombre del sistema del cliente (para las pantallas)", "text", None, "Ej.: Saint, A2, sistema propio")]),
}
MODOS = {"agente": "Conector en la red del cliente", "nube": "Surtia lo consulta por Internet", "archivo": "Carga de archivo", "api": "API de entrada"}


def nombre(sistema: str | None, opciones: dict | None = None) -> str:
    """Nombre visible del sistema de la empresa («Profit Plus», «Odoo», «su sistema»)."""
    if sistema == "api" and (opciones or {}).get("nombre_sistema"): return str(opciones["nombre_sistema"])[:60]
    if sistema in ("archivo", "hoja", "api", None): return "su sistema"
    return SISTEMAS.get(sistema, {}).get("nombre", "su sistema")


class ErrorFuente(ValueError):
    pass


# ====================================================================== red: solo HTTPS a direcciones públicas
def url_publica(url: str) -> str:
    """Valida que la URL sea https y que su servidor NO sea una dirección interna (evita que se use para leer la red privada)."""
    url = (url or "").strip()
    p = urllib.parse.urlsplit(url)
    if p.scheme != "https" or not p.hostname: raise ErrorFuente("La dirección debe empezar por https://")
    if p.username or p.password: raise ErrorFuente("La dirección no debe llevar usuario ni clave")
    try:
        ips = {a[4][0] for a in socket.getaddrinfo(p.hostname, p.port or 443, proto=socket.IPPROTO_TCP)}
    except socket.gaierror as e:
        raise ErrorFuente(f"No se encontró el servidor «{p.hostname}»") from e
    for ip in ips:
        d = ipaddress.ip_address(ip.split("%")[0])
        if not d.is_global or d.is_multicast: raise ErrorFuente("La dirección apunta a una red interna: no está permitida")
    return url


class _Redireccion(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        url_publica(newurl)                               # cada salto también debe ser público
        return super().redirect_request(req, fp, code, msg, headers, newurl)


MAX_RESPUESTA = 30 * 1024 * 1024


def transporte_http(metodo: str, url: str, cuerpo: bytes | None = None, cabeceras: dict | None = None, timeout=40) -> tuple[int, bytes]:
    url_publica(url)
    req = urllib.request.Request(url, cuerpo, {"User-Agent": "Surtia/1.0 (lectura de catálogo)", **(cabeceras or {})}, method=metodo)
    try:
        with urllib.request.build_opener(_Redireccion()).open(req, timeout=timeout) as r:  # noqa: S310 (https público validado)
            datos = r.read(MAX_RESPUESTA + 1)
            if len(datos) > MAX_RESPUESTA: raise ErrorFuente("La respuesta del sistema es demasiado grande")
            return r.status, datos
    except urllib.error.HTTPError as e:
        return e.code, e.read(4000) if e.fp else b""
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ErrorFuente(f"No se pudo conectar ({getattr(e, 'reason', e)})") from e


# ====================================================================== números y columnas
def numero(v, coma: bool | None = None) -> str:
    """«1.234,56», «1,234.56», «Bs 45,5», «$3.10», 12 → texto decimal con punto. Vacío → «0».
    `coma`: el archivo usa coma decimal (True) o punto decimal (False); así «1.200» es mil doscientos y no 1,2."""
    if v is None: return "0"
    if isinstance(v, (int, float)): return repr(v) if isinstance(v, float) else str(v)
    s = re.sub(r"[^\d,.\-]", "", str(v))
    if not s or s in "-.,": return "0"
    if coma is True and "," not in s: return s.replace(".", "")
    if coma is False and "." not in s: return s.replace(",", "")
    if "," in s and "." in s:
        s = s.replace(".", "").replace(",", ".") if s.rfind(",") > s.rfind(".") else s.replace(",", "")
    elif "," in s:
        s = s.replace(".", "").replace(",", ".") if s.count(",") == 1 else s.replace(",", "")
    elif s.count(".") > 1:
        s = s.replace(".", "")
    return s


def _clave(t) -> str:
    t = str(t or "").strip().lower()
    for a, b in zip("áéíóúñ", "aeioun"): t = t.replace(a, b)
    return re.sub(r"[^a-z0-9]", "", t)


SINONIMOS = {"codigo": ("codigo", "cod", "sku", "referencia", "ref", "coart", "codart", "item", "codigoarticulo", "codproducto", "id"),
             "nombre": ("descripcion", "nombre", "producto", "articulo", "artdes", "desc", "detalle"),
             "precio": ("precio", "pvp", "preciodeventa", "precioventa", "monto", "precio1", "venta", "price"),
             "existencia": ("existencia", "stock", "cantidad", "disponible", "inventario", "exist", "qty", "existencias"),
             "alicuota": ("iva", "alicuota", "impuesto", "tipoimp", "tax"), "moneda": ("moneda", "comone", "divisa", "currency"),
             "linea": ("linea", "categoria", "grupo", "familia", "colin")}


def columnas(encabezados: list, opciones: dict) -> dict:
    """{campo: índice} según el mapeo del Maestro o, si no lo indicó, por sinónimos del encabezado."""
    claves = [_clave(h) for h in encabezados]
    out = {}
    for campo, sin in SINONIMOS.items():
        pedido = _clave(opciones.get(f"col_{campo}"))
        if pedido:
            if pedido not in claves: raise ErrorFuente(f"No hay una columna «{opciones.get(f'col_{campo}')}» en el archivo")
            out[campo] = claves.index(pedido)
        else:
            out[campo] = next((claves.index(s) for s in sin if s in claves), None)
    for campo in ("codigo", "nombre", "precio"):
        if out[campo] is None: raise ErrorFuente(f"No se encontró la columna de {'la descripción' if campo == 'nombre' else 'el ' + campo}: indíquela en las opciones")
    return out


def alicuota_de(v, defecto="G") -> str:
    t = _clave(v)
    if not t: return defecto
    if t in ("g", "r", "a", "e"): return t.upper()
    if t.startswith(("ex", "0", "no")): return "E"
    if t in ("8", "reducida", "r8"): return "R"
    if t in ("31", "15", "adicional", "lujo"): return "A"
    return "G"


def leer_tabla(contenido: bytes, nombre_archivo: str = "") -> tuple[list, list[list]]:
    """Excel (.xlsx) o CSV/TXT → (encabezados, filas). El encabezado es la primera fila con 2 o más celdas."""
    if contenido[:2] == b"PK" or nombre_archivo.lower().endswith((".xlsx", ".xlsm")):
        try:
            import openpyxl
            libro = openpyxl.load_workbook(io.BytesIO(contenido), read_only=True, data_only=True)
            filas = [list(f) for f in libro.worksheets[0].iter_rows(values_only=True)]
        except Exception as e:  # noqa: BLE001 — archivo dañado o con contraseña
            raise ErrorFuente("No se pudo leer el Excel (¿está protegido o dañado? Guárdelo como .xlsx o CSV)") from e
    elif contenido[:4] == b"\xd0\xcf\x11\xe0":
        raise ErrorFuente("Es un Excel antiguo (.xls): ábralo y guárdelo como .xlsx o CSV")
    else:
        try: texto = contenido.decode("utf-8-sig")
        except UnicodeDecodeError: texto = contenido.decode("latin-1")
        muestra = texto[:5000]
        try: sep = csv.Sniffer().sniff(muestra, delimiters=",;\t|").delimiter
        except csv.Error: sep = ";" if muestra.count(";") > muestra.count(",") else ","
        filas = list(csv.reader(io.StringIO(texto), delimiter=sep))
    filas = [[("" if c is None else c) for c in f] for f in filas]
    i = next((n for n, f in enumerate(filas) if sum(1 for c in f if str(c).strip()) >= 2), None)
    if i is None: raise ErrorFuente("El archivo está vacío o no tiene encabezados")
    return [str(c).strip() for c in filas[i]], [f for f in filas[i + 1:] if any(str(c).strip() for c in f)]


def foto_de_tabla(encabezados: list, filas: list[list], opciones: dict) -> dict:
    """Tabla del cliente → foto normalizada (sin clientes: la hoja solo trae el catálogo)."""
    col = columnas(encabezados, opciones)
    toma = lambda f, k: f[col[k]] if col.get(k) is not None and col[k] < len(f) else None
    mon, ali = opciones.get("moneda") or "VES", opciones.get("alicuota") or "G"
    # separador decimal del archivo: si algún precio o existencia trae coma (y no es separador de miles «1,234.5»), la coma es decimal
    textos = [str(toma(f, k)) for f in filas[:2000] for k in ("precio", "existencia") if isinstance(toma(f, k), str)]
    coma = True if any(re.search(r",\d{1,2}$|,\d+$", t.strip()) and not re.search(r"\.\d+$", t.strip()) for t in textos if "," in t) else \
        (False if any(re.search(r"\.\d{1,2}$", t.strip()) for t in textos) else None)
    articulos = []
    for f in filas:
        cod = str(toma(f, "codigo") or "").strip()
        if cod.endswith(".0") and cod[:-2].isdigit(): cod = cod[:-2]          # Excel convierte 1001 en 1001.0
        if not cod: continue
        m = str(toma(f, "moneda") or "").strip().upper()
        articulos.append(dict(codigo=cod, nombre=str(toma(f, "nombre") or cod).strip(), linea=str(toma(f, "linea") or "").strip() or None,
                              alicuota=alicuota_de(toma(f, "alicuota"), ali), precio=numero(toma(f, "precio"), coma),
                              moneda="USD" if m.startswith(("US", "$", "DOL")) else "EUR" if m.startswith(("EU", "€")) else (mon if not m else "VES"),
                              existencia=numero(toma(f, "existencia"), coma) if col.get("existencia") is not None else "0", por_almacen={}))
    return dict(articulos=articulos, clientes=[], disponibles={}, version="tabla")


# ====================================================================== Odoo (JSON-RPC, solo search_read)
def foto_odoo(op: dict, cred: dict, transporte=transporte_http, max_articulos=20000, max_clientes=50000) -> dict:
    url, bd, usuario, clave = (op.get("url") or "").rstrip("/"), op.get("base_datos") or "", op.get("usuario") or "", cred.get("clave") or ""
    if not (url and bd and usuario and clave): raise ErrorFuente("Faltan la dirección, la base de datos, el usuario o la clave de Odoo")

    def rpc(servicio, metodo, *args):
        cuerpo = json.dumps({"jsonrpc": "2.0", "method": "call", "params": {"service": servicio, "method": metodo, "args": list(args)}, "id": 1}).encode()
        st, r = transporte("POST", url + "/jsonrpc", cuerpo, {"Content-Type": "application/json"})
        if st != 200: raise ErrorFuente(f"Odoo respondió {st}")
        try: d = json.loads(r)
        except ValueError as e: raise ErrorFuente("Odoo no devolvió JSON (¿la dirección es correcta?)") from e
        if d.get("error"): raise ErrorFuente("Odoo: " + str((d["error"].get("data") or {}).get("message") or d["error"].get("message"))[:200])
        return d.get("result")

    uid = rpc("common", "authenticate", bd, usuario, clave, {})
    if not uid: raise ErrorFuente("Odoo rechazó el usuario o la clave API")
    ctx = {"warehouse": int(op["almacen"])} if str(op.get("almacen") or "").isdigit() else {}
    leer = lambda modelo, dominio, campos, limite: rpc("object", "execute_kw", bd, uid, clave, modelo, "search_read", [dominio],
                                                       {"fields": campos, "limit": limite, "context": ctx})
    prods = leer("product.product", [["sale_ok", "=", True], ["active", "=", True]], ["default_code", "name", "list_price", "qty_available", "taxes_id", "categ_id"], max_articulos + 1)
    socios = leer("res.partner", [["customer_rank", ">", 0], ["active", "=", True]], ["ref", "name", "vat", "street", "phone", "mobile", "email"], max_clientes + 1)
    mon = op.get("moneda") or "USD"
    articulos = [dict(codigo=(p.get("default_code") or f"OD{p['id']}").strip(), nombre=p.get("name") or "", precio=str(p.get("list_price") or 0), moneda=mon,
                      existencia=str(max(p.get("qty_available") or 0, 0)), alicuota="G" if p.get("taxes_id") else "E",
                      linea=(p.get("categ_id") or [None, None])[1] if isinstance(p.get("categ_id"), list) else None, por_almacen={}) for p in prods or []]
    clientes = [dict(codigo=(s.get("ref") or f"P{s['id']}"), nombre=s.get("name") or "", rif=s.get("vat") or None, direccion=s.get("street") or None,
                     telefonos=" / ".join(x for x in (s.get("mobile"), s.get("phone")) if x) or None, email=s.get("email") or None) for s in socios or []]
    return dict(articulos=articulos, clientes=clientes, disponibles={}, version="odoo")


# ====================================================================== WooCommerce (REST v3, solo GET)
def foto_woocommerce(op: dict, cred: dict, transporte=transporte_http, max_articulos=20000, max_clientes=50000) -> dict:
    url, ck, cs = (op.get("url") or "").rstrip("/"), cred.get("ck") or "", cred.get("cs") or ""
    if not (url and ck and cs): raise ErrorFuente("Faltan la dirección o las claves de WooCommerce")
    auth = {"Authorization": "Basic " + base64.b64encode(f"{ck}:{cs}".encode()).decode(), "Accept": "application/json"}

    def todas(ruta, maximo, extra=""):
        out, pagina = [], 1
        while len(out) <= maximo and pagina <= 400:
            st, r = transporte("GET", f"{url}/wp-json/wc/v3/{ruta}?per_page=100&page={pagina}{extra}", None, auth)
            if st in (401, 403): raise ErrorFuente("WooCommerce rechazó las claves (deben tener permiso de lectura)")
            if st != 200: raise ErrorFuente(f"WooCommerce respondió {st}")
            try: lote = json.loads(r)
            except ValueError as e: raise ErrorFuente("WooCommerce no devolvió JSON (¿la dirección es correcta?)") from e
            if not isinstance(lote, list) or not lote: break
            out += lote; pagina += 1
            if len(lote) < 100: break
        return out

    sin_control = numero(op.get("sin_control") or "0")
    prods = todas("products", max_articulos, "&status=publish")
    articulos = []
    for p in prods:
        if p.get("type") == "variable": continue                         # las variantes se leerán en una versión siguiente
        existencia = p.get("stock_quantity") if p.get("manage_stock") else (sin_control if p.get("stock_status") == "instock" else 0)
        articulos.append(dict(codigo=(p.get("sku") or f"WC{p['id']}").strip(), nombre=p.get("name") or "", precio=str(p.get("price") or 0), moneda=op.get("moneda") or "USD",
                              existencia=str(max(float(existencia or 0), 0)), alicuota="E" if p.get("tax_status") == "none" else "G",
                              linea=((p.get("categories") or [{}])[0].get("name") or None), por_almacen={}))
    clis = todas("customers", max_clientes)
    clientes = []
    for c in clis:
        b = c.get("billing") or {}
        clientes.append(dict(codigo=f"WC{c['id']}", nombre=(b.get("company") or f"{c.get('first_name', '')} {c.get('last_name', '')}".strip() or c.get("email") or f"WC{c['id']}"),
                             rif=None, direccion=b.get("address_1") or None, telefonos=b.get("phone") or None, email=c.get("email") or None))
    return dict(articulos=articulos, clientes=clientes, disponibles={}, version="woocommerce")


def foto_hoja(op: dict, cred: dict, transporte=transporte_http, **_) -> dict:
    enlace = (cred.get("enlace") or "").strip()
    if not enlace: raise ErrorFuente("Falta el enlace del CSV publicado")
    st, r = transporte("GET", enlace, None, {"Accept": "text/csv"})
    if st != 200: raise ErrorFuente(f"La hoja respondió {st} (¿está publicada en la Web como CSV?)")
    if r[:200].lstrip().lower().startswith((b"<!doctype", b"<html")): raise ErrorFuente("El enlace abre una página, no un CSV: publíquela como «Valores separados por comas (.csv)»")
    return foto_de_tabla(*leer_tabla(r, "hoja.csv"), op)


LECTORES = {"odoo": foto_odoo, "woocommerce": foto_woocommerce, "hoja": foto_hoja}


# ====================================================================== consultas del conector (sistemas con SQL Server)
def consultas_ini(opciones: dict) -> str:
    """Sección [consultas] del .ini para «Otro sistema con SQL Server» (vacía = las de Profit Plus 2K12)."""
    lineas = []
    for k in ("almacenes", "listas", "articulos", "stock", "precios", "clientes"):
        q = re.sub(r"\s+", " ", str(opciones.get("q_" + k) or "")).strip()
        if q:
            if not re.match(r"(?i)^\s*(select|with)\b", q) or re.search(r"(?i)\b(insert|update|delete|merge|drop|alter|create|exec|execute|grant|truncate)\b", q):
                raise ErrorFuente(f"La consulta de {k} debe ser solo de lectura (SELECT)")
            lineas.append(f"{k} = {q}")
    return "\n".join(lineas)
