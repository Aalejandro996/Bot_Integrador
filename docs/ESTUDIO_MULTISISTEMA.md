# Surtia™ más allá de Profit Plus: estudio de factibilidad

> **Actualización 5 nov 2026 — fase 1 implementada (beta):** el Maestro registra el sistema de cada empresa y lo configura
> (Profit Plus, otro SQL Server, Odoo, WooCommerce, hoja en línea, Excel/CSV y API). La empresa no configura conexiones. Ver el README,
> sección «Sistemas de cada empresa». Pendiente: validar Odoo y WooCommerce con una instancia real, variantes de WooCommerce,
> `ConfigEmpresa` (tono, horario) y la API oficial de WhatsApp.
>
> Estado original: **exploración**. Fecha: 5 nov 2026. Sirve de base técnica para el desarrollo y de fuente
> veraz para la sesión que preparará la promoción (redes sociales, posts). Lo marcado *«por validar»* no debe
> anunciarse hasta comprobarlo con un cliente real.

## 1. Conclusión
**Sí es factible, y con poco riesgo.** El corazón de Surtia ya no depende de Profit:

| Parte | ¿Depende de Profit? | Detalle |
|---|---|---|
| Conversación del bot (`app/bot.py`) | **No** | Trabaja con un catálogo genérico `{código: (nombre, precio Bs, existencia)}`, clientes y alícuotas. |
| Pedidos, presupuestos, órdenes de venta, lectura de PDF | **No** | Usan ese mismo catálogo. |
| WhatsApp, correo, usuarios, MFA, consola Maestro | **No** | |
| Protocolo de la «foto» (`app/profit.py`) | **Casi no** | Es un JSON firmado (HMAC) con artículos, existencias, clientes, almacenes y listas: sirve para cualquier sistema. |
| Conector Windows (`conector_profit/`) | **Sí** | Las consultas SQL son de las tablas de Profit Plus. |
| Modelos y pantallas | **De nombre** | `ProfitConexion`, `ProfitArticulo`, «Facturado en Profit», CSV para Profit. |

El trabajo es sobre todo **cambiar «Profit» por «fuente de datos»** y escribir un adaptador por cada sistema nuevo.

## 2. Arquitectura propuesta: «fuentes de datos» por empresa
Cada empresa elige **una fuente**. Todas entregan la **misma foto normalizada**, así el bot no cambia.

```
            ┌─ A. Agente local (Windows) ─ perfiles SQL: Profit Plus · otros ERP con SQL Server/Firebird*
 Fuente ────┼─ B. Nube (el servidor consulta la API) ─ Odoo · WooCommerce · Shopify · Google Sheets*
 (por       ├─ C. Archivo ─ el cliente sube Excel/CSV desde el panel (o por correo)
  empresa)  └─ D. API de entrada ─ el sistema del cliente envía la foto firmada (ya existe: /api/profit/sync)
                       │
                       ▼
        Foto normalizada {artículos, existencias por almacén, precios/listas, clientes, alícuotas}
                       │
                       ▼
        Catálogo del bot · pedidos · presupuestos · avisos   (sin cambios)
```
\* *por validar* sistema por sistema.

**A. Agente local**: el mismo `surtia_conector.exe` con **perfiles** (`[perfil] sistema = profit | otro`) que traen
sus consultas SQL. Sirve para ERP instalados en la red del cliente. Ventaja: no se abren puertos; solo lectura.

**B. Nube**: un trabajo programado en Render consulta la API del sistema cada N minutos con credenciales que guarda
la empresa, cifradas con Fernet como los demás secretos. No hay que instalar nada.

**C. Archivo**: la opción universal para cualquier sistema que exporte a Excel. Es ideal para vender a negocios
pequeños y para pilotos: el cliente sube su lista de precios y su inventario. Se valida, se muestra una vista previa y
se reemplaza la foto completa en una sola operación (igual que hoy).

**D. API de entrada**: ya existe. Se documenta como «API pública de Surtia» para que un integrador o el proveedor del
ERP envíe la foto firmada.

## 3. Configuración distinta por empresa
Hoy todo está fijo para Profit. Propuesta de un registro `ConfigEmpresa` (o columnas JSON en `empresas`):

| Grupo | Opciones |
|---|---|
| **Fuente** | tipo (A/B/C/D), sistema, credenciales cifradas, intervalo, mapeo de campos (qué columna es código, nombre, precio, existencia) |
| **Catálogo** | almacenes, lista de precios, moneda base (Bs/USD), mostrar precio en USD además de Bs, ocultar agotados, máximo de productos |
| **Impuestos** | alícuotas por artículo o fija, IVA incluido o no en los precios mostrados |
| **Bot** | módulos activos (pedidos, presupuestos, PDF, proveedores), saludo, tono (formal «usted» / cercano «tú»), horario de atención y mensaje fuera de horario, palabras para «hablar con una persona» |
| **Pedidos** | destino: solo panel, **exportar CSV/Excel en el formato del ERP**, o **crear el pedido en el sistema** (solo fuentes B que lo permitan, con permiso explícito) |
| **Avisos** | a quién llega cada correo, avisos por WhatsApp al encargado |
| **Marca** | logo y colores de la empresa en las órdenes de venta y presupuestos |

El Maestro elige la fuente al crear la empresa; el administrador ajusta el resto desde su panel.

## 4. Cambios técnicos (en orden)
1. **Renombrar** `ProfitConexion/ProfitArticulo/ProfitCliente/ProfitSincronizacion` → `Fuente/Articulo/Cliente/Sincronizacion`, con
   columna `sistema`. Migración: `ALTER TABLE … RENAME` y vistas de compatibilidad. Textos de pantalla: «Facturado en Profit»
   → «Facturado en su sistema» (o el nombre del sistema de la empresa).
2. **Interfaz `Adaptador`**: `probar()`, `foto() -> dict`, `capacidades` (lee clientes, multi-almacén, multi-lista, escribe pedidos).
   El de Profit pasa a ser `adaptadores/profit.py`, y `aplicar_foto` queda igual.
3. **Fuente C (Excel/CSV)**: pantalla de carga con mapeo de columnas y vista previa. Es la que más mercado abre con menos
   esfuerzo, así que conviene hacerla primero.
4. **Fuente B**: tarea programada (cron de Render o hilo con bloqueo en la base) y un adaptador por API.
5. **`ConfigEmpresa`** con las opciones de §3, empezando por moneda, horario y tono del bot.
6. **Exportación de pedidos por sistema** (plantillas de columnas por ERP).

Estimación orientativa: 1–2 semanas para 1+2+3 y 1 semana por cada adaptador de nube. Cada ERP local lleva más tiempo,
porque hay que validar su esquema con una instalación real.

## 5. Candidatos (todos *por validar* salvo Profit)
- **Venezuela, instalados en la red del cliente:** Profit Plus (funciona), y otros ERP administrativos comunes en
  distribuidoras. Para cada uno hay que confirmar con un cliente el motor de base de datos, las tablas de inventario y
  precios, y la licencia que permite leerlas.
- **Nube con API pública documentada:** Odoo, WooCommerce, Shopify, Google Sheets. Son más fáciles: no hay instalación.
- **Universal:** Excel/CSV. Funciona con cualquier sistema que exporte a Excel.

## 6. Riesgos y decisiones abiertas
- **WhatsApp:** hoy se usa una pasarela propia no oficial (OpenWA). Sirve para pilotos, pero WhatsApp puede **bloquear
  números** que la usen de forma masiva. Para escalar y para anunciarlo con tranquilidad, conviene **WhatsApp Business
  Platform (Cloud API) oficial**: tiene costo por conversación y exige plantillas aprobadas para mensajes iniciados por
  la empresa. **Recomendación:** hacer `app/openwa.py` intercambiable por `app/whatsapp_cloud.py`, igual que las fuentes.
- **Escritura en el ERP:** por diseño Surtia es de solo lectura. Escribir pedidos solo con permiso explícito de la empresa y donde
  el sistema lo soporte de forma oficial.
- **Soporte:** cada sistema nuevo agrega casos. Conviene un «modo prueba» del conector (`--probar`) por perfil.
- **Marca:** usar **™** hasta el registro ante el SAPI; no usar nombres ni logos de terceros en la publicidad sin permiso
  (decir «compatible con» solo cuando esté probado).

## 7. Para la sesión de promoción (hechos que sí se pueden decir hoy)
**Qué es:** Surtia™ es un asistente de ventas por WhatsApp para distribuidoras y comercios. Atiende a los clientes a
cualquier hora, con el catálogo, las existencias y los precios reales del sistema administrativo, y deja los pedidos listos
para facturar. Es una marca de Gigadevs Corp (Venezuela).

**Lo que hace hoy (probado):**
- Conversa de forma natural: saluda por el nombre, entiende «tienen harina?», «quiero 3 harina pan», «eso es todo».
- Muestra precios en Bs (convierte desde USD con la tasa BCV del día) y la existencia real por almacén.
- Toma pedidos, envía presupuestos con enlace al documento y lee órdenes de compra en PDF.
- Avisa al equipo por correo de cada pedido y cuando un cliente pide hablar con una persona.
- Panel web para el equipo: pedidos, conversaciones, presupuestos, con filtros y exportación a Excel.
- Conectado hoy a **Profit Plus**, en **solo lectura** (no modifica nada del sistema).
- Seguridad: verificación en dos pasos obligatoria, datos cifrados, sin abrir puertos en la empresa.

**Lo que NO se debe prometer todavía:** integración con otros sistemas (ya está en beta, pero sin validar con un cliente real), pagos dentro del chat,
facturación fiscal (se sigue emitiendo en el sistema de la empresa), «WhatsApp oficial» (hoy no lo es).

**Público:** distribuidoras de consumo masivo, ferreterías, farmacias, repuestos y mayoristas con catálogo grande y
clientes que ya piden por WhatsApp.

**Beneficios para comunicar:** atención 24/7 sin contratar más personal, menos errores al tomar pedidos (código y
existencia reales), clientes que no esperan respuesta, pedidos ordenados listos para facturar.

**Identidad visual:** nombre «surtia» en minúsculas con la «i» en mandarina; ícono de burbuja de WhatsApp con la «S» y un
punto de pedido nuevo (`app/static/marca/`). Colores: petróleo `#0B2530`, menta `#16C79A`, mandarina `#FF7A45`.
