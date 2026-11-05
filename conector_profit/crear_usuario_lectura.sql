-- Usuario de SOLO LECTURA para el conector de Pedyra. Ejecútelo en SQL Server Management Studio como administrador (sa o similar).
-- 1) Cambie la contraseña y el nombre de la base de datos de Profit Plus (la de la EMPRESA, p. ej. DEMOA o EMPRESA01).
-- 2) El usuario solo puede hacer SELECT sobre las 6 tablas que lee el conector. No puede modificar, borrar ni ver otras tablas.

USE [master];
IF NOT EXISTS (SELECT 1 FROM sys.server_principals WHERE name = 'pedyra_lectura')
    CREATE LOGIN [pedyra_lectura] WITH PASSWORD = 'Cambie-Esta-Clave#2026', CHECK_POLICY = ON, DEFAULT_DATABASE = [master];
GO

USE [NOMBRE_DE_LA_EMPRESA_EN_PROFIT];   -- ← cambie aquí
IF NOT EXISTS (SELECT 1 FROM sys.database_principals WHERE name = 'pedyra_lectura')
    CREATE USER [pedyra_lectura] FOR LOGIN [pedyra_lectura];
GRANT SELECT ON dbo.saArticulo      TO [pedyra_lectura];
GRANT SELECT ON dbo.saStockAlmacen  TO [pedyra_lectura];
GRANT SELECT ON dbo.saArtPrecio     TO [pedyra_lectura];
GRANT SELECT ON dbo.saTipoPrecio    TO [pedyra_lectura];
GRANT SELECT ON dbo.saAlmacen       TO [pedyra_lectura];
GRANT SELECT ON dbo.saCliente       TO [pedyra_lectura];
DENY INSERT, UPDATE, DELETE, EXECUTE, ALTER TO [pedyra_lectura];
GO

-- Verificación (debe devolver solo permisos SELECT y DENY):
-- SELECT * FROM fn_my_permissions(NULL, 'DATABASE');   -- ejecutado como pedyra_lectura
