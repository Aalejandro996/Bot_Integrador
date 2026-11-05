"""Plantilla HTML de los correos de Surtia™ (misma identidad que la aplicación: petróleo #0B2530, menta #16C79A, mandarina #FF7A45).

Los clientes de correo no aceptan hojas de estilo externas ni SVG: todo va con estilos en línea, maquetado con tablas y con el
ícono en PNG. Cada correo lleva además una versión de texto plano equivalente. Todo valor dinámico se escapa."""
from html import escape

FONDO, TINTA, TEXTO, SUAVE, LINEA = "#F3F6F6", "#0B2530", "#22343A", "#5B6B70", "#DCE4E5"
MENTA, MENTA_TINTA, MENTA_CLARO, MANDARINA = "#16C79A", "#04281E", "#E2F8F1", "#FF7A45"
FUENTE = "'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
PIE = "Mensaje automático: no responda a este correo. Si no reconoce esta acción, contacte de inmediato al administrador de su empresa."
TONOS = {"info": (MENTA_CLARO, "#0A6B53"), "alerta": ("#FFEDE5", "#A8401A"), "peligro": ("#FCE8E6", "#B3261E")}


def _marca(nombre: str) -> str:
    """«Surtia» con el punto de la i en mandarina (la palabra va en blanco sobre la barra petróleo)."""
    e = escape(nombre)
    if nombre.lower() == "surtia":
        return f'surt<span style="color:{MANDARINA}">i</span>a'
    return e


def html(titulo, parrafos, base_url, nombre="Surtia", lema="", boton=None, destacado=None, filas=(), nota=None, pie=None, aviso=None):
    """titulo: encabezado. parrafos: textos (se escapan). boton: (texto, url). destacado: (etiqueta, valor) en un recuadro.
    filas: [(etiqueta, valor)] en tabla de datos. nota: aviso de seguridad al final. aviso: (tono, texto) franja de color
    arriba del título (tono: info | alerta | peligro)."""
    e = escape
    logo = f"{base_url.rstrip('/')}/static/marca/icono-192.png"
    cuerpo = "".join(f'<p style="margin:0 0 16px;font-size:15px;line-height:1.65;color:{TEXTO}">{e(p)}</p>' for p in parrafos)
    if destacado:
        cuerpo += (f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:8px 0 20px"><tr>'
                   f'<td style="background:{TINTA};border-radius:14px;padding:18px 20px">'
                   f'<div style="font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:#9FB6BD">{e(destacado[0])}</div>'
                   f'<div style="font-family:Menlo,Consolas,monospace;font-size:20px;font-weight:700;color:{MENTA};margin-top:6px;word-break:break-all">{e(destacado[1])}</div>'
                   f'</td></tr></table>')
    if filas:
        cuerpo += f'<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="margin:0 0 20px;border-top:1px solid {LINEA}">'
        for k, v in filas:
            cuerpo += (f'<tr><td style="padding:10px 0;border-bottom:1px solid {LINEA};font-size:14px;color:{SUAVE}">{e(str(k))}</td>'
                       f'<td align="right" style="padding:10px 0 10px 12px;border-bottom:1px solid {LINEA};font-size:14px;font-weight:600;color:{TINTA}">{e(str(v))}</td></tr>')
        cuerpo += "</table>"
    if boton:
        cuerpo += (f'<table role="presentation" cellpadding="0" cellspacing="0" style="margin:8px 0 24px"><tr>'
                   f'<td style="background:{MENTA};border-radius:999px">'
                   f'<a href="{e(boton[1])}" style="display:inline-block;padding:14px 28px;font-family:{FUENTE};font-size:15px;font-weight:700;'
                   f'color:{MENTA_TINTA};text-decoration:none">{e(boton[0])}</a></td></tr></table>'
                   f'<p style="margin:0 0 16px;font-size:12px;line-height:1.5;color:{SUAVE}">Si el botón no funciona, copie este enlace en su navegador:<br>'
                   f'<span style="color:{TINTA};word-break:break-all">{e(boton[1])}</span></p>')
    if nota:
        cuerpo += (f'<div style="margin-top:8px;padding:14px 16px;background:{FONDO};border-left:4px solid {MENTA};border-radius:10px;font-size:13px;line-height:1.55;color:{SUAVE}">'
                   f'{e(nota)}</div>')
    franja = ""
    if aviso:
        fondo, color = TONOS.get(aviso[0], TONOS["info"])
        franja = (f'<div style="display:inline-block;margin:0 0 14px;padding:4px 12px;border-radius:999px;background:{fondo};color:{color};'
                  f'font-size:12px;font-weight:700;letter-spacing:.04em;text-transform:uppercase">{e(aviso[1])}</div>')
    pie = pie or PIE
    return f"""<!doctype html><html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><meta name="supported-color-schemes" content="light"><title>{e(titulo)}</title></head>
<body style="margin:0;padding:0;background:{FONDO};font-family:{FUENTE};-webkit-font-smoothing:antialiased">
<span style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent">{e(parrafos[0] if parrafos else titulo)}</span>
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:{FONDO}"><tr><td align="center" style="padding:32px 12px">
<table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="max-width:580px">
  <tr><td style="background:{TINTA};border-radius:18px 18px 0 0;padding:22px 28px">
    <table role="presentation" cellpadding="0" cellspacing="0"><tr>
      <td style="padding-right:12px"><img src="{e(logo)}" width="40" height="40" alt="{e(nombre)}" style="display:block;border:0;border-radius:11px"></td>
      <td><div style="font-size:22px;font-weight:800;color:#ffffff;letter-spacing:-.02em;line-height:1.1">{_marca(nombre)}<sup style="font-size:10px;color:{MENTA}">™</sup></div>
          <div style="font-size:12px;color:#9FB6BD;margin-top:2px">{e(lema)}</div></td>
    </tr></table>
  </td></tr>
  <tr><td style="height:4px;background:{MENTA};background-image:linear-gradient(90deg,{MENTA},{MANDARINA});font-size:0;line-height:0">&nbsp;</td></tr>
  <tr><td style="background:#ffffff;padding:32px 28px;border:1px solid {LINEA};border-top:0;border-radius:0 0 18px 18px">
    {franja}<h1 style="margin:0 0 18px;font-size:22px;line-height:1.3;color:{TINTA};font-weight:700;letter-spacing:-.01em">{e(titulo)}</h1>
    {cuerpo}
  </td></tr>
  <tr><td style="padding:20px 8px;text-align:center;font-size:12px;line-height:1.6;color:{SUAVE}">
    {e(pie)}<br><strong style="color:{TINTA}">{e(nombre)}</strong>™ · una marca de Gigadevs Corp · Venezuela
  </td></tr>
</table></td></tr></table></body></html>"""


def texto(titulo, parrafos, boton=None, destacado=None, filas=(), nota=None, nombre="Surtia"):
    """Versión de texto plano equivalente (para clientes sin HTML y filtros antispam)."""
    partes = [titulo, "=" * len(titulo), "", *[p + "\n" for p in parrafos]]
    if destacado: partes += [f"{destacado[0]}: {destacado[1]}", ""]
    if filas: partes += [f"  {k}: {v}" for k, v in filas] + [""]
    if boton: partes += [f"{boton[0]}:", boton[1], ""]
    if nota: partes += [nota, ""]
    partes += ["—", f"{nombre}™ · una marca de Gigadevs Corp"]
    return "\n".join(partes)
