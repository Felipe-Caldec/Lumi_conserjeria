from django import template
from django.core.exceptions import ObjectDoesNotExist

register = template.Library()


@register.filter
def nombre_conserje(user):
    """
    Muestra 'Nombre Apellido' del PerfilConserje asociado al User.
    Si el usuario no tiene perfil (ej: es el Administrador/Superuser),
    muestra su username como respaldo. Si no hay usuario, muestra '—'.
    """
    if not user:
        return '—'

    try:
        perfil = user.perfil
    except ObjectDoesNotExist:
        perfil = None

    if perfil:
        return f'{perfil.nombre} {perfil.apellido}'

    return user.username
