"""
Consulta el estado de cada Lavadora vía HTTP REST a su Shelly 1PM local y
guarda la lectura en Postgres.

Uso manual (durante el piloto, corriendo desde una máquina en la MISMA red
que el Shelly -- ver nota en el chat sobre por qué esto no puede correr
desde el servidor de Lightsail hasta resolver el acceso a la LAN del
edificio):

    python manage.py consultar_lavadoras
    python manage.py consultar_lavadoras --edificio-id 1
    python manage.py consultar_lavadoras --verbosity 2

Cuando se valide el umbral/debounce con datos reales del piloto (revisando
LecturaLavadora en /admin/), este comando se puede colgar de un systemd
timer igual que conserjeria-backup.timer, para correr cada N segundos.

Referencia de la API local de Shelly 1PM (Gen1), GET http://<ip>/status:
{
  "relays": [{"ison": true, ...}],
  "meters": [{"power": 34.5, "is_valid": true, ...}],
  ...
}
"""
import requests
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from conserjeria.models import Lavadora, LecturaLavadora

# Umbral de potencia (watts) sobre el cual se considera que la lavadora está
# en uso. Es un primer valor conservador para partir el piloto -- una vez
# que tengas varias LecturaLavadora reales guardadas (ciclo completo: llenado,
# lavado, centrifugado, pausas entre etapas), ajústalo viendo el mínimo real
# durante el ciclo vs. el consumo en stand-by/enchufada-pero-apagada.
UMBRAL_EN_USO_W = 15.0

# Timeout corto: es una red local, si no responde en este tiempo asumimos
# que el dispositivo está caído/inalcanzable en vez de dejar el comando
# colgado esperando.
TIMEOUT_SEGUNDOS = 5


class Command(BaseCommand):
    help = (
        'Consulta cada Lavadora activa vía HTTP REST a su Shelly 1PM '
        '(GET http://<ip>/status) y guarda la lectura en Postgres.'
    )

    def add_arguments(self, parser):
        parser.add_argument(
            '--edificio-id',
            type=int,
            default=None,
            help='Si se especifica, solo consulta las lavadoras de ese edificio (útil para probar el piloto).',
        )

    def handle(self, *args, **options):
        # Sólo procesa edificios con el módulo de lavadoras habilitado (ver
        # Edificio.modulo_lavadoras) -- es un complemento opcional, y cada
        # consulta acá es una llamada HTTP saliente + una escritura a
        # Postgres que no tiene sentido pagar en CPU/RAM si el edificio no
        # lo tiene contratado.
        lavadoras = Lavadora.objects.filter(
            activa=True, edificio__modulo_lavadoras=True
        ).select_related('edificio')

        edificio_id = options.get('edificio_id')
        if edificio_id is not None:
            lavadoras = lavadoras.filter(edificio_id=edificio_id)
            if not lavadoras.exists():
                raise CommandError(
                    f'No hay lavadoras activas con el módulo habilitado para edificio_id={edificio_id}. '
                    'Revisa Edificio.modulo_lavadoras si esperabas encontrar alguna.'
                )

        if not lavadoras.exists():
            self.stdout.write(self.style.WARNING(
                'No hay lavadoras activas configuradas. Cárgalas en /admin/ (Lavadoras) primero.'
            ))
            return

        ok, errores = 0, 0
        for lavadora in lavadoras:
            if self._consultar_una(lavadora):
                ok += 1
            else:
                errores += 1

        resumen = f'Listo: {ok} lectura(s) exitosa(s), {errores} error(es).'
        self.stdout.write(self.style.SUCCESS(resumen) if errores == 0 else self.style.WARNING(resumen))

    def _consultar_una(self, lavadora):
        """Consulta un Shelly 1PM y guarda el resultado. Retorna True/False según éxito."""
        url = f'http://{lavadora.ip_shelly}/status'
        etiqueta = f'[{lavadora.edificio.nombre} / {lavadora.nombre}]'

        try:
            resp = requests.get(url, timeout=TIMEOUT_SEGUNDOS)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as exc:
            self._registrar_error(lavadora, f'No se pudo consultar {url}: {exc}')
            self.stderr.write(self.style.ERROR(f'{etiqueta} {lavadora.ultimo_error}'))
            return False

        try:
            meter = data['meters'][0]
            relay_on = bool(data['relays'][0]['ison'])
            potencia_w = float(meter['power'])
            lectura_valida = meter.get('is_valid', True)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            self._registrar_error(lavadora, f'Respuesta inesperada de {url}: {exc}')
            self.stderr.write(self.style.ERROR(f'{etiqueta} {lavadora.ultimo_error} -- data={data!r}'))
            return False

        if not lectura_valida:
            # El propio Shelly marca la lectura como no confiable (ej. justo
            # después de un reinicio). No la guardamos como estado oficial,
            # pero tampoco es un error de red -- lo dejamos en log y salimos.
            self.stdout.write(self.style.WARNING(f'{etiqueta} lectura marcada is_valid=False por el dispositivo, se omite.'))
            return False

        estado = Lavadora.EN_USO if potencia_w > UMBRAL_EN_USO_W else Lavadora.LIBRE

        lavadora.estado = estado
        lavadora.potencia_actual_w = potencia_w
        lavadora.ultima_lectura = timezone.now()
        lavadora.ultimo_error = ''
        lavadora.save(update_fields=['estado', 'potencia_actual_w', 'ultima_lectura', 'ultimo_error'])

        LecturaLavadora.objects.create(
            lavadora=lavadora,
            potencia_w=potencia_w,
            relay_on=relay_on,
            estado_calculado=estado,
        )

        self.stdout.write(f'{etiqueta} {potencia_w:.1f}W -> {estado}')
        return True

    @staticmethod
    def _registrar_error(lavadora, mensaje):
        lavadora.estado = Lavadora.DESCONOCIDO
        lavadora.ultima_lectura = timezone.now()
        lavadora.ultimo_error = mensaje[:255]
        lavadora.save(update_fields=['estado', 'ultima_lectura', 'ultimo_error'])
