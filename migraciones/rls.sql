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
