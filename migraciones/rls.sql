-- Row-Level Security de Surtia: cada empresa solo ve lo suyo. Idempotente (inicializar.py lo aplica en cada despliegue, como dueño).
CREATE OR REPLACE FUNCTION surtia_empresa() RETURNS integer LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('surtia.empresa_id', true), '')::integer $$;
CREATE OR REPLACE FUNCTION surtia_maestro() RETURNS boolean LANGUAGE sql STABLE AS
$$ SELECT coalesce(current_setting('surtia.maestro', true), '') = '1' $$;

-- Secretos → empresa, sin abrir la tabla al rol de la app (solo devuelven el número de empresa)
CREATE OR REPLACE FUNCTION surtia_empresa_por_webhook(clave text) RETURNS integer LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$$ SELECT empresa_id FROM whatsapp_config WHERE clave_webhook = clave $$;
CREATE OR REPLACE FUNCTION surtia_empresa_por_conector(cid text) RETURNS integer LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$$ SELECT empresa_id FROM profit_conexion WHERE conector_id = cid $$;
REVOKE ALL ON FUNCTION surtia_empresa_por_webhook(text), surtia_empresa_por_conector(text) FROM PUBLIC;

ALTER TABLE empresas ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_empresa ON empresas;
CREATE POLICY p_empresa ON empresas USING (surtia_maestro() OR id = surtia_empresa()) WITH CHECK (surtia_maestro() OR id = surtia_empresa());

DO $$ DECLARE t text; BEGIN
  FOREACH t IN ARRAY ARRAY['contactos','mensajes','pedidos','presupuestos','whatsapp_config','profit_conexion','profit_articulos',
                           'profit_clientes','profit_sincronizaciones','aceptaciones_legales'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('DROP POLICY IF EXISTS p_empresa ON %I', t);
    EXECUTE format('CREATE POLICY p_empresa ON %I USING (surtia_maestro() OR empresa_id = surtia_empresa()) '
                   'WITH CHECK (surtia_maestro() OR empresa_id = surtia_empresa())', t);
  END LOOP;
END $$;

-- Bitácora: se escribe siempre (también intentos fallidos sin sesión); se lee solo lo propio; nunca se modifica ni se borra
ALTER TABLE bitacora ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_leer ON bitacora;
DROP POLICY IF EXISTS p_escribir ON bitacora;
CREATE POLICY p_leer ON bitacora FOR SELECT USING (surtia_maestro() OR empresa_id = surtia_empresa());
CREATE POLICY p_escribir ON bitacora FOR INSERT WITH CHECK (true);

-- ===================== Usuarios (también protegidos por PostgreSQL) =====================
-- surtia.usuario_id = la persona que usa (o intenta usar) la cuenta. Las búsquedas previas a la sesión (login, enlaces de
-- activación y recuperación, duplicados) pasan por funciones SECURITY DEFINER que solo devuelven un número.
CREATE OR REPLACE FUNCTION surtia_usuario() RETURNS integer LANGUAGE sql STABLE AS
$$ SELECT NULLIF(current_setting('surtia.usuario_id', true), '')::integer $$;
CREATE OR REPLACE FUNCTION surtia_usuario_por_nombre(valor text) RETURNS integer LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$$ SELECT id FROM usuarios WHERE lower(username) = lower(valor) OR lower(email) = lower(valor) ORDER BY id LIMIT 1 $$;
CREATE OR REPLACE FUNCTION surtia_usuario_por_enlace(tipo text, hash text) RETURNS integer LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$$ SELECT id FROM usuarios WHERE (tipo = 'activacion' AND token_activacion_hash = hash) OR (tipo = 'recuperacion' AND token_recuperacion_hash = hash) LIMIT 1 $$;
CREATE OR REPLACE FUNCTION surtia_existe_usuario(valor text) RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$$ SELECT EXISTS (SELECT 1 FROM usuarios WHERE lower(username) = lower(valor)) $$;
CREATE OR REPLACE FUNCTION surtia_empresa_de_usuario(uid integer) RETURNS integer LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$$ SELECT empresa_id FROM usuarios WHERE id = uid $$;
-- Correos de los Maestros activos (alertas de la plataforma enviadas desde el contexto de una empresa)
CREATE OR REPLACE FUNCTION surtia_correos_maestros() RETURNS SETOF text LANGUAGE sql STABLE SECURITY DEFINER SET search_path = public AS
$$ SELECT email FROM usuarios WHERE rol = 'Maestro' AND activo AND email IS NOT NULL $$;
REVOKE ALL ON FUNCTION surtia_correos_maestros() FROM PUBLIC;
REVOKE ALL ON FUNCTION surtia_usuario_por_nombre(text), surtia_usuario_por_enlace(text, text), surtia_existe_usuario(text),
                       surtia_empresa_de_usuario(integer) FROM PUBLIC;

ALTER TABLE usuarios ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS p_usuario ON usuarios;
CREATE POLICY p_usuario ON usuarios
  USING (surtia_maestro() OR id = surtia_usuario() OR (surtia_empresa() IS NOT NULL AND empresa_id = surtia_empresa()))
  -- la propia cuenta se actualiza (clave, MFA, intentos) pero nunca cambia de empresa
  WITH CHECK (surtia_maestro() OR (surtia_empresa() IS NOT NULL AND empresa_id = surtia_empresa())
              OR (id = surtia_usuario() AND empresa_id IS NOT DISTINCT FROM surtia_empresa_de_usuario(surtia_usuario())));
