import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
import time

# ==========================================
# 1. CONFIGURACIÓN Y LECTURA DEL EXCEL
# ==========================================
ruta_excel = r"ESTIMULO 004\GRUP CONF\DIEGO FERNANDO MUÑOZ ALVAREZ\30007-6a6cb92535970-ANEXOTECNICO2.INFORMACIONARTISTASFESTIVALMUNDIALDESALSA2026SOCIA.xlsx"

print("Leyendo el archivo Excel...")
# Leer la primera hoja, usando la fila 29 como encabezados (índice 28)
df = pd.read_excel(ruta_excel, sheet_name=0, header=28)

# Limpiar los nombres de las columnas para evitar errores de espacios invisibles
df.columns = df.columns.str.strip()

# Filtrar: Quitar las filas que no tengan Número de Documento (como las que dicen "MUJERES" o "HOMBRES")
df = df.dropna(subset=['# DOC. IDENTIDAD'])

# ==========================================
# 2. INICIAR NAVEGADOR Y AUTOMATIZACIÓN
# ==========================================
driver = webdriver.Chrome()
wait = WebDriverWait(driver, 15)

try:
    # 3. Iterar sobre cada persona en el Excel
    for index, row in df.iterrows():
        # Extracción y limpieza de datos
        tipo_doc_crudo = str(row['TIPO DOCUMENTO \n(RC - TI - PP)']).strip()
        num_doc = str(row['# DOC. IDENTIDAD']).strip()
        
        # Quitar decimales si Excel lo lee como número flotante (ej: 66915081.0 -> 66915081)
        if num_doc.endswith('.0'):
            num_doc = num_doc[:-2]
            
        # Formatear la fecha a DD/MM/AAAA
        try:
            fecha_obj = pd.to_datetime(row['FECHA DE EXPEDICION (DD/MM/AA)'])
            fecha_exp = fecha_obj.strftime('%d/%m/%Y')
        except:
            fecha_exp = str(row['FECHA DE EXPEDICION (DD/MM/AA)'])

        print(f"\n--- Procesando Documento: {num_doc} ({tipo_doc_crudo}) ---")
        
        # Navegar a la página limpia para cada registro
        driver.get("https://www.procuraduria.gov.co/Pages/Generacion-de-antecedentes.aspx")
        
        # CAMPO: Tipo de documento (Usando el ID "tipo")
        elemento_tipo_doc = wait.until(EC.presence_of_element_located((By.ID, "tipo")))
        selector_tipo_doc = Select(elemento_tipo_doc)
        
        # Seleccionar por 'value' según el HTML de la página
        if "CC" in tipo_doc_crudo.upper() or "CIUDADAN" in tipo_doc_crudo.upper():
            selector_tipo_doc.select_by_value("CC") 
        elif "CX" in tipo_doc_crudo.upper() or "EXTRANJER" in tipo_doc_crudo.upper() or "CE" in tipo_doc_crudo.upper():
            selector_tipo_doc.select_by_value("CX")
        elif "PA" in tipo_doc_crudo.upper() or "PASAPORTE" in tipo_doc_crudo.upper():
            selector_tipo_doc.select_by_value("PA")
        else:
            print(f"⚠️ Tipo de doc desconocido: {tipo_doc_crudo}. Intentando con Cédula de Ciudadanía.")
            selector_tipo_doc.select_by_value("CC")

        # CAMPO: Número de documento (ID "nuip")
        campo_documento = driver.find_element(By.ID, "nuip")
        campo_documento.send_keys(num_doc)

        # CAMPO: Fecha de Expedición (ID "fechaExpNuip")
        campo_fecha = driver.find_element(By.ID, "fechaExpNuip")
        campo_fecha.send_keys(fecha_exp)

        # CAMPO: Empresa o Entidad Consultante (ID "nombreEmpresa")
        campo_empresa = driver.find_element(By.ID, "nombreEmpresa")
        campo_empresa.send_keys("Alcaldia de Cali")

        # CAMPO: NIT de la Empresa (ID "nitEmpresa")
        campo_nit = driver.find_element(By.ID, "nitEmpresa")
        campo_nit.send_keys("8903990113")

        # CHECKBOX: Acepto los términos de uso (ID "cbCondiciones")
        checkbox_terminos = driver.find_element(By.ID, "cbCondiciones")
        driver.execute_script("arguments[0].click();", checkbox_terminos)

        print("Datos llenados correctamente. ⏳ Esperando que resuelvas el Captcha y des clic en 'Consultar'...")
        
        # ==========================================
        # 4. PAUSA ACTIVA (Human-in-the-Loop)
        # ==========================================
        # Espera hasta 5 minutos (300 seg) a que el botón consultar desaparezca
        WebDriverWait(driver, 300).until(
            EC.invisibility_of_element_located((By.ID, "btnConsultar"))
        )
        print(f"✅ ¡Consulta exitosa para el documento {num_doc}!")
        
        # Pausa breve para estabilizar el navegador antes de regresar al formulario para el siguiente
        time.sleep(3) 

except Exception as e:
    print(f"\n❌ Ocurrió un error inesperado durante el ciclo: {e}")

finally:
    print("\nCerrando navegador...")
    driver.quit()