import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select
import time
import base64
import os
import sys
import shutil
import PyPDF2

FRASE_LIMPIA = "NO TIENE MEDIDAS CORRECTIVAS PENDIENTES POR CUMPLIR"

# ==========================================
# 0. FUNCIÓN DE AUDITORÍA RETROACTIVA
# ==========================================
def auditar_descargas_anteriores(carpeta_origen, carpeta_alertas):
    """
    Lee los PDF ya descargados. Si no contienen la frase de resultado limpio,
    los mueve a la carpeta de alertas.
    """
    alertas_encontradas = []
    frase_normalizada = "".join(FRASE_LIMPIA.split())

    for archivo in os.listdir(carpeta_origen):
        if archivo.endswith('.pdf'):
            ruta_pdf = os.path.join(carpeta_origen, archivo)

            try:
                with open(ruta_pdf, 'rb') as f:
                    lector = PyPDF2.PdfReader(f)
                    texto_pdf = ""
                    for pagina in lector.pages:
                        texto_pdf += pagina.extract_text() or ""

                texto_limpio = "".join(texto_pdf.upper().split())

                if frase_normalizada not in texto_limpio:
                    ruta_nueva = os.path.join(carpeta_alertas, archivo)
                    shutil.move(ruta_pdf, ruta_nueva)
                    alertas_encontradas.append(archivo.replace(".pdf", ""))

            except Exception as e:
                print(f"No se pudo auditar el archivo {archivo}: {e}")

    return alertas_encontradas

# ==========================================
# 1. CONFIGURACIÓN Y LECTURA DEL EXCEL
# ==========================================
def obtener_ruta_excel():
    if len(sys.argv) > 1:
        return sys.argv[1]
    return input("Ruta del archivo Excel con la información de los postulantes: ").strip('"').strip()

ruta_excel = obtener_ruta_excel()
if not os.path.isfile(ruta_excel):
    raise FileNotFoundError(f"No se encontró el archivo: {ruta_excel}")

directorio_base = os.path.dirname(ruta_excel)
carpeta_destino = os.path.join(directorio_base, "Certificados_RNMC")
carpeta_inhabilitados = os.path.join(directorio_base, "Certificados_RNMC_INHABILITADOS")
os.makedirs(carpeta_destino, exist_ok=True)
os.makedirs(carpeta_inhabilitados, exist_ok=True)

print("Auditando certificados previamente descargados...")
alertas_historicas = auditar_descargas_anteriores(carpeta_destino, carpeta_inhabilitados)
if alertas_historicas:
    print(f"Se movieron {len(alertas_historicas)} certificados sospechosos a la carpeta de INHABILITADOS.")

print(f"Los PDF se guardarán en: {carpeta_destino}")

print("\nLeyendo el archivo Excel...")
df = pd.read_excel(ruta_excel, sheet_name=0, header=28)
df.columns = df.columns.str.strip()
df = df.dropna(subset=['# DOC. IDENTIDAD'])

lista_alertas_finales = alertas_historicas.copy()

# ==========================================
# 2. INICIAR NAVEGADOR Y AUTOMATIZACIÓN
# ==========================================
driver = webdriver.Chrome()
wait = WebDriverWait(driver, 15)

errores_no_manejados = 0

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

        # ==========================================
        # 3. VALIDACIÓN PREVIA (LOOK BEFORE YOU LEAP)
        # ==========================================
        nombre_archivo_esperado = f"{nombre_completo} - {num_doc} - RNMC.pdf"
        ruta_esperada_normal = os.path.join(carpeta_destino, nombre_archivo_esperado)
        ruta_esperada_inhab = os.path.join(carpeta_inhabilitados, nombre_archivo_esperado)

        if os.path.exists(ruta_esperada_normal) or os.path.exists(ruta_esperada_inhab):
            print("El certificado ya existe en los registros. Se omite la descarga...")
            continue

        try:
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
            except Exception:
                pass

            print("Datos llenados. Consultando...")
            btn_buscar = driver.find_element(By.ID, "ctl00_ContentPlaceHolder3_btnConsultar2")
            driver.execute_script("arguments[0].click();", btn_buscar)

            # ==========================================
            # 4. ANÁLISIS DE RESULTADOS Y GENERACIÓN VÍA CDP
            # ==========================================
            try:
                wait.until(EC.presence_of_element_located((By.XPATH, "//*[contains(text(), 'informa:')]")))
                time.sleep(2)

                texto_pantalla = driver.find_element(By.TAG_NAME, "body").text

                if FRASE_LIMPIA in texto_pantalla.upper():
                    ruta_final_guardado = ruta_esperada_normal
                    print("Resultado limpio. Se guarda en la carpeta estándar...")
                else:
                    ruta_final_guardado = ruta_esperada_inhab
                    print("Atención: se detectó una posible medida correctiva pendiente. Se guarda en la carpeta de alertas...")
                    lista_alertas_finales.append(nombre_archivo_esperado.replace(".pdf", ""))
                    import winsound
                    winsound.Beep(2000, 1000)

                pdf_data = driver.execute_cdp_cmd("Page.printToPDF", {
                    "printBackground": True,
                    "landscape": False
                })

                with open(ruta_final_guardado, "wb") as file:
                    file.write(base64.b64decode(pdf_data['data']))

                print("PDF guardado correctamente.")

            except Exception:
                print(f"No se pudo procesar el resultado de {num_doc}. Se guarda una captura de pantalla en la carpeta de alertas para revisión manual...")
                nombre_error = f"ERROR_{nombre_completo}_{num_doc}.png"
                ruta_error = os.path.join(carpeta_inhabilitados, nombre_error)
                driver.save_screenshot(ruta_error)
                lista_alertas_finales.append(nombre_error.replace(".png", ""))

        except Exception as e:
            print(f"Error inesperado procesando a {nombre_completo} ({num_doc}): {e}")
            print("Se salta a la siguiente persona...")
            errores_no_manejados += 1
            continue

        time.sleep(1)

except Exception as e:
    print(f"\nOcurrió un error inesperado durante el ciclo: {e}")
    sys.exit(1)

finally:
    print("\n" + "="*50)
    print("RESUMEN DE EJECUCIÓN Y ALERTAS")
    print("="*50)

    if lista_alertas_finales:
        print(f"Se detectaron {len(lista_alertas_finales)} registro(s) con posible medida correctiva o error de consulta:")
        for alerta in lista_alertas_finales:
            print(f"   - {alerta}")
        print(f"\nRevisa manualmente los documentos en la carpeta:\n{carpeta_inhabilitados}")
    else:
        print("No se encontraron medidas correctivas pendientes en esta tanda.")

    print("="*50)
    print("Cerrando navegador...")
    driver.quit()

if errores_no_manejados:
    print(f"\n{errores_no_manejados} persona(s) no se pudieron procesar por errores inesperados. Vuelve a correr este script para reintentarlas (los ya descargados se omiten automáticamente).")
    sys.exit(1)
