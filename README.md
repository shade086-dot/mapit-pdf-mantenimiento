# Mapit Mantenimiento V3

Automatización para importar informes PDF de Mapit desde Gmail y llevar el mantenimiento de la moto por kilómetros.

## Comando Render

```bash
python mapit_mantenimiento.py importar-gmail --ntfy
```

## Variables

```env
GMAIL_ADDRESS=tu_correo@gmail.com
GMAIL_APP_PASSWORD=contraseña_de_aplicacion_de_gmail
NTFY_TOPIC=tu_topic_ntfy
NTFY_TITLE=Mapit mantenimiento
GITHUB_TOKEN=github_pat_xxx
GITHUB_REPO=shade086-dot/mapit-pdf-mantenimiento
GITHUB_BRANCH=main
GITHUB_DB_PATH=data/moto_maintenance.db
```

## Comandos por email

Envíate un correo a ti mismo con uno de estos asuntos:

```text
mapit engrase
mapit limpieza
mapit aceite
mapit revision
mapit estado
mapit neumaticos
mapit itv
```

El cuerpo del correo se guarda como nota.

## Contadores

- `mapit engrase`: reinicia solo contador de engrase de cadena.
- `mapit limpieza`: reinicia solo contador de limpieza de cadena.
- `mapit aceite`: reinicia contador de aceite/revisión.
- `mapit revision`: registra revisión general, no reinicia contadores específicos.
- `mapit estado`: no modifica nada.
- `mapit itv`: registra ITV, no modifica contadores.
- `mapit neumaticos`: registra neumáticos y empieza a contar km para ese juego.

## Recordatorios

Si pasan varios días sin informes nuevos, ntfy recordará generar un informe de Mapit.

```env
REPORT_REMINDER_DAYS=7
REMINDER_COOLDOWN_DAYS=3
```
