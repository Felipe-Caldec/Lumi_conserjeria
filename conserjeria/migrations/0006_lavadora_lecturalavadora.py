# Generado a mano a partir de `makemigrations`, recortando las operaciones
# AlterField de `id` en modelos existentes (drift preexistente AutoField ->
# BigAutoField no relacionado con este cambio) para no mezclarlas con el
# alta de las tablas de Lavadora. Si en algún momento se decide resolver
# ese drift, debe hacerse en una migración aparte y explícita.
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conserjeria', '0005_reservaareacomun_estado_cancelada'),
    ]

    operations = [
        migrations.CreateModel(
            name='Lavadora',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('nombre', models.CharField(help_text='Ej: "Lavadora 1"', max_length=50)),
                ('ip_shelly', models.GenericIPAddressField(help_text='IP local del Shelly 1PM en la red del edificio (ej: 192.168.1.50)')),
                ('activa', models.BooleanField(default=True, help_text='Desmarcar para excluirla de las consultas automáticas')),
                ('estado', models.CharField(choices=[('LIBRE', 'Libre'), ('EN_USO', 'En uso'), ('DESCONOCIDO', 'Desconocido')], default='DESCONOCIDO', max_length=15)),
                ('potencia_actual_w', models.FloatField(blank=True, null=True)),
                ('ultima_lectura', models.DateTimeField(blank=True, null=True)),
                ('ultimo_error', models.CharField(blank=True, max_length=255)),
                ('edificio', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lavadoras', to='conserjeria.edificio')),
            ],
            options={
                'verbose_name': 'Lavadora',
                'verbose_name_plural': 'Lavadoras (Catálogo)',
                'ordering': ['edificio', 'nombre'],
                'unique_together': {('edificio', 'nombre')},
            },
        ),
        migrations.CreateModel(
            name='LecturaLavadora',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('potencia_w', models.FloatField()),
                ('relay_on', models.BooleanField()),
                ('estado_calculado', models.CharField(choices=[('LIBRE', 'Libre'), ('EN_USO', 'En uso'), ('DESCONOCIDO', 'Desconocido')], max_length=15)),
                ('creado_en', models.DateTimeField(auto_now_add=True)),
                ('lavadora', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='lecturas', to='conserjeria.lavadora')),
            ],
            options={
                'verbose_name': 'Lectura de Lavadora',
                'verbose_name_plural': 'Lecturas de Lavadoras (Historial)',
                'ordering': ['-creado_en'],
            },
        ),
    ]
