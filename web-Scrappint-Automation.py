import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select # Necesario para interactuar con listas desplegables
import time

# 1. Leer el archivo Excel
# Asegúrate de que el nombre del archivo y las columnas coincidan exactamente con tu Excel
ruta_excel = "Revisión técnico administrativa Ecosistema de la Salsa.xlsx"
df = pd.read_excel(ruta_excel) 

# 2. Iniciar el navegador
driver = webdriver.Chrome()
wait = WebDriverWait(driver, 15)

try:
    # 3. Iniciar el ciclo: Iterar sobre cada fila del DataFrame de Pandas
    for index, row in df.iterrows():
        tipo_doc = row['Tipo de documento']
        num_doc = row['Numero de documento']
        fecha_exp = row['Fecha de expedicion']
        
        print(f"\n--- Procesando registro {index + 1}: Documento {num_doc} ---")
        
        # Navegar a la página (lo hacemos en cada ciclo para tener un formulario limpio)
        driver.get("https://inhabilidades.policia.gov.co:8080/consulta")
        
        # CAMPO: Tipo de documento (Lista desplegable)
        # ⚠️ IMPORTANTE: Debes inspeccionar el ID real de este campo en la página
        elemento_tipo_doc = wait.until(EC.presence_of_element_located((By.ID, "REEMPLAZAR_CON_ID_TIPO_DOC")))
        selector_tipo_doc = Select(elemento_tipo_doc)
        # Selecciona la opción basándose en el texto visible que viene del Excel
        selector_tipo_doc.select_by_visible_text(str(tipo_doc)) 
        
        # CAMPO: Número de documento
        campo_documento = driver.find_element(By.ID, "nuip")
        campo_documento.send_keys(str(num_doc))

        # CAMPO: Fecha de Expedición
        campo_fecha = driver.find_element(By.ID, "fechaExpNuip")
        # Aseguramos que la fecha se envíe como texto. Si Pandas la lee con otro formato (ej. Timestamp), 
        # podrías necesitar formatearla así: row['Fecha de expedicion'].strftime('%d/%m/%Y')
        campo_fecha.send_keys(str(fecha_exp))

        # CAMPO: Empresa o Entidad Consultante (Dato Fijo)
        campo_empresa = driver.find_element(By.ID, "nombreEmpresa")
        campo_empresa.send_keys("Alcaldia de Cali")

        # CAMPO: NIT de la Empresa (Dato Fijo)
        campo_nit = driver.find_element(By.ID, "nitEmpresa") # Verifica si este ID es correcto
        campo_nit.send_keys("8903990113")

        # CHECKBOX: Acepto los términos de uso
        checkbox_terminos = driver.find_element(By.ID, "cbCondiciones")
        driver.execute_script("arguments[0].click();", checkbox_terminos)

        print("Datos llenados correctamente.")
        print("⏳ PAUSA ACTIVA: Resuelve el reCAPTCHA y haz clic en 'Consultar'...")
        
        # 4. PAUSA ACTIVA
        # Espera hasta 5 minutos a que el botón de consultar desaparezca (indicando que avanzó)
        WebDriverWait(driver, 300).until(
            EC.invisibility_of_element_located((By.ID, "btnConsultar"))
        )
        print(f"✅ ¡Consulta exitosa para el documento {num_doc}!")
        
        # Aquí iría el código para extraer la información de la pantalla de resultados
        # o descargar el PDF antes de que el ciclo vuelva a empezar.
        
        time.sleep(3) # Pausa breve para estabilizar antes de la siguiente consulta

except Exception as e:
    print(f"\n❌ Ocurrió un error en el ciclo: {e}")

finally:
    print("Cerrando navegador...")
    driver.quit()