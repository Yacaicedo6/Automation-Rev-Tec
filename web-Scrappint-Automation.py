from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# 1. Iniciar el navegador
driver = webdriver.Chrome()

try:
    # 2. Navegar a la página web
    print("Abriendo la página web...")
    driver.get("https://inhabilidades.policia.gov.co:8080/consulta")

    # 3. Esperar a que el formulario cargue realmente
    print("Esperando a que cargue el formulario...")
    
    # IMPORTANTE: Debes cambiar "ID_DEL_CAMPO_DOCUMENTO" por el ID real.
    # (Para averiguarlo: clic derecho en el campo de la web -> Inspeccionar)
    campo_documento = WebDriverWait(driver, 15).until(
        EC.presence_of_element_located((By.ID, "ID_DEL_CAMPO_DOCUMENTO"))
    )
    
    # 4. Llenar los campos del formulario
    # Descomenta y ajusta los siguientes bloques una vez obtengas los IDs reales
    
    print("Llenando formulario...")
    campo_documento.send_keys("123456789")
    
    # -- Fecha de Expedición --
    # campo_fecha = driver.find_element(By.ID, "ID_DEL_CAMPO_FECHA")
    # campo_fecha.send_keys("01/01/2010") 
    
    # -- Empresa o Entidad Consultante --
    # campo_empresa = driver.find_element(By.ID, "ID_DEL_CAMPO_EMPRESA")
    # campo_empresa.send_keys("Nombre de tu Entidad")
    
    # -- NIT de la Empresa --
    # campo_nit = driver.find_element(By.ID, "ID_DEL_CAMPO_NIT")
    # campo_nit.send_keys("900000000-1")

    # -- Checkbox de Términos de Uso --
    # checkbox_terminos = driver.find_element(By.ID, "ID_DEL_CHECKBOX_TERMINOS")
    # checkbox_terminos.click()
    
    print("Datos automatizados llenados correctamente.")
    print("--------------------------------------------------")
    print("⏳ PAUSA ACTIVA (HUMANO REQUERIDO):")
    print("1. Resuelve el reCAPTCHA ('No soy un robot').")
    print("2. Haz clic en el botón de Consultar en el navegador.")
    print("El script esperará hasta 5 minutos a que lo hagas...")
    print("--------------------------------------------------")

    # 5. Esperar la página de resultados
    # Aquí debes poner el ID de algún elemento que SOLO aparezca cuando la consulta fue exitosa
    # (Ejemplo: el ID de la tabla de resultados o del botón de descarga)
    elemento_resultado = WebDriverWait(driver, 300).until(
        EC.presence_of_element_located((By.ID, "ID_DEL_ELEMENTO_RESULTADO"))
    )

    print("✅ ¡Página de resultados detectada! Reanudando el script...")

    # 6. Extraer la información
    texto_resultado = elemento_resultado.text
    print(f"Información encontrada:\n{texto_resultado}")

except Exception as e:
    print(f"Ocurrió un error o se agotó el tiempo de espera: {e}")

finally:
    print("Cerrando navegador...")
    driver.quit()