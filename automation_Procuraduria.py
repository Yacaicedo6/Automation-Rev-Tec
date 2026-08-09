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

FRASE_LIMPIA = "NO REGISTRA SANCIONES NI INHABILIDADES VIGENTES"

# Patrón de la frase de autorización que trae cada persona en el PDF de
# "Autorización para consulta de antecedentes" (usado cuando no hay Excel).
PATRON_AUTORIZACION_PDF = re.compile(
    r"El \(la\) suscrito\(a\)\s+(?P<nombre>.+?)\s*,\s+identificado\(a\) con\s+"
    r"(?P<tipo_doc>c[eé]dula de ciudadan[ií]a|tarjeta de identidad|c[eé]dula de extranjer[ií]a|pasaporte)\s+No\.\s+"
    r"(?P<doc>[\d.]+)\s*,\s+expedida en\s+.+?\s*,\s+con fecha de expedici[oó]n\s+"
    r"(?P<fecha>\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)

# ==========================================
# 0. FUNCIONES PYTHONICAS DE APOYO
# ==========================================
def resolver_pregunta_procuraduria(pregunta_texto, num_doc, p_nombre):
    """
    Evalúa la pregunta dinámica, detecta el tipo y retorna la respuesta exacta.
    """
    p = unicodedata.normalize('NFKD', pregunta_texto).encode('ASCII', 'ignore').decode('utf-8').lower()
    p = p.replace("vallle", "valle")

    # 1. Lógica Matemática (Suma, Resta y Multiplicación)
    match_math = re.search(r'(\d+)\s*([\+\-xX\*])\s*(\d+)', p)
    if match_math:
        num1 = int(match_math.group(1))
        operador = match_math.group(2).lower()
        num2 = int(match_math.group(3))

        if operador == '+': return str(num1 + num2)
        elif operador == '-': return str(num1 - num2)
        elif operador in ['x', '*']: return str(num1 * num2)

    # 2. Lógica de Datos Personales
    if "primer nombre" in p:
        if "cantidad de letras" in p:
            return str(len(str(p_nombre).strip()))
        elif "dos primeras letras" in p or "2 primeras letras" in p:
            return str(p_nombre)[:2].lower() if p_nombre else ""
        else:
            return str(p_nombre).lower()

    if "tres primeros" in p or "3 primeros" in p: return str(num_doc)[:3]
    if "dos ultimos" in p or "2 ultimos" in p: return str(num_doc)[-2:]
    if "tres ultimos" in p or "3 ultimos" in p: return str(num_doc)[-3:]

    # 3. Lógica de Geografía
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
                    ruta_nueva = os.path.join(carpeta_alertas, archivo)
                    shutil.move(ruta_pdf, ruta_nueva)
                    alertas_encontradas.append(archivo.replace(".pdf", ""))

            except Exception as e:
                print(f"No se pudo auditar el archivo {archivo}: {e}")

    return alertas_encontradas

def circuito_abierto(fallos_consecutivos, nombre_entidad="la Procuraduría"):
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

def leer_personas(ruta_excel):
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

def leer_personas_desde_pdf(ruta_pdf):
    """
    Extrae a las personas directamente del PDF de "Autorización para consulta
    de antecedentes" (una autorización por persona, dentro del mismo archivo),
    para las convocatorias que ya no traen un Excel de postulantes.
    """
    with open(ruta_pdf, 'rb') as f:
        lector = PyPDF2.PdfReader(f)
        texto_completo = ""
        for pagina in lector.pages:
            texto_completo += (pagina.extract_text() or "") + " "

    texto_normalizado = " ".join(texto_completo.split())

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
            'FECHA DE EXPEDICION (DD/MM/AA)': pd.to_datetime(coincidencia.group("fecha"), dayfirst=True),
        })

    df = pd.DataFrame(filas)
    return df.drop_duplicates(subset=['# DOC. IDENTIDAD']) if not df.empty else df

# ==========================================
# 1. CONFIGURACIÓN Y RUTAS
# ==========================================
def obtener_ruta_datos():
    if len(sys.argv) > 1:
        return sys.argv[1]
    return input("Ruta del archivo (Excel o PDF) con la información de los postulantes: ").strip('"').strip()

ruta_datos = obtener_ruta_datos()
if not os.path.isfile(ruta_datos):
    raise FileNotFoundError(f"No se encontró el archivo: {ruta_datos}")

directorio_base = os.path.dirname(ruta_datos)
carpeta_destino = os.path.join(directorio_base, "Cert_PROC")
carpeta_inhabilitados = os.path.join(directorio_base, "Cert_PROC_INHABILITADOS")
os.makedirs(carpeta_destino, exist_ok=True)
os.makedirs(carpeta_inhabilitados, exist_ok=True)

# Carpetas con el nombre largo que usaban las corridas anteriores a este cambio.
# Se siguen revisando para no volver a descargar (y perder tiempo) lo que ya
# quedó guardado ahí.
carpeta_destino_vieja = os.path.join(directorio_base, "Certificados_Procuraduria")
carpeta_inhabilitados_vieja = os.path.join(directorio_base, "Certificados_Procuraduria_INHABILITADOS")

print("Auditando certificados previamente descargados...")
alertas_historicas = auditar_descargas_anteriores(carpeta_destino, carpeta_inhabilitados)
if alertas_historicas:
    print(f"Se movieron {len(alertas_historicas)} certificados sospechosos a la carpeta de INHABILITADOS.")

print(f"Los PDF se guardarán y renombrarán automáticamente en:\n{carpeta_destino}")

print("\nLeyendo el archivo de postulantes...")
if ruta_datos.lower().endswith('.pdf'):
    df = leer_personas_desde_pdf(ruta_datos)
else:
    df = leer_personas(ruta_datos)
if df.empty:
    print("No se encontró ninguna persona con documento válido en el archivo.")

lista_alertas_finales = alertas_historicas.copy()

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

driver = webdriver.Chrome(options=opciones)
wait = WebDriverWait(driver, 25)

errores_no_manejados = 0
fallos_consecutivos = 0

try:
    for index, row in df.iterrows():
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

        print(f"\n--- Procesando Documento: {num_doc} ({nombre_completo}) ---")

        # ==========================================
        # 3. VALIDACIÓN PREVIA (LOOK BEFORE YOU LEAP)
        # ==========================================
        # Nombre y carpeta cortos para no exceder el límite de ruta de Windows.
        # También se reconocen la carpeta y el nombre largos de antes de este
        # cambio, para no volver a descargar lo que ya se había guardado ahí.
        primer_nombre_archivo = "".join(c for c in p_nombre.strip() if c.isalnum()) or "SN"
        nombre_archivo_esperado = f"PROC_{primer_nombre_archivo}_{num_doc}.pdf"
        ruta_esperada_normal = os.path.join(carpeta_destino, nombre_archivo_esperado)
        ruta_esperada_inhab = os.path.join(carpeta_inhabilitados, nombre_archivo_esperado)

        nombre_limpio = "".join(c for c in nombre_completo if c.isalnum() or c in " -_").strip()
        nombre_archivo_viejo = f"Procuraduria-{nombre_limpio}.pdf"
        ruta_vieja_normal = os.path.join(carpeta_destino_vieja, nombre_archivo_viejo)
        ruta_vieja_inhab = os.path.join(carpeta_inhabilitados_vieja, nombre_archivo_viejo)

        if any(os.path.exists(r) for r in (ruta_esperada_normal, ruta_esperada_inhab, ruta_vieja_normal, ruta_vieja_inhab)):
            print("El certificado ya existe en la carpeta. Se omite la descarga...")
            continue

        try:
            # ==========================================
            # 4. MANEJO DE PÁGINA Y FORMULARIO
            # ==========================================
            driver.get("https://www.procuraduria.gov.co/Pages/Generacion-de-antecedentes.aspx")
            time.sleep(4)

            try:
                iframes = driver.find_elements(By.TAG_NAME, "iframe")
                if len(iframes) > 0:
                    print("Iframe detectado en la página. Cambiando el contexto...")
                    driver.switch_to.frame(iframes[0])
                    time.sleep(1)
            except Exception:
                pass

            try:
                elemento_tipo_doc = wait.until(EC.presence_of_element_located((By.ID, "ddlTipoID")))
                selector_tipo_doc = Select(elemento_tipo_doc)
            except Exception:
                print("El formulario no cargó a tiempo o la página está saturada. Se salta este registro...")
                driver.switch_to.default_content()
                errores_no_manejados += 1
                fallos_consecutivos += 1
                if circuito_abierto(fallos_consecutivos):
                    break
                continue

            if "CC" in tipo_doc_crudo.upper() or "CIUDADAN" in tipo_doc_crudo.upper():
                selector_tipo_doc.select_by_value("1")
            elif "CX" in tipo_doc_crudo.upper() or "EXTRANJER" in tipo_doc_crudo.upper() or "CE" in tipo_doc_crudo.upper():
                selector_tipo_doc.select_by_value("5")
            elif "PPT" in tipo_doc_crudo.upper() or "PERMISO" in tipo_doc_crudo.upper():
                selector_tipo_doc.select_by_value("10")
            else:
                selector_tipo_doc.select_by_value("1")

            campo_documento = driver.find_element(By.ID, "txtNumID")
            campo_documento.send_keys(num_doc)

            # ==========================================
            # 5. RESOLVER PREGUNTA DE SEGURIDAD
            # ==========================================
            label_pregunta = driver.find_element(By.ID, "lblPregunta").text
            print(f"Pregunta detectada: {label_pregunta}")

            respuesta_calculada = resolver_pregunta_procuraduria(label_pregunta, num_doc, p_nombre)

            if not respuesta_calculada:
                import winsound
                winsound.Beep(1000, 500)
                respuesta_calculada = input("Pregunta desconocida. Escribe la respuesta aquí en la consola y presiona Enter: ")

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
                    import winsound
                    winsound.Beep(1000, 500)
                    nueva_respuesta = input("Intento fallido. Ingresa la respuesta correcta (o escribe 'saltar' para omitir persona): ")

                    if nueva_respuesta.lower() == 'saltar':
                        break

                    campo_respuesta = driver.find_element(By.ID, "txtRespuestaPregunta")
                    campo_respuesta.clear()
                    campo_respuesta.send_keys(nueva_respuesta)
                    intentos_validacion += 1
                else:
                    exito_generacion = True

            if not exito_generacion:
                print("No se pudo superar la validación. Se salta a la siguiente persona...")
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

                btn_descarga = wait.until(EC.element_to_be_clickable((By.XPATH, xpath_boton_descarga)))

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

            except Exception:
                print("No se detectó el botón de descarga en la segunda pantalla. Revisando carpeta...")
                descarga_lista = esperar_y_renombrar_descarga(carpeta_destino, nombre_archivo_esperado, timeout=10)
                if not descarga_lista:
                    print("No se pudo procesar la descarga de este documento.")

            driver.switch_to.default_content()

            if not descarga_lista:
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
                texto_limpio = ""

            frase_normalizada = "".join(FRASE_LIMPIA.split())

            if frase_normalizada in texto_limpio:
                print(f"Resultado limpio. Documento guardado como '{nombre_archivo_esperado}'.")
            else:
                shutil.move(ruta_esperada_normal, ruta_esperada_inhab)
                print("Atención: se detectó una posible sanción o inhabilidad vigente. Se movió a la carpeta de alertas...")
                lista_alertas_finales.append(nombre_archivo_esperado.replace(".pdf", ""))
                import winsound
                winsound.Beep(2000, 1000)

            fallos_consecutivos = 0

        except Exception as e:
            print(f"Error inesperado procesando a {nombre_completo} ({num_doc}): {e}")
            print("Se salta a la siguiente persona...")
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

    print("="*50)
    print("Cerrando navegador...")
    driver.quit()

if errores_no_manejados:
    print(f"\n{errores_no_manejados} persona(s) no se pudieron procesar por errores inesperados. Vuelve a correr este script para reintentarlas (los ya descargados se omiten automáticamente).")
    sys.exit(1)
