# Mapit Mantenimiento V4

Asistente de mantenimiento para la moto usando informes PDF de Mapit por Gmail y comandos rápidos por email.

## Render

Cron Job:

```bash
python mapit_mantenimiento.py importar-gmail --ntfy
```

Variables necesarias:

```text
GMAIL_ADDRESS
GMAIL_APP_PASSWORD
NTFY_TOPIC
GITHUB_TOKEN
GITHUB_REPO
GITHUB_BRANCH=main
NTFY_TITLE=Mapit mantenimiento
```

## Comandos por email

Envíate un correo con uno de estos asuntos:

```text
mapit estado
mapit engrase
mapit limpieza
mapit aceite
mapit revision
mapit itv
mapit neumaticos
mapit repostaje
mapit historial
mapit stats
```

El cuerpo del correo se guarda como nota. Si escribes algo como `18540 km`, se guarda como odómetro opcional del evento.

## Contadores

Los contadores se respetan mediante `trip_total_km`: cada mantenimiento registra en qué total importado se hizo. Después el contador se calcula como:

```text
km actuales importados - km importados en el último evento
```

Así `mapit engrase` reinicia solo engrase; `mapit limpieza` reinicia solo limpieza; `mapit aceite` reinicia solo aceite/revisión.

## Recordatorios ntfy

Las notificaciones incluyen:

- estado de cadena, limpieza y aceite,
- recordatorio de generar informe de Mapit,
- comandos rápidos disponibles por correo.

## Comandos manuales Render Shell

```bash
python mapit_mantenimiento.py estado
python mapit_mantenimiento.py historial
python mapit_mantenimiento.py stats
python mapit_mantenimiento.py engrase --nota "Engrase cadena"
```


## V4.1 estabilidad

Cambios incluidos:

- No envía ntfy cuando no hay informes ni comandos nuevos, salvo recordatorio inteligente y con cooldown.
- Cooldown de recordatorios configurable con `REMINDER_COOLDOWN_HOURS` (por defecto 48h).
- Migración automática de la DB antigua y subida a GitHub para que no vuelva a aparecer el error `processed_emails has no column named kind`.
- Comando nuevo: `mapit ayuda`.
- Se mantienen los contadores existentes; no borra rutas ni mantenimientos.


## Integración con el cron de gasolina

La V5 añade salidas pensadas para que el proyecto de gasolina pueda mostrar estado de moto sin mezclar proyectos:

```bash
python mapit_mantenimiento.py estado-json
python mapit_mantenimiento.py estado-corto
```

`estado-json` devuelve campos como `texto_bloque`, `texto_corto`, `alert_level`, `cadena`, `limpieza` y `aceite`.

Recomendación: el cron de gasolina debe mostrar `texto_bloque` solo cada varias notificaciones o siempre que `alert_level` sea `soon` o `due`.
