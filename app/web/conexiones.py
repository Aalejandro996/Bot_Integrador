"""Consola Maestro → Empresas → «Sistema»: el equipo de Surtia registra qué sistema administrativo usa cada empresa y lo
configura según su tipo (conector, nube, archivo o API). La empresa no instala ni configura nada: solo ve el estado y su catálogo.
El Maestro ve conteos y estados; la vista previa de un archivo solo muestra lo que él mismo está subiendo."""
from pathlib import Path

from flask import Response, abort, current_app, flash, redirect, request, url_for
from sqlalchemy import delete, func, select

from .. import servicios as SV
from .. import sistemas as SIS
from ..extensions import db
from ..models import Empresa, ProfitArticulo, ProfitCliente, ProfitSincronizacion
from ..seguridad import registrar, requiere
from . import F, bp, campo, hora, intentar, oculto, pagina
from .cliente import atraso

CARPETA_CONECTOR = Path(__file__).resolve().parents[2] / "conector_profit"
# Sin extensión en la URL (.py, .sql, .ini): así ningún filtro de rastreos las confunde con un escaneo.
DESCARGAS = {"conector": ("surtia_conector.py", "text/x-python"), "usuario-sql": ("crear_usuario_lectura.sql", "text/plain"),
             "instrucciones": ("LEAME.md", "text/markdown"), "requisitos": ("requirements.txt", "text/plain")}
OPCIONES_SISTEMA = [(k, f"{v['nombre']} · {SIS.MODOS[v['modo']]}" + ("" if v["estado"] == "Probado" else f" ({v['estado'].lower()})")) for k, v in SIS.SISTEMAS.items()]


def _empresa(eid):
    e = db.session.get(Empresa, eid)
    if e is None: abort(404)
    return e


def _campos_sistema(c, sis):
    """Campos propios del sistema + credenciales (las claves nunca se muestran: vacío = conservar)."""
    op, guardadas = c.opciones or {}, SV.credenciales(c)
    out = []
    for n, l, t, ops, ayuda in sis["opciones"]:
        out.append(campo(n, l, t, req=False, opciones=ops, valor=op.get(n, (ops or [[None]])[0][0] if t == "select" else None), ayuda=ayuda,
                         filas=4 if t == "textarea" else None, ph="Vacía = la de Profit Plus 2K12" if n.startswith("q_") else None))
    for n, l, t, _, ayuda in sis["credenciales"]:
        out.append(campo(n, l + (" · guardada" if guardadas.get(n) else ""), t, req=False,
                         ayuda=("Déjela vacía para conservar la guardada. " if guardadas.get(n) else "") + (ayuda or "")))
    return out


def _campos_catalogo(c, sis):
    campos = [campo("activo", "Lectura del sistema", "select", opciones=[("1", "Activa"), ("0", "Pausada: el bot no ofrece productos")], valor="1" if c.activo else "0")]
    if sis["modo"] == "agente":
        disp = c.disponibles or {}
        alms = [(x["codigo"], f"{x['codigo']} · {x['nombre']}") for x in disp.get("almacenes") or []]
        listas = [(x["codigo"], f"{x['codigo']} · {x['nombre']}") for x in disp.get("listas_precio") or []]
        campos.append(campo("almacenes", "Almacenes que cuentan para la existencia (ninguno = todos los de venta)", "checks", req=False, opciones=alms, valor=c.almacenes) if alms
                      else campo("almacenes_txt", "Almacenes (códigos separados por coma; vacío = todos los de venta)", req=False, valor=", ".join(c.almacenes or []),
                                 ayuda="La lista aparece cuando el conector se conecta por primera vez"))
        campos += [campo("lista_precio", "Lista de precios de venta", "select", opciones=listas, valor=c.lista_precio) if listas
                   else campo("lista_precio", "Lista de precios (código, p. ej. 01)", req=False, valor=c.lista_precio),
                   campo("restar_comprometido", "Existencia que ve el cliente", "select", opciones=[("1", "Actual menos lo comprometido"), ("0", "Existencia actual")],
                         valor="1" if c.restar_comprometido else "0")]
    campos.append(campo("solo_con_existencia", "Artículos que ofrece el bot", "select", opciones=[("0", "Todos los que tienen precio (los agotados se marcan)"),
                                                                                                 ("1", "Solo los que tienen existencia")], valor="1" if c.solo_con_existencia else "0"))
    return campos


def _guardar_opciones(c, sis):
    c.activo, c.solo_con_existencia = F.get("activo", "1") == "1", F.get("solo_con_existencia") == "1"
    if sis["modo"] == "agente":
        elegidos = [x.strip()[:10] for x in request.form.getlist("almacenes") if x.strip()]
        elegidos += [x.strip()[:10] for x in (F.get("almacenes_txt") or "").replace(";", ",").split(",") if x.strip()]
        c.almacenes = sorted(set(elegidos))[:50]
        c.lista_precio = (F.get("lista_precio") or "01").strip()[:10] or "01"
        c.restar_comprometido = F.get("restar_comprometido", "1") == "1"
    op = dict(c.opciones or {})
    for n, _, t, ops, _ in sis["opciones"]:
        v = (F.get(n) or "").strip()[:4000 if t == "textarea" else 300]
        if t == "select" and v and v not in {o[0] for o in ops}: raise ValueError("Opción inválida")
        if n == "url" and v: v = SIS.url_publica(v.rstrip("/"))
        op[n] = v
    if c.sistema == "sql": SIS.consultas_ini(op)                         # valida que sean de solo lectura
    nuevas = {n: (F.get(n) or "").strip()[:2000] for n, *_ in sis["credenciales"]}
    if nuevas.get("enlace"): SIS.url_publica(nuevas["enlace"])
    c.opciones = op
    SV.guardar_credenciales(c, nuevas)


@bp.route("/maestro/empresas/<int:eid>", methods=["GET", "POST"])
@requiere("Maestro")
def maestro_sistema(eid):
    e, c = _empresa(eid), SV.profit_conexion(eid)
    vista = None
    if request.method == "POST":
        a = F.get("accion")
        try:
            if a == "sistema":
                nuevo = F.get("sistema")
                if nuevo not in SIS.SISTEMAS: raise ValueError("Sistema desconocido")
                if c is None: c = SV.crear_conexion_profit(eid, nuevo)
                elif c.sistema != nuevo:
                    # otro sistema: se cortan la clave y las credenciales del anterior y se borra su catálogo
                    c.sistema, c.opciones, c.credenciales_cifradas, c.disponibles, c.almacenes = nuevo, {}, None, {}, []
                    c.secreto_cifrado = SV.cifrador().cifrar(__import__("secrets").token_urlsafe(32))
                    c.ultima_sync = c.ultimo_error = c.ultimo_estado = c.ultimo_intento = None; c.n_articulos = c.n_clientes = 0
                    db.session.execute(delete(ProfitArticulo).where(ProfitArticulo.empresa_id == eid))
                    db.session.execute(delete(ProfitCliente).where(ProfitCliente.empresa_id == eid))
                registrar("SISTEMA_REGISTRADO", f"{e.razon_social}: {nuevo}", empresa_id=eid); db.session.commit()
                flash(f"Sistema registrado: {SIS.SISTEMAS[nuevo]['nombre']}. Complete ahora sus opciones.")
            elif c is None: raise ValueError("Primero registre el sistema de la empresa")
            elif a == "opciones":
                _guardar_opciones(c, SIS.SISTEMAS[c.sistema])
                registrar("SISTEMA_CONFIGURADO", f"{e.razon_social}: {c.sistema} activo={c.activo}", empresa_id=eid); db.session.commit()
                flash("Opciones guardadas." + (" El conector las aplica en su próxima lectura." if SIS.SISTEMAS[c.sistema]["modo"] == "agente" else ""))
            elif a == "sincronizar":
                r = SV.sincronizar_fuente(eid); registrar("SISTEMA_LEIDO", f"{e.razon_social}: {r['articulos']} artículos", empresa_id=eid); db.session.commit()
                flash(f"Lectura correcta: {r['articulos']} artículo(s) y {r['clientes']} cliente(s)." + (f" {r['omitidos']} sin código válido se omitieron." if r["omitidos"] else ""))
            elif a == "importar":
                f = request.files.get("archivo")
                if f is None or not f.filename: raise ValueError("Elija el archivo Excel o CSV")
                contenido = f.read()
                if F.get("modo") == "vista":
                    vista = SV.importar_tabla(eid, contenido, f.filename, solo_vista=True); vista["archivo"] = f.filename
                else:
                    r = SV.importar_tabla(eid, contenido, f.filename)
                    registrar("CATALOGO_IMPORTADO", f"{e.razon_social}: {f.filename} {r['articulos']} artículos", empresa_id=eid); db.session.commit()
                    flash(f"Catálogo importado: {r['articulos']} artículo(s)." + (f" {r['omitidos']} fila(s) sin código válido se omitieron." if r["omitidos"] else ""))
            elif a == "rotar":
                c.secreto_cifrado = SV.cifrador().cifrar(__import__("secrets").token_urlsafe(32))
                registrar("SISTEMA_CLAVE_ROTADA", e.razon_social, empresa_id=eid); db.session.commit()
                flash("Clave renovada: entregue el archivo nuevo. El conector o la integración anterior deja de funcionar.")
            else: raise ValueError("Acción inválida")
        except ValueError as ex:
            db.session.rollback(); flash(str(ex))
        if vista is None: return redirect(request.url)
    return pagina(f"Sistema de {e.razon_social}", _secciones(e, SV.profit_conexion(eid), vista),
                  acciones=[dict(url=url_for("web.maestro_empresas"), texto="Volver a Empresas", icono="atras")],
                  subtitulo="Usted registra y conecta el sistema; la empresa solo ve su catálogo")


def _secciones(e, c, vista):
    plat = SV.profit_plataforma()
    intervalo = plat.intervalo_min if plat else 15
    eleccion = dict(titulo="Sistema administrativo de la empresa", abierto=c is None,
                    nota="Elija qué sistema usa la empresa. Las opciones de abajo cambian según el sistema. Cambiar de sistema borra el catálogo actual "
                         "y anula la clave del conector o integración anterior.",
                    form=dict(action=request.path, boton="Registrar sistema", confirmar="¿Cambiar el sistema? Se borra el catálogo actual." if c else None,
                              campos=[oculto("sistema"), campo("sistema", "Sistema", "select", opciones=OPCIONES_SISTEMA, valor=c.sistema if c else "profit")]))
    if c is None:
        return [dict(titulo="Sistemas disponibles", tabla=dict(cols=["Sistema", "Cómo se lee", "Estado", "Para qué sirve"], filas=[
            dict(cells=[v["nombre"], SIS.MODOS[v["modo"]], "Activo" if v["estado"] == "Probado" else "Beta", v["resumen"]]) for v in SIS.SISTEMAS.values()])), eleccion]
    sis = SIS.SISTEMAS[c.sistema]
    cuenta = lambda *w: db.session.execute(select(func.count()).select_from(ProfitArticulo).where(ProfitArticulo.empresa_id == e.id, *w)).scalar() or 0
    secs = [dict(kpis=[("Sistema", sis["nombre"], SIS.MODOS[sis["modo"]], "base", "azul"),
                       ("Lectura", ("Activa" if c.activo else "Pausada") + (" · con error" if c.ultimo_estado == "error" else ""), sis["estado"]),
                       ("Último catálogo", atraso(c.ultima_sync, intervalo) if sis["modo"] in ("agente", "nube") else hora(c.ultima_sync), "Fecha de la última foto"),
                       ("Artículos", c.n_articulos, f"{cuenta(ProfitArticulo.existencia > 0)} con existencia", "paquete"),
                       ("Clientes", c.n_clientes, "Reconocidos por su WhatsApp o RIF", "usuarios")]),
            dict(pares=[("Qué es", sis["resumen"]), ("Último error", c.ultimo_error or "—"), ("Último contacto", hora(c.ultimo_contacto)),
                        ("Equipo / versión", f"{c.agente_equipo or '—'} · {c.agente_version or '—'}" if sis["modo"] == "agente" else "—")]),
            eleccion,
            dict(titulo=f"Opciones de {sis['nombre']}", abierto=True, form=dict(action=request.path, boton="Guardar opciones",
                 campos=[oculto("opciones")] + _campos_sistema(c, sis) + _campos_catalogo(c, sis)))]
    secs += _por_modo(e, c, sis, intervalo, vista)
    sincs = db.session.execute(select(ProfitSincronizacion).where(ProfitSincronizacion.empresa_id == e.id).order_by(ProfitSincronizacion.id.desc()).limit(15)).scalars()
    secs.append(dict(titulo="Lecturas recientes", tabla=dict(cols=["Fecha", "Resultado", "Artículos", "Clientes", "Duración", "Detalle"], filas=[
        dict(cells=[hora(s.ts), s.resultado, s.articulos, s.clientes, f"{s.duracion_ms} ms" if s.duracion_ms is not None else "—", s.detalle or ""]) for s in sincs])))
    return secs


def _por_modo(e, c, sis, intervalo, vista):
    url = lambda archivo: url_for("web.maestro_sistema_descarga", eid=e.id, archivo=archivo)
    rotar = dict(titulo="Renovar la clave", nota="Úsela si la clave se filtró o cambió el equipo. Lo anterior deja de funcionar hasta que instale el archivo nuevo.",
                 form=dict(action=request.path, boton="Renovar clave", boton_clase="sec", confirmar="¿Renovar la clave? La conexión actual dejará de funcionar.",
                           campos=[oculto("rotar")]))
    if sis["modo"] == "agente":
        plat = SV.profit_plataforma()
        enlaces = [dict(url=url("configuracion"), texto="surtia_conector.ini (con la clave de esta empresa)")]
        enlaces += [dict(url=url(k), texto=v[0]) for k, v in DESCARGAS.items()]
        if plat and plat.url_instalador: enlaces.insert(0, dict(url=plat.url_instalador, texto="Instalador surtia_conector.exe (Windows)"))
        return [dict(titulo="Instalación del conector (la hace el equipo de Surtia)", abierto=True, enlaces=enlaces,
                     nota="Con el técnico de la empresa, en su servidor: 1) ejecute crear_usuario_lectura.sql en SQL Server (usuario que solo puede LEER"
                          + (", ajústelo a las tablas del sistema" if c.sistema == "sql" else "") + "). 2) Copie surtia_conector.exe y surtia_conector.ini en "
                          "C:\\Surtia. 3) Complete la sección [sqlserver] del .ini. 4) «surtia_conector --probar». 5) «surtia_conector --instalar-tarea» "
                          f"(arranca con Windows y lee cada {intervalo} minutos). No se abren puertos: solo sale por HTTPS."), rotar]
    if sis["modo"] == "nube":
        return [dict(titulo=f"Leer {sis['nombre']} ahora", nota=f"Surtia lo lee solo cada {intervalo} minutos. Guarde primero las opciones y las claves; "
                     "use este botón para probar la conexión.", form=dict(action=request.path, boton="Probar y leer ahora", campos=[oculto("sincronizar")]))]
    if sis["modo"] == "archivo":
        secs = [dict(titulo="Subir el catálogo (Excel o CSV)", abierto=True,
                     nota="Primera fila = encabezados. Se detectan solas las columnas de código, descripción, precio y existencia (o indíquelas arriba). "
                          "Importar reemplaza el catálogo completo; si el archivo tiene un error, se queda el anterior.",
                     form=dict(action=request.path, boton="Continuar", campos=[oculto("importar"), campo("archivo", "Archivo (.xlsx o .csv, hasta 10 MB)", "file",
                               accept=".xlsx,.xlsm,.csv,.txt"), campo("modo", "Qué hacer", "select", opciones=[("vista", "Ver vista previa sin importar"), ("importar", "Importar")])]))]
        if vista:
            secs.insert(0, dict(titulo=f"Vista previa de {vista['archivo']}", nota=f"{vista['filas']} filas · {len(vista['articulos'])} artículos válidos"
                                + (f" · {vista['omitidos']} sin código válido (con espacios o símbolos)" if vista["omitidos"] else "")
                                + f" · columnas: {', '.join(vista['encabezados'][:15])}. Si se ve bien, súbalo de nuevo con «Importar».",
                                tabla=dict(cols=["Código", "Descripción", "Precio", "Moneda", "Existencia", "IVA"], num=[2, 4], filas=[
                                    dict(cells=[a["codigo"], a["nombre"], f"{a['precio']:,.2f}", a["moneda"], f"{a['existencia'].normalize():f}", a["alicuota"]])
                                    for a in vista["articulos"][:50]])))
        return secs
    base = SV.url_api_profit()
    return [dict(titulo="Datos para el integrador", abierto=True, enlaces=[dict(url=url("credenciales"), texto="surtia_api.txt (ID, clave y protocolo)")],
                 pares=[("URL", base + "/api/fuente/v1/sync"), ("Prueba", base + "/api/fuente/v1/ping"), ("ID (X-Surtia-Conector)", c.conector_id),
                        ("Firma", "X-Surtia-Firma: sha256=HMAC(clave, «fecha.sha256(cuerpo)»)")]), rotar]


@bp.route("/maestro/empresas/<int:eid>/descargar/<archivo>")
@requiere("Maestro")
def maestro_sistema_descarga(eid, archivo):
    e, c = _empresa(eid), SV.profit_conexion(eid)
    if c is None: abort(404)
    if archivo in ("configuracion", "credenciales"):
        if (archivo == "configuracion") != (SIS.SISTEMAS[c.sistema]["modo"] == "agente"): abort(404)
        try: texto = SV.conector_ini(c) if archivo == "configuracion" else _api_txt(c)
        except SIS.ErrorFuente as ex: flash(str(ex)); return redirect(url_for("web.maestro_sistema", eid=eid))
        registrar("SISTEMA_CLAVE_DESCARGADA", f"{e.razon_social}: {archivo}", empresa_id=eid); db.session.commit()
        nombre = "surtia_conector.ini" if archivo == "configuracion" else "surtia_api.txt"
        return Response(texto, mimetype="text/plain; charset=utf-8", headers={"Content-Disposition": f"attachment; filename={nombre}", "Cache-Control": "no-store"})
    if archivo not in DESCARGAS: abort(404)
    nombre, tipo = DESCARGAS[archivo]
    return Response((CARPETA_CONECTOR / nombre).read_bytes(), mimetype=tipo + "; charset=utf-8", headers={"Content-Disposition": f"attachment; filename={nombre}"})


def _api_txt(c) -> str:
    base = SV.url_api_profit()
    return f"""API de Surtia · integración de catálogo (SOLO LECTURA del sistema del cliente)
No comparta este archivo: la clave identifica a la empresa.

URL de prueba : POST {base}/api/fuente/v1/ping
URL de envío  : POST {base}/api/fuente/v1/sync
ID            : {c.conector_id}
Clave         : {SV.cifrador().descifrar(c.secreto_cifrado)}

Cabeceras:
  Content-Type: application/json   (opcional Content-Encoding: gzip)
  X-Surtia-Conector: <ID>
  X-Surtia-Fecha: <segundos Unix>   (la firma vale 5 minutos)
  X-Surtia-Firma: sha256=<hex HMAC-SHA256(clave, "<fecha>." + sha256_hex(cuerpo tal cual viaja))>

Cuerpo de /sync (la foto reemplaza COMPLETA a la anterior):
{{"articulos": [{{"codigo": "HAR-01", "nombre": "HARINA PAN 1KG", "precio": "45.50", "moneda": "VES|USD|EUR",
                 "existencia": "120", "alicuota": "G|R|A|E", "linea": "VIVERES", "por_almacen": {{"01": "120"}}}}],
 "clientes":  [{{"codigo": "C001", "nombre": "COMERCIAL ORITUCO CA", "rif": "J-12345678-9", "direccion": "...",
                 "telefonos": "0414-7778899", "email": "compras@cliente.com"}}],
 "version": "1.0", "equipo": "nombre del sistema"}}
Códigos: letras, números y . - _ / # (sin espacios), hasta 40 caracteres.
Respuesta: {{"ok": true, "articulos": N, "clientes": N, "config": {{...}}}}
"""
