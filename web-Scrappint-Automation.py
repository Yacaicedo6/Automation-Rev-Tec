from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time

# 1. Iniciar el navegador
driver = webdriver.Chrome()

try:
    # 2. Navegar a la página web
    print("Abriendo la página web...")
    driver.get("https://inhabilidades.policia.gov.co:8080/consulta")

    # Inicializamos una espera explícita de hasta 15 segundos para que cargue la web
    wait = WebDriverWait(driver, 15)

    print("Esperando a que cargue el formulario...")
    
    # 3. Llenar el formulario
    
    # CAMPO: Número de documento (Usamos wait.until para asegurar que la página ya cargó)
    campo_documento = wait.until(EC.presence_of_element_located((By.ID, "nuip")))
    campo_documento.send_keys("123456789") # <-- Reemplaza con la cédula real

    # CAMPO: Fecha de Expedición
    campo_fecha = driver.find_element(By.ID, "fechaExpNuip")
    campo_fecha.send_keys("01/01/2000") # <-- Reemplaza con la fecha real (DD/MM/AAAA)

    # CAMPO: Empresa o Entidad Consultante
    campo_empresa = driver.find_element(By.ID, "nombreEmpresa")
    campo_empresa.send_keys("Nombre de tu Entidad") # <-- Reemplaza con el nombre real

    # CAMPO: NIT de la Empresa
    # ⚠️ NOTA: Verifica que el ID del NIT sea realmente "nitEmpresa". Si no, cámbialo aquí.
    campo_nit = driver.find_element(By.ID, "nitEmpresa")
    campo_nit.send_keys("900000000") # <-- Reemplaza con el NIT real

    # CHECKBOX: Acepto los términos de uso y la política
    checkbox_terminos = driver.find_element(By.ID, "cbCondiciones")
    # Usamos JavaScript para hacer clic, ya que es un "custom-control-input" que suele fallar con .click() normal
    driver.execute_script("arguments[0].click();", checkbox_terminos)

    print("Datos llenados correctamente.")
    print("--------------------------------------------------")
    print("⏳ PAUSA ACTIVA: Por favor, resuelve el reCAPTCHA")
    print("y haz clic en 'Consultar' directamente en el navegador.")
    print("El script esperará hasta 5 minutos a que lo hagas...")
    print("--------------------------------------------------")

    # 4. LA PAUSA ACTIVA (Human-in-the-Loop)
    # Aquí el script espera a que la URL cambie (es decir, que se haya enviado el formulario exitosamente)
    # o a que aparezca un elemento de la página de resultados. 
    # Usaremos una espera basada en que el botón de consultar desaparezca tras hacer la consulta.
    
    WebDriverWait(driver, 300).until(
        EC.invisibility_of_element_located((By.ID, "btnConsultar"))
    )

    print("✅ ¡Consulta enviada y procesada! Reanudando el script...")

    # 5. Pausa final para que alcances a leer la terminal y ver la siguiente página
    # Aquí iría el código para extraer la información de la nueva pantalla o descargar el PDF
    time.sleep(10) 

except Exception as e:
    print(f"Ocurrió un error o se agotó el tiempo de espera: {e}")

finally:
    print("Cerrando navegador...")
    driver.quit()