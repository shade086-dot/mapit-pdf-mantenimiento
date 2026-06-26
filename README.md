# Mapit PDF Mantenimiento Moto

Importa informes PDF de Mapit desde una carpeta de Google Drive, suma kilómetros reales y controla mantenimiento básico de la moto.

La gracia de esta versión es que está pensada para Render Cron: baja los PDF de Google Drive, procesa los trayectos nuevos, guarda el histórico en `moto_maintenance.db` y vuelve a subir esa base de datos a la misma carpeta de Drive para no duplicar trayectos aunque Render tenga disco temporal.

## Estructura recomendada en Google Drive

Crea una carpeta, por ejemplo:

```text
Mapit mantenimiento moto/
  InformeMapit_01-06_25-06.pdf
  InformeMapit_26-06_30-06.pdf
  moto_maintenance.db   # la crea/sube el script automáticamente
```

Puedes subir PDFs por rango de fechas. Si se solapan, no pasa nada: el script deduplica por fecha/hora, coordenadas y distancia.

## Instalación local

```bash
pip install -r requirements.txt
```

## Configuración Google Drive

Necesitas una cuenta de servicio de Google Cloud y compartir la carpeta de Drive con el email del service account.

Variables necesarias:

```bash
export GOOGLE_DRIVE_FOLDER_ID="id_de_la_carpeta"
export GOOGLE_SERVICE_ACCOUNT_JSON="service-account.json"
```

En Render, en vez de subir el archivo JSON, pega el contenido completo del JSON en la variable `GOOGLE_SERVICE_ACCOUNT_JSON`.

## Uso principal

Procesar carpeta de Google Drive:

```bash
python mapit_mantenimiento.py importar-drive --ntfy
```

Importar una carpeta local:

```bash
python mapit_mantenimiento.py importar-carpeta pdfs_mapit --ntfy
```

Ver estado:

```bash
python mapit_mantenimiento.py estado
```

Registrar engrase de cadena:

```bash
python mapit_mantenimiento.py engrase --nota "Engrasada tras ruta"
```

Registrar limpieza de cadena:

```bash
python mapit_mantenimiento.py limpieza-cadena
```

Registrar revisión/cambio aceite:

```bash
python mapit_mantenimiento.py revision --tipo aceite --km-actuales 12000
```

## Variables opcionales

```bash
export MAPIT_DB="moto_maintenance.db"
export GOOGLE_DRIVE_DOWNLOAD_DIR="pdfs_mapit"
export GOOGLE_DRIVE_DB_NAME="moto_maintenance.db"
export CHAIN_GREASE_INTERVAL_KM="1000"
export CHAIN_CLEAN_INTERVAL_KM="3000"
export OIL_INTERVAL_KM="12000"
export NTFY_TOPIC="tu_topic_ntfy"
```

## Render Cron

Este proyecto incluye `render.yaml` con:

```bash
python mapit_mantenimiento.py importar-drive --ntfy
```

Configura en Render estas variables de entorno:

```text
GOOGLE_DRIVE_FOLDER_ID
GOOGLE_SERVICE_ACCOUNT_JSON
NTFY_TOPIC
CHAIN_GREASE_INTERVAL_KM
CHAIN_CLEAN_INTERVAL_KM
OIL_INTERVAL_KM
```

Horario recomendado inicial:

```text
0 22 * * *
```

Una ejecución diaria por la noche es suficiente si subes los PDFs cuando quieras.
