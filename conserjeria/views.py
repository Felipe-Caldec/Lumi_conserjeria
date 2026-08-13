from datetime import datetime, time, timedelta
from functools import wraps

from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth import logout as auth_logout
from django.contrib.auth import update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Count, ProtectedError, Q
from django.db.models.functions import ExtractHour, TruncDate, TruncMonth, TruncWeek
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt


from django_ratelimit.core import is_ratelimited

from transbank.error.transbank_error import TransbankError

from . import pagos

from .forms import (
    AreaComunForm,
    BloqueHorarioAreaComunForm,
    CambiarPasswordObligatorioForm,
    ConsultaEstacionamientoForm,
    CrearAdminEdificioForm,
    CrearConserjeForm,
    DepartamentoForm,
    EdificioForm,
    EdificioPagoForm,
    EditarConserjeForm,
    EstacionamientoHistorialFiltroForm,
    EstacionarForm,
    LoginForm,
    MudanzaForm,
    NumeroEstacionamientoForm,
    PaqueteriaEntregarForm,
    PaqueteriaHistorialFiltroForm,
    PaqueteriaRecibirForm,
    ReportesFiltroForm,
    ReservaPublicaForm,
    ResidenteForm,
    ReservaAreaComunForm,
    VisitaForm,
)
from .models import (
    AreaComun,
    BloqueHorarioAreaComun,
    Departamento,
    Edificio,
    EstacionamientoVisita,
    Lavadora,
    Mudanza,
    NumeroEstacionamiento,
    Paqueteria,
    PagoReserva,
    PerfilUsuario,
    Residente,
    ReservaAreaComun,
    Visita,
)
from .validators import formatear_rut


# ---------------------------------------------------------------------------
# Decoradores de control de acceso
# ---------------------------------------------------------------------------
def superadmin_required(view_func):
    """Sólo el administrador de la aplicación (User.is_superuser=True)."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_superuser:
            raise PermissionDenied('Sólo el Administrador de la aplicación puede acceder a esta sección.')
        return view_func(request, *args, **kwargs)
    return _wrapped


def admin_edificio_required(view_func):
    """Sólo usuarios con PerfilUsuario.rol == ADMIN_EDIFICIO."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not request.perfil or not request.perfil.es_admin_edificio:
            raise PermissionDenied('Sólo un Administrador de Edificio puede acceder a esta sección.')
        return view_func(request, *args, **kwargs)
    return _wrapped


def staff_edificio_required(view_func):
    """AdminEdificio o Conserje: cualquier cuenta con edificio asignado.
    Usado en los módulos operativos, que ambos roles pueden operar."""
    @wraps(view_func)
    @login_required
    def _wrapped(request, *args, **kwargs):
        if not request.perfil:
            raise PermissionDenied('No tienes un perfil de edificio asignado.')
        return view_func(request, *args, **kwargs)
    return _wrapped


def requiere_modulo(campo_modulo):
    """Bloquea la vista si el edificio del usuario logueado no tiene
    habilitado el módulo complementario `campo_modulo` (uno de
    Edificio.modulo_reservas / modulo_consulta_estacionamiento /
    modulo_lavadoras). Se usa DESPUÉS de admin_edificio_required o
    staff_edificio_required en la cadena de decoradores, así el rol ya se
    validó antes de fijarnos en el módulo.

    Sólo el SuperAdmin puede prender/apagar estos módulos (ver EdificioForm
    y editar_edificio) -- son complementos que agregan carga al servidor,
    no algo que el AdminEdificio pueda activarse a sí mismo."""
    def decorador(view_func):
        @wraps(view_func)
        def _wrapped(request, *args, **kwargs):
            if not getattr(request.edificio, campo_modulo, False):
                raise PermissionDenied(
                    'Este módulo no está habilitado para tu edificio. '
                    'Contacta al Administrador de la aplicación si lo necesitas.'
                )
            return view_func(request, *args, **kwargs)
        return _wrapped
    return decorador


# ---------------------------------------------------------------------------
# Reserva pública de Áreas Comunes (sin login, desde la pantalla de login)
# ---------------------------------------------------------------------------

def _demasiados_intentos(request, accion, limite='10/m'):
    """Throttling por IP para los formularios públicos que validan un RUT
    contra Residente (reserva pública, consulta de estacionamiento, consulta
    de lavadoras). Sin login de por medio, son el único punto donde alguien
    podría automatizar prueba-y-error de RUTs por departamento -- esto no
    reemplaza el mensaje de error genérico ya existente, lo complementa.

    `group` distinto por acción: agotar el cupo de una consulta no bloquea
    las otras. Devuelve True (y ya deja el mensaje de error listo) si hay
    que cortar la request acá."""
    bloqueado = is_ratelimited(
        request, group=f'conserjeria-{accion}', key='ip', rate=limite,
        method='POST', increment=True,
    )
    if bloqueado:
        messages.error(
            request, 'Demasiados intentos. Espera un minuto antes de volver a intentarlo.'
        )
    return bloqueado

def _generar_opciones_horas(hora_inicio, hora_termino, paso_minutos=30):
    """Genera horas en incrementos de `paso_minutos` dentro de [inicio, término]."""
    cursor = datetime.combine(datetime.min, hora_inicio)
    fin = datetime.combine(datetime.min, hora_termino)
    opciones = []
    while cursor <= fin:
        opciones.append(cursor.time())
        cursor += timedelta(minutes=paso_minutos)
    return opciones


def _calcular_bloques_libres(area_comun, dia_reserva, duracion_minima_min=30):
    """Cruza el horario configurado del área (BloqueHorarioAreaComun) con las
    reservas ya existentes ese día (PENDIENTE o CONFIRMADA, ambas ocupan el
    horario) y devuelve los tramos libres restantes como lista de
    (hora_inicio, hora_termino)."""
    codigo_dia = BloqueHorarioAreaComun.CODIGOS_POR_WEEKDAY[dia_reserva.weekday()]
    bloques = BloqueHorarioAreaComun.objects.filter(area_comun=area_comun).filter(
        Q(dia_semana=codigo_dia) | Q(dia_semana=BloqueHorarioAreaComun.DIA_TODOS)
    ).order_by('hora_inicio')

    ocupados = sorted(
        ReservaAreaComun.objects.filter(area_comun=area_comun, dia_reserva=dia_reserva)
        .exclude(estado=ReservaAreaComun.CANCELADA)
        .values_list('hora_inicio', 'hora_termino')
    )

    libres = []
    for bloque in bloques:
        cursor = bloque.hora_inicio
        fin_bloque = bloque.hora_termino
        relevantes = [o for o in ocupados if o[0] < fin_bloque and o[1] > cursor]
        for o_inicio, o_fin in relevantes:
            if o_inicio > cursor:
                libres.append((cursor, min(o_inicio, fin_bloque)))
            if o_fin > cursor:
                cursor = o_fin
            if cursor >= fin_bloque:
                break
        if cursor < fin_bloque:
            libres.append((cursor, fin_bloque))

    def _minutos(t):
        return t.hour * 60 + t.minute

    return [
        (ini, fin) for ini, fin in libres
        if _minutos(fin) - _minutos(ini) >= duracion_minima_min
    ]


# ---------------------------------------------------------------------------
# Autenticación (+ reserva pública de área común, en la misma pantalla)
# ---------------------------------------------------------------------------
def login_view(request):
    if request.user.is_authenticated:
        return redirect('conserjeria:home_redirect')

    # ----- Formulario de login (siempre presente) -----
    if request.method == 'POST' and request.POST.get('action') != 'reservar_area_comun':
        login_form = LoginForm(request, data=request.POST)
        if login_form.is_valid():
            auth_login(request, login_form.get_user())
            return redirect('conserjeria:home_redirect')
    else:
        login_form = LoginForm(request)

    # ----- Reserva pública de área común (estado del wizard vía GET/POST) -----
    datos = request.POST if request.method == 'POST' else request.GET
    edificios = Edificio.objects.filter(activo=True)

    edificio_sel = edificios.filter(pk=datos.get('edificio_id')).first() if datos.get('edificio_id') else None
    areas = (
        AreaComun.objects.filter(edificio=edificio_sel)
        if edificio_sel and edificio_sel.modulo_reservas else AreaComun.objects.none()
    )
    area_sel = areas.filter(pk=datos.get('area_id')).first() if datos.get('area_id') else None
    departamentos = Departamento.objects.filter(edificio=edificio_sel) if edificio_sel else Departamento.objects.none()

    dia_sel = None
    if datos.get('dia'):
        try:
            candidato = datetime.strptime(datos['dia'], '%Y-%m-%d').date()
            if candidato >= timezone.localdate():
                dia_sel = candidato
        except ValueError:
            dia_sel = None

    bloques_libres = _calcular_bloques_libres(area_sel, dia_sel) if (area_sel and dia_sel) else []

    bloque_sel = None
    if datos.get('bloque_idx') is not None and datos.get('bloque_idx') != '':
        try:
            bloque_sel = bloques_libres[int(datos['bloque_idx'])]
        except (ValueError, IndexError):
            bloque_sel = None

    opciones_horas = _generar_opciones_horas(bloque_sel[0], bloque_sel[1]) if bloque_sel else []

    if request.method == 'POST' and request.POST.get('action') == 'reservar_area_comun':
        reserva_form = ReservaPublicaForm(
            request.POST, departamentos=departamentos, opciones_horas=opciones_horas
        )
        if edificio_sel and not edificio_sel.modulo_reservas:
            messages.error(request, 'Este edificio no tiene reservas de áreas comunes habilitado.')
        elif not (edificio_sel and area_sel and dia_sel and bloque_sel):
            messages.error(request, 'Selecciona edificio, área, día y horario antes de reservar.')
        elif reserva_form.is_valid():
            hora_inicio = reserva_form.cleaned_data['hora_inicio']
            hora_termino = reserva_form.cleaned_data['hora_termino']
            rut = reserva_form.cleaned_data['rut']
            departamento = reserva_form.cleaned_data['departamento']
            duracion_horas = (
                datetime.combine(datetime.min, hora_termino) - datetime.combine(datetime.min, hora_inicio)
            ).total_seconds() / 3600 if hora_inicio and hora_termino else 0

            if hora_inicio >= hora_termino:
                reserva_form.add_error('hora_termino', 'La hora de término debe ser posterior al inicio.')
            elif not (bloque_sel[0] <= hora_inicio and hora_termino <= bloque_sel[1]):
                reserva_form.add_error(None, 'Ese horario ya no está disponible. Vuelve a seleccionar.')
            elif area_sel.duracion_maxima_horas and duracion_horas > area_sel.duracion_maxima_horas:
                reserva_form.add_error(
                    None,
                    f'{area_sel.nombre} solo puede reservarse por un máximo de '
                    f'{area_sel.duracion_maxima_horas} horas seguidas.'
                )
            elif not Residente.objects.filter(
                departamento=departamento, rut=formatear_rut(rut)
            ).exists():
                # Exige que el RUT coincida con un residente de ESE departamento
                # exacto (no basta con ser residente de cualquier depto del
                # edificio). Mensaje deliberadamente genérico: no confirma ni
                # descarta si el RUT existe en otro departamento/edificio, para
                # no filtrar información de residentes a través de un
                # formulario público.
                reserva_form.add_error(
                    'rut', 'No pudimos verificarte como residente de ese departamento con ese RUT.'
                )
            else:
                nueva_reserva = None
                with transaction.atomic():
                    superpuestas = ReservaAreaComun.objects.select_for_update().filter(
                        area_comun=area_sel, dia_reserva=dia_sel,
                        hora_inicio__lt=hora_termino, hora_termino__gt=hora_inicio,
                    ).exclude(estado=ReservaAreaComun.CANCELADA)
                    if superpuestas.exists():
                        reserva_form.add_error(
                            None, 'Justo se ocupó ese horario. Por favor elige otro tramo disponible.'
                        )
                    else:
                        # No todas las áreas cobran garantía (AreaComun.requiere_pago):
                        # si no cobra, se confirma de inmediato -- no hay nada que
                        # esperar. Si cobra, queda PENDIENTE hasta que Webpay
                        # confirme el pago (ver más abajo); el pago presencial ya
                        # no existe como camino, así que acá nunca se vuelve a
                        # crear una reserva PENDIENTE "esperando en conserjería".
                        nueva_reserva = ReservaAreaComun.objects.create(
                            area_comun=area_sel,
                            departamento=departamento,
                            dia_reserva=dia_sel,
                            responsable=reserva_form.cleaned_data['nombre'],
                            rut_responsable=rut,
                            hora_inicio=hora_inicio,
                            hora_termino=hora_termino,
                            comentario=reserva_form.cleaned_data.get('comentario', ''),
                            estado=(
                                ReservaAreaComun.PENDIENTE if area_sel.requiere_pago
                                else ReservaAreaComun.CONFIRMADA
                            ),
                            conserje_registra=None,
                        )

                # OJO: todo lo que sigue queda A PROPÓSITO fuera del
                # transaction.atomic() de arriba -- iniciar_pago() hace una
                # llamada HTTP a Transbank, y esa llamada no debe hacerse
                # mientras la fila de la reserva sigue con lock tomado.
                if nueva_reserva and not area_sel.requiere_pago:
                    messages.success(request, 'Reserva confirmada.')
                    return redirect('conserjeria:login')
                elif nueva_reserva and area_sel.requiere_pago:
                    return_url = request.build_absolute_uri(reverse('conserjeria:webpay_retorno'))
                    try:
                        url_pago, token_ws, _pago = pagos.iniciar_pago(nueva_reserva, return_url)
                    except (pagos.PagoNoDisponibleError, TransbankError):
                        # El edificio no está afiliado a Webpay, o Transbank no
                        # respondió: no dejamos el horario bloqueado por una
                        # reserva que nunca va a poder pagarse.
                        nueva_reserva.estado = ReservaAreaComun.CANCELADA
                        nueva_reserva.save(update_fields=['estado'])
                        messages.error(
                            request,
                            'No fue posible iniciar el pago en este momento. '
                            'Intenta nuevamente en unos minutos o contacta a conserjería.'
                        )
                    else:
                        return render(request, 'conserjeria/webpay_redirigir.html', {
                            'form_url': url_pago, 'token_ws': token_ws,
                        })
    else:
        reserva_form = ReservaPublicaForm(departamentos=departamentos, opciones_horas=opciones_horas)

    # ----- Consulta pública de estacionamientos de visita disponibles -----
    # Bloque independiente del de reserva de área común (edificio propio,
    # sin pasar por área/día/horario). El RUT se valida contra Residente del
    # departamento exacto elegido, igual criterio y mismo mensaje genérico
    # que en la reserva pública, para no filtrar información de residentes.
    estac_edificio_sel = (
        edificios.filter(pk=datos.get('estac_edificio_id')).first()
        if datos.get('estac_edificio_id') else None
    )
    estac_departamentos = (
        Departamento.objects.filter(edificio=estac_edificio_sel)
        if estac_edificio_sel and estac_edificio_sel.modulo_consulta_estacionamiento else Departamento.objects.none()
    )
    estacionamientos_disponibles = None  # None = aún no se ha consultado

    if request.method == 'POST' and request.POST.get('action') == 'consultar_estacionamiento':
        consulta_form = ConsultaEstacionamientoForm(request.POST, departamentos=estac_departamentos)
        if not estac_edificio_sel:
            messages.error(request, 'Selecciona tu edificio antes de consultar.')
        elif not estac_edificio_sel.modulo_consulta_estacionamiento:
            messages.error(request, 'Este edificio no tiene la consulta de estacionamientos de visita habilitada.')
        elif consulta_form.is_valid():
            departamento = consulta_form.cleaned_data['departamento']
            rut = consulta_form.cleaned_data['rut']
            if not Residente.objects.filter(departamento=departamento, rut=formatear_rut(rut)).exists():
                consulta_form.add_error(
                    'rut', 'No pudimos verificarte como residente de ese departamento con ese RUT.'
                )
            else:
                ocupados_ids = EstacionamientoVisita.objects.filter(
                    estado=EstacionamientoVisita.ACTIVO,
                    numero_estacionamiento__edificio=estac_edificio_sel,
                ).values_list('numero_estacionamiento_id', flat=True)
                estacionamientos_disponibles = list(
                    NumeroEstacionamiento.objects.filter(edificio=estac_edificio_sel).exclude(id__in=ocupados_ids)
                )
    else:
        consulta_form = ConsultaEstacionamientoForm(departamentos=estac_departamentos)

    # ----- Consulta pública de disponibilidad de lavadoras -----
    # Mismo patrón que la consulta de estacionamientos de visita: bloque
    # independiente, edificio propio, verificación de RUT contra Residente
    # del departamento exacto elegido. Reutiliza ConsultaEstacionamientoForm
    # porque el formulario (departamento + RUT) es idéntico -- no hay nada
    # específico de lavadoras que justifique un form aparte.
    lavadoras_edificio_sel = (
        edificios.filter(pk=datos.get('lavadoras_edificio_id')).first()
        if datos.get('lavadoras_edificio_id') else None
    )
    lavadoras_departamentos = (
        Departamento.objects.filter(edificio=lavadoras_edificio_sel)
        if lavadoras_edificio_sel and lavadoras_edificio_sel.modulo_lavadoras else Departamento.objects.none()
    )
    lavadoras_disponibles = None  # None = aún no se ha consultado

    if request.method == 'POST' and request.POST.get('action') == 'consultar_lavadoras':
        consulta_lavadoras_form = ConsultaEstacionamientoForm(request.POST, departamentos=lavadoras_departamentos)
        if not lavadoras_edificio_sel:
            messages.error(request, 'Selecciona tu edificio antes de consultar.')
        elif not lavadoras_edificio_sel.modulo_lavadoras:
            messages.error(request, 'Este edificio no tiene el monitoreo de lavadoras habilitado.')
        elif consulta_lavadoras_form.is_valid():
            departamento = consulta_lavadoras_form.cleaned_data['departamento']
            rut = consulta_lavadoras_form.cleaned_data['rut']
            if not Residente.objects.filter(departamento=departamento, rut=formatear_rut(rut)).exists():
                consulta_lavadoras_form.add_error(
                    'rut', 'No pudimos verificarte como residente de ese departamento con ese RUT.'
                )
            else:
                lavadoras_disponibles = list(
                    Lavadora.objects.filter(edificio=lavadoras_edificio_sel, activa=True).order_by('nombre')
                )
    else:
        consulta_lavadoras_form = ConsultaEstacionamientoForm(departamentos=lavadoras_departamentos)

    return render(request, 'registration/login.html', {
        'form': login_form,
        'edificios': edificios,
        'edificio_sel': edificio_sel,
        'areas': areas,
        'area_sel': area_sel,
        'dia_sel': dia_sel,
        'bloques_libres': list(enumerate(bloques_libres)),
        'bloque_idx_sel': datos.get('bloque_idx', ''),
        'bloque_sel': bloque_sel,
        'reserva_form': reserva_form,
        'hoy': timezone.localdate(),
        'estac_edificio_sel': estac_edificio_sel,
        'consulta_form': consulta_form,
        'estacionamientos_disponibles': estacionamientos_disponibles,
        'lavadoras_edificio_sel': lavadoras_edificio_sel,
        'consulta_lavadoras_form': consulta_lavadoras_form,
        'lavadoras_disponibles': lavadoras_disponibles,
    })


@csrf_exempt
def webpay_retorno(request):
    """Transbank redirige el navegador del residente para acá (vía POST)
    después de que completa -- o abandona -- el pago en Webpay. Es pública
    (sin login: el residente nunca inició sesión) y csrf_exempt porque el
    POST lo arma Transbank, no un formulario nuestro con token CSRF."""
    datos = request.POST if request.method == 'POST' else request.GET
    token_ws = datos.get('token_ws')
    tbk_token = datos.get('TBK_TOKEN')

    if token_ws:
        pago = pagos.confirmar_pago(token_ws)
        if pago and pago.estado == PagoReserva.APROBADO:
            messages.success(request, 'Pago aprobado. Tu reserva quedó confirmada.')
        else:
            messages.error(request, 'El pago fue rechazado. La reserva se canceló y el horario quedó libre.')
    elif tbk_token:
        # El residente abandonó Webpay antes de pagar (botón "volver" o
        # cerró la pestaña). Transbank manda TBK_TOKEN, no token_ws, en
        # este caso -- nunca hubo un commit que hacer.
        pago = PagoReserva.objects.filter(token_ws=tbk_token).first()
        if pago:
            pagos.abortar_pago(pago, {'abandono': True})
        messages.warning(request, 'Pago no completado. La reserva se canceló y el horario quedó libre.')
    else:
        # Timeout de Transbank sin token alguno: no hay forma de saber a
        # qué pago corresponde desde acá. El management command de barrido
        # (expirar_pagos_pendientes) libera el horario igual, más tarde.
        messages.warning(request, 'No se recibió confirmación de pago desde Webpay.')

    return redirect('conserjeria:login')


@login_required
def logout_view(request):
    auth_logout(request)
    return redirect('conserjeria:login')


@login_required
def cambiar_password_obligatorio(request):
    """Pantalla forzada por EdificioMiddleware mientras
    perfil.debe_cambiar_contrasena sea True (siempre en el primer ingreso de
    un Conserje o AdminEdificio recién creado). Al guardar, limpia el flag y
    deja seguir a la pantalla que le corresponda según su rol."""
    if request.method == 'POST':
        form = CambiarPasswordObligatorioForm(request.user, request.POST)
        if form.is_valid():
            user = form.save()
            update_session_auth_hash(request, user)  # no cerrar la sesión actual
            if request.perfil:
                request.perfil.debe_cambiar_contrasena = False
                request.perfil.save(update_fields=['debe_cambiar_contrasena'])
            messages.success(request, 'Contraseña actualizada correctamente.')
            return redirect('conserjeria:home_redirect')
    else:
        form = CambiarPasswordObligatorioForm(request.user)
    return render(request, 'conserjeria/cambiar_password_obligatorio.html', {'form': form})


@login_required
def home_redirect(request):
    """Redirige según el rol: SuperAdmin -> Edificios, AdminEdificio -> su
    dashboard, Conserje -> su dashboard."""
    if request.user.is_superuser:
        return redirect('conserjeria:listar_edificios')
    if request.perfil and request.perfil.es_admin_edificio:
        return redirect('conserjeria:admin_dashboard')
    if request.perfil and request.perfil.es_conserje:
        return redirect('conserjeria:conserje_dashboard')
    raise PermissionDenied('Tu cuenta no tiene un rol válido asignado.')


# ---------------------------------------------------------------------------
# SuperAdmin: gestión de Edificios y cuentas de AdminEdificio
# ---------------------------------------------------------------------------
@superadmin_required
def listar_edificios(request):
    edificios = Edificio.objects.all()
    return render(request, 'conserjeria/listar_edificios.html', {'edificios': edificios})


@superadmin_required
def crear_edificio(request):
    if request.method == 'POST':
        form = EdificioForm(request.POST)
        if form.is_valid():
            edificio = form.save()
            messages.success(request, f"Edificio '{edificio.nombre}' creado.")
            return redirect('conserjeria:listar_edificios')
    else:
        form = EdificioForm()
    return render(request, 'conserjeria/crear_edificio.html', {'form': form})


@superadmin_required
def editar_edificio(request, pk):
    """Acá el SuperAdmin habilita/deshabilita los módulos complementarios
    (reservas, consulta de estacionamiento, lavadoras) de un edificio ya
    existente, y actualiza su código de comercio Webpay cuando Transbank
    lo entrega."""
    edificio = get_object_or_404(Edificio, pk=pk)
    if request.method == 'POST':
        form = EdificioForm(request.POST, instance=edificio)
        if form.is_valid():
            form.save()
            messages.success(request, f"Edificio '{edificio.nombre}' actualizado.")
            return redirect('conserjeria:listar_edificios')
    else:
        form = EdificioForm(instance=edificio)
    return render(request, 'conserjeria/editar_edificio.html', {'form': form, 'edificio': edificio})


@superadmin_required
def listar_admins_edificio(request):
    admins = PerfilUsuario.objects.filter(
        rol=PerfilUsuario.ROL_ADMIN_EDIFICIO
    ).select_related('edificio', 'user')
    return render(request, 'conserjeria/listar_admins_edificio.html', {'admins': admins})


@superadmin_required
def crear_admin_edificio(request):
    if request.method == 'POST':
        form = CrearAdminEdificioForm(request.POST)
        if form.is_valid():
            perfil = form.save()
            messages.success(
                request, f"Administrador '{perfil.nombre} {perfil.apellido}' creado para {perfil.edificio}."
            )
            return redirect('conserjeria:listar_admins_edificio')
    else:
        form = CrearAdminEdificioForm()
    return render(request, 'conserjeria/crear_admin_edificio.html', {'form': form})


@superadmin_required
def editar_admin_edificio(request, pk):
    """El SuperAdmin edita los datos de un AdminEdificio y, si lo necesita
    (ej. olvidó su contraseña), se la resetea desde acá. Reutiliza
    EditarConserjeForm -- la lógica de forzar el cambio de contraseña en el
    próximo ingreso (debe_cambiar_contrasena) es exactamente la misma que
    para Conserje, sólo cambia el rol del PerfilUsuario que se edita."""
    perfil = get_object_or_404(PerfilUsuario, pk=pk, rol=PerfilUsuario.ROL_ADMIN_EDIFICIO)
    if request.method == 'POST':
        form = EditarConserjeForm(request.POST, perfil=perfil)
        if form.is_valid():
            form.save()
            messages.success(request, f"Administrador '{perfil.nombre} {perfil.apellido}' actualizado.")
            return redirect('conserjeria:listar_admins_edificio')
    else:
        form = EditarConserjeForm(perfil=perfil)
    return render(request, 'conserjeria/editar_admin_edificio.html', {
        'form': form, 'admin_edificio': perfil,
    })


# ---------------------------------------------------------------------------
# AdminEdificio: dashboard, cuentas de Conserje, Departamentos, Residentes
# (Todo acotado a request.edificio, resuelto por EdificioMiddleware).
# ---------------------------------------------------------------------------
@admin_edificio_required
def admin_dashboard(request):
    edificio = request.edificio
    paquetes = Paqueteria.objects.filter(departamento__edificio=edificio).select_related(
        'departamento', 'destinatario'
    )[:20]
    estacionamientos = EstacionamientoVisita.objects.filter(
        departamento_destino__edificio=edificio
    ).select_related('numero_estacionamiento', 'departamento_destino')[:20]
    mudanzas = Mudanza.objects.filter(departamento__edificio=edificio).select_related('departamento')[:20]
    reservas = ReservaAreaComun.objects.filter(departamento__edificio=edificio).select_related(
        'area_comun', 'departamento'
    )[:20]
    visitas = Visita.objects.filter(departamento__edificio=edificio).select_related('departamento')[:20]
    return render(request, 'conserjeria/admin_dashboard.html', {
        'paquetes': paquetes,
        'estacionamientos': estacionamientos,
        'mudanzas': mudanzas,
        'reservas': reservas,
        'visitas': visitas,
    })


@admin_edificio_required
def listar_conserjes(request):
    """CRUD principal de Conserje: lista con la info clave (nombre, usuario,
    rut, teléfono, dirección) y botones de editar/eliminar. Sólo conserjes
    activos del propio edificio -- los "eliminados" (desactivados) dejan de
    aparecer acá, ver `eliminar_conserje`."""
    conserjes = PerfilUsuario.objects.filter(
        edificio=request.edificio, rol=PerfilUsuario.ROL_CONSERJE, user__is_active=True,
    ).select_related('user').order_by('apellido', 'nombre')
    return render(request, 'conserjeria/listar_conserjes.html', {'conserjes': conserjes})


@admin_edificio_required
def crear_conserje(request):
    if request.method == 'POST':
        form = CrearConserjeForm(request.POST)
        if form.is_valid():
            perfil = form.save(edificio=request.edificio)  # <- clave del aislamiento
            messages.success(request, f"Conserje '{perfil.nombre} {perfil.apellido}' creado exitosamente.")
            return redirect('conserjeria:listar_conserjes')
    else:
        form = CrearConserjeForm()
    return render(request, 'conserjeria/crear_conserje.html', {'form': form})


@admin_edificio_required
def editar_conserje(request, pk):
    """Edición acotada al edificio del administrador: get_object_or_404 con
    filtro de edificio impide editar (o siquiera detectar) conserjes de otro
    edificio, aunque el pk se manipule en la URL."""
    perfil = get_object_or_404(
        PerfilUsuario, pk=pk, edificio=request.edificio, rol=PerfilUsuario.ROL_CONSERJE,
    )
    if request.method == 'POST':
        form = EditarConserjeForm(request.POST, perfil=perfil)
        if form.is_valid():
            form.save()
            messages.success(request, f"Conserje '{perfil.nombre} {perfil.apellido}' actualizado.")
            return redirect('conserjeria:listar_conserjes')
    else:
        form = EditarConserjeForm(perfil=perfil)
    return render(request, 'conserjeria/editar_conserje.html', {'form': form, 'conserje': perfil})


@admin_edificio_required
def eliminar_conserje(request, pk):
    """Elimina la cuenta del conserje. Si el conserje ya tiene historial
    asociado (paquetes recibidos, visitas registradas, etc. -- protegidos con
    on_delete=PROTECT), no se puede borrar sin perder trazabilidad: en ese
    caso se desactiva la cuenta (User.is_active=False) en vez de borrarla,
    lo que ya la saca de `listar_conserjes` y le impide iniciar sesión."""
    perfil = get_object_or_404(
        PerfilUsuario, pk=pk, edificio=request.edificio, rol=PerfilUsuario.ROL_CONSERJE,
    )
    if request.method == 'POST':
        nombre_completo = f'{perfil.nombre} {perfil.apellido}'
        user = perfil.user
        try:
            with transaction.atomic():
                user.delete()  # cascada: borra también el PerfilUsuario
            messages.success(request, f"Conserje '{nombre_completo}' eliminado.")
        except ProtectedError:
            user.is_active = False
            user.save(update_fields=['is_active'])
            messages.warning(
                request,
                f"'{nombre_completo}' tiene historial asociado (paquetes, visitas, etc.), así "
                "que no se puede borrar sin perder esos registros. Se desactivó su cuenta: ya "
                "no aparecerá en la lista ni podrá iniciar sesión."
            )
        return redirect('conserjeria:listar_conserjes')
    return render(request, 'conserjeria/eliminar_conserje.html', {'conserje': perfil})


@admin_edificio_required
def crear_departamento(request):
    if request.method == 'POST':
        form = DepartamentoForm(request.POST)
        if form.is_valid():
            departamento = form.save(commit=False)
            departamento.edificio = request.edificio  # nunca desde el POST
            departamento.save()
            messages.success(request, 'Departamento creado.')
            return redirect('conserjeria:crear_departamento')
    else:
        form = DepartamentoForm()
    departamentos = Departamento.objects.filter(edificio=request.edificio)
    return render(request, 'conserjeria/crear_departamento.html', {
        'form': form, 'departamentos': departamentos,
    })


@admin_edificio_required
def editar_departamento(request, pk):
    """Edición acotada al edificio del administrador: get_object_or_404 con
    filtro de edificio impide editar (o siquiera detectar) departamentos de
    otro edificio, aunque el pk se manipule en la URL."""
    departamento = get_object_or_404(Departamento, pk=pk, edificio=request.edificio)
    if request.method == 'POST':
        form = DepartamentoForm(request.POST, instance=departamento)
        if form.is_valid():
            form.save()
            messages.success(request, f"Departamento '{departamento.numero}' actualizado.")
            return redirect('conserjeria:crear_departamento')
    else:
        form = DepartamentoForm(instance=departamento)
    return render(request, 'conserjeria/editar_departamento.html', {
        'form': form, 'departamento': departamento,
    })


@admin_edificio_required
def eliminar_departamento(request, pk):
    """OJO: Residente, Paqueteria, Mudanza, ReservaAreaComun y Visita
    apuntan a Departamento con on_delete=CASCADE, así que borrar un
    departamento borra también TODO su historial asociado (residentes,
    paquetes, mudanzas, reservas, visitas). Se avisa explícitamente en la
    plantilla de confirmación, igual que con Área Común."""
    departamento = get_object_or_404(Departamento, pk=pk, edificio=request.edificio)
    if request.method == 'POST':
        numero = departamento.numero
        departamento.delete()
        messages.success(request, f"Departamento '{numero}' eliminado.")
        return redirect('conserjeria:crear_departamento')
    return render(request, 'conserjeria/eliminar_departamento.html', {'departamento': departamento})


@admin_edificio_required
def crear_residente(request):
    if request.method == 'POST':
        form = ResidenteForm(request.POST, edificio=request.edificio)
        if form.is_valid():
            form.save()
            messages.success(request, 'Residente creado.')
            return redirect('conserjeria:crear_residente')
    else:
        form = ResidenteForm(edificio=request.edificio)
    residentes = Residente.objects.filter(
        departamento__edificio=request.edificio
    ).select_related('departamento')
    return render(request, 'conserjeria/crear_residente.html', {
        'form': form, 'residentes': residentes,
    })


@admin_edificio_required
def editar_residente(request, pk):
    """Edición acotada al edificio del administrador vía el departamento
    (Residente no tiene FK directa a Edificio)."""
    residente = get_object_or_404(
        Residente, pk=pk, departamento__edificio=request.edificio
    )
    if request.method == 'POST':
        form = ResidenteForm(request.POST, instance=residente, edificio=request.edificio)
        if form.is_valid():
            form.save()
            messages.success(request, f"Residente '{residente.nombre} {residente.apellido}' actualizado.")
            return redirect('conserjeria:crear_residente')
    else:
        form = ResidenteForm(instance=residente, edificio=request.edificio)
    return render(request, 'conserjeria/editar_residente.html', {
        'form': form, 'residente': residente,
    })


@admin_edificio_required
def eliminar_residente(request, pk):
    """A diferencia de Departamento, esto es seguro: Paqueteria.destinatario
    y Paqueteria.retirado_por usan on_delete=SET_NULL hacia Residente, así
    que borrar un residente no borra historial de paquetes -- sólo deja esos
    dos campos en null (el paquete y su departamento siguen intactos)."""
    residente = get_object_or_404(
        Residente, pk=pk, departamento__edificio=request.edificio
    )
    if request.method == 'POST':
        nombre_completo = f'{residente.nombre} {residente.apellido}'
        residente.delete()
        messages.success(request, f"Residente '{nombre_completo}' eliminado.")
        return redirect('conserjeria:crear_residente')
    return render(request, 'conserjeria/eliminar_residente.html', {'residente': residente})


@admin_edificio_required
def visualizar_residentes(request):
    """Visualizador de residentes filtrado por departamento (recarga de página)."""
    departamento_id = request.GET.get('departamento_id')
    departamentos = Departamento.objects.filter(edificio=request.edificio)
    departamento_actual = None
    residentes = Residente.objects.none()

    if departamento_id:
        departamento_actual = departamentos.filter(pk=departamento_id).first()
        if departamento_actual:
            residentes = Residente.objects.filter(departamento=departamento_actual)

    return render(request, 'conserjeria/visualizar_residentes.html', {
        'departamentos': departamentos,
        'departamento_id': departamento_id,
        'departamento_actual': departamento_actual,
        'residentes': residentes,
    })


# ---------------------------------------------------------------------------
# Dashboard principal del Conserje (8 botones)
# ---------------------------------------------------------------------------
@staff_edificio_required
def conserje_dashboard(request):
    return render(request, 'conserjeria/conserje_dashboard.html')


# ---------------------------------------------------------------------------
# MÓDULO PAQUETERÍA
# ---------------------------------------------------------------------------
@staff_edificio_required
def paqueteria_recibir(request):
    departamento_id = request.POST.get('departamento_id') or request.GET.get('departamento_id')

    if request.method == 'POST':
        form = PaqueteriaRecibirForm(request.POST, departamento_id=departamento_id)
        if form.is_valid():
            # get_object_or_404 con el filtro de edificio: un ID de otro
            # edificio nunca es válido acá, aunque llegue manipulado.
            departamento = get_object_or_404(
                Departamento, pk=departamento_id, edificio=request.edificio
            ) if departamento_id else None
            if departamento is None:
                messages.error(request, 'Debe seleccionar un departamento.')
            else:
                paquete = form.save(commit=False)
                paquete.departamento = departamento
                paquete.conserje_recibe = request.user
                paquete.save()
                messages.success(request, 'Paquete recibido correctamente.')
                return redirect('conserjeria:paqueteria_recibir')
    else:
        form = PaqueteriaRecibirForm(departamento_id=departamento_id)

    departamentos = Departamento.objects.filter(edificio=request.edificio)
    departamento_actual = None
    if departamento_id:
        departamento_actual = departamentos.filter(pk=departamento_id).first()

    return render(request, 'conserjeria/paqueteria_recibir.html', {
        'form': form,
        'departamentos': departamentos,
        'departamento_id': departamento_id,
        'departamento_actual': departamento_actual,
    })


@staff_edificio_required
def paqueteria_entregar(request):
    if request.method == 'POST':
        action = request.POST.get('action')
        paquete = get_object_or_404(
            Paqueteria, pk=request.POST.get('paquete_id'), estado='PENDIENTE',
            departamento__edificio=request.edificio,
        )

        if action == 'set_recibido':
            form = PaqueteriaEntregarForm(
                request.POST, instance=paquete, departamento_id=paquete.departamento_id
            )
            if form.is_valid():
                form.save()
        elif action == 'entregar':
            if not paquete.recibido_por_id:
                messages.error(request, 'Debe seleccionar quién retira el paquete antes de entregarlo.')
            else:
                paquete.fecha_entrega = timezone.now()
                paquete.conserje_entrega = request.user
                paquete.estado = 'ENTREGADO'
                paquete.save()
                messages.success(request, 'Paquete entregado correctamente.')
        return redirect('conserjeria:paqueteria_entregar')

    paquetes = Paqueteria.objects.filter(
        estado='PENDIENTE', departamento__edificio=request.edificio
    ).select_related('departamento', 'destinatario', 'recibido_por')
    filas = [
        (p, PaqueteriaEntregarForm(instance=p, departamento_id=p.departamento_id))
        for p in paquetes
    ]
    return render(request, 'conserjeria/paqueteria_entregar.html', {'filas': filas})


@staff_edificio_required
def paqueteria_historial(request):
    filtro_form = PaqueteriaHistorialFiltroForm(request.GET or None, edificio=request.edificio)
    paquetes = Paqueteria.objects.filter(departamento__edificio=request.edificio).select_related(
        'departamento', 'destinatario', 'recibido_por', 'conserje_recibe', 'conserje_entrega'
    )
    if filtro_form.is_valid():
        if filtro_form.cleaned_data.get('fecha_desde'):
            paquetes = paquetes.filter(fecha_recepcion__date__gte=filtro_form.cleaned_data['fecha_desde'])
        if filtro_form.cleaned_data.get('fecha_hasta'):
            paquetes = paquetes.filter(fecha_recepcion__date__lte=filtro_form.cleaned_data['fecha_hasta'])
        if filtro_form.cleaned_data.get('departamento'):
            paquetes = paquetes.filter(departamento=filtro_form.cleaned_data['departamento'])

    return render(request, 'conserjeria/paqueteria_historial.html', {
        'filtro_form': filtro_form,
        'paquetes': paquetes,
    })


# ---------------------------------------------------------------------------
# MÓDULO ESTACIONAMIENTO DE VISITAS
# ---------------------------------------------------------------------------
@staff_edificio_required
def estacionamiento_estacionar(request):
    if request.method == 'POST':
        form = EstacionarForm(request.POST, edificio=request.edificio)
        if form.is_valid():
            registro = form.save(commit=False)
            registro.conserje_entrada = request.user
            registro.save()
            messages.success(request, 'Vehículo estacionado correctamente.')
            return redirect('conserjeria:estacionamiento_estacionar')
    else:
        form = EstacionarForm(edificio=request.edificio)
    return render(request, 'conserjeria/estacionar.html', {'form': form})


@staff_edificio_required
def estacionamiento_salir(request):
    if request.method == 'POST':
        registro = get_object_or_404(
            EstacionamientoVisita, pk=request.POST.get('registro_id'), estado='ACTIVO',
            departamento_destino__edificio=request.edificio,
        )
        registro.fecha_hora_salida = timezone.now()
        registro.conserje_salida = request.user
        registro.estado = 'FINALIZADO'
        registro.save()
        messages.success(request, 'Salida registrada correctamente.')
        return redirect('conserjeria:estacionamiento_salir')

    ahora = timezone.now()
    registros = EstacionamientoVisita.objects.filter(
        estado='ACTIVO', departamento_destino__edificio=request.edificio
    ).select_related('numero_estacionamiento', 'departamento_destino')
    limite_rojo = ahora - timedelta(hours=6)
    return render(request, 'conserjeria/estacionamiento_salir.html', {
        'registros': registros,
        'limite_rojo': limite_rojo,
    })


@staff_edificio_required
def estacionamiento_historial(request):
    filtro_form = EstacionamientoHistorialFiltroForm(request.GET or None, edificio=request.edificio)
    registros = EstacionamientoVisita.objects.filter(
        departamento_destino__edificio=request.edificio
    ).select_related('numero_estacionamiento', 'departamento_destino', 'conserje_entrada', 'conserje_salida')
    if filtro_form.is_valid():
        if filtro_form.cleaned_data.get('fecha_desde'):
            registros = registros.filter(fecha_hora_entrada__date__gte=filtro_form.cleaned_data['fecha_desde'])
        if filtro_form.cleaned_data.get('fecha_hasta'):
            registros = registros.filter(fecha_hora_entrada__date__lte=filtro_form.cleaned_data['fecha_hasta'])
        if filtro_form.cleaned_data.get('departamento'):
            registros = registros.filter(departamento_destino=filtro_form.cleaned_data['departamento'])

    return render(request, 'conserjeria/estacionamiento_historial.html', {
        'filtro_form': filtro_form,
        'registros': registros,
    })


# ---------------------------------------------------------------------------
# MÓDULO REGISTRO DE VISITAS
# ---------------------------------------------------------------------------
@staff_edificio_required
def registro_visitas(request):
    if request.method == 'POST':
        form = VisitaForm(request.POST, edificio=request.edificio)
        if form.is_valid():
            visita = form.save(commit=False)
            visita.conserje_registra = request.user
            visita.save()
            messages.success(request, 'Visita registrada correctamente.')
            return redirect('conserjeria:registro_visitas')
    else:
        form = VisitaForm(edificio=request.edificio)

    visitas = Visita.objects.filter(departamento__edificio=request.edificio).select_related('departamento')

    return render(request, 'conserjeria/visitas.html', {
        'form': form,
        'visitas': visitas,
        'hoy': timezone.localdate(),
    })


# ---------------------------------------------------------------------------
# MÓDULO MUDANZAS
# ---------------------------------------------------------------------------
@staff_edificio_required
def mudanzas_view(request):
    if request.method == 'POST':
        form = MudanzaForm(request.POST, edificio=request.edificio)
        if form.is_valid():
            mudanza = form.save(commit=False)
            mudanza.conserje_registra = request.user
            mudanza.save()
            messages.success(request, 'Mudanza agendada correctamente.')
            return redirect('conserjeria:mudanzas')
    else:
        form = MudanzaForm(edificio=request.edificio)

    hoy = timezone.localdate()
    base = Mudanza.objects.filter(departamento__edificio=request.edificio)
    pendientes = base.filter(dia_mudanza__gte=hoy).select_related('departamento')
    historial = base.filter(dia_mudanza__lt=hoy).select_related('departamento')

    return render(request, 'conserjeria/mudanzas.html', {
        'form': form,
        'pendientes': pendientes,
        'historial': historial,
        'hoy': hoy,
    })


# ---------------------------------------------------------------------------
# MÓDULO ÁREAS COMUNES
# ---------------------------------------------------------------------------
@staff_edificio_required
@requiere_modulo('modulo_reservas')
def reservas_view(request):
    if request.method == 'POST':
        form = ReservaAreaComunForm(request.POST, edificio=request.edificio)
        if form.is_valid():
            reserva = form.save(commit=False)
            reserva.conserje_registra = request.user
            reserva.save()
            messages.success(request, 'Reserva agendada correctamente.')
            return redirect('conserjeria:reservas')
    else:
        form = ReservaAreaComunForm(edificio=request.edificio)

    hoy = timezone.localdate()
    base = ReservaAreaComun.objects.filter(departamento__edificio=request.edificio)
    pendientes = base.filter(dia_reserva__gte=hoy).select_related('area_comun', 'departamento')
    historial = base.filter(dia_reserva__lt=hoy).select_related('area_comun', 'departamento')

    return render(request, 'conserjeria/reservas.html', {
        'form': form,
        'pendientes': pendientes,
        'historial': historial,
        'hoy': hoy,
    })


@staff_edificio_required
@requiere_modulo('modulo_reservas')
def reservas_confirmar(request, pk):
    """Confirmación manual, sólo para reservas SIN pago online asociado (áreas
    con requiere_pago=False). Para reservas con pago, la única forma de pasar
    a CONFIRMADA es que Webpay apruebe el pago (ver pagos.confirmar_pago) --
    permitir que el conserje la confirme a mano acá sería saltarse el cobro."""
    reserva = get_object_or_404(
        ReservaAreaComun, pk=pk, departamento__edificio=request.edificio, estado=ReservaAreaComun.PENDIENTE
    )
    if hasattr(reserva, 'pago'):
        raise PermissionDenied(
            'Esta reserva requiere pago online: sólo Webpay puede confirmarla. '
            'Si el residente ya pagó y sigue pendiente, revisa el pago en la sección correspondiente.'
        )
    if request.method == 'POST':
        reserva.estado = ReservaAreaComun.CONFIRMADA
        reserva.conserje_confirma = request.user
        reserva.fecha_confirmacion = timezone.now()
        reserva.save()
        messages.success(request, f'Reserva de {reserva.responsable} confirmada.')
    return redirect('conserjeria:reservas')


@staff_edificio_required
@requiere_modulo('modulo_reservas')
def reservas_cancelar(request, pk):
    """Cancela una reserva PENDIENTE o CONFIRMADA (ej. el residente avisó que
    ya no la necesita, o no llegó a dejar la garantía). Queda marcada como
    CANCELADA -- no se borra, para mantener historial -- y libera el horario
    para que otro residente pueda reservarlo (ver exclude(estado=CANCELADA)
    en `_calcular_bloques_libres` y en la verificación de superposición)."""
    reserva = get_object_or_404(
        ReservaAreaComun, pk=pk, departamento__edificio=request.edificio,
        estado__in=[ReservaAreaComun.PENDIENTE, ReservaAreaComun.CONFIRMADA],
    )
    if request.method == 'POST':
        reserva.estado = ReservaAreaComun.CANCELADA
        reserva.save(update_fields=['estado'])
        messages.success(request, f'Reserva de {reserva.responsable} cancelada.')
    return redirect('conserjeria:reservas')


# ---------------------------------------------------------------------------
# AdminEdificio: catálogo de Áreas Comunes (+ sus bloques de horario) y de
# Estacionamientos de Visita. Antes sólo se podían crear desde /admin/ de
# Django, lo que exigía ser superusuario; ahora tienen su propio CRUD acá,
# igual de acotado a request.edificio que Departamento y Residente.
# ---------------------------------------------------------------------------
@admin_edificio_required
@requiere_modulo('modulo_reservas')
def configuracion_pagos(request):
    """El AdminEdificio decide la política de reembolso de la garantía de
    SU edificio. El código de comercio Webpay no se edita acá -- lo carga
    el SuperAdmin al afiliar el edificio con Transbank (ver EdificioForm)."""
    edificio = request.edificio
    if request.method == 'POST':
        form = EdificioPagoForm(request.POST, instance=edificio)
        if form.is_valid():
            form.save()
            messages.success(request, 'Configuración de pagos actualizada.')
            return redirect('conserjeria:configuracion_pagos')
    else:
        form = EdificioPagoForm(instance=edificio)
    return render(request, 'conserjeria/configuracion_pagos.html', {
        'form': form, 'edificio': edificio,
    })


@admin_edificio_required
@requiere_modulo('modulo_reservas')
def listar_areas_comunes(request):
    areas = AreaComun.objects.filter(edificio=request.edificio).prefetch_related('bloques_horario')
    return render(request, 'conserjeria/listar_areas_comunes.html', {'areas': areas})


@admin_edificio_required
@requiere_modulo('modulo_reservas')
def crear_area_comun(request):
    if request.method == 'POST':
        form = AreaComunForm(request.POST)
        if form.is_valid():
            area = form.save(commit=False)
            area.edificio = request.edificio  # nunca desde el POST
            area.save()
            messages.success(request, f"Área común '{area.nombre}' creada.")
            return redirect('conserjeria:editar_area_comun', pk=area.pk)
    else:
        form = AreaComunForm()
    return render(request, 'conserjeria/crear_area_comun.html', {'form': form})


@admin_edificio_required
@requiere_modulo('modulo_reservas')
def editar_area_comun(request, pk):
    """Edita los datos del área y, en la misma pantalla, administra sus
    bloques de horario disponible (agregar/eliminar) -- sin horario
    configurado, el formulario público de reservas del login no le muestra
    ningún tramo disponible a los residentes."""
    area = get_object_or_404(AreaComun, pk=pk, edificio=request.edificio)
    if request.method == 'POST':
        form = AreaComunForm(request.POST, instance=area)
        if form.is_valid():
            form.save()
            messages.success(request, f"Área común '{area.nombre}' actualizada.")
            return redirect('conserjeria:editar_area_comun', pk=area.pk)
    else:
        form = AreaComunForm(instance=area)
    bloque_form = BloqueHorarioAreaComunForm()
    bloques = area.bloques_horario.all()
    return render(request, 'conserjeria/editar_area_comun.html', {
        'form': form, 'area': area, 'bloque_form': bloque_form, 'bloques': bloques,
    })


@admin_edificio_required
@requiere_modulo('modulo_reservas')
def eliminar_area_comun(request, pk):
    """Borra el área. OJO: ReservaAreaComun.area_comun es on_delete=CASCADE,
    así que también borra el historial de reservas de esa área -- se avisa
    explícitamente en la plantilla de confirmación."""
    area = get_object_or_404(AreaComun, pk=pk, edificio=request.edificio)
    if request.method == 'POST':
        nombre = area.nombre
        area.delete()
        messages.success(request, f"Área común '{nombre}' eliminada.")
        return redirect('conserjeria:listar_areas_comunes')
    return render(request, 'conserjeria/eliminar_area_comun.html', {'area': area})


@admin_edificio_required
@requiere_modulo('modulo_reservas')
def agregar_bloque_horario(request, pk):
    area = get_object_or_404(AreaComun, pk=pk, edificio=request.edificio)
    if request.method == 'POST':
        bloque_form = BloqueHorarioAreaComunForm(request.POST)
        if bloque_form.is_valid():
            dia_semana = bloque_form.cleaned_data['dia_semana']
            hora_inicio = bloque_form.cleaned_data['hora_inicio']
            hora_termino = bloque_form.cleaned_data['hora_termino']

            if hora_termino > hora_inicio:
                # Caso normal: el bloque no cruza la medianoche.
                BloqueHorarioAreaComun.objects.create(
                    area_comun=area, dia_semana=dia_semana,
                    hora_inicio=hora_inicio, hora_termino=hora_termino,
                )
                messages.success(request, 'Bloque de horario agregado.')
            else:
                # Cruza la medianoche (ej. Viernes 20:00 -> Sábado 02:00):
                # se divide automáticamente en dos bloques de un solo día,
                # que es como los entiende `_calcular_bloques_libres`.
                dia_siguiente = BloqueHorarioAreaComun.dia_siguiente(dia_semana)
                BloqueHorarioAreaComun.objects.create(
                    area_comun=area, dia_semana=dia_semana,
                    hora_inicio=hora_inicio, hora_termino=time(23, 59, 59),
                )
                BloqueHorarioAreaComun.objects.create(
                    area_comun=area, dia_semana=dia_siguiente,
                    hora_inicio=time(0, 0), hora_termino=hora_termino,
                )
                messages.success(
                    request,
                    'Ese horario cruza la medianoche, así que se agregó como dos bloques: '
                    f'uno hasta las 23:59 y otro desde las 00:00 del día siguiente hasta las '
                    f'{hora_termino.strftime("%H:%M")}.'
                )
        else:
            for error in bloque_form.errors.get('__all__', []):
                messages.error(request, error)
    return redirect('conserjeria:editar_area_comun', pk=area.pk)


@admin_edificio_required
@requiere_modulo('modulo_reservas')
def eliminar_bloque_horario(request, pk):
    bloque = get_object_or_404(
        BloqueHorarioAreaComun, pk=pk, area_comun__edificio=request.edificio,
    )
    area_pk = bloque.area_comun_id
    if request.method == 'POST':
        bloque.delete()
        messages.success(request, 'Bloque de horario eliminado.')
    return redirect('conserjeria:editar_area_comun', pk=area_pk)


@admin_edificio_required
def listar_estacionamientos(request):
    estacionamientos = NumeroEstacionamiento.objects.filter(edificio=request.edificio)
    return render(request, 'conserjeria/listar_estacionamientos.html', {
        'estacionamientos': estacionamientos,
    })


@admin_edificio_required
def crear_estacionamiento(request):
    if request.method == 'POST':
        form = NumeroEstacionamientoForm(request.POST)
        if form.is_valid():
            estacionamiento = form.save(commit=False)
            estacionamiento.edificio = request.edificio  # nunca desde el POST
            estacionamiento.save()
            messages.success(request, f"Estacionamiento '{estacionamiento.numero}' creado.")
            return redirect('conserjeria:listar_estacionamientos')
    else:
        form = NumeroEstacionamientoForm()
    return render(request, 'conserjeria/crear_estacionamiento.html', {'form': form})


@admin_edificio_required
def editar_estacionamiento(request, pk):
    estacionamiento = get_object_or_404(NumeroEstacionamiento, pk=pk, edificio=request.edificio)
    if request.method == 'POST':
        form = NumeroEstacionamientoForm(request.POST, instance=estacionamiento)
        if form.is_valid():
            form.save()
            messages.success(request, f"Estacionamiento '{estacionamiento.numero}' actualizado.")
            return redirect('conserjeria:listar_estacionamientos')
    else:
        form = NumeroEstacionamientoForm(instance=estacionamiento)
    return render(request, 'conserjeria/editar_estacionamiento.html', {
        'form': form, 'estacionamiento': estacionamiento,
    })


@admin_edificio_required
def eliminar_estacionamiento(request, pk):
    """Si el estacionamiento tiene historial (EstacionamientoVisita usa
    on_delete=PROTECT), no se puede borrar sin perder trazabilidad: se avisa
    y no se elimina, en vez de fallar con un error sin explicación."""
    estacionamiento = get_object_or_404(NumeroEstacionamiento, pk=pk, edificio=request.edificio)
    if request.method == 'POST':
        numero = estacionamiento.numero
        try:
            estacionamiento.delete()
            messages.success(request, f"Estacionamiento '{numero}' eliminado.")
        except ProtectedError:
            messages.error(
                request,
                f"No se puede eliminar '{numero}': tiene historial de estacionamientos de "
                "visita asociado. Si ya no se usa, deja de asignarlo desde 'Estacionar' en "
                "vez de intentar borrarlo."
            )
        return redirect('conserjeria:listar_estacionamientos')
    return render(request, 'conserjeria/eliminar_estacionamiento.html', {
        'estacionamiento': estacionamiento,
    })


# ---------------------------------------------------------------------------
# MÓDULO REPORTES (AdminEdificio)
# ---------------------------------------------------------------------------
TRAMOS_HORARIOS = [(h, h + 3) for h in range(0, 24, 3)]


def _serie_tramo_horario(queryset, campo, es_datetime):
    """Cuenta registros de `queryset` agrupados en 8 tramos de 3 horas,
    según la hora extraída de `campo` (DateTimeField o TimeField)."""
    extra = {'tzinfo': timezone.get_current_timezone()} if es_datetime else {}
    filas = (
        queryset
        .annotate(_hora=ExtractHour(campo, **extra))
        .values('_hora')
        .annotate(total=Count('id'))
    )
    conteo_por_hora = {h: 0 for h in range(24)}
    for fila in filas:
        if fila['_hora'] is not None:
            conteo_por_hora[fila['_hora']] += fila['total']

    labels = [f'{ini:02d}-{fin:02d} h' for ini, fin in TRAMOS_HORARIOS]
    valores = [sum(conteo_por_hora[h] for h in range(ini, fin)) for ini, fin in TRAMOS_HORARIOS]
    return {'labels': labels, 'valores': valores}


def _serie_periodo(queryset, campo, trunc_func, es_datetime, formato_label):
    """Cuenta registros de `queryset` agrupados por período (día/semana/mes),
    truncando `campo` con `trunc_func` (TruncDate/TruncWeek/TruncMonth).

    Si `campo` ya es un DateField (es_datetime=False) y se pide granularidad
    de día, no hace falta truncar: se agrupa directo por el campo. Esto evita
    aplicar TruncDate sobre un campo que ya es fecha (redundante, y con
    comportamiento inconsistente entre motores de base de datos)."""
    if trunc_func is TruncDate and not es_datetime:
        filas = queryset.values(campo).annotate(total=Count('id')).order_by(campo)
        labels, valores = [], []
        for fila in filas:
            valor = fila[campo]
            if valor is not None:
                labels.append(valor.strftime(formato_label))
                valores.append(fila['total'])
        return {'labels': labels, 'valores': valores}

    extra = {'tzinfo': timezone.get_current_timezone()} if es_datetime else {}
    filas = (
        queryset
        .annotate(_periodo=trunc_func(campo, **extra))
        .values('_periodo')
        .annotate(total=Count('id'))
        .order_by('_periodo')
    )
    labels, valores = [], []
    for fila in filas:
        if fila['_periodo'] is not None:
            labels.append(fila['_periodo'].strftime(formato_label))
            valores.append(fila['total'])
    return {'labels': labels, 'valores': valores}


@admin_edificio_required
def reportes_dashboard(request):
    edificio = request.edificio
    hoy = timezone.localdate()

    fecha_desde_default = hoy - timedelta(days=89)  # ~13 semanas / 3 meses
    fecha_hasta_default = hoy

    filtro_form = ReportesFiltroForm(
        request.GET or None,
        initial={'fecha_desde': fecha_desde_default, 'fecha_hasta': fecha_hasta_default},
    )
    fecha_desde, fecha_hasta = fecha_desde_default, fecha_hasta_default
    if filtro_form.is_valid():
        fecha_desde = filtro_form.cleaned_data.get('fecha_desde') or fecha_desde_default
        fecha_hasta = filtro_form.cleaned_data.get('fecha_hasta') or fecha_hasta_default

    # --- Paquetería (pedidos) ---
    paquetes_qs = Paqueteria.objects.filter(
        departamento__edificio=edificio,
        fecha_recepcion__date__gte=fecha_desde,
        fecha_recepcion__date__lte=fecha_hasta,
    )
    paquetes_tramo = _serie_tramo_horario(paquetes_qs, 'fecha_recepcion', True)
    paquetes_dia = _serie_periodo(paquetes_qs, 'fecha_recepcion', TruncDate, True, '%d-%m-%Y')
    paquetes_semana = _serie_periodo(paquetes_qs, 'fecha_recepcion', TruncWeek, True, '%d-%m-%Y')
    paquetes_mes = _serie_periodo(paquetes_qs, 'fecha_recepcion', TruncMonth, True, '%m-%Y')

    # Origen de los pedidos: métricas fijas de hoy / semana en curso / mes en curso
    inicio_semana = hoy - timedelta(days=hoy.weekday())
    inicio_mes = hoy.replace(day=1)
    origen_stats = list(
        Paqueteria.objects.filter(departamento__edificio=edificio)
        .values('origen')
        .annotate(
            hoy_count=Count('id', filter=Q(fecha_recepcion__date=hoy)),
            semana_count=Count('id', filter=Q(fecha_recepcion__date__gte=inicio_semana)),
            mes_count=Count('id', filter=Q(fecha_recepcion__date__gte=inicio_mes)),
        )
        .filter(Q(hoy_count__gt=0) | Q(semana_count__gt=0) | Q(mes_count__gt=0))
        .order_by('-mes_count', '-semana_count', '-hoy_count')
    )

    # --- Estacionamiento de visitas ---
    estac_qs = EstacionamientoVisita.objects.filter(
        departamento_destino__edificio=edificio,
        fecha_hora_entrada__date__gte=fecha_desde,
        fecha_hora_entrada__date__lte=fecha_hasta,
    )
    estac_tramo = _serie_tramo_horario(estac_qs, 'fecha_hora_entrada', True)
    estac_dia = _serie_periodo(estac_qs, 'fecha_hora_entrada', TruncDate, True, '%d-%m-%Y')
    estac_semana = _serie_periodo(estac_qs, 'fecha_hora_entrada', TruncWeek, True, '%d-%m-%Y')
    estac_mes = _serie_periodo(estac_qs, 'fecha_hora_entrada', TruncMonth, True, '%m-%Y')

    # --- Registro de visitas ---
    visitas_qs = Visita.objects.filter(
        departamento__edificio=edificio,
        dia_visita__gte=fecha_desde,
        dia_visita__lte=fecha_hasta,
    )
    visitas_tramo = _serie_tramo_horario(visitas_qs, 'hora_visita', False)
    visitas_dia = _serie_periodo(visitas_qs, 'dia_visita', TruncDate, False, '%d-%m-%Y')
    visitas_semana = _serie_periodo(visitas_qs, 'dia_visita', TruncWeek, False, '%d-%m-%Y')
    visitas_mes = _serie_periodo(visitas_qs, 'dia_visita', TruncMonth, False, '%m-%Y')

    # --- Áreas comunes ---
    reservas_qs = ReservaAreaComun.objects.filter(
        departamento__edificio=edificio,
        dia_reserva__gte=fecha_desde,
        dia_reserva__lte=fecha_hasta,
    )
    reservas_dia = _serie_periodo(reservas_qs, 'dia_reserva', TruncDate, False, '%d-%m-%Y')
    reservas_semana = _serie_periodo(reservas_qs, 'dia_reserva', TruncWeek, False, '%d-%m-%Y')
    reservas_mes = _serie_periodo(reservas_qs, 'dia_reserva', TruncMonth, False, '%m-%Y')

    datos_graficos = {
        'paquetes_tramo': paquetes_tramo,
        'paquetes_dia': paquetes_dia,
        'paquetes_semana': paquetes_semana,
        'paquetes_mes': paquetes_mes,
        'estac_tramo': estac_tramo,
        'estac_dia': estac_dia,
        'estac_semana': estac_semana,
        'estac_mes': estac_mes,
        'visitas_tramo': visitas_tramo,
        'visitas_dia': visitas_dia,
        'visitas_semana': visitas_semana,
        'visitas_mes': visitas_mes,
        'reservas_dia': reservas_dia,
        'reservas_semana': reservas_semana,
        'reservas_mes': reservas_mes,
    }

    return render(request, 'conserjeria/reportes.html', {
        'filtro_form': filtro_form,
        'fecha_desde': fecha_desde,
        'fecha_hasta': fecha_hasta,
        'total_paquetes': paquetes_qs.count(),
        'total_estacionamientos': estac_qs.count(),
        'total_visitas': visitas_qs.count(),
        'total_reservas': reservas_qs.count(),
        'origen_stats': origen_stats,
        'datos_graficos': datos_graficos,
    })
