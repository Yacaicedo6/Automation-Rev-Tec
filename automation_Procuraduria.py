import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
import time
import os
import unicodedata
import re

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
                    print(f"⚠️ Error al renombrar el archivo: {e}")
                    return False
        
        time.sleep(1)
        
    return False

# ==========================================
# 1. CONFIGURACIÓN Y RUTAS
# ==========================================
ruta_excel = r"E:\COMPUTADOR YAN\ALCALDIA DE CALI\2026\2026-2\ESTIMULOS\REV TEC ADMIN\REV TEC ADMIN MUNDIAL DE SALSA\ESTIMULO 004\GRUP CONF\DIEGO FERNANDO MUÑOZ ALVAREZ\30007-6a6cb92535970-ANEXOTECNICO2.INFORMACIONARTISTASFESTIVALMUNDIALDESALSA2026SOCIA.xlsx"

directorio_base = os.path.dirname(ruta_excel)
carpeta_destino = os.path.join(directorio_base, "Certificados_Procuraduria")
if not os.path.exists(carpeta_destino):
    os.makedirs(carpeta_destino)

print(f"Los PDF se guardarán y renombrarán automáticamente en:\n{carpeta_destino}")

print("\nLeyendo el archivo Excel...")
df = pd.read_excel(ruta_excel, sheet_name=0, header=28)
df.columns = df.columns.str.strip()
df = df.dropna(subset=['# DOC. IDENTIDAD'])

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
        nombre_limpio = "".join(c for c in nombre_completo if c.isalnum() or c in " -_").strip()
        nombre_archivo_esperado = f"Procuraduria-{nombre_limpio}.pdf"
        ruta_esperada = os.path.join(carpeta_destino, nombre_archivo_esperado)
        
        if os.path.exists(ruta_esperada):
            print(f"⏭️ El certificado ya existe en la carpeta. Omitiendo descarga...")
            continue
        
        # ==========================================
        # 4. MANEJO DE PÁGINA Y FORMULARIO
        # ==========================================
        driver.get("https://www.procuraduria.gov.co/Pages/Generacion-de-antecedentes.aspx")
        time.sleep(4) 
        
        try:
            iframes = driver.find_elements(By.TAG_NAME, "iframe")
            if len(iframes) > 0:
                print("🔄 Iframe detectado en la página. Cambiando el contexto...")
                driver.switch_to.frame(iframes[0])
                time.sleep(1)
        except Exception as e:
            pass 

        try:
            elemento_tipo_doc = wait.until(EC.presence_of_element_located((By.ID, "ddlTipoID")))
            selector_tipo_doc = Select(elemento_tipo_doc)
        except Exception as e:
            print("❌ El formulario no cargó a tiempo o la página está saturada. Saltando registro...")
            driver.switch_to.default_content()
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
            respuesta_calculada = input("⚠️ ¡Pregunta desconocida! Escribe la respuesta aquí en la consola y presiona Enter: ")

        campo_respuesta = driver.find_element(By.ID, "txtRespuestaPregunta")
        campo_respuesta.send_keys(respuesta_calculada)
        print(f"✅ Respuesta ingresada: {respuesta_calculada}")

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
                        print(f"⚠️ Alerta del portal detectada: {error.text.strip()}")
                        errores_visibles = True
                        break
            except Exception:
                pass

            if errores_visibles:
                import winsound
                winsound.Beep(1000, 500)
                nueva_respuesta = input("⚠️ Intento fallido. Ingresa la respuesta correcta (o escribe 'saltar' para omitir persona): ")
                
                if nueva_respuesta.lower() == 'saltar':
                    break
                    
                campo_respuesta = driver.find_element(By.ID, "txtRespuestaPregunta")
                campo_respuesta.clear()
                campo_respuesta.send_keys(nueva_respuesta)
                intentos_validacion += 1
            else:
                exito_generacion = True
        
        if not exito_generacion:
            print("❌ No se pudo superar la validación. Saltando a la siguiente persona...")
            driver.switch_to.default_content()
            continue

        # ==========================================
        # 7. GENERAR, DESCARGAR Y RENOMBRAR
        # ==========================================
        print("Navegando a la pantalla de descarga...")
        time.sleep(5) 
        
        try:
            xpath_boton_descarga = """
            //a[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'descargar') and not(contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'descargue'))] | 
            //input[contains(translate(@value, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'descargar')] | 
            //input[contains(translate(@src, 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'descargar')]
            """
            
            btn_descarga = wait.until(EC.element_to_be_clickable((By.XPATH, xpath_boton_descarga)))
            
            try:
                btn_descarga.click()
                print("✅ Clic físico en el botón Descargar exitoso.")
            except Exception as e:
                driver.execute_script("arguments[0].click();", btn_descarga)
                print("✅ Clic forzado (JS) en el botón Descargar exitoso.")
            
            print("Esperando a que el archivo se guarde y renombrando...")
            if esperar_y_renombrar_descarga(carpeta_destino, nombre_archivo_esperado, timeout=30):
                print(f"✅ ¡Documento guardado como '{nombre_archivo_esperado}'!")
            else:
                print("⚠️ Se agotó el tiempo de espera. El servidor de la Procuraduría no generó el archivo.")
                
        except Exception as e:
            print("⚠️ No se detectó el botón de descarga en la segunda pantalla. Revisando carpeta...")
            if esperar_y_renombrar_descarga(carpeta_destino, nombre_archivo_esperado, timeout=10):
                print(f"✅ ¡Documento guardado directamente como '{nombre_archivo_esperado}'!")
            else:
                print("⚠️ No se pudo procesar la descarga de este documento.")

        driver.switch_to.default_content() 

except Exception as e:
    print(f"\n❌ Ocurrió un error inesperado durante el ciclo general: {e}")

finally:
    print("\nCerrando navegador...")
    driver.quit()