from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conserjeria', '0003_areacomun_duracion_maxima_horas'),
    ]

    operations = [
        migrations.AddField(
            model_name='perfilusuario',
            name='debe_cambiar_contrasena',
            field=models.BooleanField(
                default=True,
                help_text='Se activa automáticamente al crear la cuenta; se desactiva '
                           'apenas el usuario cambia su contraseña por primera vez.',
                verbose_name='Debe cambiar contraseña',
            ),
        ),
    ]
