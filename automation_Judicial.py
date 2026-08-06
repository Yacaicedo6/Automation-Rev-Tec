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
import PyPDF2
from twocaptcha import TwoCaptcha
from dotenv import load_dotenv

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

# ==========================================
# 2. RUTAS Y LECTURA DE DATOS
# ==========================================
def obtener_ruta_excel():
    if len(sys.argv) > 1:
        return sys.argv[1]
    return input("Ruta del archivo Excel con la información de los postulantes: ").strip('"').strip()

ruta_excel = obtener_ruta_excel()
if not os.path.isfile(ruta_excel):
    raise FileNotFoundError(f"No se encontró el archivo: {ruta_excel}")

directorio_base = os.path.dirname(ruta_excel)
carpeta_destino = os.path.join(directorio_base, "Certificados_Policia")
carpeta_inhabilitados = os.path.join(directorio_base, "Certificados_Policia_INHABILITADOS")

# Crear carpetas si no existen
os.makedirs(carpeta_destino, exist_ok=True)
os.makedirs(carpeta_inhabilitados, exist_ok=True)

# Ejecutar auditoría antes de iniciar la automatización
print("Auditando certificados previamente descargados...")
alertas_historicas = auditar_descargas_anteriores(carpeta_destino, carpeta_inhabilitados)
if alertas_historicas:
    print(f"Se movieron {len(alertas_historicas)} certificados sospechosos a la carpeta de INHABILITADOS.")

print("\nLeyendo el archivo Excel...")
df = pd.read_excel(ruta_excel, sheet_name=0, header=28)
df.columns = df.columns.str.strip()
df = df.dropna(subset=['# DOC. IDENTIDAD'])

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

        print(f"\n--- Procesando Documento: {num_doc} ({nombre_completo}) ---")

        # ==========================================
        # 4. VALIDACIÓN PREVIA (LOOK BEFORE YOU LEAP)
        # ==========================================
        nombre_limpio = "".join(c for c in nombre_completo if c.isalnum() or c in " -_").strip()
        nombre_archivo_esperado = f"Policia-{nombre_limpio}.pdf"

        ruta_esperada_normal = os.path.join(carpeta_destino, nombre_archivo_esperado)
        ruta_esperada_inhab = os.path.join(carpeta_inhabilitados, nombre_archivo_esperado)

        if os.path.exists(ruta_esperada_normal) or os.path.exists(ruta_esperada_inhab):
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
            # 9. ANÁLISIS DE RESULTADOS Y GENERACIÓN VÍA CDP
            # ==========================================
            print("Analizando pantalla de resultados...")

            try:
                # Pausa para dar tiempo a que cargue el texto de respuesta
                time.sleep(3)
                texto_pantalla = driver.find_element(By.TAG_NAME, "body").text

                if "NO TIENE ASUNTOS PENDIENTES" in texto_pantalla.upper():
                    ruta_final_guardado = ruta_esperada_normal
                    print("Resultados limpios. Se guarda en la carpeta estándar...")
                else:
                    ruta_final_guardado = ruta_esperada_inhab
                    print("Atención: se detectó un posible asunto pendiente. Se guarda en la carpeta de alertas...")
                    lista_alertas_finales.append(nombre_archivo_esperado.replace(".pdf", ""))
                    import winsound
                    winsound.Beep(2000, 1000)

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
        print(f"Se detectaron {len(lista_alertas_finales)} registros con asuntos pendientes:")
        for alerta in lista_alertas_finales:
            print(f"   - {alerta}")
        print(f"\nRevisa manualmente los documentos en la carpeta:\n{carpeta_inhabilitados}")
    else:
        print("No se encontraron registros con asuntos pendientes en esta tanda.")

    print("="*50)
    print("Cerrando navegador...")
    driver.quit()

if errores_no_manejados:
    print(f"\n{errores_no_manejados} persona(s) no se pudieron procesar por errores inesperados. Vuelve a correr este script para reintentarlas (los ya descargados se omiten automáticamente).")
    sys.exit(1)
