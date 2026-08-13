"""
Agente local de Lavadoras -- corre DENTRO de la red del edificio, no en el
servidor de Lightsail.

Por qué existe este script: Lightsail (São Paulo) no puede alcanzar la IP
local del Shelly dentro de la red del edificio (ej. 192.168.1.50). Este
agente es el puente: corre en cualquier máquina con acceso a esa red
(notebook durante el piloto, o eventualmente un mini-PC/Raspberry Pi fijo
en la sala de lavandería) y hace dos saltos:

  1. GET http://<ip_shelly>/status       (red local del edificio)
  2. POST https://<tu-dominio>/api/lavadoras/<token>/lectura/   (internet)

No depende de Django ni de acceso directo a Postgres -- solo de `requests`
y de la URL pública de tu app. Así el agente puede vivir en una máquina
mínima sin necesitar el proyecto completo instalado.

INSTALACIÓN
    pip install requests

CONFIGURACIÓN
    Copia dispositivos.ejemplo.json a dispositivos.json y complétalo con
    tus lavadoras reales. El `token` de cada una lo sacas de /admin/ ->
    Lavadoras -> (la lavadora) -> campo "Token" (solo lectura, generado
    automático al crearla).

USO MANUAL (durante el piloto)
    python agente_lavadoras.py
    python agente_lavadoras.py --config /ruta/a/dispositivos.json
    python agente_lavadoras.py --intervalo 30   # corre en loop cada 30s

USO EN PRODUCCIÓN
    Una vez validado, se cuelga de un systemd timer en la máquina local
    del edificio (mismo patrón que conserjeria-backup.timer en el
    servidor, pero corriendo en otra máquina) en vez de dejarlo en loop
    infinito con --intervalo.
"""
import argparse
import json
import sys
import time
from pathlib import Path

import requests

TIMEOUT_LOCAL_SEG = 5    # timeout al Shelly (red local, debería responder rápido)
TIMEOUT_REMOTO_SEG = 10  # timeout a Lightsail (internet, puede tener más latencia)

CONFIG_POR_DEFECTO = Path(__file__).parent / 'dispositivos.json'


def cargar_configuracion(ruta):
    if not ruta.exists():
        print(
            f'No encontré {ruta}. Copia dispositivos.ejemplo.json a dispositivos.json '
            f'y complétalo con tus lavadoras (ver encabezado de este script).',
            file=sys.stderr,
        )
        sys.exit(1)
    with open(ruta, encoding='utf-8') as f:
        config = json.load(f)
    if 'url_base' not in config or not config.get('dispositivos'):
        print('dispositivos.json inválido: falta "url_base" o "dispositivos".', file=sys.stderr)
        sys.exit(1)
    return config


def consultar_shelly(ip_shelly):
    """GET local al Shelly. Retorna (potencia_w, relay_on) o None si falla."""
    url = f'http://{ip_shelly}/status'
    try:
        resp = requests.get(url, timeout=TIMEOUT_LOCAL_SEG)
        resp.raise_for_status()
        data = resp.json()
        meter = data['meters'][0]
        if not meter.get('is_valid', True):
            print(f'  [{ip_shelly}] lectura marcada is_valid=False por el dispositivo, se omite.')
            return None
        potencia_w = float(meter['power'])
        relay_on = bool(data['relays'][0]['ison'])
        return potencia_w, relay_on
    except requests.RequestException as exc:
        print(f'  [{ip_shelly}] error consultando Shelly local: {exc}', file=sys.stderr)
        return None
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        print(f'  [{ip_shelly}] respuesta inesperada del Shelly: {exc}', file=sys.stderr)
        return None


def enviar_lectura(url_base, token, potencia_w, relay_on):
    """POST al endpoint de Lightsail. Retorna True/False según éxito."""
    url = f"{url_base.rstrip('/')}/api/lavadoras/{token}/lectura/"
    try:
        resp = requests.post(
            url,
            json={'potencia_w': potencia_w, 'relay_on': relay_on},
            timeout=TIMEOUT_REMOTO_SEG,
        )
        resp.raise_for_status()
        return True
    except requests.RequestException as exc:
        print(f'  error enviando a {url}: {exc}', file=sys.stderr)
        return False


def ejecutar_una_ronda(config):
    url_base = config['url_base']
    ok, fallos = 0, 0
    for disp in config['dispositivos']:
        nombre = disp.get('nombre', disp['ip_shelly'])
        print(f'[{nombre}]')
        lectura = consultar_shelly(disp['ip_shelly'])
        if lectura is None:
            fallos += 1
            continue
        potencia_w, relay_on = lectura
        enviado = enviar_lectura(url_base, disp['token'], potencia_w, relay_on)
        if enviado:
            print(f'  {potencia_w:.1f}W, relay_on={relay_on} -> enviado OK')
            ok += 1
        else:
            fallos += 1
    print(f'Ronda terminada: {ok} enviada(s), {fallos} fallo(s).')


def main():
    parser = argparse.ArgumentParser(description='Agente local: Shelly (LAN edificio) -> Django (Lightsail)')
    parser.add_argument('--config', type=Path, default=CONFIG_POR_DEFECTO, help='Ruta a dispositivos.json')
    parser.add_argument(
        '--intervalo', type=int, default=None,
        help='Si se especifica, corre en loop cada N segundos en vez de una sola vez.',
    )
    args = parser.parse_args()

    config = cargar_configuracion(args.config)

    if args.intervalo is None:
        ejecutar_una_ronda(config)
        return

    print(f'Corriendo en loop cada {args.intervalo}s. Ctrl+C para detener.')
    try:
        while True:
            ejecutar_una_ronda(config)
            time.sleep(args.intervalo)
    except KeyboardInterrupt:
        print('Detenido por el usuario.')


if __name__ == '__main__':
    main()
