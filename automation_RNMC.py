import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
import time
import base64
import os

# ==========================================
# 1. CONFIGURACIÓN Y LECTURA DEL EXCEL
# ==========================================
# ⚠️ IMPORTANTE: Pon la ruta absoluta completa para que la carpeta se cree ahí mismo
ruta_excel = r"E:\COMPUTADOR YAN\ALCALDIA DE CALI\2026\2026-2\ESTIMULOS\REV TEC ADMIN\REV TEC ADMIN MUNDIAL DE SALSA\ESTIMULO 004\GRUP CONF\DIEGO FERNANDO MUÑOZ ALVAREZ\30007-6a6cb92535970-ANEXOTECNICO2.INFORMACIONARTISTASFESTIVALMUNDIALDESALSA2026SOCIA.xlsx"

# Crear carpeta para los PDF en el MISMO lugar donde está el Excel
directorio_base = os.path.dirname(ruta_excel)
carpeta_destino = os.path.join(directorio_base, "Certificados_RNMC")
if not os.path.exists(carpeta_destino):
    os.makedirs(carpeta_destino)

print(f"Los PDF se guardarán en: {carpeta_destino}")

print("Leyendo el archivo Excel...")
df = pd.read_excel(ruta_excel, sheet_name=0, header=28)
df.columns = df.columns.str.strip()
df = df.dropna(subset=['# DOC. IDENTIDAD'])

# ==========================================
# 2. INICIAR NAVEGADOR Y AUTOMATIZACIÓN
# ==========================================
driver = webdriver.Chrome()
wait = WebDriverWait(driver, 15)

try:
    for index, row in df.iterrows():
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
        except:
            fecha_exp = str(row['FECHA DE EXPEDICION (DD/MM/AA)'])

        p_nombre = str(row['PRIMER NOMBRE']) if pd.notna(row['PRIMER NOMBRE']) else ""
        s_nombre = str(row['SEGUNDO NOMBRE']) if pd.notna(row['SEGUNDO NOMBRE']) else ""
        p_apellido = str(row['PRIMER APELLIDO']) if pd.notna(row['PRIMER APELLIDO']) else ""
        s_apellido = str(row['SEGUNDO APELLIDO']) if pd.notna(row['SEGUNDO APELLIDO']) else ""
        nombre_completo = f"{p_nombre} {s_nombre} {p_apellido} {s_apellido}".replace("  ", " ").strip()

        print(f"\n--- Procesando Documento: {num_doc} ({nombre_completo}) ---")
        
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
        except Exception as e:
            pass


        print("Datos llenados. Consultando...")
        btn_buscar = driver.find_element(By.ID, "ctl00_ContentPlaceHolder3_btnConsultar2")
        driver.execute_script("arguments[0].click();", btn_buscar)

        try:
            # Esperamos el resultado exitoso
            wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'informa:')]")))
            time.sleep(2) 
            
            pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                "printBackground": True,
                "landscape": False
            })
            
            nombre_archivo = f"{nombre_completo} - {num_doc} - RNMC.pdf"
            ruta_guardado = os.path.join(carpeta_destino, nombre_archivo)
            
            with open(ruta_guardado, "wb") as file:
                file.write(base64.b64decode(pdf_data['data']))
                
            print(f"✅ ¡Éxito! PDF guardado")
            
        except Exception as e:
            print(f"⚠️ Fallo en el resultado de {num_doc}. Guardando captura de pantalla del error...")
            # Tomar captura de pantalla de lo que sea que esté mostrando la página (errores, multas, etc.)
            nombre_error = f"ERROR_{nombre_completo}_{num_doc}.png"
            ruta_error = os.path.join(carpeta_destino, nombre_error)
            driver.save_screenshot(ruta_error)
        
        time.sleep(1)

except Exception as e:
    print(f"\n❌ Ocurrió un error inesperado durante el ciclo: {e}")

finally:
    print("\nCerrando navegador...")
    driver.quit()