from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

# 1. Iniciar el navegador (Abre una ventana de Chrome)
# Nota: En versiones recientes, Selenium gestiona el driver automáticamente.
driver = webdriver.Chrome()

try:
    # 2. Navegar a la página web (Usamos una URL de ejemplo)
    print("Abriendo la página web...")
    driver.get("https://inhabilidades.policia.gov.co:8080/consulta")

    # 3. Encontrar los campos del formulario y llenarlos
    # Se usan selectores (ID, Name, o XPath) para ubicar la caja de texto en el código HTML
    campo_documento = driver.find_element(By.ID, "numero_cedula")
    campo_documento.send_keys("123456789")
    
    print("Datos llenados correctamente.")
    print("--------------------------------------------------")
    print("⏳ PAUSA ACTIVA: Por favor, resuelve el reCAPTCHA")
    print("y haz clic en 'Consultar' directamente en el navegador.")
    print("El script esperará hasta 5 minutos a que lo hagas...")
    print("--------------------------------------------------")

    # 4. LA PAUSA ACTIVA (Human-in-the-Loop)
    # Le decimos al script que espere un máximo de 300 segundos (5 minutos)
    # ¿Qué está esperando? A que aparezca un elemento que SOLO existe en la siguiente página
    # (por ejemplo, el botón para descargar el certificado o una tabla de resultados).
    
    elemento_resultado = WebDriverWait(driver, 300).until(
        EC.presence_of_element_located((By.ID, "boton_descargar_pdf"))
    )

    # Si llegamos a esta línea, significa que resolviste el puzzle y la página avanzó
    print("✅ ¡Página de resultados detectada! Reanudando el script...")

    # 5. Extraer la información o descargar
    texto_resultado = elemento_resultado.text
    print(f"Información encontrada: {texto_resultado}")

    # Aquí podrías poner el código para volver a iniciar el ciclo con el siguiente documento

except Exception as e:
    # Si pasan los 5 minutos y no se resolvió, o si la página cambió, arrojará un error
    print(f"Ocurrió un error o se agotó el tiempo de espera: {e}")

finally:
    # Cierra el navegador al finalizar, sin importar si falló o tuvo éxito
    print("Cerrando navegador...")
    driver.quit()