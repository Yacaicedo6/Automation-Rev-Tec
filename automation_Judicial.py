import pandas as pd
import requests
import urllib3
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
import time
import os
import sys
import base64
import shutil
import re
import PyPDF2
import pymupdf
from twocaptcha import TwoCaptcha
from dotenv import load_dotenv

URL_JUDICIAL = "https://antecedentes.policia.gov.co:7005/WebJudicial/index.xhtml"
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def verificar_portal_disponible(url, timeout=8):
    """
    Comprobación rápida (sin abrir Chrome) de si el portal responde antes de
    gastar tiempo y créditos de captcha en todo el lote. No garantiza que las
    consultas individuales vayan a funcionar -el sitio puede cargar bien y
    aun así el motor de consulta del fondo devolver la pantalla vacía-, pero
    detecta el caso más común: el portal está caído, muy lento o da error de
    servidor.

    Este portal usa un certificado que Chrome solo acepta porque el script
    abre el navegador con --ignore-certificate-errors; se hace lo mismo aquí
    (verify=False) para no marcarlo como caído por un problema de certificado
    que de todas formas no impide la consulta real.
    """
    try:
        respuesta = requests.get(url, timeout=timeout, verify=False)
        return respuesta.status_code < 500
    except requests.exceptions.RequestException:
        return False

# Patrón de la frase de autorización que trae cada persona en el PDF de
# "Autorización para consulta de antecedentes" (usado cuando no hay Excel).
PATRON_AUTORIZACION_PDF = re.compile(
    r"(?:El|La)\s+(?:\((?:la|el)\)\s+)?suscrit[oa]\s*(?:\(\s*[ao]\s*\))?\s+(?P<nombre>.+?)\s*,?\s+"
    r"identificad[oa]\s*(?:\(\s*a\s*\))?\s+con\s+"
    r"(?P<tipo_doc>c[eé]dula de ciudadan[ií]a|tarjeta de identidad|c[eé]dula de extranjer[ií]a|pasaporte)\s+No\.\s+"
    r"(?P<doc>[\d.]+)\s*,?\s+expedida en\s+.+?\s*,?\s+con fecha de expedici[oó]n\s+"
    r"(?P<fecha>\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{4})",
    re.IGNORECASE,
)

# ==========================================
# 0. CONFIGURACIÓN SEGURA
# ==========================================
load_dotenv()

API_KEY_2CAPTCHA = os.getenv("API_KEY_2CAPTCHA")
# SiteKey extraído del iframe del portal
SITE_KEY_POLICIA = "6LcsIwQaAAAAAFCsaI-dkR6hgKsZwwJRsmE0tIJH"

if not API_KEY_2CAPTCHA:
    raise ValueError(
        "ERROR CRÍTICO: No se encontró la API_KEY_2CAPTCHA. "
        "Asegúrate de que el archivo .env existe y está configurado."
    )

solver = TwoCaptcha(API_KEY_2CAPTCHA)

# ==========================================
# 1. FUNCIÓN DE AUDITORÍA RETROACTIVA
# ==========================================
def auditar_descargas_anteriores(carpeta_origen, carpeta_alertas):
    """
    Lee los PDF ya descargados. Si no dicen 'NO TIENE ASUNTOS PENDIENTES',
    los mueve a la carpeta de alertas.
    """
    alertas_encontradas = []

    if not os.path.isdir(carpeta_origen):
        return alertas_encontradas

    for archivo in os.listdir(carpeta_origen):
        if archivo.endswith('.pdf'):
            ruta_pdf = os.path.join(carpeta_origen, archivo)

            try:
                with open(ruta_pdf, 'rb') as f:
                    lector = PyPDF2.PdfReader(f)
                    texto_pdf = ""
                    for pagina in lector.pages:
                        texto_pdf += pagina.extract_text() or ""

                # Normalización Pythonica: Eliminar todos los espacios y saltos de línea
                texto_limpio = "".join(texto_pdf.upper().split())

                # Frase clave normalizada
                if "NOTIENEASUNTOSPENDIENTES" not in texto_limpio:
                    os.makedirs(carpeta_alertas, exist_ok=True)
                    ruta_nueva = os.path.join(carpeta_alertas, archivo)
                    shutil.move(ruta_pdf, ruta_nueva)
                    alertas_encontradas.append(archivo.replace(".pdf", ""))

            except Exception as e:
                print(f"No se pudo auditar el archivo {archivo}: {e}")

    return alertas_encontradas

def circuito_abierto(fallos_consecutivos, nombre_entidad="Judicial de la Policía"):
    """
    Si el portal falla dos veces seguidas por algo técnico (no por un dato puntual
    de la persona), lo más probable es un bloqueo de IP o una caída temporal.
    Seguir intentando con el resto solo perdería tiempo, así que se corta aquí.
    """
    if fallos_consecutivos >= 2:
        print(f"\nSe detectaron {fallos_consecutivos} fallos técnicos consecutivos en el portal {nombre_entidad}.")
        print("Es posible que esté bloqueando las solicitudes automatizadas o esté caído en este momento.")
        print("Se detiene esta verificación para no perder más tiempo.")
        return True
    return False

def _quitar_acentos(texto):
    return texto.translate(str.maketrans("ÁÉÍÓÚáéíóúÑñ", "AEIOUaeiouNn"))

def _columna_que_contiene(columnas, *fragmentos):
    for columna in columnas:
        normalizada = _quitar_acentos(str(columna)).upper()
        if all(fragmento in normalizada for fragmento in fragmentos):
            return columna
    return None

def _leer_personas_desde_excel_simple(ruta_excel):
    """
    Lee la plantilla simple de postulantes (Código, Nombre Completo, Número
    de Identificación, Fecha de expedición y, si la trae, Tipo de
    documento), usada desde la convocatoria del Mundial de Salsa en
    adelante. Si el archivo no tiene estas columnas -por ejemplo, es la
    plantilla larga "ANEXO TÉCNICO" de antes- devuelve None para que se
    intente con leer_personas_excel_legado.
    """
    try:
        encabezados = pd.read_excel(ruta_excel, sheet_name=0, header=0, nrows=0)
    except Exception:
        return None
    encabezados.columns = encabezados.columns.str.strip()
    columnas = list(encabezados.columns)

    col_codigo = _columna_que_contiene(columnas, "CODIGO")
    col_tipo_doc = _columna_que_contiene(columnas, "TIPO")
    col_nombre = _columna_que_contiene(columnas, "NOMBRE")
    # "Tipo de identificación" y "Número de identificación" comparten la
    # palabra "identificación", así que primero se busca la combinación
    # "número" + "identificación" y solo si no aparece se cae a una
    # búsqueda más laxa, siempre excluyendo la columna ya usada como tipo.
    columnas_sin_tipo = [c for c in columnas if c != col_tipo_doc]
    col_doc = (
        _columna_que_contiene(columnas_sin_tipo, "NUMERO", "IDENTIFICACION")
        or _columna_que_contiene(columnas_sin_tipo, "IDENTIFICACION")
        or _columna_que_contiene(columnas_sin_tipo, "DOCUMENTO")
    )
    col_fecha = _columna_que_contiene(columnas, "FECHA")

    if not (col_codigo and col_nombre and col_doc and col_fecha):
        return None

    try:
        # El código y el número de documento se leen como texto para no
        # perder ceros a la izquierda (Excel los tomaría como número).
        df = pd.read_excel(ruta_excel, sheet_name=0, header=0, dtype={col_codigo: str, col_doc: str})
    except Exception:
        return None
    df.columns = df.columns.str.strip()
    df = df.dropna(subset=[col_doc])

    filas = []
    for _, fila in df.iterrows():
        nombre_completo = str(fila[col_nombre]).strip() if pd.notna(fila[col_nombre]) else ""
        tokens = nombre_completo.split()
        primer_nombre = tokens[0] if tokens else ""
        resto_nombre = " ".join(tokens[1:])

        try:
            fecha_valor = pd.to_datetime(fila[col_fecha], dayfirst=True)
        except Exception:
            fecha_valor = fila[col_fecha]

        filas.append({
            'CODIGO': str(fila[col_codigo]).strip() if pd.notna(fila[col_codigo]) else "",
            '# DOC. IDENTIDAD': str(fila[col_doc]).strip(),
            'TIPO DOCUMENTO \n(RC - TI - PP)': str(fila[col_tipo_doc]).strip() if col_tipo_doc and pd.notna(fila[col_tipo_doc]) else 'CC',
            'PRIMER NOMBRE': primer_nombre,
            'SEGUNDO NOMBRE': "",
            'PRIMER APELLIDO': resto_nombre,
            'SEGUNDO APELLIDO': "",
            'FECHA DE EXPEDICION (DD/MM/AA)': fecha_valor,
        })

    resultado = pd.DataFrame(filas)
    return resultado.drop_duplicates(subset=['# DOC. IDENTIDAD']) if not resultado.empty else resultado

def leer_personas_excel_legado(ruta_excel):
    """
    Lee todas las hojas del libro y combina las filas con un documento de
    identidad válido. Algunas plantillas separan a las personas en varias
    hojas (por ejemplo BAILARINES / MUSICOS), así que no basta con leer
    solo la primera.
    """
    libro = pd.ExcelFile(ruta_excel)
    hojas_validas = []

    for nombre_hoja in libro.sheet_names:
        try:
            df_hoja = pd.read_excel(ruta_excel, sheet_name=nombre_hoja, header=28)
            df_hoja.columns = df_hoja.columns.str.strip()

            # Algunas hojas nombran esta columna sin el sufijo "(DD/MM/AA)"; se
            # normaliza para que no queden como dos columnas distintas al combinar.
            for columna in list(df_hoja.columns):
                if columna.startswith('FECHA DE EXPEDICION') and columna != 'FECHA DE EXPEDICION (DD/MM/AA)':
                    df_hoja = df_hoja.rename(columns={columna: 'FECHA DE EXPEDICION (DD/MM/AA)'})

            if '# DOC. IDENTIDAD' not in df_hoja.columns:
                continue
            df_hoja = df_hoja.dropna(subset=['# DOC. IDENTIDAD'])
            if not df_hoja.empty:
                hojas_validas.append(df_hoja)
        except Exception:
            continue

    if not hojas_validas:
        return pd.DataFrame()

    combinado = pd.concat(hojas_validas, ignore_index=True)

    # Si la misma persona aparece varias veces (distintos roles o secciones),
    # nos quedamos con la fila más completa (menos datos vacíos), no con la
    # primera que aparezca, que puede estar incompleta.
    combinado['_completitud'] = combinado.notna().sum(axis=1)
    combinado = combinado.sort_values('_completitud', ascending=False)
    combinado = combinado.drop_duplicates(subset=['# DOC. IDENTIDAD'], keep='first')
    return combinado.drop(columns=['_completitud'])

def leer_personas(ruta_excel):
    """
    Lee el Excel de postulantes: primero intenta la plantilla simple
    (Código, Nombre Completo, Número de Identificación, Fecha de
    expedición); si no calza con esas columnas, cae a la plantilla larga
    "ANEXO TÉCNICO" de convocatorias anteriores.
    """
    df_simple = _leer_personas_desde_excel_simple(ruta_excel)
    if df_simple is not None and not df_simple.empty:
        return df_simple
    return leer_personas_excel_legado(ruta_excel)


def _pitido(frecuencia, duracion_ms):
    """
    Pitido audible para quien esté frente al equipo cuando hay una alerta.
    winsound solo existe en Windows -- el panel corre este script dentro de
    WSL (Linux), así que ahí simplemente se ignora en silencio en vez de
    tumbar el guardado de la alerta real con un ModuleNotFoundError.
    """
    try:
        import winsound
        winsound.Beep(frecuencia, duracion_ms)
    except Exception:
        pass


def _pedir_respuesta_manual(mensaje):
    """
    Pide una respuesta escrita a mano -- pero solo si hay una consola real
    detrás (por ejemplo, corriendo el script directo con doble clic). Si no
    hay una terminal interactiva (como cuando el panel web lanza este
    script como subproceso), no hay quien la escriba: en vez de colgarse
    esperando una entrada que nunca llega, se devuelve None de una vez para
    que la persona quede marcada como fallida y se pueda reintentar luego.
    """
    if not sys.stdin.isatty():
        return None
    return input(mensaje)


def _guardar_reporte_fallidos(df, documentos_fallidos, directorio_base, codigo_entidad):
    """
    Junta a quienes NO se pudieron consultar de verdad (dato rechazado por
    el portal, tiempo de espera agotado, error inesperado) y los deja en un
    Excel aparte (Fallidos_<ENTIDAD>.xlsx), para que sea fácil ver a quién
    hay que volver a intentarle sin mezclarlos con la gente que sí tuvo una
    alerta real.
    """
    ruta_fallidos = os.path.join(directorio_base, f"Fallidos_{codigo_entidad}.xlsx")

    if not documentos_fallidos:
        if os.path.exists(ruta_fallidos):
            os.remove(ruta_fallidos)
        return

    filas_fallidos = []
    for _, fila_persona in df.iterrows():
        doc_persona = str(fila_persona['# DOC. IDENTIDAD']).strip()
        if doc_persona.endswith('.0'):
            doc_persona = doc_persona[:-2]
        if doc_persona not in documentos_fallidos:
            continue

        p_nombre_f = str(fila_persona['PRIMER NOMBRE']) if pd.notna(fila_persona['PRIMER NOMBRE']) else ""
        s_nombre_f = str(fila_persona['SEGUNDO NOMBRE']) if pd.notna(fila_persona['SEGUNDO NOMBRE']) else ""
        p_apellido_f = str(fila_persona['PRIMER APELLIDO']) if pd.notna(fila_persona['PRIMER APELLIDO']) else ""
        s_apellido_f = str(fila_persona['SEGUNDO APELLIDO']) if pd.notna(fila_persona['SEGUNDO APELLIDO']) else ""
        nombre_completo_f = f"{p_nombre_f} {s_nombre_f} {p_apellido_f} {s_apellido_f}".replace("  ", " ").strip()

        filas_fallidos.append({
            "CODIGO": fila_persona.get('CODIGO', '') if 'CODIGO' in df.columns else "",
            "NOMBRE": nombre_completo_f,
            "DOCUMENTO": doc_persona,
            "MOTIVO": documentos_fallidos[doc_persona],
        })

    try:
        pd.DataFrame(filas_fallidos).to_excel(ruta_fallidos, index=False)
        print(f"\nSe guardó el listado de personas con consulta fallida en:\n{ruta_fallidos}")
    except Exception as error_reporte:
        print(f"Aviso: no se pudo generar el Excel de fallidos: {error_reporte}")

def _guardar_reporte_inhabilitados(df, documentos_inhabilitados, directorio_base, codigo_entidad):
    """
    Junta a TODAS las personas con alerta real guardada en la carpeta de
    inhabilitados -de esta corrida o de corridas anteriores- en un Excel
    aparte (Inhabilitados_<ENTIDAD>.xlsx). El motivo de esta corrida ya
    viene capturado en vivo (documentos_inhabilitados); el de corridas
    anteriores se lee directo del PDF ya guardado, para no dejar por fuera
    a quien ya se había consultado antes de que existiera este reporte.
    """
    carpeta_inhabilitados_dir = os.path.join(directorio_base, f"Cert_{codigo_entidad}_INHABILITADOS")
    ruta_inhabilitados = os.path.join(directorio_base, f"Inhabilitados_{codigo_entidad}.xlsx")

    motivos_por_doc = dict(documentos_inhabilitados)

    if os.path.isdir(carpeta_inhabilitados_dir):
        for nombre_archivo in os.listdir(carpeta_inhabilitados_dir):
            if not nombre_archivo.endswith('.pdf'):
                continue
            doc_archivo = os.path.splitext(nombre_archivo)[0].rsplit('_', 1)[-1]
            if doc_archivo in motivos_por_doc:
                continue  # ya se tiene el motivo capturado en vivo de esta corrida
            ruta_pdf_existente = os.path.join(carpeta_inhabilitados_dir, nombre_archivo)
            try:
                with open(ruta_pdf_existente, 'rb') as f:
                    lector = PyPDF2.PdfReader(f)
                    texto_pdf_existente = ""
                    for pagina in lector.pages:
                        texto_pdf_existente += pagina.extract_text() or ""
                motivos_por_doc[doc_archivo] = " ".join(texto_pdf_existente.split())
            except Exception as error_lectura:
                print(f"Aviso: no se pudo leer {nombre_archivo} para el reporte de inhabilitados: {error_lectura}")

    if not motivos_por_doc:
        if os.path.exists(ruta_inhabilitados):
            os.remove(ruta_inhabilitados)
        return

    filas_inhabilitados = []
    for _, fila_persona in df.iterrows():
        doc_persona = str(fila_persona['# DOC. IDENTIDAD']).strip()
        if doc_persona.endswith('.0'):
            doc_persona = doc_persona[:-2]
        if doc_persona not in motivos_por_doc:
            continue

        p_nombre_i = str(fila_persona['PRIMER NOMBRE']) if pd.notna(fila_persona['PRIMER NOMBRE']) else ""
        s_nombre_i = str(fila_persona['SEGUNDO NOMBRE']) if pd.notna(fila_persona['SEGUNDO NOMBRE']) else ""
        p_apellido_i = str(fila_persona['PRIMER APELLIDO']) if pd.notna(fila_persona['PRIMER APELLIDO']) else ""
        s_apellido_i = str(fila_persona['SEGUNDO APELLIDO']) if pd.notna(fila_persona['SEGUNDO APELLIDO']) else ""
        nombre_completo_i = f"{p_nombre_i} {s_nombre_i} {p_apellido_i} {s_apellido_i}".replace("  ", " ").strip()
        tipo_doc_i = str(fila_persona['TIPO DOCUMENTO \n(RC - TI - PP)']).strip() if pd.notna(fila_persona['TIPO DOCUMENTO \n(RC - TI - PP)']) else ""

        filas_inhabilitados.append({
            "CODIGO": fila_persona.get('CODIGO', '') if 'CODIGO' in df.columns else "",
            "NOMBRE": nombre_completo_i,
            "TIPO_DOCUMENTO": tipo_doc_i,
            "DOCUMENTO": doc_persona,
            "MOTIVO": motivos_por_doc[doc_persona],
        })

    try:
        pd.DataFrame(filas_inhabilitados).to_excel(ruta_inhabilitados, index=False)
        print(f"\nSe guardó el listado de personas con alerta real en:\n{ruta_inhabilitados}")
    except Exception as error_reporte:
        print(f"Aviso: no se pudo generar el Excel de inhabilitados: {error_reporte}")

def leer_personas_desde_pdf(ruta_pdf):
    """
    Extrae a las personas directamente del PDF de "Autorización para consulta
    de antecedentes" (una autorización por persona, dentro del mismo archivo),
    para las convocatorias que ya no traen un Excel de postulantes.
    """
    documento = pymupdf.open(ruta_pdf)
    texto_completo = ""
    for pagina in documento:
        texto_completo += pagina.get_text() + " "

    texto_normalizado = " ".join(texto_completo.replace("_", "").replace(chr(0x200b), " ").split())

    filas = []
    for coincidencia in PATRON_AUTORIZACION_PDF.finditer(texto_normalizado):
        nombre_completo = " ".join(coincidencia.group("nombre").split())
        tokens = nombre_completo.split()
        primer_nombre = tokens[0] if tokens else ""
        resto_nombre = " ".join(tokens[1:])

        tipo_doc_texto = coincidencia.group("tipo_doc").lower()
        if "extranjer" in tipo_doc_texto:
            tipo_doc = "CE"
        elif "tarjeta" in tipo_doc_texto:
            tipo_doc = "TI"
        elif "pasaporte" in tipo_doc_texto:
            tipo_doc = "PA"
        else:
            tipo_doc = "CC"

        filas.append({
            '# DOC. IDENTIDAD': re.sub(r"\D", "", coincidencia.group("doc")),
            'TIPO DOCUMENTO \n(RC - TI - PP)': tipo_doc,
            'PRIMER NOMBRE': primer_nombre,
            'SEGUNDO NOMBRE': "",
            'PRIMER APELLIDO': resto_nombre,
            'SEGUNDO APELLIDO': "",
            # dayfirst=True porque el PDF trae las fechas en formato DD/MM/AAAA
            'FECHA DE EXPEDICION (DD/MM/AA)': pd.to_datetime(re.sub(r"\s+", "", coincidencia.group("fecha")), dayfirst=True),
        })

    df = pd.DataFrame(filas)
    return df.drop_duplicates(subset=['# DOC. IDENTIDAD']) if not df.empty else df

def leer_personas_desde_csv(ruta_csv):
    """
    Lee el CSV que genera preparar_personas.py (columnas DOC, TIPO_DOC,
    PRIMER_NOMBRE, SEGUNDO_NOMBRE, PRIMER_APELLIDO, SEGUNDO_APELLIDO,
    FECHA_EXPEDICION) y lo traduce a los nombres de columna que usa el resto
    del script.
    """
    df = pd.read_csv(ruta_csv, dtype={'DOC': str})
    return pd.DataFrame({
        '# DOC. IDENTIDAD': df['DOC'],
        'TIPO DOCUMENTO \n(RC - TI - PP)': df['TIPO_DOC'],
        'PRIMER NOMBRE': df['PRIMER_NOMBRE'],
        'SEGUNDO NOMBRE': df.get('SEGUNDO_NOMBRE', ""),
        'PRIMER APELLIDO': df['PRIMER_APELLIDO'],
        'SEGUNDO APELLIDO': df.get('SEGUNDO_APELLIDO', ""),
        'FECHA DE EXPEDICION (DD/MM/AA)': pd.to_datetime(df['FECHA_EXPEDICION'], dayfirst=True),
    })

# ==========================================
# 2. RUTAS Y LECTURA DE DATOS
# ==========================================
def obtener_ruta_datos():
    if len(sys.argv) > 1:
        return sys.argv[1]
    return input("Ruta del archivo (Excel, PDF o CSV ya preparado) con la información de los postulantes: ").strip('"').strip()

ruta_datos = obtener_ruta_datos()
if not os.path.isfile(ruta_datos):
    raise FileNotFoundError(f"No se encontró el archivo: {ruta_datos}")

directorio_base = os.path.normpath(os.path.dirname(ruta_datos))
carpeta_destino = os.path.join(directorio_base, "Cert_JUD")
carpeta_inhabilitados = os.path.join(directorio_base, "Cert_JUD_INHABILITADOS")
# Resultados donde el portal no devolvió nada (probable caída/lentitud del
# servicio): se guardan aparte de las alertas reales, y a propósito no se
# revisan en el "ya existe, se omite" de más abajo, para que la próxima
# corrida los vuelva a intentar automáticamente en vez de darlos por hechos.
carpeta_inconclusos = os.path.join(directorio_base, "Cert_JUD_INCONCLUSOS")

# Carpetas con el nombre largo que usaban las corridas anteriores a este cambio.
# Se siguen revisando para no volver a descargar (y gastar créditos de 2Captcha)
# lo que ya quedó guardado ahí.
carpeta_destino_vieja = os.path.join(directorio_base, "Certificados_Policia")
carpeta_inhabilitados_vieja = os.path.join(directorio_base, "Certificados_Policia_INHABILITADOS")

# Las 3 carpetas se crean solo cuando de verdad hay algo que guardar en cada
# una (más abajo, justo antes de escribir el PDF), para no dejar carpetas
# vacías si nadie tuvo alertas o inconclusos en esta tanda.

# Ejecutar auditoría antes de iniciar la automatización
print("Auditando certificados previamente descargados...")
alertas_historicas = auditar_descargas_anteriores(carpeta_destino, carpeta_inhabilitados)
if alertas_historicas:
    print(f"Se movieron {len(alertas_historicas)} certificados sospechosos a la carpeta de INHABILITADOS.")

print("\nLeyendo el archivo de postulantes...")
if ruta_datos.lower().endswith('.csv'):
    df = leer_personas_desde_csv(ruta_datos)
elif ruta_datos.lower().endswith('.pdf'):
    df = leer_personas_desde_pdf(ruta_datos)
else:
    df = leer_personas(ruta_datos)
if df.empty:
    print("No se encontró ninguna persona con documento válido en el archivo.")

lista_alertas_finales = alertas_historicas.copy()
lista_inconclusos = []
# Documento -> motivo, para el Excel de personas a las que falló la
# consulta (distinto de una alerta real: aquí no se logró determinar nada).
documentos_fallidos = {}
# Documento -> texto del portal, para las personas con un asunto judicial
# real pendiente (distinto de "inconcluso": aquí sí se confirmó un resultado).
documentos_inhabilitados = {}

print("Comprobando disponibilidad del portal Judicial de la Policía...")
if not verificar_portal_disponible(URL_JUDICIAL):
    print("Atención: el portal Judicial no respondió bien en esta comprobación (puede estar caído, muy lento o en mantenimiento).")
    respuesta_continuar = _pedir_respuesta_manual("¿Continuar de todas formas? (s/n): ")
    if respuesta_continuar is None or respuesta_continuar.strip().lower() != "s":
        print("Ejecución cancelada: el portal no respondió y no hay confirmación para continuar de todas formas.")
        sys.exit(1)
else:
    print("El portal Judicial respondió correctamente.")

# ==========================================
# 3. CONFIGURAR NAVEGADOR
# ==========================================
opciones = webdriver.ChromeOptions()
opciones.add_argument("--ignore-certificate-errors")

# Ocultar las señales típicas de que Chrome está siendo controlado por Selenium,
# ya que algunos portales con reCAPTCHA responden con error cuando las detectan.
opciones.add_argument("--disable-blink-features=AutomationControlled")
opciones.add_experimental_option("excludeSwitches", ["enable-automation"])
opciones.add_experimental_option("useAutomationExtension", False)

driver = webdriver.Chrome(options=opciones)
driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
    "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
})
wait = WebDriverWait(driver, 15)

errores_no_manejados = 0
fallos_consecutivos = 0
total_personas = len(df)

try:
    for contador_persona, (index, row) in enumerate(df.iterrows(), start=1):
        num_doc = str(row['# DOC. IDENTIDAD']).strip()

        if not num_doc[0].isdigit():
            continue
        if num_doc.endswith('.0'):
            num_doc = num_doc[:-2]

        tipo_doc_crudo = str(row['TIPO DOCUMENTO \n(RC - TI - PP)']).strip()

        p_nombre = str(row['PRIMER NOMBRE']) if pd.notna(row['PRIMER NOMBRE']) else ""
        s_nombre = str(row['SEGUNDO NOMBRE']) if pd.notna(row['SEGUNDO NOMBRE']) else ""
        p_apellido = str(row['PRIMER APELLIDO']) if pd.notna(row['PRIMER APELLIDO']) else ""
        s_apellido = str(row['SEGUNDO APELLIDO']) if pd.notna(row['SEGUNDO APELLIDO']) else ""
        nombre_completo = f"{p_nombre} {s_nombre} {p_apellido} {s_apellido}".replace("  ", " ").strip()

        print(f"\n[{contador_persona}/{total_personas}] {nombre_completo} ({num_doc})")

        # ==========================================
        # 4. VALIDACIÓN PREVIA (LOOK BEFORE YOU LEAP)
        # ==========================================
        # Nombre y carpeta cortos para no exceder el límite de ruta de Windows.
        # También se reconocen la carpeta y el nombre largos de antes de este
        # cambio, para no volver a descargar lo que ya se había guardado ahí.
        primer_nombre_archivo = "".join(c for c in p_nombre.strip() if c.isalnum()) or "SN"
        codigo_val = row.get('CODIGO') if 'CODIGO' in df.columns else None
        prefijo_archivo = "".join(c for c in str(codigo_val).strip() if c.isalnum()) if pd.notna(codigo_val) and str(codigo_val).strip() else "JUD"
        nombre_archivo_esperado = f"{prefijo_archivo}_{primer_nombre_archivo}_{num_doc}.pdf"
        ruta_esperada_normal = os.path.join(carpeta_destino, nombre_archivo_esperado)
        ruta_esperada_inhab = os.path.join(carpeta_inhabilitados, nombre_archivo_esperado)
        ruta_esperada_inconcluso = os.path.join(carpeta_inconclusos, nombre_archivo_esperado)

        nombre_limpio = "".join(c for c in nombre_completo if c.isalnum() or c in " -_").strip()
        nombre_archivo_viejo = f"Policia-{nombre_limpio}.pdf"
        ruta_vieja_normal = os.path.join(carpeta_destino_vieja, nombre_archivo_viejo)
        ruta_vieja_inhab = os.path.join(carpeta_inhabilitados_vieja, nombre_archivo_viejo)

        if any(os.path.exists(r) for r in (ruta_esperada_normal, ruta_esperada_inhab, ruta_vieja_normal, ruta_vieja_inhab)):
            print("El certificado ya existe en los registros. Se omite la descarga...")
            continue

        try:
            # ==========================================
            # 5. NAVEGACIÓN Y TÉRMINOS DE USO CONDICIONALES
            # ==========================================
            driver.get("https://antecedentes.policia.gov.co:7005/WebJudicial/index.xhtml")

            try:
                # Esperamos máximo 3 segundos a ver si aparece el botón de "Acepto"
                radio_acepto = WebDriverWait(driver, 3).until(EC.presence_of_element_located((By.ID, "aceptaOption:0")))
                driver.execute_script("arguments[0].click();", radio_acepto)
                time.sleep(1)

                # Buscar el botón Enviar y hacer clic
                btn_enviar = driver.find_element(By.XPATH, "//span[text()='Enviar']/parent::button | //button[contains(., 'Enviar')]")
                driver.execute_script("arguments[0].click();", btn_enviar)
                print("Términos de uso aceptados.")
                time.sleep(3)  # Pausa para que cargue el formulario principal
            except Exception:
                pass  # Si no aparece el botón en 3 segundos, ya estamos en el formulario

            # ==========================================
            # 6. LLENAR FORMULARIO
            # ==========================================
            elemento_tipo_doc = wait.until(EC.presence_of_element_located((By.ID, "cedulaTipo")))
            selector_tipo_doc = Select(elemento_tipo_doc)

            if "CC" in tipo_doc_crudo.upper() or "CIUDADAN" in tipo_doc_crudo.upper():
                selector_tipo_doc.select_by_value("cc")
            elif "CX" in tipo_doc_crudo.upper() or "EXTRANJER" in tipo_doc_crudo.upper() or "CE" in tipo_doc_crudo.upper():
                selector_tipo_doc.select_by_value("cx")
            elif "PA" in tipo_doc_crudo.upper() or "PASAPORTE" in tipo_doc_crudo.upper():
                selector_tipo_doc.select_by_value("pa")
            else:
                selector_tipo_doc.select_by_value("cc")

            # Limpiar el campo antes de escribir para evitar concatenaciones
            campo_documento = driver.find_element(By.ID, "cedulaInput")
            campo_documento.clear()
            campo_documento.send_keys(num_doc)

            # ==========================================
            # 7. RESOLVER RECAPTCHA
            # ==========================================
            intentos = 0
            max_intentos = 3
            captcha_resuelto = False

            while intentos < max_intentos and not captcha_resuelto:
                try:
                    print(f"Enviando reCAPTCHA a 2Captcha (intento {intentos + 1}/{max_intentos})...")
                    resultado = solver.recaptcha(sitekey=SITE_KEY_POLICIA, url=driver.current_url)
                    codigo_token = resultado['code']
                    print("reCAPTCHA resuelto correctamente.")

                    driver.execute_script(f"document.getElementById('g-recaptcha-response').innerHTML = '{codigo_token}';")
                    # Obligar al JavaScript a registrar el cambio
                    driver.execute_script("document.getElementById('g-recaptcha-response').dispatchEvent(new Event('change'));")

                    time.sleep(1)
                    captcha_resuelto = True

                except Exception as e:
                    intentos += 1
                    print(f"Error de red o conexión con la API: {e}")
                    if intentos < max_intentos:
                        time.sleep(5)

            if not captcha_resuelto:
                print(f"No fue posible resolver el captcha tras {max_intentos} intentos. Se salta a la siguiente persona...")
                errores_no_manejados += 1
                fallos_consecutivos += 1
                if circuito_abierto(fallos_consecutivos):
                    break
                continue

            # ==========================================
            # 8. BUCLE DE VALIDACIÓN INTERACTIVA
            # ==========================================
            exito_generacion = False
            intentos_validacion = 0

            # Estado de la pantalla justo antes de consultar, para poder saber
            # después si el portal de verdad cambió algo o se quedó en blanco.
            texto_antes_consulta = driver.find_element(By.TAG_NAME, "body").text

            while intentos_validacion < 2 and not exito_generacion:
                print("Enviando formulario...")

                # Buscar el botón de consultar (normalmente es un botón de PrimeFaces)
                btn_consultar = driver.find_element(By.XPATH, "//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'consultar')]")
                driver.execute_script("arguments[0].click();", btn_consultar)
                time.sleep(4)

                errores_visibles = False
                try:
                    alertas = driver.find_elements(By.XPATH, "//*[contains(@class, 'ui-messages-error') or contains(@class, 'ui-message-error')]")
                    for error in alertas:
                        texto_error = error.text.strip()
                        if error.is_displayed() and len(texto_error) > 2:
                            print(f"Alerta del portal detectada: {texto_error}")
                            errores_visibles = True
                            break
                except Exception:
                    pass

                if errores_visibles:
                    _pitido(1000, 500)
                    accion = _pedir_respuesta_manual("Intento fallido. Revisa Chrome, corrige el error y presiona Enter para reintentar (o escribe 'saltar'): ")

                    if accion is None or accion.lower() == 'saltar':
                        break
                    intentos_validacion += 1
                else:
                    exito_generacion = True

            if not exito_generacion:
                documentos_fallidos[num_doc] = "No se pudo superar la validación del portal"
                fallos_consecutivos = 0  # hubo intervención humana; el portal está respondiendo
                continue

            # ==========================================
            # 9. ANÁLISIS DE RESULTADOS Y GENERACIÓN VÍA CDP
            # ==========================================
            print("Analizando pantalla de resultados...")

            try:
                # El resultado se carga por AJAX y el portal tarda un tiempo
                # variable en renderizarlo: una pausa fija a veces no alcanzaba
                # y se capturaba la página todavía en blanco. La primera
                # versión de esta espera daba la página por "estable" con que
                # UNA sola pareja de lecturas seguidas se viera igual -- eso
                # bastaba para que una página vacía que nunca cargó nada se
                # confundiera con un resultado real ya asentado. Ahora hacen
                # falta 3 lecturas seguidas idénticas (no 1) para darla por
                # estable, y con más margen total (hasta 25s).
                texto_pantalla = ""
                texto_anterior = None
                lecturas_iguales_seguidas = 0
                for _ in range(25):
                    texto_pantalla = driver.find_element(By.TAG_NAME, "body").text
                    if "NO TIENE ASUNTOS PENDIENTES" in texto_pantalla.upper():
                        break
                    if texto_pantalla == texto_anterior:
                        lecturas_iguales_seguidas += 1
                        if lecturas_iguales_seguidas >= 3:
                            break
                    else:
                        lecturas_iguales_seguidas = 0
                    texto_anterior = texto_pantalla
                    time.sleep(1)

                if "NO TIENE ASUNTOS PENDIENTES" in texto_pantalla.upper():
                    ruta_final_guardado = ruta_esperada_normal
                    print("Resultados limpios. Se guarda en la carpeta estándar...")
                elif "INFORMA:" in texto_pantalla.upper():
                    # El portal sí alcanzó a mostrar un resultado real (no es
                    # la pantalla a medio cargar, que nunca llega a incluir
                    # "informa:") y ese resultado no es la frase de "limpio"
                    # conocida: es un asunto judicial real que requiere
                    # revisión manual, igual que RNMC con su "INFORMA:".
                    ruta_final_guardado = ruta_esperada_inhab
                    print("Atención: se detectó un posible asunto judicial pendiente. Se guarda en la carpeta de alertas...")
                    lista_alertas_finales.append(nombre_archivo_esperado.replace(".pdf", ""))
                    documentos_inhabilitados[num_doc] = " ".join(texto_pantalla.split())
                    _pitido(2000, 1000)
                else:
                    # Ni la frase de limpio ni "informa:" aparecieron: la
                    # página se quedó a medio cargar (p. ej. solo el pie de
                    # página, sin resultado real) en vez de mostrar algo
                    # concreto. Es más seguro pedir reintento que asumir
                    # cualquiera de las dos cosas sin evidencia real detrás.
                    ruta_final_guardado = ruta_esperada_inconcluso
                    print("Atención: no se pudo confirmar el resultado (el portal no devolvió el mensaje esperado). Se guarda para reintentar más tarde, sin marcarlo como alerta...")
                    lista_inconclusos.append(nombre_archivo_esperado.replace(".pdf", ""))
                    documentos_fallidos[num_doc] = "No se pudo confirmar el resultado (posible caída o lentitud del portal)"

                # Generar el PDF
                pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                    "printBackground": True,
                    "landscape": False,
                    "preferCSSPageSize": True
                })

                os.makedirs(os.path.dirname(ruta_final_guardado), exist_ok=True)
                with open(ruta_final_guardado, "wb") as f:
                    f.write(base64.b64decode(pdf_data['data']))

                print("Documento guardado correctamente.")
                fallos_consecutivos = 0

            except Exception as e:
                print(f"Error inesperado al analizar o generar el PDF: {e}")
                documentos_fallidos[num_doc] = f"Error inesperado: {e}"
                errores_no_manejados += 1
                fallos_consecutivos += 1
                if circuito_abierto(fallos_consecutivos):
                    break

        except Exception as e:
            print(f"Error inesperado procesando a {nombre_completo} ({num_doc}): {e}")
            print("Se salta a la siguiente persona...")
            documentos_fallidos[num_doc] = f"Error inesperado: {e}"
            errores_no_manejados += 1
            fallos_consecutivos += 1
            if circuito_abierto(fallos_consecutivos):
                break
            continue

except Exception as e:
    print(f"\nOcurrió un error inesperado durante el ciclo general: {e}")
    sys.exit(1)

finally:
    print("\n" + "="*50)
    print("RESUMEN DE EJECUCIÓN Y ALERTAS")
    print("="*50)

    if lista_alertas_finales:
        print(f"Se detectaron {len(lista_alertas_finales)} registros con asuntos pendientes:")
        for alerta in lista_alertas_finales:
            print(f"   - {alerta}")
        print(f"\nRevisa manualmente los documentos en la carpeta:\n{carpeta_inhabilitados}")
    else:
        print("No se encontraron registros con asuntos pendientes en esta tanda.")

    if lista_inconclusos:
        print(f"\nAdemás, {len(lista_inconclusos)} consulta(s) no obtuvieron respuesta del portal (posible caída o lentitud, no son alertas reales):")
        for inconcluso in lista_inconclusos:
            print(f"   - {inconcluso}")
        print(f"Quedaron en:\n{carpeta_inconclusos}\nVuelve a correr el script más tarde para reintentarlas.")

    _guardar_reporte_fallidos(df, documentos_fallidos, directorio_base, "JUD")
    _guardar_reporte_inhabilitados(df, documentos_inhabilitados, directorio_base, "JUD")

    print("="*50)
    print("Cerrando navegador...")
    driver.quit()

if errores_no_manejados:
    print(f"\n{errores_no_manejados} persona(s) no se pudieron procesar por errores inesperados. Vuelve a correr este script para reintentarlas (los ya descargados se omiten automáticamente).")
    sys.exit(1)
