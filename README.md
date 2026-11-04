# Contax Bot · Profit Plus — General de Suministros Orituco

Bot de WhatsApp conectado **en solo lectura** a Profit Plus. Gigadevs Corp. Repositorio **privado**.

Los clientes piden por WhatsApp con el catálogo, las existencias por almacén y los precios de Profit (actualizados cada 15 min).
El bot arma pedidos, envía presupuestos, lee órdenes de compra en PDF y deja **órdenes de venta** con lo disponible.
La factura fiscal se sigue emitiendo en Profit Plus: nada se escribe ni se envía a Profit.

```
 Red del cliente (Windows)                               Render (proyecto «Bot Profit Orituco», Ohio)
 ┌─────────────────────────────┐  HTTPS 443, solo SALIDA  ┌──────────────────────┐   red privada   ┌──────────────┐
 │ SQL Server (Profit Plus)    │  foto firmada (HMAC)     │ bot-profit-orituco   │◄──────────────►│ bot-whatsapp │◄─► WhatsApp
 │   ▲ SELECT (usuario lectura)│ ───────────────────────► │ (web, consola, API)  │                │ (OpenWA, disco)│
 │ contax_profit.exe (tarea)   │ ◄──── configuración ──── │          │           │                └──────────────┘
 └─────────────────────────────┘                          │   bot-profit-db (PostgreSQL, sin acceso desde Internet)
```

## Quién hace qué
- **Cliente (Administrador):** WhatsApp (vincula su número con QR, opciones del bot), Profit Plus (activa el conector, descarga
  `contax_profit.ini`, elige almacenes, lista de precios y existencia), Usuarios (invita operadores).
- **Cliente (Operador y Administrador):** Panel, Pedidos (cambiar estado y avisar por WhatsApp, exportar CSV para cargar en Profit),
  Conversaciones (responder, «pidió una persona»), Presupuestos.
- **Gigadevs (Maestro):** Monitoreo (estado del WhatsApp, mensajes recibidos/enviados/fallidos 24 h, pedidos, última foto de Profit,
  alertas) y API de Profit (activar/pausar, URL, intervalo, topes, instalador); Empresas (alta con enlace de activación, suspender);
  Pasarela de WhatsApp (URL y clave de OpenWA); Tasa BCV e IVA; Maestros; Bitácora. Ve conteos y estados, no conversaciones ni catálogos.

## Despliegue en Render (una vez)
1. Dashboard → **New → Blueprint** → este repositorio → indique `MAESTRO_USUARIO` (y `MAESTRO_EMAIL`) → **Apply**.
   Crea la app web, la pasarela **privada** de WhatsApp con disco y la base de datos sin acceso desde Internet. `SECRET_KEY` y
   `FIELD_ENCRYPTION_KEY` las genera Render (no las cambie después: cifran los secretos guardados).
2. Logs de `bot-profit-orituco` → copie el enlace «Active el Maestro…» (72 h, un uso) → cree su clave y su MFA.
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

## Desarrollo
```
python -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt -r requirements-dev.txt
cp .env.example .env    # complete SECRET_KEY, FIELD_ENCRYPTION_KEY, DATABASE_URL
python inicializar.py && flask --app wsgi run
pytest                                   # pruebas puras
TEST_DATABASE_URL=postgresql+psycopg://postgres@localhost:5432/bot_pruebas pytest   # + recorrido completo (¡borra esa base!)
```
