from django import forms
from django.contrib.auth.forms import AuthenticationForm, PasswordChangeForm
from django.contrib.auth.models import User
from django.db import transaction

from .models import (
    AreaComun,
    BloqueHorarioAreaComun,
    Departamento,
    Edificio,
    EstacionamientoVisita,
    Mudanza,
    NumeroEstacionamiento,
    Paqueteria,
    PerfilUsuario,
    Residente,
    ReservaAreaComun,
    Visita,
)
from .validators import formatear_rut, validar_rut, validar_telefono

# ---------------------------------------------------------------------------
# Clases de utilidad Tailwind reutilizadas en todos los widgets
# ---------------------------------------------------------------------------
INPUT_CLASSES = (
    'w-full rounded-lg border border-gray-300 px-3 py-2 text-sm text-gray-900 '
    'shadow-sm focus:border-indigo-500 focus:ring-2 focus:ring-indigo-200 '
    'focus:outline-none transition'
)
SELECT_CLASSES = INPUT_CLASSES
TEXTAREA_CLASSES = INPUT_CLASSES


def _base_attrs(extra=None):
    attrs = {'class': INPUT_CLASSES}
    if extra:
        attrs.update(extra)
    return attrs


# ---------------------------------------------------------------------------
# Autenticación
# ---------------------------------------------------------------------------
class LoginForm(AuthenticationForm):
    username = forms.CharField(
        widget=forms.TextInput(attrs=_base_attrs({'placeholder': 'Nombre de usuario', 'autofocus': True}))
    )
    password = forms.CharField(
        widget=forms.PasswordInput(attrs=_base_attrs({'placeholder': 'Contraseña'}))
    )


# ---------------------------------------------------------------------------
# SuperAdmin: Edificios
# ---------------------------------------------------------------------------
class EdificioForm(forms.ModelForm):
    class Meta:
        model = Edificio
        fields = [
            'nombre', 'direccion', 'activo', 'codigo_comercio_webpay',
            'modulo_reservas', 'modulo_consulta_estacionamiento', 'modulo_lavadoras',
        ]
        widgets = {
            'nombre': forms.TextInput(attrs=_base_attrs()),
            'direccion': forms.TextInput(attrs=_base_attrs()),
            'codigo_comercio_webpay': forms.TextInput(attrs=_base_attrs({
                'placeholder': 'Código de comercio hijo entregado por Transbank (dejar vacío si aún no está afiliado)',
            })),
            'modulo_reservas': forms.CheckboxInput(attrs={'class': 'h-4 w-4 rounded border-gray-300 text-indigo-600'}),
            'modulo_consulta_estacionamiento': forms.CheckboxInput(attrs={'class': 'h-4 w-4 rounded border-gray-300 text-indigo-600'}),
            'modulo_lavadoras': forms.CheckboxInput(attrs={'class': 'h-4 w-4 rounded border-gray-300 text-indigo-600'}),
        }
        # codigo_comercio_webpay lo asigna Transbank al afiliar el edificio
        # como comercio hijo dentro del Mall que administra la plataforma
        # (Felipe gestiona esa relación con Transbank), por eso lo carga el
        # SuperAdmin acá y no el AdminEdificio. La política de si la
        # garantía se devuelve o no (garantia_reembolsable) sí es decisión
        # de cada edificio -- ver EdificioPagoForm más abajo.
        #
        # Los módulos (modulo_*) son complementos que cuestan carga extra
        # de servidor -- sólo el SuperAdmin decide qué edificio los tiene
        # activos, nunca el AdminEdificio.


# ---------------------------------------------------------------------------
# AdminEdificio: configuración de pago del propio edificio (no elige código
# de comercio -- eso lo carga el SuperAdmin en EdificioForm -- pero sí
# decide su política de reembolso de garantía).
# ---------------------------------------------------------------------------
class EdificioPagoForm(forms.ModelForm):
    class Meta:
        model = Edificio
        fields = ['garantia_reembolsable']
        widgets = {
            'garantia_reembolsable': forms.CheckboxInput(attrs={'class': 'h-4 w-4 rounded border-gray-300 text-indigo-600'}),
        }
        help_texts = {
            'garantia_reembolsable': (
                'Si está marcado, la garantía se puede reembolsar al residente después de '
                'usar el área (requiere que el conserje o administrador confirme la devolución). '
                'Si no, la garantía queda como tarifa de uso.'
            ),
        }


# ---------------------------------------------------------------------------
# SuperAdmin: creación de cuentas AdminEdificio.
# Acá SÍ se elige el edificio (el SuperAdmin administra los 40 edificios).
# ---------------------------------------------------------------------------
class CrearAdminEdificioForm(forms.Form):
    edificio = forms.ModelChoiceField(
        queryset=Edificio.objects.filter(activo=True),
        widget=forms.Select(attrs=_base_attrs()),
    )
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs=_base_attrs()))
    password = forms.CharField(widget=forms.PasswordInput(attrs=_base_attrs()))
    nombre = forms.CharField(max_length=100, widget=forms.TextInput(attrs=_base_attrs()))
    apellido = forms.CharField(max_length=100, widget=forms.TextInput(attrs=_base_attrs()))
    rut = forms.CharField(max_length=12, widget=forms.TextInput(attrs=_base_attrs({'placeholder': '12345678-9'})))
    direccion = forms.CharField(max_length=255, widget=forms.TextInput(attrs=_base_attrs()))
    telefono = forms.CharField(max_length=9, widget=forms.TextInput(attrs=_base_attrs()))
    correo = forms.EmailField(widget=forms.EmailInput(attrs=_base_attrs()))
    numero_emergencia = forms.CharField(max_length=9, widget=forms.TextInput(attrs=_base_attrs()))
    contacto_emergencia = forms.CharField(max_length=150, widget=forms.TextInput(attrs=_base_attrs()))

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError('Ese nombre de usuario ya existe.')
        return username

    def clean_rut(self):
        # validar_rut() lanza su propia ValidationError si el RUT es inválido;
        # no retorna booleano, así que NO se debe envolver en "if not ...".
        rut = self.cleaned_data['rut']
        validar_rut(rut)
        # PerfilUsuario.rut es unique=True a nivel de modelo: si no se valida
        # acá, PerfilUsuario.objects.create() en save() lanza IntegrityError
        # DESPUÉS de haber creado el User, dejando una cuenta huérfana sin
        # perfil (visible en /admin/ pero no en el panel del SuperAdmin).
        if PerfilUsuario.objects.filter(rut=formatear_rut(rut)).exists():
            raise forms.ValidationError('Ya existe una persona registrada con ese RUT.')
        return rut

    def clean_telefono(self):
        telefono = self.cleaned_data['telefono']
        validar_telefono(telefono)
        return telefono

    def clean_numero_emergencia(self):
        numero = self.cleaned_data['numero_emergencia']
        validar_telefono(numero)
        return numero

    def save(self):
        data = self.cleaned_data
        # Atómico: si PerfilUsuario.objects.create() falla por cualquier
        # motivo (ej. condición de carrera con el mismo RUT), el User
        # recién creado se revierte también. Nunca queda un User huérfano.
        with transaction.atomic():
            user = User.objects.create_user(username=data['username'], password=data['password'])
            return PerfilUsuario.objects.create(
                user=user,
                edificio=data['edificio'],
                rol=PerfilUsuario.ROL_ADMIN_EDIFICIO,
                nombre=data['nombre'],
                apellido=data['apellido'],
                rut=data['rut'],
                direccion=data['direccion'],
                telefono=data['telefono'],
                correo=data['correo'],
                numero_emergencia=data['numero_emergencia'],
                contacto_emergencia=data['contacto_emergencia'],
            )


# ---------------------------------------------------------------------------
# AdminEdificio: creación de cuentas Conserje. NO incluye campo `edificio`:
# se asigna en la vista desde request.edificio, nunca desde el POST.
# ---------------------------------------------------------------------------
class CrearConserjeForm(forms.Form):
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs=_base_attrs()))
    password = forms.CharField(widget=forms.PasswordInput(attrs=_base_attrs()))
    nombre = forms.CharField(max_length=100, widget=forms.TextInput(attrs=_base_attrs()))
    apellido = forms.CharField(max_length=100, widget=forms.TextInput(attrs=_base_attrs()))
    rut = forms.CharField(max_length=12, widget=forms.TextInput(attrs=_base_attrs({'placeholder': '12345678-9'})))
    direccion = forms.CharField(max_length=255, widget=forms.TextInput(attrs=_base_attrs()))
    telefono = forms.CharField(max_length=9, widget=forms.TextInput(attrs=_base_attrs()))
    correo = forms.EmailField(widget=forms.EmailInput(attrs=_base_attrs()))
    numero_emergencia = forms.CharField(max_length=9, widget=forms.TextInput(attrs=_base_attrs()))
    contacto_emergencia = forms.CharField(max_length=150, widget=forms.TextInput(attrs=_base_attrs()))

    def clean_username(self):
        username = self.cleaned_data['username']
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError('Ese nombre de usuario ya existe.')
        return username

    def clean_rut(self):
        rut = self.cleaned_data['rut']
        validar_rut(rut)
        if PerfilUsuario.objects.filter(rut=formatear_rut(rut)).exists():
            raise forms.ValidationError('Ya existe una persona registrada con ese RUT.')
        return rut

    def clean_telefono(self):
        telefono = self.cleaned_data['telefono']
        validar_telefono(telefono)
        return telefono

    def clean_numero_emergencia(self):
        numero = self.cleaned_data['numero_emergencia']
        validar_telefono(numero)
        return numero

    def save(self, edificio):
        """`edificio` se pasa explícitamente desde la vista (request.edificio),
        nunca se lee de self.cleaned_data."""
        data = self.cleaned_data
        with transaction.atomic():
            user = User.objects.create_user(username=data['username'], password=data['password'])
            return PerfilUsuario.objects.create(
                user=user,
                edificio=edificio,
                rol=PerfilUsuario.ROL_CONSERJE,
                nombre=data['nombre'],
                apellido=data['apellido'],
                rut=data['rut'],
                direccion=data['direccion'],
                telefono=data['telefono'],
                correo=data['correo'],
                numero_emergencia=data['numero_emergencia'],
                contacto_emergencia=data['contacto_emergencia'],
            )


# ---------------------------------------------------------------------------
# AdminEdificio: edición de cuentas Conserje ya existentes. Similar a
# CrearConserjeForm pero sin exigir contraseña (sólo se resetea si el
# AdminEdificio completa el campo opcional `nueva_password`). El `pk` del
# PerfilUsuario que se está editando se recibe en __init__ para excluirlo de
# las validaciones de unicidad de username/RUT.
# ---------------------------------------------------------------------------
class EditarConserjeForm(forms.Form):
    username = forms.CharField(max_length=150, widget=forms.TextInput(attrs=_base_attrs()))
    nueva_password = forms.CharField(
        required=False, widget=forms.PasswordInput(attrs=_base_attrs({'placeholder': 'Dejar en blanco para no cambiarla'})),
        label='Nueva contraseña (opcional)',
        help_text='Si la completas, se le volverá a pedir al conserje que la cambie en su próximo ingreso.',
    )
    nombre = forms.CharField(max_length=100, widget=forms.TextInput(attrs=_base_attrs()))
    apellido = forms.CharField(max_length=100, widget=forms.TextInput(attrs=_base_attrs()))
    rut = forms.CharField(max_length=12, widget=forms.TextInput(attrs=_base_attrs({'placeholder': '12345678-9'})))
    direccion = forms.CharField(max_length=255, widget=forms.TextInput(attrs=_base_attrs()))
    telefono = forms.CharField(max_length=9, widget=forms.TextInput(attrs=_base_attrs()))
    correo = forms.EmailField(widget=forms.EmailInput(attrs=_base_attrs()))
    numero_emergencia = forms.CharField(max_length=9, widget=forms.TextInput(attrs=_base_attrs()))
    contacto_emergencia = forms.CharField(max_length=150, widget=forms.TextInput(attrs=_base_attrs()))

    def __init__(self, *args, perfil=None, **kwargs):
        self.perfil = perfil
        if perfil is not None and 'initial' not in kwargs:
            kwargs['initial'] = {
                'username': perfil.user.username,
                'nombre': perfil.nombre,
                'apellido': perfil.apellido,
                'rut': perfil.rut,
                'direccion': perfil.direccion,
                'telefono': perfil.telefono,
                'correo': perfil.correo,
                'numero_emergencia': perfil.numero_emergencia,
                'contacto_emergencia': perfil.contacto_emergencia,
            }
        super().__init__(*args, **kwargs)

    def clean_username(self):
        username = self.cleaned_data['username']
        qs = User.objects.filter(username=username)
        if self.perfil is not None:
            qs = qs.exclude(pk=self.perfil.user_id)
        if qs.exists():
            raise forms.ValidationError('Ese nombre de usuario ya existe.')
        return username

    def clean_rut(self):
        rut = self.cleaned_data['rut']
        validar_rut(rut)
        qs = PerfilUsuario.objects.filter(rut=formatear_rut(rut))
        if self.perfil is not None:
            qs = qs.exclude(pk=self.perfil.pk)
        if qs.exists():
            raise forms.ValidationError('Ya existe una persona registrada con ese RUT.')
        return rut

    def clean_telefono(self):
        telefono = self.cleaned_data['telefono']
        validar_telefono(telefono)
        return telefono

    def clean_numero_emergencia(self):
        numero = self.cleaned_data['numero_emergencia']
        validar_telefono(numero)
        return numero

    def save(self):
        """Actualiza el PerfilUsuario y su User asociado (pasado en __init__).
        Si se completó `nueva_password`, resetea la contraseña y vuelve a
        marcar `debe_cambiar_contrasena=True`."""
        data = self.cleaned_data
        with transaction.atomic():
            user = self.perfil.user
            user.username = data['username']
            if data.get('nueva_password'):
                user.set_password(data['nueva_password'])
                self.perfil.debe_cambiar_contrasena = True
            user.save()

            self.perfil.nombre = data['nombre']
            self.perfil.apellido = data['apellido']
            self.perfil.rut = data['rut']
            self.perfil.direccion = data['direccion']
            self.perfil.telefono = data['telefono']
            self.perfil.correo = data['correo']
            self.perfil.numero_emergencia = data['numero_emergencia']
            self.perfil.contacto_emergencia = data['contacto_emergencia']
            self.perfil.save()
            return self.perfil


# ---------------------------------------------------------------------------
# Cambio de contraseña obligatorio en el primer ingreso. Hereda de
# PasswordChangeForm (exige la contraseña actual/temporal, que el usuario
# conoce porque se la entregó el administrador) en lugar de SetPasswordForm,
# para no permitir que cualquiera con la sesión abierta cambie la contraseña
# sin saber la anterior.
# ---------------------------------------------------------------------------
class CambiarPasswordObligatorioForm(PasswordChangeForm):
    old_password = forms.CharField(
        label='Contraseña actual',
        widget=forms.PasswordInput(attrs=_base_attrs({'autofocus': True})),
    )
    new_password1 = forms.CharField(
        label='Nueva contraseña', widget=forms.PasswordInput(attrs=_base_attrs()),
    )
    new_password2 = forms.CharField(
        label='Confirmar nueva contraseña', widget=forms.PasswordInput(attrs=_base_attrs()),
    )


# ---------------------------------------------------------------------------
# AdminEdificio: Departamentos y Residentes (dentro de SU edificio)
# ---------------------------------------------------------------------------
class DepartamentoForm(forms.ModelForm):
    class Meta:
        model = Departamento
        fields = ['numero']
        widgets = {'numero': forms.TextInput(attrs=_base_attrs({'placeholder': 'Ej: 501'}))}


class ResidenteForm(forms.ModelForm):
    """El queryset de `departamento` se filtra por edificio en __init__:
    nunca debe mostrar departamentos de otro edificio en el select."""

    class Meta:
        model = Residente
        fields = ['departamento', 'nombre', 'apellido', 'rut']
        widgets = {
            'departamento': forms.Select(attrs=_base_attrs()),
            'nombre': forms.TextInput(attrs=_base_attrs()),
            'apellido': forms.TextInput(attrs=_base_attrs()),
            'rut': forms.TextInput(attrs=_base_attrs({'placeholder': '12345678-9'})),
        }

    def __init__(self, *args, edificio=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Departamento.objects.filter(edificio=edificio) if edificio else Departamento.objects.none()
        self.fields['departamento'].queryset = qs


# ---------------------------------------------------------------------------
# Módulo Paquetería
# ---------------------------------------------------------------------------
class PaqueteriaRecibirForm(forms.ModelForm):
    """
    El select de Departamento recarga la página (onchange -> submit) para
    filtrar el select de Destinatario mediante el parámetro departamento_id.
    El propio departamento_id ya llega acotado al edificio desde la vista.
    """
    class Meta:
        model = Paqueteria
        # 'departamento' NO forma parte de este form: se selecciona en un
        # <select> GET independiente que recarga la página con
        # ?departamento_id=N (ver plantilla).
        fields = ['origen', 'destinatario', 'descripcion']
        widgets = {
            'origen': forms.TextInput(attrs=_base_attrs()),
            'destinatario': forms.Select(attrs=_base_attrs()),
            'descripcion': forms.Textarea(attrs=_base_attrs({'rows': 3})),
        }

    def __init__(self, *args, departamento_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['destinatario'].required = False
        if departamento_id:
            self.fields['destinatario'].queryset = Residente.objects.filter(
                departamento_id=departamento_id
            )
        else:
            self.fields['destinatario'].queryset = Residente.objects.none()


class PaqueteriaEntregarForm(forms.ModelForm):
    class Meta:
        model = Paqueteria
        fields = ['recibido_por']
        widgets = {
            'recibido_por': forms.Select(attrs=_base_attrs({'onchange': 'this.form.submit()'})),
        }

    def __init__(self, *args, departamento_id=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['recibido_por'].required = True
        self.fields['recibido_por'].empty_label = 'Seleccione residente...'
        if departamento_id:
            self.fields['recibido_por'].queryset = Residente.objects.filter(
                departamento_id=departamento_id
            )
        else:
            self.fields['recibido_por'].queryset = Residente.objects.none()


class PaqueteriaHistorialFiltroForm(forms.Form):
    """El queryset de `departamento` se filtra por edificio en __init__."""
    fecha_desde = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs=_base_attrs({'type': 'date', 'onchange': 'this.form.submit()'}))
    )
    fecha_hasta = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs=_base_attrs({'type': 'date', 'onchange': 'this.form.submit()'}))
    )
    departamento = forms.ModelChoiceField(
        required=False, queryset=Departamento.objects.none(), empty_label='Todos',
        widget=forms.Select(attrs=_base_attrs({'onchange': 'this.form.submit()'}))
    )

    def __init__(self, *args, edificio=None, **kwargs):
        super().__init__(*args, **kwargs)
        if edificio:
            self.fields['departamento'].queryset = Departamento.objects.filter(edificio=edificio)


# ---------------------------------------------------------------------------
# AdminEdificio: catálogos Áreas Comunes / Horarios / Estacionamientos
# (antes sólo editables desde /admin/ de Django, ahora con CRUD propio).
# `edificio` nunca es un campo del form: se asigna en la vista desde
# request.edificio, igual que Departamento.
# ---------------------------------------------------------------------------
class AreaComunForm(forms.ModelForm):
    class Meta:
        model = AreaComun
        fields = ['nombre', 'duracion_maxima_horas', 'requiere_pago', 'monto_garantia']
        widgets = {
            'nombre': forms.TextInput(attrs=_base_attrs({'placeholder': 'Ej: Quincho'})),
            'duracion_maxima_horas': forms.NumberInput(attrs=_base_attrs({'placeholder': 'Sin límite si se deja vacío'})),
            'requiere_pago': forms.CheckboxInput(attrs={'class': 'h-4 w-4 rounded border-gray-300 text-indigo-600'}),
            'monto_garantia': forms.NumberInput(attrs=_base_attrs({'placeholder': 'Ej: 30000', 'min': 0, 'step': 1})),
        }
        help_texts = {
            'requiere_pago': 'Si está marcado, la reserva pública exige pago online (Webpay) antes de confirmarse.',
            'monto_garantia': 'Monto de la garantía en pesos chilenos. Obligatorio si el área requiere pago.',
        }
    # La validación cruzada (monto obligatorio si requiere_pago, prohibido si
    # no) vive en AreaComun.clean(); ModelForm ya la ejecuta sola vía
    # instance.full_clean() en _post_clean() y mapea el ValidationError a
    # errores de campo -- no hace falta repetir la lógica acá.


class BloqueHorarioAreaComunForm(forms.ModelForm):
    class Meta:
        model = BloqueHorarioAreaComun
        fields = ['dia_semana', 'hora_inicio', 'hora_termino']
        widgets = {
            'dia_semana': forms.Select(attrs=_base_attrs()),
            'hora_inicio': forms.TimeInput(attrs=_base_attrs({'type': 'time'})),
            'hora_termino': forms.TimeInput(attrs=_base_attrs({'type': 'time'})),
        }

    def clean(self):
        # OJO: a propósito NO se exige hora_inicio < hora_termino acá. Un
        # área que abre, por ejemplo, de 20:00 a 02:00 del día siguiente se
        # ingresa tal cual (hora_termino "menor" que hora_inicio) y la vista
        # `agregar_bloque_horario` la divide en dos bloques de un solo día
        # cada uno. Sólo se rechaza el caso ambiguo de horas iguales.
        cleaned_data = super().clean()
        hora_inicio = cleaned_data.get('hora_inicio')
        hora_termino = cleaned_data.get('hora_termino')
        if hora_inicio and hora_termino and hora_inicio == hora_termino:
            raise forms.ValidationError(
                'La hora de inicio y término no pueden ser iguales.'
            )
        return cleaned_data


class NumeroEstacionamientoForm(forms.ModelForm):
    class Meta:
        model = NumeroEstacionamiento
        fields = ['numero']
        widgets = {'numero': forms.TextInput(attrs=_base_attrs({'placeholder': 'Ej: E-12'}))}


# ---------------------------------------------------------------------------
# Módulo Estacionamiento de Visitas
# ---------------------------------------------------------------------------
class EstacionarForm(forms.ModelForm):
    """Los querysets de `numero_estacionamiento` y `departamento_destino` se
    filtran por edificio en __init__: nunca deben mostrar catálogos ni
    departamentos de otro edificio."""

    class Meta:
        model = EstacionamientoVisita
        fields = ['numero_estacionamiento', 'patente', 'conductor', 'departamento_destino']
        widgets = {
            'numero_estacionamiento': forms.Select(attrs=_base_attrs()),
            'patente': forms.TextInput(attrs=_base_attrs({'placeholder': 'Patente (texto libre)'})),
            'conductor': forms.TextInput(attrs=_base_attrs()),
            'departamento_destino': forms.Select(attrs=_base_attrs()),
        }

    def __init__(self, *args, edificio=None, **kwargs):
        super().__init__(*args, **kwargs)

        if edificio:
            # Sólo estacionamientos del propio edificio, y sin un registro
            # activo (ocupados) dentro de ese mismo edificio.
            ocupados_ids = EstacionamientoVisita.objects.filter(
                estado='ACTIVO', numero_estacionamiento__edificio=edificio
            ).values_list('numero_estacionamiento_id', flat=True)
            self.fields['numero_estacionamiento'].queryset = NumeroEstacionamiento.objects.filter(
                edificio=edificio
            ).exclude(id__in=ocupados_ids)
            self.fields['departamento_destino'].queryset = Departamento.objects.filter(edificio=edificio)
        else:
            self.fields['numero_estacionamiento'].queryset = NumeroEstacionamiento.objects.none()
            self.fields['departamento_destino'].queryset = Departamento.objects.none()


# ---------------------------------------------------------------------------
# Formulario PÚBLICO (sin login) — consulta de estacionamientos de visita
# disponibles, desde la pantalla de login. `departamentos` se inyecta desde
# la vista según el edificio ya seleccionado. El RUT se valida contra
# Residente en la vista (no acá), igual que en ReservaPublicaForm, porque
# requiere cruzar edificio + departamento + rut.
# ---------------------------------------------------------------------------
class ConsultaEstacionamientoForm(forms.Form):
    departamento = forms.ModelChoiceField(
        queryset=Departamento.objects.none(),
        widget=forms.Select(attrs=_base_attrs()),
        label='Tu departamento',
    )
    rut = forms.CharField(
        max_length=12, widget=forms.TextInput(attrs=_base_attrs({'placeholder': '12345678-9'})), label='Tu RUT',
    )

    def __init__(self, *args, departamentos=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['departamento'].queryset = (
            departamentos if departamentos is not None else Departamento.objects.none()
        )

    def clean_rut(self):
        rut = self.cleaned_data['rut']
        validar_rut(rut)
        return rut


class ReportesFiltroForm(forms.Form):
    """Filtro de rango de fechas para el panel de Reportes (AdminEdificio).
    Aplica a los gráficos de tramo horario, día, semana y mes de todos los
    módulos. No incluye departamento: el reporte es agregado a nivel edificio."""
    fecha_desde = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs=_base_attrs({'type': 'date', 'onchange': 'this.form.submit()'}))
    )
    fecha_hasta = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs=_base_attrs({'type': 'date', 'onchange': 'this.form.submit()'}))
    )


class EstacionamientoHistorialFiltroForm(forms.Form):
    fecha_desde = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs=_base_attrs({'type': 'date', 'onchange': 'this.form.submit()'}))
    )
    fecha_hasta = forms.DateField(
        required=False,
        widget=forms.DateInput(attrs=_base_attrs({'type': 'date', 'onchange': 'this.form.submit()'}))
    )
    departamento = forms.ModelChoiceField(
        required=False, queryset=Departamento.objects.none(), empty_label='Todos',
        widget=forms.Select(attrs=_base_attrs({'onchange': 'this.form.submit()'}))
    )

    def __init__(self, *args, edificio=None, **kwargs):
        super().__init__(*args, **kwargs)
        if edificio:
            self.fields['departamento'].queryset = Departamento.objects.filter(edificio=edificio)


# ---------------------------------------------------------------------------
# Módulo Registro de Visitas
# ---------------------------------------------------------------------------
class VisitaForm(forms.ModelForm):
    class Meta:
        model = Visita
        fields = ['nombre_visita', 'rut', 'departamento', 'dia_visita', 'hora_visita']
        widgets = {
            'nombre_visita': forms.TextInput(attrs=_base_attrs()),
            'rut': forms.TextInput(attrs=_base_attrs({'placeholder': '12345678-9'})),
            'departamento': forms.Select(attrs=_base_attrs()),
            'dia_visita': forms.DateInput(attrs=_base_attrs({'type': 'date'})),
            'hora_visita': forms.TimeInput(attrs=_base_attrs({'type': 'time'})),
        }

    def __init__(self, *args, edificio=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Departamento.objects.filter(edificio=edificio) if edificio else Departamento.objects.none()
        self.fields['departamento'].queryset = qs

    def clean_rut(self):
        rut = self.cleaned_data['rut']
        validar_rut(rut)
        return rut


# ---------------------------------------------------------------------------
# Módulo Mudanzas
# ---------------------------------------------------------------------------
class MudanzaForm(forms.ModelForm):
    class Meta:
        model = Mudanza
        fields = ['departamento', 'dia_mudanza', 'responsable', 'hora_inicio', 'comentario']
        widgets = {
            'departamento': forms.Select(attrs=_base_attrs()),
            'dia_mudanza': forms.DateInput(attrs=_base_attrs({'type': 'date'})),
            'responsable': forms.TextInput(attrs=_base_attrs()),
            'hora_inicio': forms.TimeInput(attrs=_base_attrs({'type': 'time'})),
            'comentario': forms.Textarea(attrs=_base_attrs({'rows': 3})),
        }

    def __init__(self, *args, edificio=None, **kwargs):
        super().__init__(*args, **kwargs)
        qs = Departamento.objects.filter(edificio=edificio) if edificio else Departamento.objects.none()
        self.fields['departamento'].queryset = qs


# ---------------------------------------------------------------------------
# Módulo Áreas Comunes
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Formulario PÚBLICO (sin login) — reserva de área común desde la pantalla
# de login. `departamentos` y `opciones_horas` se inyectan desde la vista
# según lo que el visitante ya seleccionó en los pasos previos (edificio,
# área, día, tramo). El RUT se valida contra Residente en la vista (no acá),
# porque requiere el edificio seleccionado, que no es un campo de este form.
# ---------------------------------------------------------------------------
class ReservaPublicaForm(forms.Form):
    departamento = forms.ModelChoiceField(
        queryset=Departamento.objects.none(),
        widget=forms.Select(attrs=_base_attrs()),
        label='Tu departamento',
    )
    nombre = forms.CharField(
        max_length=150, widget=forms.TextInput(attrs=_base_attrs()), label='Nombre completo',
    )
    rut = forms.CharField(
        max_length=12, widget=forms.TextInput(attrs=_base_attrs({'placeholder': '12345678-9'})), label='Tu RUT',
    )
    hora_inicio = forms.TimeField(widget=forms.Select(attrs=_base_attrs()), label='Hora de inicio')
    hora_termino = forms.TimeField(widget=forms.Select(attrs=_base_attrs()), label='Hora de término')
    comentario = forms.CharField(
        required=False, widget=forms.Textarea(attrs=_base_attrs({'rows': 2})), label='Comentario (opcional)',
    )

    def __init__(self, *args, departamentos=None, opciones_horas=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['departamento'].queryset = departamentos if departamentos is not None else Departamento.objects.none()
        opciones = [(h.strftime('%H:%M'), h.strftime('%H:%M')) for h in (opciones_horas or [])]
        self.fields['hora_inicio'].widget.choices = opciones
        self.fields['hora_termino'].widget.choices = opciones

    def clean_rut(self):
        rut = self.cleaned_data['rut']
        validar_rut(rut)
        return rut


class ReservaAreaComunForm(forms.ModelForm):
    class Meta:
        model = ReservaAreaComun
        fields = [
            'area_comun', 'departamento', 'dia_reserva', 'responsable',
            'hora_inicio', 'hora_termino', 'comentario',
        ]
        widgets = {
            'area_comun': forms.Select(attrs=_base_attrs()),
            'departamento': forms.Select(attrs=_base_attrs()),
            'dia_reserva': forms.DateInput(attrs=_base_attrs({'type': 'date'})),
            'responsable': forms.TextInput(attrs=_base_attrs()),
            'hora_inicio': forms.TimeInput(attrs=_base_attrs({'type': 'time'})),
            'hora_termino': forms.TimeInput(attrs=_base_attrs({'type': 'time'})),
            'comentario': forms.Textarea(attrs=_base_attrs({'rows': 3})),
        }

    def __init__(self, *args, edificio=None, **kwargs):
        super().__init__(*args, **kwargs)
        if edificio:
            self.fields['area_comun'].queryset = AreaComun.objects.filter(edificio=edificio)
            self.fields['departamento'].queryset = Departamento.objects.filter(edificio=edificio)
        else:
            self.fields['area_comun'].queryset = AreaComun.objects.none()
            self.fields['departamento'].queryset = Departamento.objects.none()
