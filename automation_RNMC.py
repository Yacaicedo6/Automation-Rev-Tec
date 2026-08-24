import pandas as pd
import requests
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
import time
import base64
import os
import sys
import shutil
import re
import PyPDF2
import pymupdf

FRASE_LIMPIA = "NO TIENE MEDIDAS CORRECTIVAS PENDIENTES POR CUMPLIR"
URL_RNMC = "https://srvcnpc.policia.gov.co/PSC/frm_cnp_consulta.aspx"


def verificar_portal_disponible(url, timeout=8):
    """
    Comprobación rápida (sin abrir Chrome) de si el portal responde antes de
    gastar tiempo y créditos de captcha en todo el lote. No garantiza que las
    consultas individuales vayan a funcionar -el sitio puede cargar bien y
    aun así el motor de consulta del fondo devolver la pantalla vacía-, pero
    detecta el caso más común: el portal está caído, muy lento o da error de
    servidor.
    """
    try:
        respuesta = requests.get(url, timeout=timeout)
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
# 0. FUNCIÓN DE AUDITORÍA RETROACTIVA
# ==========================================
def auditar_descargas_anteriores(carpeta_origen, carpeta_alertas):
    """
    Lee los PDF ya descargados. Si no contienen la frase de resultado limpio,
    los mueve a la carpeta de alertas.
    """
    alertas_encontradas = []
    frase_normalizada = "".join(FRASE_LIMPIA.split())

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

                texto_limpio = "".join(texto_pdf.upper().split())

                if frase_normalizada not in texto_limpio:
                    os.makedirs(carpeta_alertas, exist_ok=True)
                    ruta_nueva = os.path.join(carpeta_alertas, archivo)
                    shutil.move(ruta_pdf, ruta_nueva)
                    alertas_encontradas.append(archivo.replace(".pdf", ""))

            except Exception as e:
                print(f"No se pudo auditar el archivo {archivo}: {e}")

    return alertas_encontradas

def circuito_abierto(fallos_consecutivos, nombre_entidad="RNMC"):
    """
    Si el portal falla dos veces seguidas por algo técnico (no por un dato puntual
    de la persona), lo más probable es un bloqueo de IP o una caída temporal.
    Seguir intentando con el resto solo perdería tiempo, así que se corta aquí.
    """
    if fallos_consecutivos >= 2:
        print(f"\nSe detectaron {fallos_consecutivos} fallos técnicos consecutivos en el portal de {nombre_entidad}.")
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
# 1. CONFIGURACIÓN Y LECTURA DE DATOS
# ==========================================
def obtener_ruta_datos():
    if len(sys.argv) > 1:
        return sys.argv[1]
    return input("Ruta del archivo (Excel, PDF o CSV ya preparado) con la información de los postulantes: ").strip('"').strip()

ruta_datos = obtener_ruta_datos()
if not os.path.isfile(ruta_datos):
    raise FileNotFoundError(f"No se encontró el archivo: {ruta_datos}")

directorio_base = os.path.normpath(os.path.dirname(ruta_datos))
carpeta_destino = os.path.join(directorio_base, "Cert_RNMC")
carpeta_inhabilitados = os.path.join(directorio_base, "Cert_RNMC_INHABILITADOS")
# Las carpetas ya no se crean las dos de una vez: cada una se crea solo
# cuando de verdad hay algo que guardar ahí, para no dejar carpetas vacías
# (p. ej. la de alertas cuando nadie tuvo ninguna).

# Carpetas con el nombre largo que usaban las corridas anteriores a este cambio.
# Se siguen revisando para no volver a descargar (y gastar tiempo/créditos) lo
# que ya quedó guardado ahí.
carpeta_destino_vieja = os.path.join(directorio_base, "Certificados_RNMC")
carpeta_inhabilitados_vieja = os.path.join(directorio_base, "Certificados_RNMC_INHABILITADOS")

print("Auditando certificados previamente descargados...")
alertas_historicas = auditar_descargas_anteriores(carpeta_destino, carpeta_inhabilitados)
if alertas_historicas:
    print(f"Se movieron {len(alertas_historicas)} certificados sospechosos a la carpeta de INHABILITADOS.")

print(f"Los PDF se guardarán en: {carpeta_destino}")

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
# Documento -> texto del portal, para el Excel de personas con alerta real.
documentos_inhabilitados = {}

print("Comprobando disponibilidad del portal de RNMC...")
if not verificar_portal_disponible(URL_RNMC):
    print("Atención: el portal de RNMC no respondió bien en esta comprobación (puede estar caído, muy lento o en mantenimiento).")
    if not sys.stdin.isatty():
        print("No hay una consola interactiva disponible (probablemente corriendo desde el panel web); no se puede confirmar si continuar. Se cancela la ejecución.")
        sys.exit(1)
    if input("¿Continuar de todas formas? (s/n): ").strip().lower() != "s":
        print("Ejecución cancelada por el usuario.")
        sys.exit(1)
else:
    print("El portal de RNMC respondió correctamente.")

# ==========================================
# 2. INICIAR NAVEGADOR Y AUTOMATIZACIÓN
# ==========================================
driver = webdriver.Chrome()
wait = WebDriverWait(driver, 15)
# Espera más larga solo para el resultado de la consulta: el portal a veces
# tarda más de 15s en responder (se ve su propio modal de "procesando"), y con
# la espera corta el script abandonaba antes de que el portal terminara.
wait_resultado = WebDriverWait(driver, 35)

errores_no_manejados = 0
fallos_consecutivos = 0
total_personas = len(df)

try:
    for contador_persona, (index, row) in enumerate(df.iterrows(), start=1):
        num_doc = str(row['# DOC. IDENTIDAD']).strip()

        # FILTRO: Si la celda dice "# DOC. IDENTIDAD" o no es un número, saltar a la siguiente fila
        if not num_doc[0].isdigit():
            continue

        if num_doc.endswith('.0'):
            num_doc = num_doc[:-2]

        tipo_doc_crudo = str(row['TIPO DOCUMENTO \n(RC - TI - PP)']).strip()

        try:
            fecha_obj = pd.to_datetime(row['FECHA DE EXPEDICION (DD/MM/AA)'])
            fecha_exp = fecha_obj.strftime('%d/%m/%Y')
        except Exception:
            # Fecha vacía o ilegible en el Excel (p. ej. una celda con formato
            # de fecha pero un número fuera de rango, que Excel/openpyxl ya
            # marca como error). Se detecta acá para no gastar una consulta
            # al portal con un dato que de todas formas va a ser rechazado.
            fecha_exp = None

        p_nombre = str(row['PRIMER NOMBRE']) if pd.notna(row['PRIMER NOMBRE']) else ""
        s_nombre = str(row['SEGUNDO NOMBRE']) if pd.notna(row['SEGUNDO NOMBRE']) else ""
        p_apellido = str(row['PRIMER APELLIDO']) if pd.notna(row['PRIMER APELLIDO']) else ""
        s_apellido = str(row['SEGUNDO APELLIDO']) if pd.notna(row['SEGUNDO APELLIDO']) else ""
        nombre_completo = f"{p_nombre} {s_nombre} {p_apellido} {s_apellido}".replace("  ", " ").strip()

        print(f"\n[{contador_persona}/{total_personas}] {nombre_completo} ({num_doc})")

        # ==========================================
        # 3. VALIDACIÓN PREVIA (LOOK BEFORE YOU LEAP)
        # ==========================================
        # Nombre y carpeta cortos para no exceder el límite de ruta de Windows.
        # También se reconocen la carpeta y el nombre largos de antes de este
        # cambio, para no volver a descargar lo que ya se había guardado ahí.
        primer_nombre_archivo = "".join(c for c in p_nombre.strip() if c.isalnum()) or "SN"
        codigo_val = row.get('CODIGO') if 'CODIGO' in df.columns else None
        prefijo_archivo = "".join(c for c in str(codigo_val).strip() if c.isalnum()) if pd.notna(codigo_val) and str(codigo_val).strip() else "RNMC"
        nombre_archivo_esperado = f"{prefijo_archivo}_{primer_nombre_archivo}_{num_doc}.pdf"
        ruta_esperada_normal = os.path.join(carpeta_destino, nombre_archivo_esperado)
        ruta_esperada_inhab = os.path.join(carpeta_inhabilitados, nombre_archivo_esperado)

        nombre_archivo_viejo = f"{nombre_completo} - {num_doc} - RNMC.pdf"
        ruta_vieja_normal = os.path.join(carpeta_destino_vieja, nombre_archivo_viejo)
        ruta_vieja_inhab = os.path.join(carpeta_inhabilitados_vieja, nombre_archivo_viejo)

        if any(os.path.exists(r) for r in (ruta_esperada_normal, ruta_esperada_inhab, ruta_vieja_normal, ruta_vieja_inhab)):
            print("El certificado ya existe en los registros. Se omite la descarga...")
            continue

        if fecha_exp is None:
            print("La fecha de expedición de esta persona está vacía o no se pudo leer del Excel. Se salta sin consultar el portal...")
            documentos_fallidos[num_doc] = "Fecha de expedición vacía o inválida en el Excel"
            continue

        try:
            driver.get("https://srvcnpc.policia.gov.co/PSC/frm_cnp_consulta.aspx")

            elemento_tipo_doc = wait.until(EC.presence_of_element_located((By.ID, "ctl00_ContentPlaceHolder3_ddlTipoDoc")))
            selector_tipo_doc = Select(elemento_tipo_doc)

            if "CC" in tipo_doc_crudo.upper() or "CIUDADAN" in tipo_doc_crudo.upper():
                selector_tipo_doc.select_by_value("55")
            elif "TI" in tipo_doc_crudo.upper() or "IDENTIDAD" in tipo_doc_crudo.upper():
                selector_tipo_doc.select_by_value("56")
            elif "CX" in tipo_doc_crudo.upper() or "EXTRANJER" in tipo_doc_crudo.upper() or "CE" in tipo_doc_crudo.upper():
                selector_tipo_doc.select_by_value("57")
            elif "PA" in tipo_doc_crudo.upper() or "PASAPORTE" in tipo_doc_crudo.upper():
                selector_tipo_doc.select_by_value("58")
            else:
                selector_tipo_doc.select_by_value("55")

            time.sleep(2)

            campo_documento = wait.until(EC.presence_of_element_located((By.ID, "ctl00_ContentPlaceHolder3_txtExpediente")))
            campo_documento.send_keys(num_doc)

            try:
                campo_fecha = driver.find_element(By.ID, "txtFechaexp")
                campo_fecha.send_keys(fecha_exp)
            except Exception:
                pass

            print("Datos llenados. Consultando...")

            # ==========================================
            # 4. ANÁLISIS DE RESULTADOS Y GENERACIÓN VÍA CDP
            # ==========================================
            try:
                # El portal es ASP.NET clásico: cambiar el tipo de documento o la fecha puede
                # recargar parte de la página, así que esperamos a que el botón esté listo
                # en vez de buscarlo de inmediato. El clic también queda dentro de este
                # bloque (no antes) porque con cédula de extranjería el formulario cambia
                # -no pide fecha de expedición- y el botón puede tardar más o comportarse
                # distinto; así cualquier falla aquí se trata igual que un timeout del
                # portal (captura de pantalla + reintento) en vez de perderse en un error
                # genérico sin diagnóstico.
                btn_buscar = wait.until(EC.element_to_be_clickable((By.ID, "ctl00_ContentPlaceHolder3_btnConsultar2")))
                driver.execute_script("arguments[0].click();", btn_buscar)

                # Esperamos lo que aparezca primero: el resultado ("informa:") o un modal de error del portal
                wait_resultado.until(lambda d: "informa:" in d.find_element(By.TAG_NAME, "body").text.lower()
                                      or "error" in d.find_element(By.TAG_NAME, "body").text.lower())
                time.sleep(2)

                texto_pantalla = driver.find_element(By.TAG_NAME, "body").text

                if "INFORMA:" not in texto_pantalla.upper():
                    # El portal mostró un modal de error (p. ej. fecha de expedición incorrecta)
                    # en vez del resultado de la consulta: es un problema del dato de origen, no del script.
                    lineas_error = [linea.strip() for linea in texto_pantalla.splitlines() if linea.strip()]
                    mensaje_portal = " ".join(lineas_error[:4])
                    print(f"El portal reportó un error: {mensaje_portal}")
                    print("Revisa el tipo/número de documento y la fecha de expedición de esta persona en el Excel.")

                    nombre_error = f"ERROR_{num_doc}.png"
                    ruta_error = os.path.join(carpeta_inhabilitados, nombre_error)
                    os.makedirs(carpeta_inhabilitados, exist_ok=True)
                    driver.save_screenshot(ruta_error)
                    lista_alertas_finales.append(nombre_error.replace(".png", ""))
                    documentos_fallidos[num_doc] = "El portal rechazó los datos (revisa tipo/número de documento o fecha de expedición)"
                    fallos_consecutivos = 0  # el portal respondió; el problema es del dato, no del sitio

                elif FRASE_LIMPIA in texto_pantalla.upper():
                    ruta_final_guardado = ruta_esperada_normal
                    print("Resultado limpio. Se guarda en la carpeta estándar...")

                    pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                        "printBackground": True,
                        "landscape": False
                    })

                    os.makedirs(carpeta_destino, exist_ok=True)
                    with open(ruta_final_guardado, "wb") as file:
                        file.write(base64.b64decode(pdf_data['data']))

                    print("PDF guardado correctamente.")
                    fallos_consecutivos = 0

                else:
                    ruta_final_guardado = ruta_esperada_inhab
                    print("Atención: se detectó una posible medida correctiva pendiente. Se guarda en la carpeta de alertas...")
                    lista_alertas_finales.append(nombre_archivo_esperado.replace(".pdf", ""))
                    documentos_inhabilitados[num_doc] = " ".join(texto_pantalla.split())
                    _pitido(2000, 1000)

                    pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                        "printBackground": True,
                        "landscape": False
                    })

                    os.makedirs(carpeta_inhabilitados, exist_ok=True)
                    with open(ruta_final_guardado, "wb") as file:
                        file.write(base64.b64decode(pdf_data['data']))

                    print("PDF guardado correctamente.")
                    fallos_consecutivos = 0

            except Exception:
                # El portal no llegó a mostrar ni el resultado ni un modal de error dentro
                # del tiempo de espera: es más probable que sea el servicio caído o lento
                # que una medida correctiva real, así que se guarda aparte para reintentar,
                # en vez de mezclarlo con las alertas de verdad.
                print(f"Se agotó el tiempo de espera procesando el resultado de {num_doc} (posible caída o lentitud del portal). Se guarda una captura de pantalla para revisión manual...")
                nombre_error = f"ERROR_{num_doc}.png"
                ruta_error = os.path.join(carpeta_inhabilitados, nombre_error)
                os.makedirs(carpeta_inhabilitados, exist_ok=True)
                driver.save_screenshot(ruta_error)
                lista_inconclusos.append(nombre_error.replace(".png", ""))
                documentos_fallidos[num_doc] = "Tiempo de espera agotado (posible caída o lentitud del portal)"
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

        time.sleep(1)

except Exception as e:
    print(f"\nOcurrió un error inesperado durante el ciclo: {e}")
    sys.exit(1)

finally:
    print("\n" + "="*50)
    print("RESUMEN DE EJECUCIÓN Y ALERTAS")
    print("="*50)

    if lista_alertas_finales:
        print(f"Se detectaron {len(lista_alertas_finales)} registro(s) con posible medida correctiva o error de consulta:")
        for alerta in lista_alertas_finales:
            print(f"   - {alerta}")
        print(f"\nRevisa manualmente los documentos en la carpeta:\n{carpeta_inhabilitados}")
    else:
        print("No se encontraron medidas correctivas pendientes en esta tanda.")

    if lista_inconclusos:
        print(f"\nAdemás, {len(lista_inconclusos)} consulta(s) no obtuvieron respuesta del portal a tiempo (posible caída o lentitud, no son alertas reales):")
        for inconcluso in lista_inconclusos:
            print(f"   - {inconcluso}")
        print("Vuelve a correr el script más tarde para reintentarlas.")

    _guardar_reporte_fallidos(df, documentos_fallidos, directorio_base, "RNMC")
    _guardar_reporte_inhabilitados(df, documentos_inhabilitados, directorio_base, "RNMC")

    print("="*50)
    print("Cerrando navegador...")
    driver.quit()

if errores_no_manejados:
    print(f"\n{errores_no_manejados} persona(s) no se pudieron procesar por errores inesperados. Vuelve a correr este script para reintentarlas (los ya descargados se omiten automáticamente).")
    sys.exit(1)
