"""
Libera el horario de reservas cuyo pago en Webpay quedó a medio camino y el
residente nunca volvió (cerró el navegador, se cortó internet, etc.) -- el
caso que `webpay_retorno` no puede resolver porque Transbank jamás avisa.

PagoReserva.estado sigue en INICIADO más allá de
settings.WEBPAY_MINUTOS_EXPIRACION_PAGO minutos después de creado =>
se marca RECHAZADO y la reserva asociada pasa a CANCELADA, liberando el
tramo horario para que otro residente lo pueda reservar.

Pensado para correr como systemd timer, mismo patrón que
conserjeria-backup.timer, cada 5-10 minutos:

    python manage.py expirar_pagos_pendientes
"""
from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from conserjeria.models import PagoReserva, ReservaAreaComun


class Command(BaseCommand):
    help = 'Cancela reservas cuyo pago Webpay quedó INICIADO más allá del tiempo de expiración.'

    def handle(self, *args, **options):
        limite = timezone.now() - timedelta(minutes=settings.WEBPAY_MINUTOS_EXPIRACION_PAGO)
        vencidos = PagoReserva.objects.filter(
            estado=PagoReserva.INICIADO, fecha_creacion__lt=limite
        ).select_related('reserva')

        total = 0
        for pago in vencidos:
            pago.estado = PagoReserva.RECHAZADO
            pago.respuesta_json = {**pago.respuesta_json, 'expirado_por_timeout': True}
            pago.fecha_pago = timezone.now()
            pago.save()

            reserva = pago.reserva
            if reserva.estado == ReservaAreaComun.PENDIENTE:
                reserva.estado = ReservaAreaComun.CANCELADA
                reserva.save(update_fields=['estado'])
            total += 1

        self.stdout.write(self.style.SUCCESS(f'{total} pago(s) expirado(s) y horario(s) liberado(s).'))
