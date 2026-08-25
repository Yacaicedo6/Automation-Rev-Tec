import pandas as pd
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
import time
import os
import sys
import shutil
import re
import PyPDF2
from twocaptcha import TwoCaptcha
from dotenv import load_dotenv

# Misma frase que usa el portal de Contraloría para persona natural -- es la
# misma familia de certificado (responsabilidad fiscal). NO se ha confirmado
# todavía contra un resultado real de la página de persona jurídica: revisa
# el primer resultado con cuidado antes de confiar del todo en esta
# clasificación (si el texto real es distinto, avisa para ajustarla).
FRASE_LIMPIA = "NO SE ENCUENTRA REPORTADO COMO RESPONSABLE FISCAL"

# ==========================================
# 0. CARGAR CONFIGURACIÓN SEGURA (.env)
# ==========================================
load_dotenv()

API_KEY_2CAPTCHA = os.getenv("API_KEY_2CAPTCHA")
# Mismo sitekey que la página de persona natural -- ambas están registradas
# contra cfiscal.contraloria.gov.co, aunque esta se vea en otra URL.
SITE_KEY_CONTRALORIA = "6LcfnjwUAAAAAIyl8ehhox7ZYqLQSVl_w1dmYIle"

if not API_KEY_2CAPTCHA:
    raise ValueError(
        "ERROR CRÍTICO: No se encontró la API_KEY_2CAPTCHA. "
        "Asegúrate de que el archivo .env existe en la misma carpeta del script "
        "y contiene la línea: API_KEY_2CAPTCHA=tu_clave_aqui"
    )

solver = TwoCaptcha(API_KEY_2CAPTCHA)


# ==========================================
# 1. FUNCIÓN DE AUDITORÍA RETROACTIVA
# ==========================================
def auditar_descargas_anteriores(carpeta_origen, carpeta_alertas):
    """
    Lee los PDF ya descargados. Si no contienen la frase de resultado limpio,
    los mueve a la carpeta de alertas.
    """
    alertas_encontradas = []
    frase_normalizada = "".join(FRASE_LIMPIA.split())

    if not os.path.isdir(carpeta_origen):
        return alertas_encontradas

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
                    os.makedirs(carpeta_alertas, exist_ok=True)
                    ruta_nueva = os.path.join(carpeta_alertas, archivo)
                    shutil.move(ruta_pdf, ruta_nueva)
                    alertas_encontradas.append(archivo.replace(".pdf", ""))
            except Exception as e:
                print(f"No se pudo auditar el archivo {archivo}: {e}")

    return alertas_encontradas


def circuito_abierto(fallos_consecutivos, nombre_entidad="la Contraloría (personas jurídicas)"):
    """
    Si el mismo portal falla dos veces seguidas (no por un dato puntual, sino
    por algo técnico), lo más probable es un bloqueo de IP o una caída
    temporal. Seguir intentando con el resto solo perdería tiempo y créditos
    de 2Captcha, así que se corta la verificación aquí.
    """
    if fallos_consecutivos >= 2:
        print(f"\nSe detectaron {fallos_consecutivos} fallos técnicos consecutivos en el portal de {nombre_entidad}.")
        print("Es posible que esté bloqueando las solicitudes automatizadas o esté caído en este momento.")
        print("Se detiene esta verificación para no perder más tiempo ni créditos de 2Captcha.")
        return True
    return False


def esperar_descarga(carpeta_destino, timeout=20):
    """
    Vigila la carpeta hasta que aparezca un nuevo PDF y retorna su ruta.
    """
    archivos_iniciales = set(os.listdir(carpeta_destino))
    tiempo_inicio = time.time()

    while time.time() - tiempo_inicio < timeout:
        archivos_actuales = set(os.listdir(carpeta_destino))
        archivos_nuevos = archivos_actuales - archivos_iniciales

        for archivo in archivos_nuevos:
            if archivo.endswith('.pdf') and not archivo.endswith('.crdownload'):
                time.sleep(1.5)
                return os.path.join(carpeta_destino, archivo)

        time.sleep(1)

    return None


def _quitar_acentos(texto):
    return texto.translate(str.maketrans("ÁÉÍÓÚáéíóúÑñ", "AEIOUaeiouNn"))


def _columna_que_contiene(columnas, *fragmentos):
    for columna in columnas:
        normalizada = _quitar_acentos(str(columna)).upper()
        if all(fragmento in normalizada for fragmento in fragmentos):
            return columna
    return None


def leer_juridicas(ruta_excel):
    """
    Lee el Excel de personas jurídicas postuladas: Código, Razón Social,
    Tipo de identificación, Número de Identificación Tributaria (NIT).
    """
    try:
        encabezados = pd.read_excel(ruta_excel, sheet_name=0, header=0, nrows=0)
    except Exception:
        return pd.DataFrame()
    encabezados.columns = encabezados.columns.str.strip()
    columnas = list(encabezados.columns)

    col_codigo = _columna_que_contiene(columnas, "CODIGO")
    col_razon = _columna_que_contiene(columnas, "RAZON")
    col_tipo = _columna_que_contiene(columnas, "TIPO")
    col_nit = (
        _columna_que_contiene(columnas, "NIT")
        or _columna_que_contiene(columnas, "IDENTIFICACION", "TRIBUTARIA")
        or _columna_que_contiene(columnas, "NUMERO", "IDENTIFICACION")
    )

    if not (col_codigo and col_razon and col_nit):
        return pd.DataFrame()

    try:
        df = pd.read_excel(ruta_excel, sheet_name=0, header=0, dtype={col_codigo: str, col_nit: str})
    except Exception:
        return pd.DataFrame()
    df.columns = df.columns.str.strip()
    df = df.dropna(subset=[col_nit])

    filas = []
    for _, fila in df.iterrows():
        nit = re.sub(r"\D", "", str(fila[col_nit])) if pd.notna(fila[col_nit]) else ""
        if not nit:
            continue
        filas.append({
            'CODIGO': str(fila[col_codigo]).strip() if pd.notna(fila[col_codigo]) else "",
            'RAZON SOCIAL': str(fila[col_razon]).strip() if pd.notna(fila[col_razon]) else "",
            'TIPO DE IDENTIFICACION': str(fila[col_tipo]).strip() if col_tipo and pd.notna(fila[col_tipo]) else 'NIT',
            'NIT': nit,
        })

    resultado = pd.DataFrame(filas)
    return resultado.drop_duplicates(subset=['NIT']) if not resultado.empty else resultado


def _pitido(frecuencia, duracion_ms):
    """
    Pitido audible para quien esté frente al equipo cuando hay una alerta.
    winsound solo existe en Windows -- el panel corre este script dentro de
    WSL (Linux), así que ahí simplemente se ignora en silencio en vez de
    tumbar el guardado de la alerta real con un ModuleNotFoundError.
    """
    try:
        import winsound
        winsound.Beep(frecuencia, duracion_ms)
    except Exception:
        pass


def _guardar_reporte_fallidos(df, documentos_fallidos, directorio_base, codigo_entidad):
    """
    Junta a las empresas que NO se pudieron consultar de verdad y las deja
    en un Excel aparte (Fallidos_<ENTIDAD>.xlsx), para reintentarlas sin
    mezclarlas con las que sí tuvieron una alerta real.
    """
    ruta_fallidos = os.path.join(directorio_base, f"Fallidos_{codigo_entidad}.xlsx")

    if not documentos_fallidos:
        if os.path.exists(ruta_fallidos):
            os.remove(ruta_fallidos)
        return

    filas_fallidos = []
    for _, fila_persona in df.iterrows():
        nit_persona = str(fila_persona['NIT']).strip()
        if nit_persona not in documentos_fallidos:
            continue
        filas_fallidos.append({
            "CODIGO": fila_persona.get('CODIGO', ''),
            "RAZON_SOCIAL": fila_persona.get('RAZON SOCIAL', ''),
            "NIT": nit_persona,
            "MOTIVO": documentos_fallidos[nit_persona],
        })

    try:
        pd.DataFrame(filas_fallidos).to_excel(ruta_fallidos, index=False)
        print(f"\nSe guardó el listado de personas jurídicas con consulta fallida en:\n{ruta_fallidos}")
    except Exception as error_reporte:
        print(f"Aviso: no se pudo generar el Excel de fallidos: {error_reporte}")


def _guardar_reporte_inhabilitados(df, documentos_inhabilitados, directorio_base, codigo_entidad):
    """
    Junta a TODAS las empresas con alerta real guardada en la carpeta de
    inhabilitados -de esta corrida o de corridas anteriores- en un Excel
    aparte (Inhabilitados_<ENTIDAD>.xlsx).
    """
    carpeta_inhabilitados_dir = os.path.join(directorio_base, f"Cert_{codigo_entidad}_INHABILITADOS")
    ruta_inhabilitados = os.path.join(directorio_base, f"Inhabilitados_{codigo_entidad}.xlsx")

    motivos_por_doc = dict(documentos_inhabilitados)

    if os.path.isdir(carpeta_inhabilitados_dir):
        for nombre_archivo in os.listdir(carpeta_inhabilitados_dir):
            if not nombre_archivo.endswith('.pdf'):
                continue
            doc_archivo = os.path.splitext(nombre_archivo)[0].rsplit('_', 1)[-1]
            if doc_archivo in motivos_por_doc:
                continue
            ruta_pdf_existente = os.path.join(carpeta_inhabilitados_dir, nombre_archivo)
            try:
                with open(ruta_pdf_existente, 'rb') as f:
                    lector = PyPDF2.PdfReader(f)
                    texto_pdf_existente = ""
                    for pagina in lector.pages:
                        texto_pdf_existente += pagina.extract_text() or ""
                motivos_por_doc[doc_archivo] = " ".join(texto_pdf_existente.split())
            except Exception as error_lectura:
                print(f"Aviso: no se pudo leer {nombre_archivo} para el reporte de inhabilitados: {error_lectura}")

    if not motivos_por_doc:
        if os.path.exists(ruta_inhabilitados):
            os.remove(ruta_inhabilitados)
        return

    filas_inhabilitados = []
    for _, fila_persona in df.iterrows():
        nit_persona = str(fila_persona['NIT']).strip()
        if nit_persona not in motivos_por_doc:
            continue
        filas_inhabilitados.append({
            "CODIGO": fila_persona.get('CODIGO', ''),
            "RAZON_SOCIAL": fila_persona.get('RAZON SOCIAL', ''),
            "NIT": nit_persona,
            "MOTIVO": motivos_por_doc[nit_persona],
        })

    try:
        pd.DataFrame(filas_inhabilitados).to_excel(ruta_inhabilitados, index=False)
        print(f"\nSe guardó el listado de personas jurídicas con alerta real en:\n{ruta_inhabilitados}")
    except Exception as error_reporte:
        print(f"Aviso: no se pudo generar el Excel de inhabilitados: {error_reporte}")


# ==========================================
# 2. CONFIGURACIÓN Y RUTAS
# ==========================================
def obtener_ruta_datos():
    if len(sys.argv) > 1:
        return sys.argv[1]
    return input("Ruta del Excel con las personas jurídicas (Código, Razón Social, Tipo de identificación, NIT): ").strip('"').strip()


ruta_datos = obtener_ruta_datos()
if not os.path.isfile(ruta_datos):
    raise FileNotFoundError(f"No se encontró el archivo: {ruta_datos}")

directorio_base = os.path.normpath(os.path.dirname(ruta_datos))
carpeta_destino = os.path.join(directorio_base, "Cert_CONTJUR")
carpeta_inhabilitados = os.path.join(directorio_base, "Cert_CONTJUR_INHABILITADOS")
os.makedirs(carpeta_destino, exist_ok=True)

print("Auditando certificados previamente descargados...")
alertas_historicas = auditar_descargas_anteriores(carpeta_destino, carpeta_inhabilitados)
if alertas_historicas:
    print(f"Se movieron {len(alertas_historicas)} certificados sospechosos a la carpeta de INHABILITADOS.")

print(f"Los PDF se guardarán automáticamente en:\n{carpeta_destino}")

print("\nLeyendo el archivo de personas jurídicas...")
df = leer_juridicas(ruta_datos)
if df.empty:
    print("No se encontró ninguna persona jurídica con NIT válido en el archivo.")

lista_alertas_finales = alertas_historicas.copy()
# NIT -> motivo, para el Excel de empresas a las que falló la consulta
# (distinto de una alerta real: aquí no se logró determinar nada).
documentos_fallidos = {}
# NIT -> texto del portal, para el Excel de empresas con alerta real.
documentos_inhabilitados = {}

# ==========================================
# 3. CONFIGURAR DESCARGAS INVISIBLES EN CHROME
# ==========================================
opciones = webdriver.ChromeOptions()
prefs = {
    "download.default_directory": carpeta_destino,
    "download.prompt_for_download": False,
    "download.directory_upgrade": True,
    "plugins.always_open_pdf_externally": True
}
opciones.add_experimental_option("prefs", prefs)

opciones.add_argument("--disable-blink-features=AutomationControlled")
opciones.add_experimental_option("excludeSwitches", ["enable-automation"])
opciones.add_experimental_option("useAutomationExtension", False)

driver = webdriver.Chrome(options=opciones)
driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument", {
    "source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
})
wait = WebDriverWait(driver, 15)

errores_no_manejados = 0
fallos_consecutivos = 0
total_personas = len(df)

try:
    for contador_persona, (index, row) in enumerate(df.iterrows(), start=1):
        nit = str(row['NIT']).strip()
        if not nit or not nit[0].isdigit():
            continue

        razon_social = str(row['RAZON SOCIAL']).strip() if pd.notna(row['RAZON SOCIAL']) else ""

        print(f"\n[{contador_persona}/{total_personas}] {razon_social} (NIT {nit})")

        # ==========================================
        # 4. VALIDACIÓN PREVIA (LOOK BEFORE YOU LEAP)
        # ==========================================
        tokens_razon = razon_social.split()
        primer_token_razon = "".join(c for c in tokens_razon[0] if c.isalnum()) if tokens_razon else "SN"
        codigo_val = row.get('CODIGO')
        prefijo_archivo = "".join(c for c in str(codigo_val).strip() if c.isalnum()) if pd.notna(codigo_val) and str(codigo_val).strip() else "CONTJUR"
        nombre_archivo_esperado = f"{prefijo_archivo}_{primer_token_razon}_{nit}.pdf"
        ruta_esperada_normal = os.path.join(carpeta_destino, nombre_archivo_esperado)
        ruta_esperada_inhab = os.path.join(carpeta_inhabilitados, nombre_archivo_esperado)

        if any(os.path.exists(r) for r in (ruta_esperada_normal, ruta_esperada_inhab)):
            print("El certificado ya existe en los registros. Se omite la descarga...")
            continue

        try:
            driver.get("https://www.contraloria.gov.co/web/guest/persona-juridica")
            time.sleep(3)

            # El reCAPTCHA de esta página está registrado contra
            # cfiscal.contraloria.gov.co, no contra este dominio -- la URL
            # "web/guest/..." es el patrón típico de un portal Liferay que
            # embebe el formulario real (el de cfiscal.contraloria.gov.co)
            # en un iframe. driver.current_url siempre devuelve la URL de la
            # ventana externa aunque ya estemos dentro del iframe, así que
            # hay que capturar el src del iframe ANTES de cambiar de
            # contexto -- 2Captcha necesita la URL real donde vive el
            # captcha (no la del portal que lo envuelve), o sus workers no
            # logran resolverlo.
            url_formulario = driver.current_url
            try:
                iframes = driver.find_elements(By.TAG_NAME, "iframe")
                for candidato in iframes:
                    src = candidato.get_attribute("src") or ""
                    if "contraloria" in src.lower() or "cfiscal" in src.lower():
                        url_formulario = src
                        driver.switch_to.frame(candidato)
                        break
            except Exception:
                pass

            # ==========================================
            # 5. VERIFICAR EL SITEKEY VIGENTE DEL RECAPTCHA
            # ==========================================
            sitekey_actual = SITE_KEY_CONTRALORIA
            try:
                elemento_recaptcha = driver.find_element(By.CSS_SELECTOR, "[data-sitekey]")
                sitekey_detectada = elemento_recaptcha.get_attribute("data-sitekey")
                if sitekey_detectada and sitekey_detectada != SITE_KEY_CONTRALORIA:
                    print(f"Aviso: el sitekey del portal cambió (antes: {SITE_KEY_CONTRALORIA}, ahora: {sitekey_detectada}). Se usará el nuevo.")
                    sitekey_actual = sitekey_detectada
            except Exception:
                print("No se pudo leer el sitekey del portal; se sigue usando el conocido.")

            # CAMPO: NIT
            campo_documento = wait.until(EC.presence_of_element_located((By.ID, "txtNumeroDocumento")))
            campo_documento.clear()
            campo_documento.send_keys(nit)

            # ==========================================
            # 6. RESOLUCIÓN DE RECAPTCHA CON REINTENTOS
            # ==========================================
            intentos = 0
            max_intentos = 3
            captcha_resuelto = False

            while intentos < max_intentos and not captcha_resuelto:
                try:
                    print(f"Enviando reCAPTCHA a 2Captcha (intento {intentos + 1}/{max_intentos})...")
                    resultado = solver.recaptcha(sitekey=sitekey_actual, url=url_formulario)
                    codigo_token = resultado['code']
                    print("reCAPTCHA resuelto correctamente.")

                    driver.execute_script(f"document.getElementById('g-recaptcha-response').innerHTML = '{codigo_token}';")
                    time.sleep(1)
                    captcha_resuelto = True

                except Exception as e:
                    intentos += 1
                    print(f"Error de red o conexión: {e}")
                    if intentos < max_intentos:
                        print("Reintentando en 5 segundos...")
                        time.sleep(5)

            if not captcha_resuelto:
                print(f"No fue posible resolver el captcha tras {max_intentos} intentos. Se salta a la siguiente empresa...")
                documentos_fallidos[nit] = "No fue posible resolver el captcha tras varios intentos"
                errores_no_manejados += 1
                fallos_consecutivos += 1
                if circuito_abierto(fallos_consecutivos):
                    break
                continue

            # ==========================================
            # 7. EJECUTAR BÚSQUEDA Y ANALIZAR LA DESCARGA
            # ==========================================
            print("Datos y token listos. Consultando...")
            btn_buscar = wait.until(EC.element_to_be_clickable((By.ID, "btnBuscar")))
            driver.execute_script("arguments[0].click();", btn_buscar)

            print("Esperando la descarga automática del PDF...")
            ruta_descargada = esperar_descarga(carpeta_destino, timeout=45)

            if not ruta_descargada:
                print("No llegó el archivo en el primer intento. Resolviendo un nuevo reCAPTCHA y reintentando...")
                try:
                    resultado_reintento = solver.recaptcha(sitekey=sitekey_actual, url=url_formulario)
                    driver.execute_script(
                        f"document.getElementById('g-recaptcha-response').innerHTML = '{resultado_reintento['code']}';"
                    )
                    time.sleep(1)
                    btn_buscar = driver.find_element(By.ID, "btnBuscar")
                    driver.execute_script("arguments[0].click();", btn_buscar)
                    ruta_descargada = esperar_descarga(carpeta_destino, timeout=45)
                except Exception as e:
                    print(f"No se pudo reintentar con un nuevo captcha: {e}")
                    ruta_descargada = None

            if not ruta_descargada:
                print("Se agotó el tiempo de espera. El portal de la Contraloría no generó el archivo.")
                documentos_fallidos[nit] = "Tiempo de espera agotado (posible caída o lentitud del portal)"
                errores_no_manejados += 1
                fallos_consecutivos += 1
                if circuito_abierto(fallos_consecutivos):
                    break
                continue

            try:
                with open(ruta_descargada, 'rb') as f:
                    lector = PyPDF2.PdfReader(f)
                    texto_pdf = ""
                    for pagina in lector.pages:
                        texto_pdf += pagina.extract_text() or ""
                texto_limpio = "".join(texto_pdf.upper().split())
            except Exception:
                texto_limpio = None

            if texto_limpio is None or len(texto_limpio) < 50:
                print("El archivo descargado no parece un certificado válido. Se descarta y se reintentará más tarde...")
                os.remove(ruta_descargada)
                documentos_fallidos[nit] = "El archivo descargado no parece un certificado válido"
                errores_no_manejados += 1
                fallos_consecutivos += 1
                if circuito_abierto(fallos_consecutivos):
                    break
                continue

            frase_normalizada = "".join(FRASE_LIMPIA.split())

            if frase_normalizada in texto_limpio:
                ruta_final = ruta_esperada_normal
                print("Resultado limpio. Se guarda en la carpeta estándar...")
            else:
                ruta_final = ruta_esperada_inhab
                print("Atención: se detectó un posible reporte como responsable fiscal. Se guarda en la carpeta de alertas...")
                lista_alertas_finales.append(nombre_archivo_esperado.replace(".pdf", ""))
                documentos_inhabilitados[nit] = " ".join(texto_pdf.split())
                _pitido(2000, 1000)

            os.makedirs(os.path.dirname(ruta_final), exist_ok=True)
            shutil.move(ruta_descargada, ruta_final)
            print("Documento procesado correctamente.")
            fallos_consecutivos = 0

        except Exception as e:
            print(f"Error inesperado procesando a {razon_social} (NIT {nit}): {e}")
            print("Se salta a la siguiente empresa...")
            documentos_fallidos[nit] = f"Error inesperado: {e}"
            errores_no_manejados += 1
            fallos_consecutivos += 1
            if circuito_abierto(fallos_consecutivos):
                break
            continue

except Exception as e:
    print(f"\nOcurrió un error inesperado durante el ciclo: {e}")
    sys.exit(1)

finally:
    print("\n" + "="*50)
    print("RESUMEN DE EJECUCIÓN Y ALERTAS")
    print("="*50)

    if lista_alertas_finales:
        print(f"Se detectaron {len(lista_alertas_finales)} registro(s) con posible responsabilidad fiscal:")
        for alerta in lista_alertas_finales:
            print(f"   - {alerta}")
        print(f"\nRevisa manualmente los documentos en la carpeta:\n{carpeta_inhabilitados}")
    else:
        print("No se encontraron registros de responsabilidad fiscal en esta tanda.")

    _guardar_reporte_fallidos(df, documentos_fallidos, directorio_base, "CONTJUR")
    _guardar_reporte_inhabilitados(df, documentos_inhabilitados, directorio_base, "CONTJUR")

    print("="*50)
    print("Cerrando navegador...")
    driver.quit()

if errores_no_manejados:
    print(f"\n{errores_no_manejados} empresa(s) no se pudieron procesar por errores inesperados. Vuelve a correr este script para reintentarlas (los ya descargados se omiten automáticamente).")
    sys.exit(1)
