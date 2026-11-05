"""Plantilla de correo y envío (sin base de datos)."""
from flask import Flask

from app import brevo, correo, plantilla_correo as PC


def _app(**cfg):
    a = Flask(__name__)
    a.config.update({**dict(APP_NOMBRE="Surtia", APP_LEMA="Lema", APP_BASE_URL="https://bot.test", MAIL_FROM="Surtia <x@bot.test>", MAIL_BACKEND="console",
                            BREVO_API_KEY="", SMTP_HOST="", SMTP_PORT=587, SMTP_USER="", SMTP_PASSWORD="", SMTP_SSL=False), **cfg})
    return a


def test_plantilla_escapa_y_lleva_marca():
    h = PC.html("Hola <b>", ["<script>alert(1)</script>"], "https://bot.test/", boton=("Entrar", "https://bot.test/a?x=1&y=2"),
                filas=[("Total", "Bs 1.000,00")], aviso=("peligro", "Alerta"))
    assert "<script>" not in h and "&lt;script&gt;" in h and "Hola &lt;b&gt;" in h
    assert "https://bot.test/static/marca/icono-192.png" in h and "a?x=1&amp;y=2" in h and "Surtia</strong>™" in h
    t = PC.texto("Título", ["Uno"], boton=("Entrar", "https://bot.test/a"), filas=[("Total", "1")])
    assert "https://bot.test/a" in t and "Total: 1" in t and "Surtia™" in t


def test_console_y_destinatarios():
    correo.ENVIADOS.clear()
    with _app().app_context():
        assert not correo.real()
        assert correo.en_segundo_plano(["A@b.test", "a@b.test", "no-es-correo", None], "Aviso", "Texto") == 1
        assert not correo.enviar_seguro("malo", "x", "y")
    assert [m["To"] for m in correo.ENVIADOS] == ["a@b.test"] and correo.ENVIADOS[0]["Subject"] == "Aviso · Surtia"


def test_brevo_remitente_y_errores():
    assert brevo.remitente("Surtia <x@bot.test>") == {"name": "Surtia", "email": "x@bot.test"}
    llamadas = []
    brevo.enviar("k", "Surtia <x@bot.test>", "a@b.test", "Asunto", "t", html="<p>h</p>",
                 transporte=lambda u, d, h: (llamadas.append((d, h)), (201, b'{"messageId":"m1"}'))[1])
    assert llamadas[0][1] == {"api-key": "k"} and llamadas[0][0]["htmlContent"] == "<p>h</p>"
    with _app(MAIL_BACKEND="brevo", BREVO_API_KEY="k").app_context():
        assert correo.real()
