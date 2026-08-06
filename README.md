# Automation-Rev-Tec

Automatiza la descarga de certificados de antecedentes para la revisión técnico-administrativa de convocatorias de estímulos de la Alcaldía de Cali (actualmente en uso para el Ecosistema Musical y Dancístico del Festival Mundial de Salsa 2026).

Por cada persona listada en el Excel de un postulante (grupo conformado o representante de persona jurídica), el proyecto consulta 5 portales públicos y descarga el certificado correspondiente, clasificando automáticamente los casos que requieren revisión manual.

## Verificaciones que realiza

| Script | Entidad | Qué consulta | Carpeta de salida |
|---|---|---|---|
| `automation_RNMC.py` | Policía Nacional | Registro Nacional de Medidas Correctivas | `Certificados_RNMC/` |
| `automation_Contraloria.py` | Contraloría | Antecedentes fiscales | `Certificados_Contraloria/` |
| `automation_Procuraduria.py` | Procuraduría | Antecedentes disciplinarios | `Certificados_Procuraduria/` |
| `automation_Judicial.py` | Policía Nacional | Antecedentes judiciales | `Certificados_Policia/` (o `_INHABILITADOS`) |
| `automation_DelitosSexuales.py` | Policía Nacional | Inhabilidad por delitos sexuales | `Certificados_Delitos_Sexuales/` (o `_INHABILITADOS`) |

Las carpetas de salida se crean junto al Excel que se use como fuente de datos. Cuando un resultado no es "limpio" (por ejemplo, sí registra algún asunto pendiente), el PDF se guarda en la carpeta `_INHABILITADOS` correspondiente para que quede visible y se revise a mano.

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

Te va a pedir el Excel (con un selector de archivos o por consola), te muestra qué verificaciones va a correr, pide confirmación y ejecuta las 5 en orden, cada una con su propia ventana de Chrome. Al final imprime un resumen de cuáles terminaron bien y cuáles con error.

### Ejecutar un script individual

```bash
python automation_Judicial.py "C:\ruta\al\ANEXOTECNICO.xlsx"
```

Si no le pasas la ruta como argumento, te la pregunta por consola.

## Intervención manual

Algunos portales piden validaciones adicionales que los scripts no pueden resolver solos:

- **Procuraduría** hace una pregunta de seguridad dinámica (operación matemática, datos del nombre/documento, capital de un departamento). El script intenta resolverla solo; si no reconoce el patrón, te pide la respuesta por consola.
- **Judicial** y **Delitos Sexuales** reintentan automáticamente si el portal muestra un error, y si persiste te piden corregirlo a mano en la ventana de Chrome (con un beep de aviso) antes de reintentar.

## Idempotencia y auditoría

- Antes de descargar, cada script revisa si ya existe el certificado esperado (por nombre de archivo) y lo omite si ya está.
- `automation_Judicial.py` y `automation_DelitosSexuales.py` además auditan, al iniciar, los PDF ya descargados: si el texto no contiene la frase de "sin novedades" esperada, el archivo se mueve a la carpeta `_INHABILITADOS` aunque ya estuviera descargado.

## Privacidad y seguridad

Este repositorio solo versiona código. Las carpetas de certificados se crean junto al Excel que uses como fuente (fuera de esta carpeta de repositorio), así que los datos personales de los postulantes nunca quedan dentro del árbol de git. Como respaldo adicional, `.gitignore` también excluye por si acaso:

- `.env` (clave de 2Captcha)
- `Certificados_Contraloria/`, `Certificados_RNMC/`, `Certificados_Procuraduria/`, `Certificados_Policia/`, `Certificados_Delitos_Sexuales/`
- `*.xlsx`, `*.csv`

No subas manualmente Excels, PDFs de certificados ni el `.env` a este repositorio.

## Próximos pasos

Se evalúa, como fase futura, una versión web para acceder a este flujo desde cualquier lugar sin depender de Chrome/Selenium local.
