from django.contrib import messages
from django.contrib.auth import logout
from django.shortcuts import redirect
from django.urls import reverse


class EdificioMiddleware:
    """
    Para todo usuario autenticado que NO sea superuser, resuelve:
        request.perfil    -> instancia de PerfilUsuario
        request.edificio  -> instancia de Edificio (para filtrar querysets)

    Si el edificio del usuario está desactivado, corta la sesión de inmediato.

    Además, si el perfil tiene `debe_cambiar_contrasena=True` (recién creado
    por un AdminEdificio o el SuperAdmin), fuerza el paso por
    `cambiar_password_obligatorio` antes de dejarlo usar cualquier otra
    pantalla -- salvo la propia vista de cambio de contraseña y el logout.

    IMPORTANTE: request.edificio nunca debe derivarse de datos del request
    (GET/POST) en ninguna vista -- siempre viene de acá, derivado del
    usuario autenticado.
    """

    # Vistas siempre accesibles aunque el usuario deba cambiar su contraseña.
    RUTAS_PERMITIDAS_SIN_CAMBIO = {
        'conserjeria:cambiar_password_obligatorio',
        'conserjeria:logout',
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request.perfil = None
        request.edificio = None

        if request.user.is_authenticated and not request.user.is_superuser:
            perfil = getattr(request.user, 'perfil', None)

            if perfil is None:
                logout(request)
                messages.error(request, 'Tu cuenta no tiene un perfil válido asignado.')
                return redirect('conserjeria:login')

            if not perfil.edificio.activo:
                logout(request)
                messages.error(request, 'El edificio asociado a tu cuenta está suspendido.')
                return redirect('conserjeria:login')

            request.perfil = perfil
            request.edificio = perfil.edificio

            if perfil.debe_cambiar_contrasena:
                rutas_permitidas = {reverse(nombre) for nombre in self.RUTAS_PERMITIDAS_SIN_CAMBIO}
                if request.path not in rutas_permitidas:
                    return redirect('conserjeria:cambiar_password_obligatorio')

        return self.get_response(request)
