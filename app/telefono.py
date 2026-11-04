"""Número de WhatsApp normalizado y error común de los canales."""
import re


class ErrorWhatsapp(Exception):
    pass


def telefono(valor) -> str:
    """Normaliza a solo dígitos con código de país; 0412… (Venezuela) → 58412…"""
    d = re.sub(r"\D", "", str(valor or ""))
    if d.startswith("00"): d = d[2:]
    if len(d) == 11 and d.startswith("0"): d = "58" + d[1:]
    if not re.fullmatch(r"\d{8,15}", d): raise ValueError(f"Número de WhatsApp inválido: «{valor}» (use el formato 58412…)")
    return d
