# Mapit Gmail Mantenimiento 🏍️

Automatiza el mantenimiento de la moto usando los informes PDF de Mapit que llegan por Gmail.

Flujo:

1. Mapit envía el email `Tu Informe de Trayectos está listo`.
2. El cron busca emails nuevos de Mapit.
3. Extrae el enlace `Ver informe`.
4. Descarga el PDF.
5. Importa los trayectos y kilómetros.
6. Actualiza el contador de mantenimiento.
7. Envía aviso por ntfy.

## Archivos que subir a GitHub

Sube:

```text
mapit_mantenimiento.py
requirements.txt
render.yaml
README.md
.gitignore
.env.example
```

No subas:

```text
.env
*.db
downloads_mapit/
PDFs de Mapit
```

## Variables de entorno en Render

Obligatorias para Gmail:

```text
GMAIL_ADDRESS=tu_email@gmail.com
GMAIL_APP_PASSWORD=contraseña_de_aplicacion
```

Recomendadas:

```text
NTFY_TOPIC=tu_topic_ntfy
GITHUB_TOKEN=github_pat_xxx
GITHUB_REPO=tu_usuario/tu_repo
GITHUB_BRANCH=main
GITHUB_DB_PATH=data/moto_maintenance.db
```

## Importante sobre la contraseña de Gmail

No uses tu contraseña normal. Usa una **contraseña de aplicación** de Google.

Ruta habitual:

```text
Cuenta de Google → Seguridad → Verificación en dos pasos → Contraseñas de aplicaciones
```

Crea una para este proyecto y pégala en Render como `GMAIL_APP_PASSWORD`.

## Persistencia en Render

Render Cron puede ejecutarse en un entorno temporal. Para no perder la base de datos, el script puede guardar `moto_maintenance.db` dentro del propio repositorio usando la API de GitHub.

Para eso crea un token de GitHub con permiso:

```text
Contents: Read and write
```

Y configúralo en Render como `GITHUB_TOKEN`.

El script guardará la DB en:

```text
data/moto_maintenance.db
```

No pasa nada si esa ruta no existe: la crea en el primer procesamiento.

## Comandos útiles

Importar desde Gmail:

```bash
python mapit_mantenimiento.py importar-gmail --ntfy
```

Importar un PDF manual:

```bash
python mapit_mantenimiento.py importar InformeMapit_01-06_25-06.pdf
```

Ver estado:

```bash
python mapit_mantenimiento.py estado
```

Registrar engrase:

```bash
python mapit_mantenimiento.py engrase --nota "Engrasada después de lavar"
```

Registrar limpieza de cadena:

```bash
python mapit_mantenimiento.py limpieza-cadena --nota "Limpieza completa"
```

Registrar revisión:

```bash
python mapit_mantenimiento.py revision --tipo aceite --nota "Revisión anual"
```

## Búsqueda de emails

Por defecto busca:

```text
(UNSEEN FROM "mapit")
```

Si quieres afinarlo más, en Render puedes poner:

```text
MAPIT_EMAIL_SEARCH=(UNSEEN FROM "mapit" SUBJECT "Informe")
```

## Cron recomendado

Una vez al día por la noche:

```text
0 22 * * *
```
