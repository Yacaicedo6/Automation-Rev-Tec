import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
import time
import os
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
SITE_KEY_POLICIA = "6LflZLwUAAAAAP6-I_SuqVa1YDSTqfMyk43peb_M"

if not API_KEY_2CAPTCHA:
    raise ValueError(
        "❌ ERROR CRÍTICO: No se encontró la API_KEY_2CAPTCHA. "
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
                print(f"⚠️ No se pudo auditar el archivo {archivo}: {e}")
                
    return alertas_encontradas

# ==========================================
# 2. RUTAS Y LECTURA DE DATOS
# ==========================================
ruta_excel = r"E:\COMPUTADOR YAN\ALCALDIA DE CALI\2026\2026-2\ESTIMULOS\REV TEC ADMIN\REV TEC ADMIN MUNDIAL DE SALSA\ESTIMULO 004\GRUP CONF\DIEGO FERNANDO MUÑOZ ALVAREZ\30007-6a6cb92535970-ANEXOTECNICO2.INFORMACIONARTISTASFESTIVALMUNDIALDESALSA2026SOCIA.xlsx"

directorio_base = os.path.dirname(ruta_excel)
carpeta_destino = os.path.join(directorio_base, "Certificados_Delitos_Sexuales")
carpeta_inhabilitados = os.path.join(directorio_base, "Certificados_Delitos_Sexuales_INHABILITADOS")

# Crear carpetas si no existen
os.makedirs(carpeta_destino, exist_ok=True)
os.makedirs(carpeta_inhabilitados, exist_ok=True)

# Ejecutar auditoría antes de iniciar la automatización
print("🔍 Auditando certificados previamente descargados...")
alertas_historicas = auditar_descargas_anteriores(carpeta_destino, carpeta_inhabilitados)
if alertas_historicas:
    print(f"⚠️ Se movieron {len(alertas_historicas)} certificados sospechosos a la carpeta de INHABILITADOS.")

print("\nLeyendo el archivo Excel...")
df = pd.read_excel(ruta_excel, sheet_name=0, header=28)
df.columns = df.columns.str.strip()
df = df.dropna(subset=['# DOC. IDENTIDAD'])

# Lista global para reportar al final
lista_alertas_finales = alertas_historicas.copy()

# ==========================================
# 3. CONFIGURAR NAVEGADOR
# ==========================================
opciones = webdriver.ChromeOptions()
opciones.add_argument("--ignore-certificate-errors") 

driver = webdriver.Chrome(options=opciones)
wait = WebDriverWait(driver, 15)

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
        nombre_limpio = "".join(c for c in nombre_completo if c.isalnum() or c in " -_").strip()
        nombre_archivo_esperado = f"DelitosSexuales-{nombre_limpio}.pdf"
        
        ruta_esperada_normal = os.path.join(carpeta_destino, nombre_archivo_esperado)
        ruta_esperada_inhab = os.path.join(carpeta_inhabilitados, nombre_archivo_esperado)
        
        # Validar en ambas carpetas
        if os.path.exists(ruta_esperada_normal) or os.path.exists(ruta_esperada_inhab):
            print(f"⏭️ El certificado ya existe en los registros. Omitiendo descarga...")
            continue

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
                print(f"⏳ Enviando reCAPTCHA a 2Captcha (Intento {intentos + 1}/{max_intentos})...")
                resultado = solver.recaptcha(sitekey=SITE_KEY_POLICIA, url=driver.current_url)
                codigo_token = resultado['code']
                print("✅ reCAPTCHA resuelto con éxito.")
                
                driver.execute_script(f"document.getElementById('g-recaptcha-response').innerHTML = '{codigo_token}';")
                driver.execute_script("document.getElementById('g-recaptcha-response').dispatchEvent(new Event('change'));")
                
                time.sleep(1) 
                captcha_resuelto = True
                
            except Exception as e:
                intentos += 1
                print(f"⚠️ Error de red/conexión con la API: {e}")
                if intentos < max_intentos:
                    time.sleep(5)

        if not captcha_resuelto:
            print(f"❌ Imposible resolver Captcha tras {max_intentos} intentos. Saltando persona...")
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
                        print(f"⚠️ Alerta del portal detectada: {texto_error}")
                        errores_visibles = True
                        break
            except Exception:
                pass

            if errores_visibles:
                import winsound
                winsound.Beep(1000, 500)
                accion = input("⚠️ Intento fallido. Revisa Chrome, corrige el error y presiona Enter para reintentar (o escribe 'saltar'): ")
                
                if accion.lower() == 'saltar':
                    break
                intentos_validacion += 1
            else:
                exito_generacion = True
        
        if not exito_generacion:
            print("❌ No se pudo superar la validación. Saltando a la siguiente persona...")
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
                print("✅ Resultados limpios. Guardando en carpeta estándar...")
            else:
                ruta_final_guardado = ruta_esperada_inhab
                print("🚨 ¡ATENCIÓN! Se detectó una posible inhabilidad. Guardando en carpeta de alertas...")
                lista_alertas_finales.append(nombre_archivo_esperado.replace(".pdf", ""))
                import winsound
                winsound.Beep(2000, 1000) # Un pitido más largo y agudo para alertarte al instante
            
            # Generar el PDF
            pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                "printBackground": True,
                "landscape": False,
                "preferCSSPageSize": True
            })
            
            with open(ruta_final_guardado, "wb") as f:
                f.write(base64.b64decode(pdf_data['data']))
                
            print(f"✅ ¡Documento guardado silenciosamente!")
                
        except Exception as e:
            print(f"⚠️ Error inesperado al analizar/generar el PDF: {e}")

except Exception as e:
    print(f"\n❌ Ocurrió un error inesperado durante el ciclo general: {e}")

finally:
    print("\n" + "="*50)
    print("🚦 RESUMEN DE EJECUCIÓN Y ALERTAS 🚦")
    print("="*50)
    
    if lista_alertas_finales:
        print(f"⚠️ SE DETECTARON {len(lista_alertas_finales)} REGISTROS CON POSIBLE INHABILIDAD:")
        for alerta in lista_alertas_finales:
            print(f"   - {alerta}")
        print(f"\nRevisa manualmente los documentos en la carpeta:\n{carpeta_inhabilitados}")
    else:
        print("✅ Todo excelente. No se encontraron registros de inhabilidad en esta tanda.")
        
    print("="*50)
    print("Cerrando navegador...")
    driver.quit()