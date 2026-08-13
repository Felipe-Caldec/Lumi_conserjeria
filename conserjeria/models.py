from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db import models

from .validators import validar_rut, validar_telefono, formatear_rut


# ---------------------------------------------------------------------------
# Edificio — raíz del multi-tenancy. Todo lo demás cuelga de acá, directa o
# indirectamente (vía Departamento).
# ---------------------------------------------------------------------------
class Edificio(models.Model):
    nombre = models.CharField(max_length=150, unique=True)
    direccion = models.CharField(max_length=255, blank=True)
    activo = models.BooleanField(default=True)
    creado_en = models.DateTimeField(auto_now_add=True)

    # --- Webpay Plus Mall: código de comercio "hijo" asignado por Transbank
    # al afiliar este edificio. Null mientras el edificio no está afiliado
    # (el trámite con Transbank es independiente del alta del edificio en el
    # sistema, por eso no es obligatorio desde el día uno). Sin este código
    # no se pueden generar reservas pagadas para las áreas comunes que lo
    # requieran.
    codigo_comercio_webpay = models.CharField(
        max_length=20, null=True, blank=True, unique=True,
        help_text='Código de comercio hijo entregado por Transbank (Webpay Plus Mall). '
                   'Vacío si el edificio aún no está afiliado.',
    )
    # Si la garantía se devuelve al residente después de usar el área (True)
    # o queda como tarifa de uso no reembolsable (False). Se define por
    # edificio, no por área común.
    garantia_reembolsable = models.BooleanField(default=False)

    # -----------------------------------------------------------------
    # Módulos opcionales (complementos al sistema base), habilitados sólo
    # por el SuperAdmin por edificio. Existen porque cada uno agrega
    # consultas/carga extra al servidor (CPU/RAM en la instancia de AWS):
    # el formulario público de reservas recalcula bloques de horario libres
    # en cada request, la consulta de estacionamientos filtra en vivo, y
    # las lavadoras generan tráfico HTTP saliente + escrituras periódicas.
    # Por defecto vienen desactivados: son complementos, no parte del
    # sistema base (Departamentos, Residentes, Paquetería, Visitas,
    # Mudanzas, Estacionamiento de visita -- la asignación en sí, no la
    # consulta pública -- siguen siempre disponibles).
    # -----------------------------------------------------------------
    modulo_reservas = models.BooleanField(
        default=False,
        verbose_name='Módulo: Reserva y pago de áreas comunes',
        help_text='Habilita el formulario público de reservas y el cobro online (Webpay) de la garantía.',
    )
    modulo_consulta_estacionamiento = models.BooleanField(
        default=False,
        verbose_name='Módulo: Consulta pública de disponibilidad de estacionamientos de visita',
        help_text='Habilita la consulta pública (sin login) de estacionamientos de visita libres en este momento.',
    )
    modulo_lavadoras = models.BooleanField(
        default=False,
        verbose_name='Módulo: Monitoreo de lavadoras (IoT)',
        help_text='Habilita que el comando consultar_lavadoras procese las lavadoras de este edificio.',
    )

    class Meta:
        ordering = ['nombre']
        verbose_name = 'Edificio'
        verbose_name_plural = 'Edificios'

    def __str__(self):
        return self.nombre


# ---------------------------------------------------------------------------
# PerfilUsuario reemplaza a PerfilConserje. Un mismo modelo para
# AdminEdificio y Conserje, distinguidos por `rol`. El SuperAdmin NO tiene
# fila acá: es sólo User.is_superuser=True, sin PerfilUsuario asociado.
# ---------------------------------------------------------------------------
class PerfilUsuario(models.Model):
    ROL_ADMIN_EDIFICIO = 'ADMIN_EDIFICIO'
    ROL_CONSERJE = 'CONSERJE'
    ROL_CHOICES = [
        (ROL_ADMIN_EDIFICIO, 'Administrador de Edificio'),
        (ROL_CONSERJE, 'Conserje'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='perfil')
    edificio = models.ForeignKey(Edificio, on_delete=models.PROTECT, related_name='perfiles')
    rol = models.CharField(max_length=20, choices=ROL_CHOICES)

    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    rut = models.CharField(max_length=12, unique=True, validators=[validar_rut])
    direccion = models.CharField(max_length=255)
    telefono = models.CharField(max_length=9, validators=[validar_telefono])
    correo = models.EmailField()
    numero_emergencia = models.CharField(max_length=9, validators=[validar_telefono])
    contacto_emergencia = models.CharField(max_length=150)
    debe_cambiar_contrasena = models.BooleanField(
        default=True,
        verbose_name='Debe cambiar contraseña',
        help_text='Se activa automáticamente al crear la cuenta; se desactiva '
                   'apenas el usuario cambia su contraseña por primera vez.',
    )

    class Meta:
        verbose_name = 'Perfil de Usuario'
        verbose_name_plural = 'Perfiles de Usuario'
        indexes = [models.Index(fields=['edificio', 'rol'])]

    def __str__(self):
        return f'{self.nombre} {self.apellido} ({self.get_rol_display()} - {self.edificio})'

    def save(self, *args, **kwargs):
        if self.rut:
            self.rut = formatear_rut(self.rut)
        super().save(*args, **kwargs)

    @property
    def es_admin_edificio(self):
        return self.rol == self.ROL_ADMIN_EDIFICIO

    @property
    def es_conserje(self):
        return self.rol == self.ROL_CONSERJE


# ---------------------------------------------------------------------------
# Departamento — ahora cuelga de Edificio. "numero" ya no es único de forma
# global (antes asumía 1 solo edificio): es único DENTRO de cada edificio.
# ---------------------------------------------------------------------------
class Departamento(models.Model):
    edificio = models.ForeignKey(Edificio, on_delete=models.CASCADE, related_name='departamentos')
    numero = models.CharField(max_length=20)

    class Meta:
        unique_together = [('edificio', 'numero')]
        ordering = ['edificio', 'numero']

    def __str__(self):
        return f'{self.numero} - {self.edificio}'


class Residente(models.Model):
    departamento = models.ForeignKey(Departamento, on_delete=models.CASCADE, related_name='residentes')
    nombre = models.CharField(max_length=100)
    apellido = models.CharField(max_length=100)
    rut = models.CharField(max_length=12, validators=[validar_rut])

    class Meta:
        ordering = ['apellido', 'nombre']

    def __str__(self):
        return f'{self.nombre} {self.apellido}'

    def save(self, *args, **kwargs):
        if self.rut:
            self.rut = formatear_rut(self.rut)
        super().save(*args, **kwargs)

    @property
    def edificio(self):
        return self.departamento.edificio


# ---------------------------------------------------------------------------
# Catálogos que ANTES eran globales (unique=True a nivel de tabla) y ahora
# son por edificio (unique_together).
# ---------------------------------------------------------------------------
class AreaComun(models.Model):
    edificio = models.ForeignKey(Edificio, on_delete=models.CASCADE, related_name='areas_comunes')
    nombre = models.CharField(max_length=100)
    duracion_maxima_horas = models.PositiveSmallIntegerField(
        null=True, blank=True,
        help_text='Máximo de horas SEGUIDAS por reserva (ej. 2 para el gimnasio). '
                   'Déjalo vacío si no hay límite (ej. quincho).',
    )
    # No todas las áreas comunes cobran garantía (ej. la sala de eventos
    # exige pago online, el patio de juegos no). requiere_pago es la fuente
    # de verdad explícita: si es False, la reserva pública se confirma igual
    # que hoy, sin pasar por Webpay. Si es True, monto_garantia es obligatorio.
    requiere_pago = models.BooleanField(
        default=False,
        help_text='Si está marcado, la reserva pública exige pago online antes de confirmarse.',
    )
    monto_garantia = models.PositiveIntegerField(
        null=True, blank=True,
        help_text='Monto de la garantía en pesos chilenos (sin decimales). '
                   'Obligatorio si "requiere pago" está marcado.',
    )

    class Meta:
        unique_together = [('edificio', 'nombre')]
        ordering = ['nombre']
        verbose_name = 'Área Común'
        verbose_name_plural = 'Áreas Comunes'

    def __str__(self):
        return f'{self.nombre} - {self.edificio}'

    def clean(self):
        super().clean()
        if self.requiere_pago and not self.monto_garantia:
            raise ValidationError(
                {'monto_garantia': 'Obligatorio cuando el área requiere pago online.'}
            )
        if not self.requiere_pago and self.monto_garantia:
            raise ValidationError(
                {'monto_garantia': 'No debe llevar monto si el área no requiere pago online.'}
            )


# ---------------------------------------------------------------------------
# Horario disponible por Área Común — configurable en base de datos por el
# Administrador de Edificio (no hardcodeado). Varios bloques por área
# (ej. 10:00-13:00 y 16:00-22:00), opcionalmente distintos por día de semana.
# ---------------------------------------------------------------------------
class BloqueHorarioAreaComun(models.Model):
    DIA_TODOS = 'TODOS'
    DIA_CHOICES = [
        (DIA_TODOS, 'Todos los días'),
        ('LUN', 'Lunes'), ('MAR', 'Martes'), ('MIE', 'Miércoles'),
        ('JUE', 'Jueves'), ('VIE', 'Viernes'), ('SAB', 'Sábado'), ('DOM', 'Domingo'),
    ]
    # Mapeo de date.weekday() (0=Lunes..6=Domingo) al código de DIA_CHOICES.
    CODIGOS_POR_WEEKDAY = ['LUN', 'MAR', 'MIE', 'JUE', 'VIE', 'SAB', 'DOM']

    @classmethod
    def dia_siguiente(cls, codigo_dia):
        """Día de la semana siguiente al recibido (para partir en dos un
        bloque que cruza la medianoche, ej. Viernes 20:00 -> Sábado 02:00).
        'TODOS' sigue siendo 'TODOS' al día siguiente."""
        if codigo_dia == cls.DIA_TODOS:
            return cls.DIA_TODOS
        idx = cls.CODIGOS_POR_WEEKDAY.index(codigo_dia)
        return cls.CODIGOS_POR_WEEKDAY[(idx + 1) % len(cls.CODIGOS_POR_WEEKDAY)]

    area_comun = models.ForeignKey(AreaComun, on_delete=models.CASCADE, related_name='bloques_horario')
    dia_semana = models.CharField(max_length=5, choices=DIA_CHOICES, default=DIA_TODOS)
    hora_inicio = models.TimeField()
    hora_termino = models.TimeField()

    class Meta:
        ordering = ['area_comun', 'dia_semana', 'hora_inicio']
        verbose_name = 'Bloque de Horario Disponible'
        verbose_name_plural = 'Bloques de Horario Disponible'

    def __str__(self):
        return f'{self.area_comun} - {self.get_dia_semana_display()} {self.hora_inicio}-{self.hora_termino}'

    @property
    def edificio(self):
        return self.area_comun.edificio


class NumeroEstacionamiento(models.Model):
    edificio = models.ForeignKey(Edificio, on_delete=models.CASCADE, related_name='estacionamientos')
    numero = models.CharField(max_length=20)

    class Meta:
        unique_together = [('edificio', 'numero')]
        ordering = ['numero']
        verbose_name = 'N° Estacionamiento'
        verbose_name_plural = 'Estacionamientos de Visita (Catálogo)'

    def __str__(self):
        return f'{self.numero} - {self.edificio}'


# ---------------------------------------------------------------------------
# Módulo Lavadoras — piloto de sensores. Cada Lavadora corresponde a un
# dispositivo Shelly 1PM físico (1 dispositivo = 1 lavadora). `ip_shelly` es
# la IP local del dispositivo dentro de la red del edificio; el estado se
# infiere a partir de la potencia reportada por el propio Shelly, consultado
# vía HTTP REST por el management command `consultar_lavadoras`.
# ---------------------------------------------------------------------------
class Lavadora(models.Model):
    LIBRE, EN_USO, DESCONOCIDO = 'LIBRE', 'EN_USO', 'DESCONOCIDO'
    ESTADO_CHOICES = [
        (LIBRE, 'Libre'),
        (EN_USO, 'En uso'),
        (DESCONOCIDO, 'Desconocido'),  # sin lectura reciente o error de consulta
    ]

    edificio = models.ForeignKey(Edificio, on_delete=models.CASCADE, related_name='lavadoras')
    nombre = models.CharField(max_length=50, help_text='Ej: "Lavadora 1"')
    ip_shelly = models.GenericIPAddressField(
        help_text='IP local del Shelly 1PM en la red del edificio (ej: 192.168.1.50)'
    )
    activa = models.BooleanField(default=True, help_text='Desmarcar para excluirla de las consultas automáticas')
    estado = models.CharField(max_length=15, choices=ESTADO_CHOICES, default=DESCONOCIDO)
    potencia_actual_w = models.FloatField(null=True, blank=True)
    ultima_lectura = models.DateTimeField(null=True, blank=True)
    ultimo_error = models.CharField(max_length=255, blank=True)

    class Meta:
        unique_together = [('edificio', 'nombre')]
        ordering = ['edificio', 'nombre']
        verbose_name = 'Lavadora'
        verbose_name_plural = 'Lavadoras (Catálogo)'

    def __str__(self):
        return f'{self.nombre} - {self.edificio}'


class LecturaLavadora(models.Model):
    """
    Historial crudo de cada consulta al Shelly, independiente del estado
    'oficial' guardado en Lavadora. Sirve para calibrar el umbral de watts
    y la lógica de debounce con datos reales durante el piloto, antes de
    ajustar la lógica final en el management command.
    """
    lavadora = models.ForeignKey(Lavadora, on_delete=models.CASCADE, related_name='lecturas')
    potencia_w = models.FloatField()
    relay_on = models.BooleanField()
    estado_calculado = models.CharField(max_length=15, choices=Lavadora.ESTADO_CHOICES)
    creado_en = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-creado_en']
        verbose_name = 'Lectura de Lavadora'
        verbose_name_plural = 'Lecturas de Lavadoras (Historial)'

    def __str__(self):
        return f'{self.lavadora} - {self.potencia_w}W - {self.creado_en:%Y-%m-%d %H:%M:%S}'


# ---------------------------------------------------------------------------
# Módulo Paquetería — campos reales rescatados de la migración original.
# ---------------------------------------------------------------------------
class Paqueteria(models.Model):
    PENDIENTE, ENTREGADO = 'PENDIENTE', 'ENTREGADO'
    ESTADO_CHOICES = [(PENDIENTE, 'Pendiente'), (ENTREGADO, 'Entregado')]

    departamento = models.ForeignKey(Departamento, on_delete=models.CASCADE, related_name='paquetes')
    origen = models.CharField(max_length=150)
    descripcion = models.TextField(blank=True)
    destinatario = models.ForeignKey(
        Residente, on_delete=models.SET_NULL, null=True, blank=True, related_name='paquetes_destinatario'
    )
    recibido_por = models.ForeignKey(
        Residente, on_delete=models.SET_NULL, null=True, blank=True, related_name='paquetes_retirados'
    )
    conserje_recibe = models.ForeignKey(User, on_delete=models.PROTECT, related_name='paquetes_recibidos')
    conserje_entrega = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='paquetes_entregados'
    )
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default=PENDIENTE)
    fecha_recepcion = models.DateTimeField(auto_now_add=True)
    fecha_entrega = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Paquete'
        verbose_name_plural = 'Paquetería'
        ordering = ['-fecha_recepcion']
        indexes = [models.Index(fields=['departamento', '-fecha_recepcion'])]

    def __str__(self):
        return f'Paquete #{self.pk} - {self.departamento}'

    @property
    def edificio(self):
        return self.departamento.edificio


# ---------------------------------------------------------------------------
# Módulo Estacionamiento de Visitas
# ---------------------------------------------------------------------------
class EstacionamientoVisita(models.Model):
    ACTIVO, FINALIZADO = 'ACTIVO', 'FINALIZADO'
    ESTADO_CHOICES = [(ACTIVO, 'Activo'), (FINALIZADO, 'Finalizado')]

    numero_estacionamiento = models.ForeignKey(NumeroEstacionamiento, on_delete=models.PROTECT)
    patente = models.CharField(max_length=20)
    conductor = models.CharField(max_length=150)
    departamento_destino = models.ForeignKey(
        Departamento, on_delete=models.CASCADE, related_name='visitas_estacionamiento'
    )
    fecha_hora_entrada = models.DateTimeField(auto_now_add=True)
    fecha_hora_salida = models.DateTimeField(null=True, blank=True)
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default=ACTIVO)
    conserje_entrada = models.ForeignKey(
        User, on_delete=models.PROTECT, related_name='estacionamientos_entrada'
    )
    conserje_salida = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='estacionamientos_salida'
    )

    class Meta:
        verbose_name = 'Estacionamiento de Visita'
        verbose_name_plural = 'Estacionamientos de Visita'
        ordering = ['-fecha_hora_entrada']
        indexes = [models.Index(fields=['departamento_destino', '-fecha_hora_entrada'])]

    def __str__(self):
        return f'{self.patente} - {self.departamento_destino}'

    @property
    def edificio(self):
        return self.departamento_destino.edificio


# ---------------------------------------------------------------------------
# Módulo Mudanzas
# ---------------------------------------------------------------------------
class Mudanza(models.Model):
    departamento = models.ForeignKey(Departamento, on_delete=models.CASCADE, related_name='mudanzas')
    dia_mudanza = models.DateField()
    responsable = models.CharField(max_length=150)
    hora_inicio = models.TimeField()
    comentario = models.TextField(blank=True)
    conserje_registra = models.ForeignKey(User, on_delete=models.PROTECT)

    class Meta:
        verbose_name = 'Mudanza'
        verbose_name_plural = 'Mudanzas'
        ordering = ['dia_mudanza', 'hora_inicio']
        indexes = [models.Index(fields=['departamento', 'dia_mudanza'])]

    def __str__(self):
        return f'Mudanza {self.departamento} - {self.dia_mudanza}'

    @property
    def edificio(self):
        return self.departamento.edificio


# ---------------------------------------------------------------------------
# Módulo Áreas Comunes
# ---------------------------------------------------------------------------
class ReservaAreaComun(models.Model):
    PENDIENTE, CONFIRMADA, CANCELADA = 'PENDIENTE', 'CONFIRMADA', 'CANCELADA'
    ESTADO_CHOICES = [
        (PENDIENTE, 'Pendiente de garantía'),
        (CONFIRMADA, 'Confirmada'),
        (CANCELADA, 'Cancelada'),
    ]

    area_comun = models.ForeignKey(AreaComun, on_delete=models.CASCADE, related_name='reservas')
    departamento = models.ForeignKey(Departamento, on_delete=models.CASCADE, related_name='reservas')
    dia_reserva = models.DateField()
    responsable = models.CharField(max_length=150)
    rut_responsable = models.CharField(max_length=12, blank=True, default='', validators=[validar_rut])
    hora_inicio = models.TimeField()
    hora_termino = models.TimeField()
    comentario = models.TextField(blank=True)
    estado = models.CharField(max_length=10, choices=ESTADO_CHOICES, default=CONFIRMADA)
    # Reservas creadas por el conserje ya quedan CONFIRMADA de inmediato (por
    # eso el default arriba); las reservas públicas (sin login, desde el
    # formulario en el login) se crean en PENDIENTE y sin conserje_registra.
    conserje_registra = models.ForeignKey(
        User, on_delete=models.PROTECT, null=True, blank=True, related_name='reservas_creadas'
    )
    conserje_confirma = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reservas_confirmadas'
    )
    fecha_confirmacion = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Reserva de Área Común'
        verbose_name_plural = 'Reservas de Áreas Comunes'
        ordering = ['dia_reserva', 'hora_inicio']
        indexes = [models.Index(fields=['departamento', 'dia_reserva'])]

    def __str__(self):
        return f'{self.area_comun} - {self.dia_reserva}'

    def save(self, *args, **kwargs):
        if self.rut_responsable:
            self.rut_responsable = formatear_rut(self.rut_responsable)
        super().save(*args, **kwargs)

    @property
    def edificio(self):
        return self.departamento.edificio


# ---------------------------------------------------------------------------
# Pago de la garantía vía Webpay Plus Mall. 1-a-1 con ReservaAreaComun: sólo
# existe para reservas de áreas con requiere_pago=True. El estado de la
# reserva (PENDIENTE/CONFIRMADA/CANCELADA) es la fuente de verdad para el
# horario; el estado del pago es la fuente de verdad para la plata. Nunca se
# confía en un monto que venga del cliente: monto se copia server-side desde
# AreaComun.monto_garantia al momento de crear el registro.
# ---------------------------------------------------------------------------
class PagoReserva(models.Model):
    INICIADO, APROBADO, RECHAZADO, ANULADO, REEMBOLSADO = (
        'INICIADO', 'APROBADO', 'RECHAZADO', 'ANULADO', 'REEMBOLSADO',
    )
    ESTADO_CHOICES = [
        (INICIADO, 'Iniciado (esperando pago en Transbank)'),
        (APROBADO, 'Aprobado'),
        (RECHAZADO, 'Rechazado'),
        (ANULADO, 'Anulado (no completado / expirado)'),
        (REEMBOLSADO, 'Reembolsado'),
    ]

    reserva = models.OneToOneField(
        ReservaAreaComun, on_delete=models.CASCADE, related_name='pago'
    )
    # buy_order de Transbank: máx. 26 caracteres, único.
    buy_order = models.CharField(max_length=26, unique=True)
    token_ws = models.CharField(max_length=64, blank=True, default='')
    monto = models.PositiveIntegerField()
    estado = models.CharField(max_length=11, choices=ESTADO_CHOICES, default=INICIADO)
    codigo_autorizacion = models.CharField(max_length=20, blank=True, default='')
    # Respuesta cruda de Transbank (create/commit/refund) para auditoría y
    # resolución de disputas. Nunca se muestra tal cual al usuario final.
    respuesta_json = models.JSONField(default=dict, blank=True)
    fecha_creacion = models.DateTimeField(auto_now_add=True)
    fecha_pago = models.DateTimeField(null=True, blank=True)
    fecha_reembolso = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Pago de Reserva'
        verbose_name_plural = 'Pagos de Reservas'
        ordering = ['-fecha_creacion']
        indexes = [models.Index(fields=['estado', 'fecha_creacion'])]

    def __str__(self):
        return f'{self.buy_order} - {self.estado}'

    @property
    def edificio(self):
        return self.reserva.departamento.edificio


# ---------------------------------------------------------------------------
# Módulo Registro de Visitas
# ---------------------------------------------------------------------------
class Visita(models.Model):
    departamento = models.ForeignKey(Departamento, on_delete=models.CASCADE, related_name='visitas')
    nombre_visita = models.CharField(max_length=150)
    rut = models.CharField(max_length=12, validators=[validar_rut])
    dia_visita = models.DateField()
    hora_visita = models.TimeField()
    fecha_registro = models.DateTimeField(auto_now_add=True)
    conserje_registra = models.ForeignKey(User, on_delete=models.PROTECT)

    class Meta:
        verbose_name = 'Visita'
        verbose_name_plural = 'Visitas'
        ordering = ['-dia_visita', '-hora_visita']
        indexes = [models.Index(fields=['departamento', '-dia_visita'])]

    def __str__(self):
        return f'{self.nombre_visita} - {self.departamento}'

    def save(self, *args, **kwargs):
        if self.rut:
            self.rut = formatear_rut(self.rut)
        super().save(*args, **kwargs)

    @property
    def edificio(self):
        return self.departamento.edificio
