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
import PyPDF2
from twocaptcha import TwoCaptcha
from dotenv import load_dotenv

FRASE_LIMPIA = "NO SE ENCUENTRA REPORTADO COMO RESPONSABLE FISCAL"

# ==========================================
# 0. CARGAR CONFIGURACIÓN SEGURA (.env)
# ==========================================
load_dotenv()

API_KEY_2CAPTCHA = os.getenv("API_KEY_2CAPTCHA")
SITE_KEY_CONTRALORIA = "6LcfnjwUAAAAAIyl8ehhox7ZYqLQSVl_w1dmYIle"

if not API_KEY_2CAPTCHA:
    raise ValueError(
        "ERROR CRÍTICO: No se encontró la API_KEY_2CAPTCHA. "
        "Asegúrate de que el archivo .env existe en la misma carpeta del script "
        "y contiene la línea: API_KEY_2CAPTCHA=tu_clave_aqui"
    )

# Inicializar el servicio de 2Captcha
solver = TwoCaptcha(API_KEY_2CAPTCHA)

# ==========================================
# 1. FUNCIÓN DE AUDITORÍA RETROACTIVA
# ==========================================
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

def esperar_descarga(carpeta_destino, timeout=20):
    """
    Vigila la carpeta hasta que aparezca un nuevo PDF y retorna su ruta.
    """
    archivos_iniciales = set(os.listdir(carpeta_destino))
    tiempo_inicio = time.time()

    while time.time() - tiempo_inicio < timeout:
        archivos_actuales = set(os.listdir(carpeta_destino))
        archivos_nuevos = archivos_actuales - archivos_iniciales

        for archivo in archivos_nuevos:
            if archivo.endswith('.pdf') and not archivo.endswith('.crdownload'):
                time.sleep(1.5)
                return os.path.join(carpeta_destino, archivo)

        time.sleep(1)

    return None

# ==========================================
# 2. CONFIGURACIÓN Y RUTAS
# ==========================================
def obtener_ruta_excel():
    if len(sys.argv) > 1:
        return sys.argv[1]
    return input("Ruta del archivo Excel con la información de los postulantes: ").strip('"').strip()

ruta_excel = obtener_ruta_excel()
if not os.path.isfile(ruta_excel):
    raise FileNotFoundError(f"No se encontró el archivo: {ruta_excel}")

directorio_base = os.path.dirname(ruta_excel)
carpeta_destino = os.path.join(directorio_base, "Certificados_Contraloria")
carpeta_inhabilitados = os.path.join(directorio_base, "Certificados_Contraloria_INHABILITADOS")
os.makedirs(carpeta_destino, exist_ok=True)
os.makedirs(carpeta_inhabilitados, exist_ok=True)

print("Auditando certificados previamente descargados...")
alertas_historicas = auditar_descargas_anteriores(carpeta_destino, carpeta_inhabilitados)
if alertas_historicas:
    print(f"Se movieron {len(alertas_historicas)} certificados sospechosos a la carpeta de INHABILITADOS.")

print(f"Los PDF se guardarán automáticamente en:\n{carpeta_destino}")

print("\nLeyendo el archivo Excel...")
df = pd.read_excel(ruta_excel, sheet_name=0, header=28)
df.columns = df.columns.str.strip()
df = df.dropna(subset=['# DOC. IDENTIDAD'])

lista_alertas_finales = alertas_historicas.copy()

# ==========================================
# 3. CONFIGURAR DESCARGAS INVISIBLES EN CHROME
# ==========================================
opciones = webdriver.ChromeOptions()
prefs = {
    "download.default_directory": carpeta_destino,
    "download.prompt_for_download": False,
    "download.directory_upgrade": True,
    "plugins.always_open_pdf_externally": True
}
opciones.add_experimental_option("prefs", prefs)

# Iniciar navegador Chrome
driver = webdriver.Chrome(options=opciones)
wait = WebDriverWait(driver, 15)

errores_no_manejados = 0

try:
    for index, row in df.iterrows():
        num_doc = str(row['# DOC. IDENTIDAD']).strip()

        # Filtro: Saltar filas de subtítulos o que no inicien con un dígito
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
        # 4. VALIDACIÓN PREVIA (LOOK BEFORE YOU LEAP)
        # ==========================================
        nombre_limpio = "".join(c for c in nombre_completo if c.isalnum() or c in " -_").strip()
        nombre_archivo_esperado = f"Contraloria-{nombre_limpio}.pdf"
        ruta_esperada_normal = os.path.join(carpeta_destino, nombre_archivo_esperado)
        ruta_esperada_inhab = os.path.join(carpeta_inhabilitados, nombre_archivo_esperado)

        if os.path.exists(ruta_esperada_normal) or os.path.exists(ruta_esperada_inhab):
            print("El certificado ya existe en los registros. Se omite la descarga...")
            continue

        try:
            driver.get("https://www.contraloria.gov.co/web/guest/persona-natural")

            # ==========================================
            # 5. ENTRAR AL IFRAME Y EXTRAER SU URL REAL
            # ==========================================
            time.sleep(3)
            try:
                iframe_formulario = wait.until(EC.presence_of_element_located((By.XPATH, "//iframe[contains(@src, 'cfiscal')]")))
                url_real_iframe = iframe_formulario.get_attribute("src")
                driver.switch_to.frame(iframe_formulario)
            except Exception:
                print("No se detectó el iframe, se usa la URL principal...")
                url_real_iframe = driver.current_url

            # CAMPO: Tipo de documento
            elemento_tipo_doc = wait.until(EC.presence_of_element_located((By.ID, "ddlTipoDocumento")))
            selector_tipo_doc = Select(elemento_tipo_doc)

            if "CC" in tipo_doc_crudo.upper() or "CIUDADAN" in tipo_doc_crudo.upper():
                selector_tipo_doc.select_by_value("CC")
            elif "TI" in tipo_doc_crudo.upper() or "IDENTIDAD" in tipo_doc_crudo.upper():
                selector_tipo_doc.select_by_value("TI")
            elif "CX" in tipo_doc_crudo.upper() or "EXTRANJER" in tipo_doc_crudo.upper() or "CE" in tipo_doc_crudo.upper():
                selector_tipo_doc.select_by_value("CE")
            elif "PA" in tipo_doc_crudo.upper() or "PASAPORTE" in tipo_doc_crudo.upper():
                selector_tipo_doc.select_by_value("PA")
            else:
                selector_tipo_doc.select_by_value("CC")

            # CAMPO: Número de documento
            campo_documento = driver.find_element(By.ID, "txtNumeroDocumento")
            campo_documento.send_keys(num_doc)

            # ==========================================
            # 6. RESOLUCIÓN DE RECAPTCHA CON REINTENTOS
            # ==========================================
            intentos = 0
            max_intentos = 3
            captcha_resuelto = False

            while intentos < max_intentos and not captcha_resuelto:
                try:
                    print(f"Enviando reCAPTCHA a 2Captcha (intento {intentos + 1}/{max_intentos})...")
                    resultado = solver.recaptcha(sitekey=SITE_KEY_CONTRALORIA, url=url_real_iframe)
                    codigo_token = resultado['code']
                    print("reCAPTCHA resuelto correctamente.")

                    # Inyectar la respuesta del token
                    driver.execute_script(f"document.getElementById('g-recaptcha-response').innerHTML = '{codigo_token}';")
                    time.sleep(1)
                    captcha_resuelto = True

                except Exception as e:
                    intentos += 1
                    print(f"Error de red o conexión: {e}")
                    if intentos < max_intentos:
                        print("Reintentando en 5 segundos...")
                        time.sleep(5)

            if not captcha_resuelto:
                print(f"No fue posible resolver el captcha tras {max_intentos} intentos. Se salta a la siguiente persona...")
                driver.switch_to.default_content()
                continue

            # ==========================================
            # 7. EJECUTAR BÚSQUEDA Y ANALIZAR LA DESCARGA
            # ==========================================
            print("Datos y token listos. Consultando...")
            btn_buscar = wait.until(EC.element_to_be_clickable((By.ID, "btnBuscar")))
            driver.execute_script("arguments[0].click();", btn_buscar)

            driver.switch_to.default_content()

            print("Esperando la descarga automática del PDF...")
            ruta_descargada = esperar_descarga(carpeta_destino, timeout=45)

            if not ruta_descargada:
                print("Se agotó el tiempo de espera. El portal de la Contraloría no generó el archivo.")
                print("Vuelve a correr el script: esta persona se reintentará automáticamente.")
                errores_no_manejados += 1
                continue

            try:
                with open(ruta_descargada, 'rb') as f:
                    lector = PyPDF2.PdfReader(f)
                    texto_pdf = ""
                    for pagina in lector.pages:
                        texto_pdf += pagina.extract_text() or ""
                texto_limpio = "".join(texto_pdf.upper().split())
            except Exception:
                texto_limpio = ""

            frase_normalizada = "".join(FRASE_LIMPIA.split())

            if frase_normalizada in texto_limpio:
                ruta_final = ruta_esperada_normal
                print("Resultado limpio. Se guarda en la carpeta estándar...")
            else:
                ruta_final = ruta_esperada_inhab
                print("Atención: se detectó un posible reporte como responsable fiscal. Se guarda en la carpeta de alertas...")
                lista_alertas_finales.append(nombre_archivo_esperado.replace(".pdf", ""))
                import winsound
                winsound.Beep(2000, 1000)

            shutil.move(ruta_descargada, ruta_final)
            print("Documento procesado correctamente.")

        except Exception as e:
            print(f"Error inesperado procesando a {nombre_completo} ({num_doc}): {e}")
            print("Se salta a la siguiente persona...")
            errores_no_manejados += 1
            driver.switch_to.default_content()
            continue

except Exception as e:
    print(f"\nOcurrió un error inesperado durante el ciclo: {e}")
    sys.exit(1)

finally:
    print("\n" + "="*50)
    print("RESUMEN DE EJECUCIÓN Y ALERTAS")
    print("="*50)

    if lista_alertas_finales:
        print(f"Se detectaron {len(lista_alertas_finales)} registro(s) con posible responsabilidad fiscal:")
        for alerta in lista_alertas_finales:
            print(f"   - {alerta}")
        print(f"\nRevisa manualmente los documentos en la carpeta:\n{carpeta_inhabilitados}")
    else:
        print("No se encontraron registros de responsabilidad fiscal en esta tanda.")

    print("="*50)
    print("Cerrando navegador...")
    driver.quit()

if errores_no_manejados:
    print(f"\n{errores_no_manejados} persona(s) no se pudieron procesar por errores inesperados. Vuelve a correr este script para reintentarlas (los ya descargados se omiten automáticamente).")
    sys.exit(1)
