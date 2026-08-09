import pandas as pd
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
from twocaptcha import TwoCaptcha
from dotenv import load_dotenv

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
# 0. CONFIGURACIÓN SEGURA
# ==========================================
load_dotenv()

API_KEY_2CAPTCHA = os.getenv("API_KEY_2CAPTCHA")
SITE_KEY_POLICIA = "6LflZLwUAAAAAP6-I_SuqVa1YDSTqfMyk43peb_M"

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
    Lee los PDF ya descargados. Si no dicen 'NO REGISTRA INHABILIDAD',
    los mueve a la carpeta de alertas.
    """
    alertas_encontradas = []

    # Recorrer los archivos en la carpeta normal
    for archivo in os.listdir(carpeta_origen):
        if archivo.endswith('.pdf'):
            ruta_pdf = os.path.join(carpeta_origen, archivo)

            try:
                # Leer el texto interno del PDF
                with open(ruta_pdf, 'rb') as f:
                    lector = PyPDF2.PdfReader(f)
                    texto_pdf = ""
                    for pagina in lector.pages:
                        texto_pdf += pagina.extract_text() or ""

                # Clasificar y mover si es necesario
                if "NO REGISTRA INHABILIDAD" not in texto_pdf.upper():
                    ruta_nueva = os.path.join(carpeta_alertas, archivo)
                    # Mover el archivo físicamente
                    shutil.move(ruta_pdf, ruta_nueva)
                    alertas_encontradas.append(archivo.replace(".pdf", ""))

            except Exception as e:
                print(f"No se pudo auditar el archivo {archivo}: {e}")

    return alertas_encontradas

def circuito_abierto(fallos_consecutivos, nombre_entidad="de Delitos Sexuales"):
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

directorio_base = os.path.dirname(ruta_datos)
carpeta_destino = os.path.join(directorio_base, "Cert_DSEX")
carpeta_inhabilitados = os.path.join(directorio_base, "Cert_DSEX_INHABILITADOS")

# Carpetas con el nombre largo que usaban las corridas anteriores a este cambio.
# Se siguen revisando para no volver a descargar (y gastar créditos de 2Captcha)
# lo que ya quedó guardado ahí.
carpeta_destino_vieja = os.path.join(directorio_base, "Certificados_Delitos_Sexuales")
carpeta_inhabilitados_vieja = os.path.join(directorio_base, "Certificados_Delitos_Sexuales_INHABILITADOS")

# Crear carpetas si no existen
os.makedirs(carpeta_destino, exist_ok=True)
os.makedirs(carpeta_inhabilitados, exist_ok=True)

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

# Lista global para reportar al final
lista_alertas_finales = alertas_historicas.copy()

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

        try:
            fecha_obj = pd.to_datetime(row['FECHA DE EXPEDICION (DD/MM/AA)'])
            fecha_exp = fecha_obj.strftime('%d/%m/%Y')
        except:
            fecha_exp = str(row['FECHA DE EXPEDICION (DD/MM/AA)'])

        print(f"\n--- Procesando Documento: {num_doc} ({nombre_completo}) ---")

        # ==========================================
        # 4. VALIDACIÓN PREVIA (LOOK BEFORE YOU LEAP)
        # ==========================================
        # Nombre y carpeta cortos para no exceder el límite de ruta de Windows.
        # También se reconocen la carpeta y el nombre largos de antes de este
        # cambio, para no volver a descargar lo que ya se había guardado ahí.
        primer_nombre_archivo = "".join(c for c in p_nombre.strip() if c.isalnum()) or "SN"
        nombre_archivo_esperado = f"DSEX_{primer_nombre_archivo}_{num_doc}.pdf"
        ruta_esperada_normal = os.path.join(carpeta_destino, nombre_archivo_esperado)
        ruta_esperada_inhab = os.path.join(carpeta_inhabilitados, nombre_archivo_esperado)

        nombre_limpio = "".join(c for c in nombre_completo if c.isalnum() or c in " -_").strip()
        nombre_archivo_viejo = f"DelitosSexuales-{nombre_limpio}.pdf"
        ruta_vieja_normal = os.path.join(carpeta_destino_vieja, nombre_archivo_viejo)
        ruta_vieja_inhab = os.path.join(carpeta_inhabilitados_vieja, nombre_archivo_viejo)

        # Validar en ambas carpetas y ambos formatos de nombre
        if any(os.path.exists(r) for r in (ruta_esperada_normal, ruta_esperada_inhab, ruta_vieja_normal, ruta_vieja_inhab)):
            print("El certificado ya existe en los registros. Se omite la descarga...")
            continue

        try:
            # ==========================================
            # 5. LLENAR FORMULARIO
            # ==========================================
            driver.get("https://inhabilidades.policia.gov.co:8080/consulta")
            time.sleep(2)

            elemento_tipo_doc = wait.until(EC.presence_of_element_located((By.ID, "tipo")))
            selector_tipo_doc = Select(elemento_tipo_doc)

            if "CC" in tipo_doc_crudo.upper() or "CIUDADAN" in tipo_doc_crudo.upper():
                selector_tipo_doc.select_by_value("CC")
            elif "CX" in tipo_doc_crudo.upper() or "EXTRANJER" in tipo_doc_crudo.upper() or "CE" in tipo_doc_crudo.upper():
                selector_tipo_doc.select_by_value("CX")
            elif "PA" in tipo_doc_crudo.upper() or "PASAPORTE" in tipo_doc_crudo.upper():
                selector_tipo_doc.select_by_value("PA")
            else:
                selector_tipo_doc.select_by_value("CC")

            driver.find_element(By.ID, "nuip").send_keys(num_doc)
            driver.find_element(By.ID, "fechaExpNuip").send_keys(fecha_exp)
            driver.find_element(By.ID, "nombreEmpresa").send_keys("Alcaldia de Cali")
            driver.find_element(By.ID, "nitEmpresa").send_keys("8903990113")

            checkbox_terminos = driver.find_element(By.ID, "cbCondiciones")
            driver.execute_script("arguments[0].click();", checkbox_terminos)

            # ==========================================
            # 6. RESOLVER RECAPTCHA
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
            # 7. BUCLE DE VALIDACIÓN INTERACTIVA
            # ==========================================
            exito_generacion = False
            intentos_validacion = 0

            while intentos_validacion < 2 and not exito_generacion:
                print("Enviando formulario...")
                btn_consultar = driver.find_element(By.ID, "btnConsultar")
                driver.execute_script("arguments[0].click();", btn_consultar)
                time.sleep(3)

                errores_visibles = False
                try:
                    alertas = driver.find_elements(By.XPATH, "//*[contains(@class, 'alert-danger') or contains(@class, 'error') or contains(@style, 'color:Red') or contains(@style, 'color: red')]")
                    for error in alertas:
                        texto_error = error.text.strip()
                        if error.is_displayed() and len(texto_error) > 2:
                            print(f"Alerta del portal detectada: {texto_error}")
                            errores_visibles = True
                            break
                except Exception:
                    pass

                if errores_visibles:
                    import winsound
                    winsound.Beep(1000, 500)
                    accion = input("Intento fallido. Revisa Chrome, corrige el error y presiona Enter para reintentar (o escribe 'saltar'): ")

                    if accion.lower() == 'saltar':
                        break
                    intentos_validacion += 1
                else:
                    exito_generacion = True

            if not exito_generacion:
                print("No se pudo superar la validación. Se salta a la siguiente persona...")
                fallos_consecutivos = 0  # hubo intervención humana; el portal está respondiendo
                continue

            # ==========================================
            # 8. ANÁLISIS DE RESULTADOS Y GENERACIÓN VÍA CDP
            # ==========================================
            print("Navegando a la pantalla de resultados...")

            try:
                xpath_botones = "//*[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'imprimir')]"
                wait.until(EC.presence_of_element_located((By.XPATH, xpath_botones)))
                time.sleep(2)

                # --- LECTURA INTELIGENTE DEL DOM ---
                texto_pantalla = driver.find_element(By.TAG_NAME, "body").text

                if "NO REGISTRA INHABILIDAD" in texto_pantalla.upper():
                    ruta_final_guardado = ruta_esperada_normal
                    print("Resultados limpios. Se guarda en la carpeta estándar...")
                else:
                    ruta_final_guardado = ruta_esperada_inhab
                    print("Atención: se detectó una posible inhabilidad. Se guarda en la carpeta de alertas...")
                    lista_alertas_finales.append(nombre_archivo_esperado.replace(".pdf", ""))
                    import winsound
                    winsound.Beep(2000, 1000)  # Un pitido más largo y agudo para alertarte al instante

                # Generar el PDF
                pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                    "printBackground": True,
                    "landscape": False,
                    "preferCSSPageSize": True
                })

                with open(ruta_final_guardado, "wb") as f:
                    f.write(base64.b64decode(pdf_data['data']))

                print("Documento guardado correctamente.")
                fallos_consecutivos = 0

            except Exception as e:
                print(f"Error inesperado al analizar o generar el PDF: {e}")
                errores_no_manejados += 1
                fallos_consecutivos += 1
                if circuito_abierto(fallos_consecutivos):
                    break

        except Exception as e:
            print(f"Error inesperado procesando a {nombre_completo} ({num_doc}): {e}")
            print("Se salta a la siguiente persona...")
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
        print(f"Se detectaron {len(lista_alertas_finales)} registros con posible inhabilidad:")
        for alerta in lista_alertas_finales:
            print(f"   - {alerta}")
        print(f"\nRevisa manualmente los documentos en la carpeta:\n{carpeta_inhabilitados}")
    else:
        print("No se encontraron registros de inhabilidad en esta tanda.")

    print("="*50)
    print("Cerrando navegador...")
    driver.quit()

if errores_no_manejados:
    print(f"\n{errores_no_manejados} persona(s) no se pudieron procesar por errores inesperados. Vuelve a correr este script para reintentarlas (los ya descargados se omiten automáticamente).")
    sys.exit(1)
