from django.urls import path

from . import views

app_name = 'conserjeria'

urlpatterns = [
    # Autenticación
    path('login/', views.login_view, name='login'),
    path('logout/', views.logout_view, name='logout'),
    path('webpay/retorno/', views.webpay_retorno, name='webpay_retorno'),
    path('cambiar-password/', views.cambiar_password_obligatorio, name='cambiar_password_obligatorio'),
    path('', views.home_redirect, name='home_redirect'),

    # --- SuperAdmin ---
    path('edificios/', views.listar_edificios, name='listar_edificios'),
    path('edificios/crear/', views.crear_edificio, name='crear_edificio'),
    path('edificios/<int:pk>/editar/', views.editar_edificio, name='editar_edificio'),
    path('admins-edificio/', views.listar_admins_edificio, name='listar_admins_edificio'),
    path('admins-edificio/crear/', views.crear_admin_edificio, name='crear_admin_edificio'),
    path('admins-edificio/<int:pk>/editar/', views.editar_admin_edificio, name='editar_admin_edificio'),

    # --- AdminEdificio ---
    path('admin-panel/', views.admin_dashboard, name='admin_dashboard'),
    path('admin-panel/conserjes/', views.listar_conserjes, name='listar_conserjes'),
    path('admin-panel/conserjes/crear/', views.crear_conserje, name='crear_conserje'),
    path('admin-panel/conserjes/<int:pk>/editar/', views.editar_conserje, name='editar_conserje'),
    path('admin-panel/conserjes/<int:pk>/eliminar/', views.eliminar_conserje, name='eliminar_conserje'),
    path('admin-panel/crear-departamento/', views.crear_departamento, name='crear_departamento'),
    path('admin-panel/editar-departamento/<int:pk>/', views.editar_departamento, name='editar_departamento'),
    path('admin-panel/eliminar-departamento/<int:pk>/', views.eliminar_departamento, name='eliminar_departamento'),
    path('admin-panel/crear-residente/', views.crear_residente, name='crear_residente'),
    path('admin-panel/editar-residente/<int:pk>/', views.editar_residente, name='editar_residente'),
    path('admin-panel/eliminar-residente/<int:pk>/', views.eliminar_residente, name='eliminar_residente'),
    path('admin-panel/residentes-por-departamento/', views.visualizar_residentes, name='visualizar_residentes'),
    path('admin-panel/areas-comunes/', views.listar_areas_comunes, name='listar_areas_comunes'),
    path('admin-panel/areas-comunes/crear/', views.crear_area_comun, name='crear_area_comun'),
    path('admin-panel/areas-comunes/<int:pk>/editar/', views.editar_area_comun, name='editar_area_comun'),
    path('admin-panel/areas-comunes/<int:pk>/eliminar/', views.eliminar_area_comun, name='eliminar_area_comun'),
    path('admin-panel/configuracion-pagos/', views.configuracion_pagos, name='configuracion_pagos'),
    path(
        'admin-panel/areas-comunes/<int:pk>/horario/agregar/',
        views.agregar_bloque_horario, name='agregar_bloque_horario',
    ),
    path(
        'admin-panel/areas-comunes/horario/<int:pk>/eliminar/',
        views.eliminar_bloque_horario, name='eliminar_bloque_horario',
    ),
    path('admin-panel/estacionamientos/', views.listar_estacionamientos, name='listar_estacionamientos'),
    path('admin-panel/estacionamientos/crear/', views.crear_estacionamiento, name='crear_estacionamiento'),
    path(
        'admin-panel/estacionamientos/<int:pk>/editar/',
        views.editar_estacionamiento, name='editar_estacionamiento',
    ),
    path(
        'admin-panel/estacionamientos/<int:pk>/eliminar/',
        views.eliminar_estacionamiento, name='eliminar_estacionamiento',
    ),
    path('admin-panel/reportes/', views.reportes_dashboard, name='reportes_dashboard'),

    # --- Conserje - Dashboard ---
    path('conserje/', views.conserje_dashboard, name='conserje_dashboard'),

    # Paquetería
    path('conserje/paqueteria/recibir/', views.paqueteria_recibir, name='paqueteria_recibir'),
    path('conserje/paqueteria/entregar/', views.paqueteria_entregar, name='paqueteria_entregar'),
    path('conserje/paqueteria/historial/', views.paqueteria_historial, name='paqueteria_historial'),

    # Estacionamiento de Visitas
    path('conserje/estacionamiento/estacionar/', views.estacionamiento_estacionar, name='estacionamiento_estacionar'),
    path('conserje/estacionamiento/salir/', views.estacionamiento_salir, name='estacionamiento_salir'),
    path('conserje/estacionamiento/historial/', views.estacionamiento_historial, name='estacionamiento_historial'),

    # Registro de Visitas
    path('conserje/visitas/', views.registro_visitas, name='registro_visitas'),

    # Mudanzas
    path('conserje/mudanzas/', views.mudanzas_view, name='mudanzas'),

    # Áreas Comunes
    path('conserje/reservas/', views.reservas_view, name='reservas'),
    path('conserje/reservas/<int:pk>/confirmar/', views.reservas_confirmar, name='reservas_confirmar'),
    path('conserje/reservas/<int:pk>/cancelar/', views.reservas_cancelar, name='reservas_cancelar'),
]
