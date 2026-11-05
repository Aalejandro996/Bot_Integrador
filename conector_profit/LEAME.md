# Conector Pedyra ↔ Profit Plus (solo lectura)

Programa pequeño que se instala **en el servidor donde corre Profit Plus** (o en cualquier PC de esa red que esté
siempre encendido y vea al SQL Server). Cada 15 minutos lee de Profit Plus artículos, existencias por almacén, precios de
venta y clientes, y los envía a Pedyra para que el bot de WhatsApp los use.

- **Solo lectura:** el usuario de SQL Server tiene únicamente `SELECT` sobre 6 tablas. No escribe ni envía nada a Profit.
- **Sin abrir puertos:** solo hace conexiones de salida por HTTPS (443) hacia Pedyra. SQL Server no se expone a Internet.
- **La clave de la base no sale de su red:** la conexión a SQL Server vive solo en `pedyra_conector.ini`, en este equipo.
- **Se configura desde Pedyra:** almacenes, lista de precios y si se descuenta lo comprometido se eligen en
  Pedyra → Clientes → Profit Plus; el conector lo aplica en su siguiente lectura.

## Requisitos
- Windows 10/11 o Windows Server 2012 R2 en adelante, con salida a Internet por HTTPS.
- Profit Plus 2K12 (12.x) sobre SQL Server 2008 R2 o superior. Otras versiones: ajuste las consultas (ver abajo).
- [ODBC Driver 18 for SQL Server](https://learn.microsoft.com/sql/connect/odbc/download-odbc-driver-for-sql-server) (o el 17).
- `pedyra_conector.exe` (no necesita Python) **o** Python 3.10+ con `pip install -r requirements.txt`.

## Instalación (10 minutos)
1. **Usuario de lectura:** abra `crear_usuario_lectura.sql` en SQL Server Management Studio, cambie la contraseña y el
   nombre de la base de la empresa, y ejecútelo.
2. **Archivos:** cree `C:\Pedyra` y copie `pedyra_conector.exe` (o `pedyra_conector.py`) y `pedyra_conector.ini`
   (descárguelo en Pedyra → Clientes → Profit Plus; ya trae la URL, el ID y la clave de su empresa).
3. **Conexión a SQL Server:** en `pedyra_conector.ini`, sección `[sqlserver]`, escriba el servidor
   (p. ej. `localhost\SQLEXPRESS` o `SERVIDOR\PROFIT`), la base de datos, el usuario `pedyra_lectura` y su contraseña.
   Sin usuario se usa la autenticación de Windows de quien ejecute el programa.
4. **Probar:** en una consola, `C:\Pedyra\pedyra_conector.exe --probar`. Debe mostrar ✔ Pedyra y ✔ SQL Server con los conteos.
5. **Dejarlo andando:** como Administrador, `C:\Pedyra\pedyra_conector.exe --instalar-tarea`. Crea la tarea programada
   «Pedyra Profit Plus» (cuenta SYSTEM, arranca con Windows): revisa cada 5 minutos y sincroniza según el intervalo
   configurado en Pedyra (15 min por defecto).
6. En Pedyra → Clientes → Profit Plus elija los almacenes y la lista de precios. Listo: el bot ya responde con el catálogo de Profit.

Registro: `C:\Pedyra\pedyra_conector.log`. Forzar una lectura ahora: `pedyra_conector.exe --forzar`.

## Qué lee (Profit Plus 2K12)
| Tabla | Para qué |
|---|---|
| `saArticulo` | código, descripción, línea y tipo de impuesto (`anulado = 0`) |
| `saStockAlmacen` | existencia por almacén: tipo `ACT` (actual) menos `COM` (comprometido) |
| `saArtPrecio` | precio vigente de la lista elegida (`co_precio`), con su moneda |
| `saTipoPrecio` | nombres de las listas de precio (para elegir en Pedyra) |
| `saAlmacen` | almacenes; por defecto cuentan los que no están marcados «no venta» |
| `saCliente` | clientes activos: RIF, dirección, teléfonos (el bot reconoce al cliente por su celular o RIF) |

`tipo_imp` → alícuota: 1 general, 2 reducida, 3 adicional, 6/7 exento (ajustable en `[alicuotas]`).
Precios en dólares: Pedyra los muestra en bolívares a la tasa BCV del día.

## Otras versiones de Profit Plus
Si su versión usa otras tablas o columnas, agregue en el `.ini` una sección `[consultas]` con las claves `almacenes`,
`listas`, `articulos`, `stock`, `precios` (recibe la lista de precio como parámetro `?`) y `clientes`. Cada consulta
debe devolver las mismas columnas y en el mismo orden que las de `CONSULTAS_2K12` en `pedyra_conector.py`.

## Compilar el .exe (Gigadevs)
```
pip install -r requirements.txt pyinstaller==6.11.1
pyinstaller --onefile --name pedyra_conector pedyra_conector.py
```
Publique `dist\pedyra_conector.exe` y pegue su URL en la consola Maestro → Bot y Profit Plus → «Descarga del instalador».

## Seguridad
- Cada petición va firmada (HMAC-SHA256 con la clave de su empresa) y vale 5 minutos.
- Si el `.ini` se filtra, renueve la clave en Pedyra → Profit Plus → «Renovar clave» y reemplace el archivo.
- Proteja la carpeta `C:\Pedyra` (solo Administradores y SYSTEM), porque el `.ini` guarda la contraseña de `pedyra_lectura`.
