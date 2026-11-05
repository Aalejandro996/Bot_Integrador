# Surtia · Profit Plus — General de Suministros Orituco

Bot de WhatsApp conectado **en solo lectura** a Profit Plus. Gigadevs Corp. Repositorio **privado**.

Los clientes piden por WhatsApp con el catálogo, las existencias por almacén y los precios de Profit (actualizados cada 15 min).
El bot arma pedidos, envía presupuestos, lee órdenes de compra en PDF y deja **órdenes de venta** con lo disponible.
La factura fiscal se sigue emitiendo en Profit Plus: nada se escribe ni se envía a Profit.

```
 Red del cliente (Windows)                                Render (proyecto «bot_integrador», Ohio)
 ┌───────────────────────────────┐  HTTPS 443, solo SALIDA  ┌──────────────────────┐  red privada  ┌────────────────┐
 │ SQL Server (Profit Plus)      │  foto firmada (HMAC)     │ bot_integrador       │◄─────────────►│ bot-whatsapp   │◄─► WhatsApp
 │   ▲ SELECT (usuario lectura)  │ ───────────────────────► │ (web, consola, API)  │               │ (OpenWA, disco)│
 │ surtia_conector.exe (tarea)   │ ◄──── configuración ──── │          │           │──► Brevo (correo)
 └───────────────────────────────┘                          │   bot-profit-db (PostgreSQL, sin acceso desde Internet)
```

## Marca
**Surtia™** (de «surtir»: nombre, logotipo e ícono originales de Gigadevs Corp) — `app/static/marca/`: `logo.svg` (fondo claro),
`logo-blanco.svg` (fondo oscuro), `logo-icono.svg` (burbuja de WhatsApp con la «S» y el punto de pedido nuevo), `favicon.ico`,
`apple-touch-icon.png`, `icono-192.png` (lo usan los correos), `icono-512.png`.
Paleta: petróleo `#0B2530` (marco), menta `#16C79A` (acción; texto `#04281E` encima), mandarina `#FF7A45` (lo que pide atención),
enlaces `#0A7C77`, fondo `#F3F6F6`. Se usa **™** hasta obtener el registro ante el SAPI (Venezuela); solo entonces puede usarse **®**.

## Correo (igual que Contax)
`MAIL_BACKEND=brevo` con `BREVO_API_KEY` (o `smtp` con `SMTP_*`; `console` en desarrollo). Remitente `MAIL_FROM` verificado en Brevo.
Plantilla HTML propia con estilos en línea y versión de texto (`app/plantilla_correo.py`); todo valor se escapa.
- **Enlaces de activación** (empresa nueva, usuario invitado, nuevo enlace, Maestro inicial): si el correo sale, el enlace ya **no**
  se muestra en pantalla ni en el log; si el usuario no tiene correo o el envío falla, se muestra como antes.
- **Seguridad de la cuenta:** nuevo inicio de sesión (IP y navegador), cuenta bloqueada, contraseña cambiada, MFA activado.
- **Operación** (al correo de la empresa + administradores y operadores): pedido u orden de venta nueva, cliente que pide una
  persona, presupuesto aceptado, WhatsApp desconectado (también al Maestro) y reconectado.
- Pedido nuevo y presupuesto aceptado llevan el **documento completo** (plantilla `documento_html`: líneas, IVA y total).
- Consola Maestro → **Correo**: estado del envío, vista previa de cada plantilla y botón de correo de prueba.
- Los avisos se envían **después** de guardar en la base y en segundo plano: no demoran al bot. Nunca llevan conversaciones.

## Quién hace qué
- **Cliente (Administrador):** WhatsApp (vincula su número con QR, opciones del bot), Usuarios (invita operadores). No configura
  conexiones con su sistema.
- **Cliente (Operador y Administrador):** Panel, Pedidos (cambiar estado y avisar por WhatsApp, exportar CSV para cargar en su sistema),
  Conversaciones (responder, «pidió una persona»), Presupuestos, Catálogo (solo lectura).
- **Gigadevs (Maestro):** Monitoreo (estado del WhatsApp, mensajes recibidos/enviados/fallidos 24 h, pedidos, última foto de Profit,
  alertas) y API de Profit (activar/pausar, URL, intervalo, topes, instalador); Empresas (alta con enlace de activación y sistema a conectar, suspender) y el **Sistema** de cada empresa (tipo y opciones, conector, claves, carga de Excel);
  Pasarela de WhatsApp (URL y clave de OpenWA); Tasa BCV e IVA; Maestros; Bitácora. Ve conteos y estados, no conversaciones ni catálogos.

## Despliegue en Render (una vez)
1. Dashboard → **New → Blueprint** → este repositorio → indique `MAESTRO_USUARIO`, `MAESTRO_EMAIL` y `BREVO_API_KEY` → **Apply**.
   Crea la app web, la pasarela **privada** de WhatsApp con disco y la base de datos sin acceso desde Internet. `SECRET_KEY` y
   `FIELD_ENCRYPTION_KEY` las genera Render (no las cambie después: cifran los secretos guardados).
2. Abra `https://bot-profit-orituco.onrender.com` o revise los logs de `bot_integrador` → el enlace de activación (72 h, un uso) llega a `MAESTRO_EMAIL`; sin correo configurado, cópielo de los logs → cree su clave y su MFA.
3. Logs de `bot-whatsapp` → copie la clave de API de OpenWA → Consola → **Pasarela de WhatsApp** (la URL ya viene puesta).
4. Consola → **Empresas** → nueva empresa con su administrador → envíele el enlace de activación.
5. El cliente vincula su WhatsApp con el QR e instala el conector (`conector_profit/LEAME.md`).

## Seguridad
- Contraseñas Argon2id (≥12 caracteres, 3 clases, sin el usuario); **MFA TOTP obligatorio** para todos; bloqueo de 15 min tras 5
  fallos; el paso intermedio (clave correcta, falta MFA) vence a los 5 min; códigos TOTP de un solo uso.
- Alta solo por enlace de un solo uso (72 h, se guarda su SHA-256); nadie conoce la clave de otro.
- Sesión: cookie `__Host-` Secure, HttpOnly, SameSite=Strict; 30 min de inactividad, 8 h máximo; cambiar la clave, desactivar o
  suspender cierra las sesiones.
- CSRF en todo formulario; CSP estricta sin scripts ni estilos en línea; HSTS, `X-Frame-Options: DENY`, `nosniff`, `no-store`.
- Límites de tasa (login, activación, avisos, API y 600/min general por IP). Errores sin trazas.
- Aislamiento: cada consulta filtra por la empresa del usuario; las rutas de cliente no aceptan otra empresa (404 a lo ajeno).
- WhatsApp: pasarela propia y privada; cada aviso firmado (HMAC-SHA256) y con clave secreta en la URL; idempotente por id.
- Profit: conector con usuario SQL de solo `SELECT` sobre 6 tablas; peticiones firmadas (HMAC, válidas 5 min); la foto se valida y
  reemplaza completa en una transacción; la clave de SQL Server nunca sale de la red del cliente.
- Secretos (MFA, pasarela, conector, avisos) cifrados en reposo con Fernet. CSV exportado protegido contra fórmulas.

## Guía de inicio
Al activar su cuenta, el administrador entra a **Guía de inicio** (también en el menú): 9 pasos con lo que hará el bot, lo que no hace,
cómo vincular WhatsApp, conectar Profit, elegir almacenes y precios, personalizar el saludo, invitar al equipo, atender pedidos y
probar. Cada paso se marca solo según el estado real, y hay una conversación de ejemplo generada por el bot con el catálogo de la
empresa. El operador ve una versión corta. También recibe un correo de bienvenida con los primeros pasos.

## Sistemas de cada empresa (Profit Plus y otros)
**La conexión la registra y la configura el Maestro; la empresa no instala ni configura nada** (solo ve su «Catálogo», en lectura).
Consola Maestro → Empresas → abrir la empresa → «Sistema administrativo»: se elige el sistema y aparecen sus opciones.

| Sistema | Cómo se lee | Opciones que pide |
|---|---|---|
| Profit Plus *(probado)* | Conector de Windows en su servidor | almacenes, lista de precios, existencia comprometida · descarga del `.ini` y del conector |
| Otro sistema con SQL Server *(beta)* | El mismo conector | las 6 consultas SELECT a la medida (se rechaza cualquier escritura) |
| Odoo *(beta)* | Surtia consulta su API JSON-RPC | dirección, base de datos, usuario, clave API (cifrada), almacén, moneda |
| WooCommerce *(beta)* | Surtia consulta su API REST | dirección, claves ck/cs de lectura (cifradas), moneda, existencia sin control de inventario |
| Hoja en línea *(beta)* | Surtia descarga el CSV publicado | enlace (cifrado), columnas, moneda, IVA |
| Archivo Excel/CSV *(beta)* | El Maestro lo sube (vista previa → importar) | columnas (se detectan solas), moneda, IVA |
| API de Surtia *(beta)* | El integrador envía la foto firmada | ID y clave en `surtia_api.txt` · `/api/fuente/v1/{ping,sync}` |

- Los sistemas en la nube se leen solos cada *N* minutos (intervalo de «Monitoreo y API») con un hilo del servidor web
  (`LECTURAS_PROGRAMADAS=1`, candado de PostgreSQL si hay varios procesos). Solo HTTPS a direcciones públicas: se bloquean redes internas,
  también tras redirecciones.
- Cambiar de sistema borra el catálogo anterior y anula la clave del conector anterior. «Facturado en Profit» pasó a «Facturado».
- Lógica de cada sistema: `app/sistemas.py` (pura, probada en `tests/test_sistemas.py`); pantallas: `app/web/conexiones.py`.
- Estudio y pendientes: [`docs/ESTUDIO_MULTISISTEMA.md`](docs/ESTUDIO_MULTISISTEMA.md).

## Tablas
Todas las tablas con más de 5 filas traen búsqueda (sin importar acentos), filtros por columna (estado, cliente, origen…), rango de
fechas Desde/Hasta, orden al tocar el encabezado, paginación de 10/25/50/100, conteo «Mostrando 1–25 de 120» y exportación a
Excel (CSV con «;», protegido contra fórmulas) de lo filtrado. Se recuerda por pestaña. `static/tablas.js`, sin dependencias.

## Carga y errores
- Barra de progreso arriba al cambiar de página o enviar un formulario, botón con giro mientras se procesa, aviso si el servidor tarda
  más de 6 s (p. ej. al despertar en Render), «Preparando la descarga…» en CSV/archivos y aviso de «sin conexión».
- Páginas de error (`app/errores.py`) para 400, 401, 403, 404, 405, 413, 429, 500, 502, 503 y formulario vencido (CSRF): explican qué
  pasó y ofrecen la salida adecuada (inicio, volver, iniciar sesión, reintentar, soporte). Un 500 muestra un **código de referencia**
  que queda en el log junto con la traza; nunca se muestran detalles técnicos. `/api/` y `/wa/` responden JSON.

## Desarrollo
```
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env    # complete SECRET_KEY, FIELD_ENCRYPTION_KEY, DATABASE_URL
python inicializar.py && flask --app wsgi run
pytest                                   # pruebas puras
TEST_DATABASE_URL=postgresql+psycopg://postgres@localhost:5432/bot_pruebas pytest   # + recorrido completo (¡borra esa base!)
```
