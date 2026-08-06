# Automation-Rev-Tec

Automatiza la descarga de certificados de antecedentes para la revisión técnico-administrativa de convocatorias de estímulos de la Alcaldía de Cali (actualmente en uso para el Ecosistema Musical y Dancístico del Festival Mundial de Salsa 2026).

Por cada persona listada en el Excel de un postulante (grupo conformado o representante de persona jurídica), el proyecto consulta 5 portales públicos y descarga el certificado correspondiente, clasificando automáticamente los casos que requieren revisión manual.

## Verificaciones que realiza

| Script | Entidad | Qué consulta | Frase de resultado limpio | Carpeta de salida |
|---|---|---|---|---|
| `automation_RNMC.py` | Policía Nacional | Registro Nacional de Medidas Correctivas | "NO TIENE MEDIDAS CORRECTIVAS PENDIENTES POR CUMPLIR" | `Certificados_RNMC/` (o `_INHABILITADOS`) |
| `automation_Contraloria.py` | Contraloría | Antecedentes fiscales | "NO SE ENCUENTRA REPORTADO COMO RESPONSABLE FISCAL" | `Certificados_Contraloria/` (o `_INHABILITADOS`) |
| `automation_Procuraduria.py` | Procuraduría | Antecedentes disciplinarios | "NO REGISTRA SANCIONES NI INHABILIDADES VIGENTES" | `Certificados_Procuraduria/` (o `_INHABILITADOS`) |
| `automation_Judicial.py` | Policía Nacional | Antecedentes judiciales | "NO TIENE ASUNTOS PENDIENTES" | `Certificados_Policia/` (o `_INHABILITADOS`) |
| `automation_DelitosSexuales.py` | Policía Nacional | Inhabilidad por delitos sexuales | "NO REGISTRA INHABILIDAD" | `Certificados_Delitos_Sexuales/` (o `_INHABILITADOS`) |

Las carpetas de salida se crean junto al Excel que se use como fuente de datos. Cuando el texto del resultado no contiene la frase de "resultado limpio" de esa entidad (por ejemplo, sí registra algún asunto pendiente), el PDF se guarda en la carpeta `_INHABILITADOS` correspondiente, con un aviso sonoro, para que quede visible y se revise a mano.

## Requisitos

- Python 3.11+
- Google Chrome instalado
- Una cuenta de [2Captcha](https://2captcha.com/) con saldo (se usa para resolver los reCAPTCHA de Contraloría, Judicial y Delitos Sexuales)

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

## El Excel de entrada

Los scripts leen la hoja 1 del Excel de anexo técnico del postulante ("ANEXO TECNICO... INFORMACION ARTISTAS..."), con los encabezados en la fila 29. Deben existir estas columnas:

- `# DOC. IDENTIDAD`
- `TIPO DOCUMENTO \n(RC - TI - PP)`
- `PRIMER NOMBRE`, `SEGUNDO NOMBRE`, `PRIMER APELLIDO`, `SEGUNDO APELLIDO`
- `FECHA DE EXPEDICION (DD/MM/AA)`

Las filas que no empiezan con un número de documento (encabezados, subtítulos) se ignoran automáticamente.

## Uso

### Opción recomendada: ejecutar todo con un solo trigger

```bash
python ejecutar_revision.py
```

Te va a pedir el Excel (con un selector de archivos o por consola), te muestra qué verificaciones va a correr, pide confirmación y ejecuta las 5 en orden, cada una con su propia ventana de Chrome. Si una verificación termina con errores, se detiene y te pregunta si quieres seguir con la siguiente o parar ahí para revisar. Al final imprime un resumen de cuáles terminaron bien, cuáles con error y cuáles no llegaron a ejecutarse.

### Ejecutar un script individual

```bash
python automation_Judicial.py "C:\ruta\al\ANEXOTECNICO.xlsx"
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

## Privacidad y seguridad

Este repositorio solo versiona código. Las carpetas de certificados se crean junto al Excel que uses como fuente (fuera de esta carpeta de repositorio), así que los datos personales de los postulantes nunca quedan dentro del árbol de git. Como respaldo adicional, `.gitignore` también excluye por si acaso:

- `.env` (clave de 2Captcha)
- `Certificados_Contraloria/`, `Certificados_RNMC/`, `Certificados_Procuraduria/`, `Certificados_Policia/`, `Certificados_Delitos_Sexuales/`
- `*.xlsx`, `*.csv`

No subas manualmente Excels, PDFs de certificados ni el `.env` a este repositorio.

## Próximos pasos

Se evalúa, como fase futura, una versión web para acceder a este flujo desde cualquier lugar sin depender de Chrome/Selenium local.
