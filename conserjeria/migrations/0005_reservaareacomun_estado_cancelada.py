from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conserjeria', '0004_perfilusuario_debe_cambiar_contrasena'),
    ]

    operations = [
        migrations.AlterField(
            model_name='reservaareacomun',
            name='estado',
            field=models.CharField(
                choices=[
                    ('PENDIENTE', 'Pendiente de garantía'),
                    ('CONFIRMADA', 'Confirmada'),
                    ('CANCELADA', 'Cancelada'),
                ],
                default='CONFIRMADA',
                max_length=10,
            ),
        ),
    ]
