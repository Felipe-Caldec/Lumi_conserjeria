from django.contrib import admin

from .models import (
    AreaComun,
    BloqueHorarioAreaComun,
    Edificio,
    Lavadora,
    LecturaLavadora,
    NumeroEstacionamiento,
    PerfilUsuario,
)

# ---------------------------------------------------------------------------
# Sólo el SuperAdmin llega a /admin/ (los usuarios AdminEdificio y Conserje
# se crean con is_staff=False, así que no pueden iniciar sesión acá).
#
# Los modelos operativos (Departamento, Residente, Paqueteria,
# EstacionamientoVisita, Mudanza, ReservaAreaComun, Visita) NO se registran
# acá: ya tienen sus propias vistas custom acotadas por edificio
# (crear_departamento, crear_residente, y los módulos del Conserje). Exponerlos
# en el admin nativo mezclaría datos de los 40 edificios sin ningún filtro.
#
# AreaComun, BloqueHorarioAreaComun, NumeroEstacionamiento, Lavadora y
# LecturaLavadora SÍ se registran acá, pero de SÓLO LECTURA (ver
# SoloLecturaAdminMixin). Es información privada de cada edificio y de
# administración exclusiva del AdminEdificio -- éste ya la gestiona desde su
# propio panel (listar/crear/editar/eliminar_area_comun, etc. en views.py),
# igual de acotado a request.edificio que Departamento y Residente. Que el
# SuperAdmin pueda editarla acá sería un segundo camino sin ese acotamiento
# (mezclando o pisando datos de los 40 edificios) y además pisaría
# decisiones operativas que no le corresponden. Queda registrada, no
# oculta, para que el SuperAdmin la pueda inspeccionar como soporte sin
# poder modificarla por accidente.
# ---------------------------------------------------------------------------
class SoloLecturaAdminMixin:
    """Deja el modelo visible en /admin/ (list + detalle) pero bloquea
    agregar, editar y borrar, sin importar quién esté logueado."""

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(Edificio)
class EdificioAdmin(admin.ModelAdmin):
    list_display = ('nombre', 'direccion', 'activo', 'codigo_comercio_webpay', 'creado_en')
    list_filter = ('activo',)
    search_fields = ('nombre',)


@admin.register(PerfilUsuario)
class PerfilUsuarioAdmin(admin.ModelAdmin):
    """
    Uso de emergencia para el SuperAdmin (ej. desactivar una cuenta puntual).
    La creación normal de AdminEdificio y Conserje pasa por las vistas custom
    (crear_admin_edificio / crear_conserje), no por acá, para mantener las
    reglas de asignación de edificio y de rol.
    """
    list_display = ('nombre', 'apellido', 'rol', 'edificio', 'user')
    list_filter = ('rol', 'edificio')
    search_fields = ('nombre', 'apellido', 'rut', 'user__username')
    autocomplete_fields = ('edificio',)


@admin.register(AreaComun)
class AreaComunAdmin(SoloLecturaAdminMixin, admin.ModelAdmin):
    list_display = ('nombre', 'edificio', 'duracion_maxima_horas', 'requiere_pago', 'monto_garantia')
    list_filter = ('edificio', 'requiere_pago')
    search_fields = ('nombre',)


@admin.register(BloqueHorarioAreaComun)
class BloqueHorarioAreaComunAdmin(SoloLecturaAdminMixin, admin.ModelAdmin):
    """Horario disponible por área común, consultado por el formulario
    público de reservas (login) para calcular tramos libres."""
    list_display = ('area_comun', 'dia_semana', 'hora_inicio', 'hora_termino')
    list_filter = ('dia_semana', 'area_comun__edificio')


@admin.register(NumeroEstacionamiento)
class NumeroEstacionamientoAdmin(SoloLecturaAdminMixin, admin.ModelAdmin):
    list_display = ('numero', 'edificio')
    list_filter = ('edificio',)
    search_fields = ('numero',)


@admin.register(Lavadora)
class LavadoraAdmin(SoloLecturaAdminMixin, admin.ModelAdmin):
    """
    Alta manual de dispositivos Shelly 1PM durante el piloto: acá se carga
    la IP local de cada Shelly y a qué lavadora/edificio corresponde. El
    estado/potencia se actualizan solos vía el management command
    `consultar_lavadoras`, no se editan a mano.
    """
    list_display = ('nombre', 'edificio', 'ip_shelly', 'activa', 'estado', 'potencia_actual_w', 'ultima_lectura')
    list_filter = ('edificio', 'estado', 'activa')
    search_fields = ('nombre', 'ip_shelly')


@admin.register(LecturaLavadora)
class LecturaLavadoraAdmin(SoloLecturaAdminMixin, admin.ModelAdmin):
    """Solo lectura (ahora también forzado por permisos): sirve para revisar
    el historial crudo de watts y calibrar el umbral/debounce durante el
    piloto."""
    list_display = ('lavadora', 'potencia_w', 'relay_on', 'estado_calculado', 'creado_en')
    list_filter = ('lavadora__edificio', 'estado_calculado')
    date_hierarchy = 'creado_en'

