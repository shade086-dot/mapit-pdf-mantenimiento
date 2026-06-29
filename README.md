# Mapit Mantenimiento V5.1

Asistente de mantenimiento para la moto usando informes PDF de Mapit por Gmail, comandos rápidos por email y salida JSON para el cron de gasolina.

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
mapit revision
mapit itv
mapit neumaticos
mapit repostaje
mapit historial
mapit stats
mapit ayuda
```

`mapit revision` registra mantenimiento completo y reinicia el contador de aceite/revisión. El aceite no se toca desde `mapit actualizar` para evitar cambios accidentales.

## Actualizar varios valores con un único email

Asunto:

```text
mapit actualizar
```

Cuerpo:

```text
km=13100
engrase=500
limpieza=1200
```

Significado:

- `km=13100`: fija los km reales estimados. Mapit mantiene sus km brutos y se guarda un ajuste.
- `engrase=500`: la cadena lleva 500 km desde el último engrase; quedan 500 km hasta 1000.
- `limpieza=1200`: la limpieza lleva 1200 km; quedan 1800 km hasta 3000.
- `aceite` se ignora en `mapit actualizar`; usa `mapit revision` para mantenimiento completo.

También puedes usar:

```text
mapit km 13100
mapit ajuste -135
mapit contador engrase 500
mapit contador limpieza 1200
```

## Contadores

Los contadores se respetan mediante `trip_total_km`: cada mantenimiento registra en qué total importado se hizo. Después el contador se calcula como:

```text
km actuales importados - km importados en el último evento
```

Así `mapit engrase` reinicia solo engrase; `mapit limpieza` reinicia solo limpieza; `mapit revision` registra revisión completa/aceite.

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
python mapit_mantenimiento.py estado-json
python mapit_mantenimiento.py estado-corto
```

## Integración con el cron de gasolina

La V5 añade salidas pensadas para que el proyecto de gasolina pueda mostrar estado de moto sin mezclar proyectos:

```bash
python mapit_mantenimiento.py estado-json
python mapit_mantenimiento.py estado-corto
```

`estado-json` devuelve campos como `texto_bloque`, `texto_corto`, `alert_level`, `cadena`, `limpieza`, `aceite`, `km_reales_estimados`, `km_mapit` y `km_ajuste`.
