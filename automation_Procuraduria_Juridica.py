import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
import time
import os
import sys
import shutil
import unicodedata
import re
import PyPDF2

# Misma frase que usa el portal de Procuraduría para persona natural --
# confirmada contra un certificado real de persona jurídica (ver
# conversación del 2026-08-25): "NO REGISTRA SANCIONES NI INHABILIDADES
# VIGENTES" aparece igual para NIT que para cédula.
FRASE_LIMPIA = "NO REGISTRA SANCIONES NI INHABILIDADES VIGENTES"


# ==========================================
# 0. FUNCIONES DE APOYO
# ==========================================
def resolver_pregunta_procuraduria(pregunta_texto, nit, razon_social):
    """
    Evalúa la pregunta dinámica de seguridad del portal y retorna la
    respuesta exacta. Las preguntas matemáticas, de dígitos del documento y
    de geografía no dependen de si es una persona o una empresa.

    La variante "primer nombre" SÍ es distinta: se probó usar la primera
    palabra de la razón social (o su conteo de letras) como respuesta y el
    portal la rechazó de forma consistente, incluso cuando el conteo de
    letras era matemáticamente correcto (ver conversación del 2026-08-25).
    Todo indica que esa pregunta pide el nombre de una persona real
    registrada ante la Procuraduría para ese NIT (probablemente el
    representante legal), dato que no está en el Excel de jurídicas -- así
    que a propósito NO se intenta adivinar una respuesta aquí: se deja caer
    al final de la función (devuelve "") para que quien llama la trate como
    pregunta desconocida y la persona jurídica quede en Fallidos con un
    motivo claro, en vez de gastar un intento en una respuesta que ya se
    sabe que va a ser rechazada.
    """
    p = unicodedata.normalize('NFKD', pregunta_texto).encode('ASCII', 'ignore').decode('utf-8').lower()
    p = p.replace("vallle", "valle")

    match_math = re.search(r'(\d+)\s*([\+\-xX\*])\s*(\d+)', p)
    if match_math:
        num1 = int(match_math.group(1))
        operador = match_math.group(2).lower()
        num2 = int(match_math.group(3))

        if operador == '+': return str(num1 + num2)
        elif operador == '-': return str(num1 - num2)
        elif operador in ['x', '*']: return str(num1 * num2)

    if "primer nombre" in p:
        return None  # pregunta reconocida, pero no aplicable a personas jurídicas

    if "tres primeros" in p or "3 primeros" in p: return str(nit)[:3]
    if "dos ultimos" in p or "2 ultimos" in p: return str(nit)[-2:]
    if "tres ultimos" in p or "3 ultimos" in p: return str(nit)[-3:]

    capitales = {
        "antioquia": "medellin", "cundinamarca": "bogota", "colombia": "bogota",
        "valle del cauca": "cali", "atlantico": "barranquilla", "bolivar": "cartagena",
        "magdalena": "santa marta", "choco": "quibdo", "narino": "pasto",
        "cauca": "popayan", "risaralda": "pereira", "quindio": "armenia",
        "caldas": "manizales", "tolima": "ibague", "huila": "neiva",
        "boyaca": "tunja", "meta": "villavicencio", "norte de santander": "cucuta",
        "santander": "bucaramanga", "sucre": "sincelejo", "cordoba": "monteria",
        "cesar": "valledupar", "guajira": "riohacha", "arauca": "arauca",
        "casanare": "yopal", "putumayo": "mocoa", "amazonas": "leticia"
    }

    for depto, capital in capitales.items():
        if depto in p:
            return capital

    return ""


def esperar_y_renombrar_descarga(carpeta_destino, nombre_final, timeout=30):
    """
    Vigila la carpeta hasta que aparezca un nuevo PDF y lo renombra.
    """
    archivos_iniciales = set(os.listdir(carpeta_destino))
    tiempo_inicio = time.time()

    while time.time() - tiempo_inicio < timeout:
        archivos_actuales = set(os.listdir(carpeta_destino))
        archivos_nuevos = archivos_actuales - archivos_iniciales

        for archivo in archivos_nuevos:
            if archivo.endswith('.pdf') and not archivo.endswith('.crdownload'):
                time.sleep(1.5)

                ruta_antigua = os.path.join(carpeta_destino, archivo)
                ruta_nueva = os.path.join(carpeta_destino, nombre_final)

                try:
                    if os.path.exists(ruta_nueva):
                        os.remove(ruta_nueva)
                    os.rename(ruta_antigua, ruta_nueva)
                    return True
                except Exception as e:
                    print(f"Error al renombrar el archivo: {e}")
                    return False

        time.sleep(1)

    return False


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


def circuito_abierto(fallos_consecutivos, nombre_entidad="la Procuraduría (personas jurídicas)"):
    """
    Si el mismo portal falla dos veces seguidas (no por un dato puntual,
    sino por algo técnico), lo más probable es un bloqueo de IP o una caída
    temporal. Seguir intentando con el resto solo perdería tiempo, así que
    se corta la verificación aquí.
    """
    if fallos_consecutivos >= 2:
        print(f"\nSe detectaron {fallos_consecutivos} fallos técnicos consecutivos en el portal de {nombre_entidad}.")
        print("Es posible que esté bloqueando las solicitudes automatizadas o esté caído en este momento.")
        print("Se detiene esta verificación para no perder más tiempo.")
        return True
    return False


def _pedir_respuesta_manual(mensaje):
    """
    Pide una respuesta escrita a mano -- pero solo si hay una consola real
    detrás. Si no hay una terminal interactiva (como cuando el panel web
    lanza este script como subproceso), no hay quien la escriba: en vez de
    colgarse esperando una entrada que nunca llega, se devuelve None de una
    vez para que la empresa quede marcada como fallida y se pueda reintentar
    luego.
    """
    if not sys.stdin.isatty():
        return None
    return input(mensaje)


def _quitar_acentos(texto):
    return texto.translate(str.maketrans("ÁÉÍÓÚáéíóúÑñ", "AEIOUaeiouNn"))


def _columna_que_contiene(columnas, *fragmentos):
    for columna in columnas:
        normalizada = _quitar_acentos(str(columna)).upper()
        if all(fragmento in normalizada for fragmento in fragmentos):
            return columna
    return None


def leer_juridicas(ruta_excel):
    """
    Lee el Excel de personas jurídicas postuladas: Código, Razón Social,
    Tipo de identificación, Número de Identificación Tributaria (NIT).
    """
    try:
        encabezados = pd.read_excel(ruta_excel, sheet_name=0, header=0, nrows=0)
    except Exception:
        return pd.DataFrame()
    encabezados.columns = encabezados.columns.str.strip()
    columnas = list(encabezados.columns)

    col_codigo = _columna_que_contiene(columnas, "CODIGO")
    col_razon = _columna_que_contiene(columnas, "RAZON")
    col_tipo = _columna_que_contiene(columnas, "TIPO")
    col_nit = (
        _columna_que_contiene(columnas, "NIT")
        or _columna_que_contiene(columnas, "IDENTIFICACION", "TRIBUTARIA")
        or _columna_que_contiene(columnas, "NUMERO", "IDENTIFICACION")
    )

    if not (col_codigo and col_razon and col_nit):
        return pd.DataFrame()

    try:
        df = pd.read_excel(ruta_excel, sheet_name=0, header=0, dtype={col_codigo: str, col_nit: str})
    except Exception:
        return pd.DataFrame()
    df.columns = df.columns.str.strip()
    df = df.dropna(subset=[col_nit])

    filas = []
    for _, fila in df.iterrows():
        nit = re.sub(r"\D", "", str(fila[col_nit])) if pd.notna(fila[col_nit]) else ""
        if not nit:
            continue
        filas.append({
            'CODIGO': str(fila[col_codigo]).strip() if pd.notna(fila[col_codigo]) else "",
            'RAZON SOCIAL': str(fila[col_razon]).strip() if pd.notna(fila[col_razon]) else "",
            'TIPO DE IDENTIFICACION': str(fila[col_tipo]).strip() if col_tipo and pd.notna(fila[col_tipo]) else 'NIT',
            'NIT': nit,
        })

    resultado = pd.DataFrame(filas)
    return resultado.drop_duplicates(subset=['NIT']) if not resultado.empty else resultado


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
    Junta a las empresas que NO se pudieron consultar de verdad y las deja
    en un Excel aparte (Fallidos_<ENTIDAD>.xlsx), para reintentarlas sin
    mezclarlas con las que sí tuvieron una alerta real.
    """
    ruta_fallidos = os.path.join(directorio_base, f"Fallidos_{codigo_entidad}.xlsx")

    if not documentos_fallidos:
        if os.path.exists(ruta_fallidos):
            os.remove(ruta_fallidos)
        return

    filas_fallidos = []
    for _, fila_persona in df.iterrows():
        nit_persona = str(fila_persona['NIT']).strip()
        if nit_persona not in documentos_fallidos:
            continue
        filas_fallidos.append({
            "CODIGO": fila_persona.get('CODIGO', ''),
            "RAZON_SOCIAL": fila_persona.get('RAZON SOCIAL', ''),
            "NIT": nit_persona,
            "MOTIVO": documentos_fallidos[nit_persona],
        })

    try:
        pd.DataFrame(filas_fallidos).to_excel(ruta_fallidos, index=False)
        print(f"\nSe guardó el listado de personas jurídicas con consulta fallida en:\n{ruta_fallidos}")
    except Exception as error_reporte:
        print(f"Aviso: no se pudo generar el Excel de fallidos: {error_reporte}")


def _guardar_reporte_inhabilitados(df, documentos_inhabilitados, directorio_base, codigo_entidad):
    """
    Junta a TODAS las empresas con alerta real guardada en la carpeta de
    inhabilitados -de esta corrida o de corridas anteriores- en un Excel
    aparte (Inhabilitados_<ENTIDAD>.xlsx).
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
                continue
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
        nit_persona = str(fila_persona['NIT']).strip()
        if nit_persona not in motivos_por_doc:
            continue
        filas_inhabilitados.append({
            "CODIGO": fila_persona.get('CODIGO', ''),
            "RAZON_SOCIAL": fila_persona.get('RAZON SOCIAL', ''),
            "NIT": nit_persona,
            "MOTIVO": motivos_por_doc[nit_persona],
        })

    try:
        pd.DataFrame(filas_inhabilitados).to_excel(ruta_inhabilitados, index=False)
        print(f"\nSe guardó el listado de personas jurídicas con alerta real en:\n{ruta_inhabilitados}")
    except Exception as error_reporte:
        print(f"Aviso: no se pudo generar el Excel de inhabilitados: {error_reporte}")


# ==========================================
# 1. CONFIGURACIÓN Y RUTAS
# ==========================================
def obtener_ruta_datos():
    if len(sys.argv) > 1:
        return sys.argv[1]
    return input("Ruta del Excel con las personas jurídicas (Código, Razón Social, Tipo de identificación, NIT): ").strip('"').strip()


ruta_datos = obtener_ruta_datos()
if not os.path.isfile(ruta_datos):
    raise FileNotFoundError(f"No se encontró el archivo: {ruta_datos}")

directorio_base = os.path.normpath(os.path.dirname(ruta_datos))
carpeta_destino = os.path.join(directorio_base, "Cert_PROCJUR")
carpeta_inhabilitados = os.path.join(directorio_base, "Cert_PROCJUR_INHABILITADOS")
os.makedirs(carpeta_destino, exist_ok=True)

print("Auditando certificados previamente descargados...")
alertas_historicas = auditar_descargas_anteriores(carpeta_destino, carpeta_inhabilitados)
if alertas_historicas:
    print(f"Se movieron {len(alertas_historicas)} certificados sospechosos a la carpeta de INHABILITADOS.")

print(f"Los PDF se guardarán y renombrarán automáticamente en:\n{carpeta_destino}")

print("\nLeyendo el archivo de personas jurídicas...")
df = leer_juridicas(ruta_datos)
if df.empty:
    print("No se encontró ninguna persona jurídica con NIT válido en el archivo.")

lista_alertas_finales = alertas_historicas.copy()
# NIT -> motivo, para el Excel de empresas a las que falló la consulta
# (distinto de una alerta real: aquí no se logró determinar nada).
documentos_fallidos = {}
# NIT -> texto del portal, para el Excel de empresas con alerta real.
documentos_inhabilitados = {}

# ==========================================
# 2. CONFIGURAR DESCARGAS INVISIBLES
# ==========================================
opciones = webdriver.ChromeOptions()
prefs = {
    "download.default_directory": carpeta_destino,
    "download.prompt_for_download": False,
    "download.directory_upgrade": True,
    "plugins.always_open_pdf_externally": True
}
opciones.add_experimental_option("prefs", prefs)
opciones.add_argument("--window-size=1280,800")

driver = webdriver.Chrome(options=opciones)
wait = WebDriverWait(driver, 25)
# Espera más larga solo para el botón de "Descargar": el propio HTML del
# portal de la Procuraduría configura su UpdatePanel de ASP.NET con un
# timeout de 360000ms (6 minutos) -- ellos mismos anticipan que la consulta
# puede tardar así de lento en días congestionados.
wait_descarga = WebDriverWait(driver, 180)

errores_no_manejados = 0
fallos_consecutivos = 0
total_personas = len(df)

try:
    for contador_persona, (index, row) in enumerate(df.iterrows(), start=1):
        nit = str(row['NIT']).strip()
        if not nit or not nit[0].isdigit():
            continue

        razon_social = str(row['RAZON SOCIAL']).strip() if pd.notna(row['RAZON SOCIAL']) else ""

        print(f"\n[{contador_persona}/{total_personas}] {razon_social} (NIT {nit})")

        # ==========================================
        # 3. VALIDACIÓN PREVIA (LOOK BEFORE YOU LEAP)
        # ==========================================
        tokens_razon = razon_social.split()
        primer_token_razon = "".join(c for c in tokens_razon[0] if c.isalnum()) if tokens_razon else "SN"
        codigo_val = row.get('CODIGO')
        prefijo_archivo = "".join(c for c in str(codigo_val).strip() if c.isalnum()) if pd.notna(codigo_val) and str(codigo_val).strip() else "PROCJUR"
        nombre_archivo_esperado = f"{prefijo_archivo}_{primer_token_razon}_{nit}.pdf"
        ruta_esperada_normal = os.path.join(carpeta_destino, nombre_archivo_esperado)
        ruta_esperada_inhab = os.path.join(carpeta_inhabilitados, nombre_archivo_esperado)

        if any(os.path.exists(r) for r in (ruta_esperada_normal, ruta_esperada_inhab)):
            print("El certificado ya existe en la carpeta. Se omite la descarga...")
            continue

        try:
            # ==========================================
            # 4. MANEJO DE PÁGINA Y FORMULARIO
            # ==========================================
            # El formulario a veces está directo en la página y a veces
            # dentro de un iframe (la página trae más de uno -- accesibilidad,
            # banners, etc. -- así que no basta con agarrar "el primero").
            # Además, la primera visita "en frío" (perfil de Chrome nuevo,
            # sin la sesión que ya tendría un navegador usado normalmente)
            # a veces no carga el formulario y en su lugar el portal
            # devuelve un error de página -- por eso se recarga una vez más
            # antes de rendirse, en vez de asumir que el portal está caído.
            elemento_tipo_doc = None
            for intento_carga in range(2):
                driver.get("https://www.procuraduria.gov.co/Pages/Generacion-de-antecedentes.aspx")
                time.sleep(4)

                try:
                    elemento_tipo_doc = WebDriverWait(driver, 8).until(EC.presence_of_element_located((By.ID, "ddlTipoID")))
                except Exception:
                    pass

                if elemento_tipo_doc is None:
                    try:
                        iframes = driver.find_elements(By.TAG_NAME, "iframe")
                        for candidato in iframes:
                            try:
                                driver.switch_to.default_content()
                                driver.switch_to.frame(candidato)
                                elemento_tipo_doc = WebDriverWait(driver, 8).until(EC.presence_of_element_located((By.ID, "ddlTipoID")))
                                print("Formulario encontrado dentro de un iframe. Se cambia el contexto...")
                                break
                            except Exception:
                                continue
                    except Exception:
                        pass

                if elemento_tipo_doc is not None:
                    break

                driver.switch_to.default_content()
                if intento_carga == 0:
                    print("La página no cargó el formulario en el primer intento (posible sesión fría). Recargando...")
                    time.sleep(2)

            if elemento_tipo_doc is None:
                print("El formulario no cargó a tiempo o la página está saturada. Se salta este registro...")
                driver.switch_to.default_content()
                errores_no_manejados += 1
                fallos_consecutivos += 1
                if circuito_abierto(fallos_consecutivos):
                    break
                continue

            selector_tipo_doc = Select(elemento_tipo_doc)
            # value="2" es NIT en este portal (confirmado en el HTML real
            # del formulario, donde incluso viene preseleccionado).
            selector_tipo_doc.select_by_value("2")

            campo_documento = driver.find_element(By.ID, "txtNumID")
            campo_documento.send_keys(nit)

            # ==========================================
            # 5. RESOLVER PREGUNTA DE SEGURIDAD
            # ==========================================
            label_pregunta = driver.find_element(By.ID, "lblPregunta").text
            print(f"Pregunta detectada: {label_pregunta}")

            respuesta_calculada = resolver_pregunta_procuraduria(label_pregunta, nit, razon_social)

            # Si toca una pregunta de "primer nombre" (pide el nombre de una
            # persona real que no tenemos para personas jurídicas), el
            # portal ofrece un botón para refrescarla y mostrar otra al azar
            # -- se reintenta unas cuantas veces antes de darse por vencido,
            # en vez de saltar la empresa a la primera pregunta desfavorable.
            intentos_refrescar_pregunta = 0
            max_intentos_refrescar_pregunta = 5
            while respuesta_calculada is None and intentos_refrescar_pregunta < max_intentos_refrescar_pregunta:
                intentos_refrescar_pregunta += 1
                try:
                    btn_refrescar = driver.find_element(By.ID, "ImageButton1")
                    driver.execute_script("arguments[0].click();", btn_refrescar)
                    time.sleep(1.5)
                    label_pregunta = driver.find_element(By.ID, "lblPregunta").text
                    print(f"Pregunta refrescada ({intentos_refrescar_pregunta}/{max_intentos_refrescar_pregunta}): {label_pregunta}")
                    respuesta_calculada = resolver_pregunta_procuraduria(label_pregunta, nit, razon_social)
                except Exception as e:
                    print(f"No se pudo refrescar la pregunta: {e}")
                    break

            if respuesta_calculada is None:
                # Se agotaron los reintentos de refrescar y ninguna pregunta
                # resultó respondible. No es un fallo del portal, así que no
                # cuenta para el circuito ni suena la alarma.
                print("No se logró una pregunta respondible para personas jurídicas tras varios intentos de refrescar. Se salta esta empresa...")
                documentos_fallidos[nit] = "Pregunta de seguridad requiere el nombre de una persona (no disponible para personas jurídicas)"
                continue

            if not respuesta_calculada:
                _pitido(1000, 500)
                respuesta_calculada = _pedir_respuesta_manual("Pregunta desconocida. Escribe la respuesta aquí en la consola y presiona Enter: ")
                if respuesta_calculada is None:
                    documentos_fallidos[nit] = "Pregunta de seguridad desconocida (sin consola interactiva para responderla a mano)"
                    errores_no_manejados += 1
                    fallos_consecutivos += 1
                    if circuito_abierto(fallos_consecutivos):
                        break
                    continue

            campo_respuesta = driver.find_element(By.ID, "txtRespuestaPregunta")
            campo_respuesta.send_keys(respuesta_calculada)
            print(f"Respuesta ingresada: {respuesta_calculada}")

            # ==========================================
            # 6. BUCLE DE VALIDACIÓN INTERACTIVA
            # ==========================================
            exito_generacion = False
            intentos_validacion = 0

            while intentos_validacion < 2 and not exito_generacion:
                btn_generar = driver.find_element(By.ID, "btnExportar")
                driver.execute_script("arguments[0].click();", btn_generar)
                time.sleep(2)

                errores_visibles = False
                try:
                    xpath_errores = "//*[contains(@style, 'color:Red') or contains(@style, 'color: red')] | //*[contains(text(), 'no corresponde')]"
                    elementos_error = driver.find_elements(By.XPATH, xpath_errores)

                    for error in elementos_error:
                        if error.is_displayed() and error.text.strip() != "*" and error.text.strip() != "":
                            print(f"Alerta del portal detectada: {error.text.strip()}")
                            errores_visibles = True
                            break
                except Exception:
                    pass

                if errores_visibles:
                    _pitido(1000, 500)
                    nueva_respuesta = _pedir_respuesta_manual("Intento fallido. Ingresa la respuesta correcta (o escribe 'saltar' para omitir empresa): ")

                    if nueva_respuesta is None or nueva_respuesta.lower() == 'saltar':
                        break

                    campo_respuesta = driver.find_element(By.ID, "txtRespuestaPregunta")
                    campo_respuesta.clear()
                    campo_respuesta.send_keys(nueva_respuesta)
                    intentos_validacion += 1
                else:
                    exito_generacion = True

            if not exito_generacion:
                documentos_fallidos[nit] = "No se pudo superar la pregunta de seguridad del portal"
                driver.switch_to.default_content()
                fallos_consecutivos = 0  # el portal respondió; el problema es de esta respuesta puntual
                continue

            # ==========================================
            # 7. GENERAR Y DESCARGAR
            # ==========================================
            print("Navegando a la pantalla de descarga...")
            time.sleep(5)

            descarga_lista = False
            try:
                xpath_boton_descarga = """
                //a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'descargar') and not(contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'descargue'))] |
                //input[contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'descargar')] |
                //input[contains(translate(@src, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'descargar')]
                """

                btn_descarga = wait_descarga.until(EC.element_to_be_clickable((By.XPATH, xpath_boton_descarga)))

                try:
                    btn_descarga.click()
                    print("Clic físico en el botón Descargar exitoso.")
                except Exception:
                    driver.execute_script("arguments[0].click();", btn_descarga)
                    print("Clic forzado (vía JS) en el botón Descargar exitoso.")

                print("Esperando a que el archivo se guarde...")
                descarga_lista = esperar_y_renombrar_descarga(carpeta_destino, nombre_archivo_esperado, timeout=30)
                if not descarga_lista:
                    print("Se agotó el tiempo de espera. El servidor de la Procuraduría no generó el archivo.")

            except Exception as e:
                print(f"No se detectó el botón de descarga en la segunda pantalla ({e}). Revisando carpeta...")
                ruta_debug = os.path.join(carpeta_destino, f"DEBUG_{nit}.png")
                try:
                    driver.save_screenshot(ruta_debug)
                    print(f"Captura de diagnóstico guardada en: {ruta_debug}")
                    with open(os.path.join(carpeta_destino, f"DEBUG_{nit}.html"), "w", encoding="utf-8") as f_debug:
                        f_debug.write(driver.page_source)
                except Exception:
                    pass
                descarga_lista = esperar_y_renombrar_descarga(carpeta_destino, nombre_archivo_esperado, timeout=10)
                if not descarga_lista:
                    print("No se pudo procesar la descarga de este documento.")

            driver.switch_to.default_content()

            if not descarga_lista:
                documentos_fallidos[nit] = "No se pudo descargar el certificado (tiempo agotado o botón de descarga no encontrado)"
                errores_no_manejados += 1
                fallos_consecutivos += 1
                if circuito_abierto(fallos_consecutivos):
                    break
                continue

            # ==========================================
            # 8. ANÁLISIS DEL RESULTADO
            # ==========================================
            try:
                with open(ruta_esperada_normal, 'rb') as f:
                    lector = PyPDF2.PdfReader(f)
                    texto_pdf = ""
                    for pagina in lector.pages:
                        texto_pdf += pagina.extract_text() or ""
                texto_limpio = "".join(texto_pdf.upper().split())
            except Exception:
                texto_pdf = ""
                texto_limpio = ""

            frase_normalizada = "".join(FRASE_LIMPIA.split())

            if frase_normalizada in texto_limpio:
                print(f"Resultado limpio. Documento guardado como '{nombre_archivo_esperado}'.")
            else:
                os.makedirs(carpeta_inhabilitados, exist_ok=True)
                shutil.move(ruta_esperada_normal, ruta_esperada_inhab)
                print("Atención: se detectó una posible sanción o inhabilidad vigente. Se movió a la carpeta de alertas...")
                lista_alertas_finales.append(nombre_archivo_esperado.replace(".pdf", ""))
                documentos_inhabilitados[nit] = " ".join(texto_pdf.split())
                _pitido(2000, 1000)

            fallos_consecutivos = 0

        except Exception as e:
            print(f"Error inesperado procesando a {razon_social} (NIT {nit}): {e}")
            print("Se salta a la siguiente empresa...")
            documentos_fallidos[nit] = f"Error inesperado: {e}"
            errores_no_manejados += 1
            fallos_consecutivos += 1
            driver.switch_to.default_content()
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
        print(f"Se detectaron {len(lista_alertas_finales)} registro(s) con posible sanción o inhabilidad vigente:")
        for alerta in lista_alertas_finales:
            print(f"   - {alerta}")
        print(f"\nRevisa manualmente los documentos en la carpeta:\n{carpeta_inhabilitados}")
    else:
        print("No se encontraron sanciones o inhabilidades vigentes en esta tanda.")

    _guardar_reporte_fallidos(df, documentos_fallidos, directorio_base, "PROCJUR")
    _guardar_reporte_inhabilitados(df, documentos_inhabilitados, directorio_base, "PROCJUR")

    print("="*50)
    print("Cerrando navegador...")
    driver.quit()

if errores_no_manejados:
    print(f"\n{errores_no_manejados} empresa(s) no se pudieron procesar por errores inesperados. Vuelve a correr este script para reintentarlas (los ya descargados se omiten automáticamente).")
    sys.exit(1)
