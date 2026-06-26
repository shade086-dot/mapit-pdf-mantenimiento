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
