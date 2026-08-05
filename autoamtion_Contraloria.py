import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
import time
import os
from twocaptcha import TwoCaptcha
from dotenv import load_dotenv

# ==========================================
# 0. CARGAR CONFIGURACIÓN SEGURA (.env)
# ==========================================
load_dotenv()

API_KEY_2CAPTCHA = os.getenv("API_KEY_2CAPTCHA")
SITE_KEY_CONTRALORIA = "6LcfnjwUAAAAAIyl8ehhox7ZYqLQSVl_w1dmYIle"

if not API_KEY_2CAPTCHA:
    raise ValueError("❌ No se encontró la API_KEY_2CAPTCHA. Verifica que exista tu archivo .env")

# Inicializar el servicio de 2Captcha
solver = TwoCaptcha(API_KEY_2CAPTCHA)

# Ruta absoluta del archivo Excel
ruta_excel = r"E:\COMPUTADOR YAN\ALCALDIA DE CALI\2026\2026-2\ESTIMULOS\REV TEC ADMIN\REV TEC ADMIN MUNDIAL DE SALSA\ESTIMULO 004\GRUP CONF\DIEGO FERNANDO MUÑOZ ALVAREZ\30007-6a6cb92535970-ANEXOTECNICO2.INFORMACIONARTISTASFESTIVALMUNDIALDESALSA2026SOCIA.xlsx"

# Crear carpeta para los PDF descargados
directorio_base = os.path.dirname(ruta_excel)
carpeta_destino = os.path.join(directorio_base, "Certificados_Contraloria")
if not os.path.exists(carpeta_destino):
    os.makedirs(carpeta_destino)

print(f"Los PDF se guardarán automáticamente en:\n{carpeta_destino}")

print("\nLeyendo el archivo Excel...")
df = pd.read_excel(ruta_excel, sheet_name=0, header=28)
df.columns = df.columns.str.strip()
df = df.dropna(subset=['# DOC. IDENTIDAD'])

# ==========================================
# 1. CONFIGURAR DESCARGAS INVISIBLES EN CHROME
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
        
        driver.get("https://www.contraloria.gov.co/web/guest/persona-natural")
        
        # ==========================================
        # 2. ENTRAR AL IFRAME Y EXTRAER SU URL REAL
        # ==========================================
        time.sleep(3) 
        try:
            iframe_formulario = wait.until(EC.presence_of_element_located((By.XPATH, "//iframe[contains(@src, 'cfiscal')]")))
            
            # ¡NUEVO!: Extraemos la URL secreta del iframe para dársela a 2Captcha
            url_real_iframe = iframe_formulario.get_attribute("src")
            
            driver.switch_to.frame(iframe_formulario)
        except Exception as e:
            print("⚠️ No se detectó el iframe, usando URL principal...")
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
        # 3. RESOLUCIÓN AUTOMÁTICA DEL RECAPTCHA
        # ==========================================
        print("⏳ Enviando reCAPTCHA a 2Captcha con la URL correcta...")
        try:
            # ¡NUEVO!: Usamos 'url_real_iframe' en lugar de 'driver.current_url'
            resultado = solver.recaptcha(sitekey=SITE_KEY_CONTRALORIA, url=url_real_iframe)
            codigo_token = resultado['code']
            print("✅ reCAPTCHA resuelto con éxito.")
            
            # Inyectar la respuesta del token
            driver.execute_script(f"document.getElementById('g-recaptcha-response').innerHTML = '{codigo_token}';")
            time.sleep(1) 
            
        except Exception as e:
            print(f"⚠️ Error al resolver el Captcha mediante API: {e}")
            driver.switch_to.default_content() 
            continue 

        # ==========================================
        # 4. EJECUTAR BÚSQUEDA Y ESPERAR DESCARGA
        # ==========================================
        print("Datos y Token listos. Consultando...")
        btn_buscar = driver.find_element(By.ID, "btnBuscar")
        driver.execute_script("arguments[0].click();", btn_buscar)

        print("Esperando la descarga automática del PDF...")
        time.sleep(10) 

        print("✅ Documento procesado correctamente.")
        
        driver.switch_to.default_content()

except Exception as e:
    print(f"\n❌ Ocurrió un error inesperado durante el ciclo: {e}")

finally:
    print("\nCerrando navegador...")
    driver.quit()