# Automation-Rev-Tec

Automatiza la descarga de certificados de antecedentes para la revisión técnico-administrativa de convocatorias de estímulos de la Alcaldía de Cali (actualmente en uso para el Ecosistema Musical y Dancístico del Festival Mundial de Salsa 2026).

Por cada persona listada en el archivo de un postulante (el Excel de anexo técnico, o el PDF de autorización de consulta de antecedentes en convocatorias que ya no usan Excel), el proyecto consulta 5 portales públicos y descarga el certificado correspondiente, clasificando automáticamente los casos que requieren revisión manual.

## Verificaciones que realiza

| Script | Entidad | Qué consulta | Frase de resultado limpio | Carpeta de salida |
|---|---|---|---|---|
| `automation_RNMC.py` | Policía Nacional | Registro Nacional de Medidas Correctivas | "NO TIENE MEDIDAS CORRECTIVAS PENDIENTES POR CUMPLIR" | `Cert_RNMC/` (o `_INHABILITADOS`) |
| `automation_Contraloria.py` | Contraloría | Antecedentes fiscales | "NO SE ENCUENTRA REPORTADO COMO RESPONSABLE FISCAL" | `Cert_CONT/` (o `_INHABILITADOS`) |
| `automation_Procuraduria.py` | Procuraduría | Antecedentes disciplinarios | "NO REGISTRA SANCIONES NI INHABILIDADES VIGENTES" | `Cert_PROC/` (o `_INHABILITADOS`) |
| `automation_Judicial.py` | Policía Nacional | Antecedentes judiciales | "NO TIENE ASUNTOS PENDIENTES" | `Cert_JUD/` (o `_INHABILITADOS`) |
| `automation_DelitosSexuales.py` | Policía Nacional | Inhabilidad por delitos sexuales | "NO REGISTRA INHABILIDAD" | `Cert_DSEX/` (o `_INHABILITADOS`) |

Las carpetas de salida se crean junto al Excel que se use como fuente de datos, con nombres de archivo cortos (`{CODIGO}_{PrimerNombre}_{documento}.pdf`) para no exceder el límite de ruta de Windows (260 caracteres), algo que sí llegó a pasar con carpetas de postulante muy largas combinadas con nombres completos. Cuando el texto del resultado no contiene la frase de "resultado limpio" de esa entidad (por ejemplo, sí registra algún asunto pendiente), el PDF se guarda en la carpeta `_INHABILITADOS` correspondiente, con un aviso sonoro, para que quede visible y se revise a mano.

> Antes de este cambio, las carpetas se llamaban `Certificados_Contraloria`, `Certificados_RNMC`, `Certificados_Procuraduria`, `Certificados_Policia` y `Certificados_Delitos_Sexuales`, con archivos nombrados por el nombre completo de la persona. Esas carpetas no se renombran ni se tocan: los scripts las siguen revisando además de las nuevas, para no volver a descargar (y gastar créditos de 2Captcha) lo que ya estaba guardado ahí con el formato viejo.

## Requisitos

- Python 3.11+
- Google Chrome instalado
- Una cuenta de [2Captcha](https://2captcha.com/) con saldo (se usa para resolver los reCAPTCHA de Contraloría, Judicial y Delitos Sexuales)
- `preparar_personas.py` usa [EasyOCR](https://github.com/JaidedAI/EasyOCR) para leer cédulas escaneadas; la primera vez que lo necesita descarga sus modelos (~100 MB). En este equipo se configuró para guardarlos en `D:\ModelosIA\EasyOCR` en vez de en `C:` (ver variable `EASYOCR_MODULE_PATH` más abajo), por el espacio limitado del disco del sistema.

## Instalación

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Crea un archivo `.env` en esta carpeta (no se sube a git) con tu clave de 2Captcha:

```
API_KEY_2CAPTCHA=tu_clave_aqui
```

Si el disco `C:` tiene poco espacio, se puede redirigir dónde EasyOCR guarda sus modelos (y dónde pip guarda su caché de descargas) a otra unidad con más espacio, con variables de entorno de usuario persistentes (PowerShell, una sola vez):

```powershell
[System.Environment]::SetEnvironmentVariable("EASYOCR_MODULE_PATH", "D:\ModelosIA\EasyOCR", "User")
[System.Environment]::SetEnvironmentVariable("PIP_CACHE_DIR", "D:\pip-cache", "User")
```

`preparar_personas.py` también configura esta variable automáticamente al arrancar si detecta que existe la unidad `D:` y la variable no está ya definida, así que este paso es opcional — solo hace falta si quieres fijarlo de forma permanente o usar otra ruta.

## El archivo de entrada

Los scripts aceptan tres formatos de origen, y detectan cuál es por la extensión del archivo:

### Excel (`.xlsx`)

Leen **todas las hojas** del Excel de anexo técnico del postulante ("ANEXO TECNICO... INFORMACION ARTISTAS..."), no solo la primera — algunas plantillas separan a las personas en varias pestañas (por ejemplo `BAILARINES` / `MUSICOS`). En cada hoja, los encabezados de la tabla deben estar en la fila 29, con estas columnas:

- `# DOC. IDENTIDAD`
- `TIPO DOCUMENTO \n(RC - TI - PP)`
- `PRIMER NOMBRE`, `SEGUNDO NOMBRE`, `PRIMER APELLIDO`, `SEGUNDO APELLIDO`
- `FECHA DE EXPEDICION (DD/MM/AA)` (algunas hojas la nombran sin el sufijo `(DD/MM/AA)`; se normaliza automáticamente al combinar)

Las hojas que no tengan esa estructura en la fila 29 se ignoran sin interrumpir la lectura de las demás. Las filas que no empiezan con un número de documento (encabezados repetidos, subtítulos, filas de plantilla sin diligenciar) se ignoran automáticamente.

Si una persona aparece más de una vez (por ejemplo, con varios roles en distintas secciones de una misma hoja), se conserva la fila más completa — la que tenga menos celdas vacías — no la primera que aparezca.

### PDF de autorización (`.pdf`)

Para convocatorias que ya no traen Excel (por ejemplo VENTANILLA ABIERTA), los scripts pueden leer directamente el PDF de "Autorización para consulta de antecedentes, registros e inhabilidades" — el mismo documento firmado por cada postulante, con una autorización por persona dentro del mismo archivo.

Cada autorización trae, en la misma frase, todo lo necesario:

> "El (la) suscrito(a) **{nombre completo}**, identificado(a) con **{tipo de documento}** No. **{número}**, expedida en {ciudad}, con fecha de expedición **{fecha}**, actuando en nombre propio..."

De ahí se extrae automáticamente: número de documento, tipo (cédula de ciudadanía → `CC`, tarjeta de identidad → `TI`, cédula de extranjería → `CE`, pasaporte → `PA`) y fecha de expedición. Como el PDF no separa "primer nombre / segundo nombre / apellidos", se toma la primera palabra del nombre completo como primer nombre (la única parte que el resto del código necesita) y el resto se conserva tal cual para el nombre completo.

Este modo no requiere una fila de encabezados ni una estructura tabular — solo que el texto del PDF sea seleccionable (no un escaneo/imagen).

### CSV ya preparado (`.csv`)

Cuando el PDF de autorización tiene errores de digitación (el caso más común: la fecha de expedición no coincide con la de la cédula real de la persona), leerlo directamente produce rechazos en los portales. Para esos casos existe `preparar_personas.py`, un script aparte que concilia dos fuentes antes de consultar nada:

```bash
python preparar_personas.py "C:\ruta\al\AUT_CONS_ANTEC.pdf" "C:\ruta\al\CC_GRUPO.pdf"
```

- El primer PDF es el de autorizaciones (igual al del modo anterior).
- El segundo (opcional) es un PDF con las copias de las cédulas de las mismas personas (frente y reverso, todas en un solo archivo).

Si una página del PDF de cédulas trae texto seleccionable, se lee directamente. Si es solo una foto o un escaneo (el caso más común), se le aplica OCR automáticamente con [EasyOCR](https://github.com/JaidedAI/EasyOCR) — que sí logra leer el texto por encima del patrón de seguridad de la cédula, algo que un OCR clásico (como Tesseract) no consigue. Para decidir a qué persona pertenece cada página, no se depende de que el OCR lea bien la etiqueta "NÚMERO": se compara cualquier número que aparezca en el texto contra los números de documento ya conocidos del PDF de autorización, lo que lo hace tolerante a errores de lectura en el resto del texto.

El script compara la fecha de expedición de cada persona entre ambas fuentes (normalizando el formato para que "1/07/2022" y "01/07/2022" se traten como la misma fecha). Si coinciden, no hay nada que hacer. Si difieren, o si no se pudo leer la copia de cédula de esa persona (ni por texto ni por OCR), se prioriza el dato de la cédula por ser la fuente más confiable, y la fila queda marcada con `REVISAR=SI` y una `MOTIVO_REVISAR` explicando por qué, para que puedas confirmarla a mano antes de correr las consultas.

El resultado se guarda como `personas_preparadas.csv` junto al PDF de autorización, y es el archivo que le pasas después a `ejecutar_revision.py` o a cualquiera de los 5 scripts — así todos consultan exactamente los mismos datos ya revisados, en vez de que cada uno vuelva a leer y a interpretar el PDF por su cuenta.

## Uso

### Opción recomendada: ejecutar todo con un solo trigger

```bash
python ejecutar_revision.py
```

Te va a pedir el archivo (Excel, PDF o el CSV ya preparado por `preparar_personas.py`, con un selector de archivos o por consola), te muestra qué verificaciones va a correr, pide confirmación y ejecuta las 5 en orden, cada una con su propia ventana de Chrome. Si una verificación termina con errores, se detiene y te pregunta si quieres seguir con la siguiente o parar ahí para revisar. Al final imprime un resumen de cuáles terminaron bien, cuáles con error y cuáles no llegaron a ejecutarse.

### Ejecutar un script individual

```bash
python automation_Judicial.py "C:\ruta\al\ANEXOTECNICO.xlsx"
python automation_Judicial.py "C:\ruta\al\AUT_CONS_ANTEC.pdf"
python automation_Judicial.py "C:\ruta\al\personas_preparadas.csv"
```

Si no le pasas la ruta como argumento, te la pregunta por consola.

## Intervención manual

Algunos portales piden validaciones adicionales que los scripts no pueden resolver solos:

- **Procuraduría** hace una pregunta de seguridad dinámica (operación matemática, datos del nombre/documento, capital de un departamento). El script intenta resolverla solo; si no reconoce el patrón, te pide la respuesta por consola.
- **Judicial** y **Delitos Sexuales** reintentan automáticamente si el portal muestra un error, y si persiste te piden corregirlo a mano en la ventana de Chrome (con un beep de aviso) antes de reintentar.

## Idempotencia, auditoría y manejo de errores

- Antes de descargar, cada script revisa si ya existe el certificado esperado (por nombre de archivo, en la carpeta normal o en `_INHABILITADOS`) y lo omite si ya está.
- Los 5 scripts auditan, al iniciar, los PDF ya descargados: si el texto no contiene la frase de "resultado limpio" de esa entidad, el archivo se mueve a `_INHABILITADOS` aunque ya estuviera descargado. Esto sirve como red de seguridad si algún certificado se guardó antes de que existiera esta verificación.
- Si un error inesperado (red, portal caído, elemento no encontrado) interrumpe la consulta de una persona puntual, el script lo registra y sigue con la siguiente en vez de detener todo el lote. Al final indica cuántas personas quedaron sin procesar y termina con un código de error para que `ejecutar_revision.py` (o tú, si corres el script suelto) se entere y puedas volver a correrlo — lo ya descargado se omite automáticamente gracias a la idempotencia.
- Si el mismo portal falla dos veces **seguidas** por algo técnico (no por un dato puntual de una persona, sino timeout, captcha no resuelto, o similar), el script asume que puede haber un bloqueo de IP o una caída temporal, corta esa verificación de inmediato y cierra el navegador, en vez de seguir gastando tiempo y créditos de 2Captcha contra un portal que no está respondiendo.
- Contraloría, Judicial y Delitos Sexuales (los tres que resuelven reCAPTCHA) ocultan las señales típicas de que Chrome está siendo controlado por Selenium, ya que algunos portales responden con error cuando las detectan aunque el captcha se haya resuelto correctamente.
- Contraloría además verifica en cada consulta el sitekey del reCAPTCHA contra el que tiene guardado, por si el portal lo cambia, y usa el vigente automáticamente.

## Privacidad y seguridad

Este repositorio solo versiona código. Las carpetas de certificados y el `personas_preparadas.csv` se crean junto al archivo que uses como fuente (fuera de esta carpeta de repositorio), así que los datos personales de los postulantes nunca quedan dentro del árbol de git. Como respaldo adicional, `.gitignore` también excluye por si acaso:

- `.env` (clave de 2Captcha)
- `Cert_CONT/`, `Cert_RNMC/`, `Cert_PROC/`, `Cert_JUD/`, `Cert_DSEX/` (y las carpetas `Certificados_*` del formato anterior)
- `*.xlsx`, `*.csv`

No subas manualmente Excels, PDFs de certificados, el `personas_preparadas.csv` ni el `.env` a este repositorio. Tampoco menciones nombres o números de documento reales en mensajes de commit.

## Próximos pasos

Se evalúa, como fase futura, una versión web para acceder a este flujo desde cualquier lugar sin depender de Chrome/Selenium local.
