import os

from app import create_app

app = create_app()

# Lecturas programadas de los sistemas en la nube (Odoo, WooCommerce, hoja en línea). Solo en el servidor web, no en
# inicializar.py ni en las pruebas.
if os.environ.get("LECTURAS_PROGRAMADAS", "1") == "1":
    from app.servicios import programador
    programador(app)
