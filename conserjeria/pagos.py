"""
Integración con Webpay Plus Mall (Transbank) para el pago online de la
garantía de reservas de áreas comunes.

Arquitectura elegida (ver conversación de diseño): un único código de
comercio "padre" (Mall, tuyo, en settings.WEBPAY_COMMERCE_CODE) y un código
de comercio "hijo" por edificio (Edificio.codigo_comercio_webpay), así el
dinero llega directo a la cuenta bancaria de cada edificio.

Las funciones de acá NUNCA confían en un monto que venga del cliente: el
monto siempre se copia server-side desde AreaComun.monto_garantia antes de
crear la transacción.
"""
from django.conf import settings
from django.db import transaction as db_transaction
from django.utils import timezone

from transbank.common.integration_type import IntegrationType
from transbank.common.options import WebpayOptions
from transbank.error.transbank_error import TransbankError
from transbank.webpay.webpay_plus.mall_transaction import MallTransaction
from transbank.webpay.webpay_plus.request import MallTransactionCreateDetails

from .models import PagoReserva, ReservaAreaComun


class PagoNoDisponibleError(Exception):
    """El edificio no tiene código de comercio Webpay asignado (aún no está
    afiliado a Transbank), así que no se puede iniciar un cobro."""


def _webpay_options():
    tipo = IntegrationType.LIVE if settings.WEBPAY_ENVIRONMENT == 'LIVE' else IntegrationType.TEST
    return WebpayOptions(settings.WEBPAY_COMMERCE_CODE, settings.WEBPAY_API_KEY, tipo)


def iniciar_pago(reserva: ReservaAreaComun, return_url: str):
    """Crea la transacción en Transbank para la garantía de `reserva` y deja
    un PagoReserva en estado INICIADO. Devuelve (url, token) para armar el
    formulario de redirección a Webpay -- Transbank exige POST, nunca un
    redirect GET normal.

    Se asume que `reserva` ya fue creada en PENDIENTE dentro de su propia
    transacción atómica (con el select_for_update de superposición ya
    resuelto) ANTES de llamar a esta función: la llamada HTTP a Transbank no
    debe hacerse con locks de fila tomados.
    """
    area = reserva.area_comun
    edificio = reserva.departamento.edificio

    if not edificio.codigo_comercio_webpay:
        raise PagoNoDisponibleError(
            f'{edificio.nombre} todavía no está afiliado a Webpay (sin código de comercio).'
        )

    monto = area.monto_garantia
    buy_order = f'RES{reserva.pk}'
    session_id = f'edif{edificio.pk}-res{reserva.pk}'
    details = MallTransactionCreateDetails(monto, edificio.codigo_comercio_webpay, buy_order)

    tx = MallTransaction(_webpay_options())
    respuesta = tx.create(buy_order, session_id, return_url, details)

    pago = PagoReserva.objects.create(
        reserva=reserva,
        buy_order=buy_order,
        token_ws=respuesta['token'],
        monto=monto,
        estado=PagoReserva.INICIADO,
        respuesta_json={'create': respuesta},
    )
    return respuesta['url'], respuesta['token'], pago


def confirmar_pago(token: str):
    """Hace el commit de la transacción tras el retorno desde Transbank y
    actualiza PagoReserva + ReservaAreaComun según el resultado. Devuelve el
    PagoReserva actualizado, o None si el token no corresponde a ningún pago
    conocido (no debería pasar en operación normal)."""
    try:
        pago = PagoReserva.objects.select_related(
            'reserva', 'reserva__area_comun', 'reserva__departamento__edificio'
        ).get(token_ws=token)
    except PagoReserva.DoesNotExist:
        return None

    # Ya procesado (ej. el usuario recargó la página de retorno): no volver
    # a hacer commit contra Transbank, que fallaría igual por token usado.
    if pago.estado != PagoReserva.INICIADO:
        return pago

    edificio = pago.reserva.departamento.edificio
    tx = MallTransaction(_webpay_options())

    try:
        respuesta = tx.commit(token)
    except TransbankError as err:
        _marcar_rechazado(pago, {'commit_error': str(err)})
        return pago

    detalle = next(
        (d for d in respuesta.get('details', []) if d.get('commerce_code') == edificio.codigo_comercio_webpay),
        None,
    )
    aprobado = bool(detalle) and detalle.get('status') == 'AUTHORIZED' and detalle.get('response_code') == 0

    with db_transaction.atomic():
        pago.respuesta_json = {**pago.respuesta_json, 'commit': respuesta}
        pago.fecha_pago = timezone.now()
        if aprobado:
            pago.estado = PagoReserva.APROBADO
            pago.codigo_autorizacion = detalle.get('authorization_code', '')
            pago.save()
            reserva = pago.reserva
            reserva.estado = ReservaAreaComun.CONFIRMADA
            reserva.fecha_confirmacion = timezone.now()
            reserva.save(update_fields=['estado', 'fecha_confirmacion'])
        else:
            pago.estado = PagoReserva.RECHAZADO
            pago.save()
            reserva = pago.reserva
            reserva.estado = ReservaAreaComun.CANCELADA
            reserva.save(update_fields=['estado'])

    return pago


def abortar_pago(pago: PagoReserva, motivo: dict):
    """El residente abandonó Webpay antes de pagar (volvió con TBK_TOKEN en
    vez de token_ws, o Transbank cerró la sesión por timeout). Libera el
    horario igual que un pago rechazado."""
    if pago.estado != PagoReserva.INICIADO:
        return pago
    _marcar_rechazado(pago, motivo)
    return pago


def _marcar_rechazado(pago: PagoReserva, datos_respuesta: dict):
    with db_transaction.atomic():
        pago.estado = PagoReserva.RECHAZADO
        pago.respuesta_json = {**pago.respuesta_json, **datos_respuesta}
        pago.fecha_pago = timezone.now()
        pago.save()
        reserva = pago.reserva
        reserva.estado = ReservaAreaComun.CANCELADA
        reserva.save(update_fields=['estado'])


def reembolsar_pago(pago: PagoReserva):
    """Reembolsa una garantía ya aprobada. Sólo tiene sentido si el edificio
    tiene garantia_reembolsable=True -- la vista que llama a esto es
    responsable de verificarlo antes (acá se valida por las dudas, pero la
    decisión de negocio de mostrar o no el botón vive en la vista)."""
    if pago.estado != PagoReserva.APROBADO:
        raise ValueError('Sólo se puede reembolsar un pago APROBADO.')

    edificio = pago.reserva.departamento.edificio
    if not edificio.garantia_reembolsable:
        raise ValueError(f'{edificio.nombre} tiene la garantía configurada como no reembolsable.')

    tx = MallTransaction(_webpay_options())
    respuesta = tx.refund(
        token=pago.token_ws,
        child_buy_order=pago.buy_order,
        child_commerce_code=edificio.codigo_comercio_webpay,
        amount=pago.monto,
    )
    pago.estado = PagoReserva.REEMBOLSADO
    pago.fecha_reembolso = timezone.now()
    pago.respuesta_json = {**pago.respuesta_json, 'refund': respuesta}
    pago.save()
    return pago
