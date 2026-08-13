"""
Validadores personalizados para la aplicación de conserjería.

- validar_rut: valida un RUT chileno (con o sin puntos/guión) usando el
  algoritmo del dígito verificador módulo 11.
- validar_telefono: RegexValidator que exige exactamente 9 dígitos numéricos.
"""
import re

from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator


def limpiar_rut(rut: str) -> str:
    """Quita puntos y guión, deja el RUT en mayúsculas (ej: '12345678K')."""
    return re.sub(r'[.\-\s]', '', str(rut)).strip().upper()


def validar_rut(rut: str) -> None:
    """
    Valida un RUT chileno mediante el algoritmo de módulo 11.
    Lanza ValidationError si el RUT es inválido.
    Acepta formatos: 12345678-9, 12.345.678-9, 123456789, 12345678K
    """
    rut_limpio = limpiar_rut(rut)

    if len(rut_limpio) < 2:
        raise ValidationError('El RUT ingresado no es válido.')

    cuerpo, dv_ingresado = rut_limpio[:-1], rut_limpio[-1]

    if not cuerpo.isdigit():
        raise ValidationError('El RUT ingresado no es válido.')

    suma = 0
    multiplo = 2
    for digito in reversed(cuerpo):
        suma += int(digito) * multiplo
        multiplo = multiplo + 1 if multiplo < 7 else 2

    resto = suma % 11
    dv_esperado_num = 11 - resto

    if dv_esperado_num == 11:
        dv_calculado = '0'
    elif dv_esperado_num == 10:
        dv_calculado = 'K'
    else:
        dv_calculado = str(dv_esperado_num)

    if dv_calculado != dv_ingresado:
        raise ValidationError(
            f'El RUT "{rut}" no es válido (dígito verificador incorrecto).'
        )


# Teléfono chileno: exactamente 9 dígitos numéricos (ej: 912345678)
validar_telefono = RegexValidator(
    regex=r'^\d{9}$',
    message='El teléfono debe contener exactamente 9 dígitos numéricos (ej: 912345678).'
)


def formatear_rut(rut: str) -> str:
    """
    Normaliza un RUT chileno al formato con puntos de miles y guión,
    ej: '12345678-5' o '12345678k' -> '12.345.678-5' / '12.345.678-K'.

    Se asume que el RUT ya fue validado (validar_rut) antes de llamar a
    esta función. Si el cuerpo no es numérico, retorna el valor limpio
    sin lanzar excepción (para no romper el guardado por un dato legado).
    """
    rut_limpio = limpiar_rut(rut)

    if len(rut_limpio) < 2:
        return rut_limpio

    cuerpo, dv = rut_limpio[:-1], rut_limpio[-1]

    if not cuerpo.isdigit():
        return rut_limpio

    cuerpo_con_puntos = f'{int(cuerpo):,}'.replace(',', '.')
    return f'{cuerpo_con_puntos}-{dv}'
