"""
Prepara la lista de personas para la revisión técnico-administrativa, a
partir del PDF de "Autorización para consulta de antecedentes" y, cuando
esté disponible, el PDF con las copias de cédula de los integrantes.

Genera un CSV limpio (con una columna REVISAR) que los 5 scripts de
verificación pueden leer directamente ya conciliado, en vez de que cada uno
vuelva a leer los PDFs por su cuenta.
"""
import gc
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime

import pandas as pd
import pymupdf

# Los modelos de EasyOCR pesan varios cientos de MB; si existe el disco D:
# (usado en este equipo como almacenamiento adicional) se guardan ahí en vez
# de en C:, para no consumir el espacio limitado del disco del sistema.
if os.path.isdir("D:\\") and "EASYOCR_MODULE_PATH" not in os.environ:
    os.environ["EASYOCR_MODULE_PATH"] = r"D:\ModelosIA\EasyOCR"

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

_LECTOR_OCR = None

PATRON_AUTORIZACION_PDF = re.compile(
    r"(?:El|La)\s+(?:\((?:la|el)\)\s+)?suscrit[oa]\s*(?:\(\s*[ao]\s*\))?\s+(?P<nombre>.+?)\s*,?\s+"
    r"identificad[oa]\s*(?:\(\s*a\s*\))?\s+con\s+"
    r"(?P<tipo_doc>c[eé]dula de ciudadan[ií]a|tarjeta de identidad|c[eé]dula de extranjer[ií]a|pasaporte)\s+No\.\s+"
    r"(?P<doc>[\d.]+)\s*,?\s+expedida en\s+.+?\s*,?\s+con fecha de expedici[oó]n\s+"
    r"(?P<fecha>\d{1,2}\s*/\s*\d{1,2}\s*/\s*\d{4})",
    re.IGNORECASE,
)

PATRON_FECHA_MES_TEXTO = re.compile(r'(\d{1,2})[\s-]+([A-Z]{3})[\s-]+(\d{4})', re.IGNORECASE)
MESES = {'ENE': 1, 'FEB': 2, 'MAR': 3, 'ABR': 4, 'MAY': 5, 'JUN': 6,
         'JUL': 7, 'AGO': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DIC': 12}


def _normalizar_fecha(fecha_str):
    """Convierte una fecha DD/MM/AAAA (con o sin ceros a la izquierda) a un
    formato fijo de dos dígitos, para poder comparar fechas iguales
    escritas de forma distinta (p. ej. "1/07/2022" y "01/07/2022")."""
    partes = fecha_str.split("/")
    if len(partes) != 3:
        return fecha_str
    dia, mes, anio = partes
    try:
        return f"{int(dia):02d}/{int(mes):02d}/{int(anio):04d}"
    except ValueError:
        return fecha_str


def leer_autorizaciones(ruta_pdf):
    """
    Extrae a las personas del PDF de "Autorización para consulta de
    antecedentes" (una autorización por persona, dentro del mismo archivo).
    """
    documento = pymupdf.open(ruta_pdf)
    texto_completo = ""
    for pagina in documento:
        texto_completo += pagina.get_text() + " "

    texto_normalizado = " ".join(texto_completo.replace("_", "").replace(chr(0x200b), " ").split())

    filas = []
    for coincidencia in PATRON_AUTORIZACION_PDF.finditer(texto_normalizado):
        nombre_completo = " ".join(coincidencia.group("nombre").split())
        tokens = nombre_completo.split()
        primer_nombre = tokens[0] if tokens else ""
        resto_nombre = " ".join(tokens[1:])

        tipo_doc_texto = coincidencia.group("tipo_doc").lower()
        if "extranjer" in tipo_doc_texto:
            tipo_doc = "CE"
        elif "tarjeta" in tipo_doc_texto:
            tipo_doc = "TI"
        elif "pasaporte" in tipo_doc_texto:
            tipo_doc = "PA"
        else:
            tipo_doc = "CC"

        filas.append({
            "DOC": re.sub(r"\D", "", coincidencia.group("doc")),
            "TIPO_DOC": tipo_doc,
            "PRIMER_NOMBRE": primer_nombre,
            "SEGUNDO_NOMBRE": "",
            "PRIMER_APELLIDO": resto_nombre,
            "SEGUNDO_APELLIDO": "",
            "FECHA_EXPEDICION": _normalizar_fecha(re.sub(r"\s+", "", coincidencia.group("fecha"))),
        })

    df = pd.DataFrame(filas)
    return df.drop_duplicates(subset=["DOC"]) if not df.empty else df


def _obtener_lector_ocr():
    """
    Crea (una sola vez por corrida) el lector de EasyOCR. Se carga solo
    cuando de verdad hace falta, porque inicializarlo toma varios segundos.
    """
    global _LECTOR_OCR
    if _LECTOR_OCR is None:
        import easyocr
        print("Cargando modelo de OCR para leer cédulas escaneadas (solo la primera vez)...")
        _LECTOR_OCR = easyocr.Reader(['es'], gpu=False, verbose=False)
    return _LECTOR_OCR


def _leer_pagina_con_ocr(pagina):
    """
    Renderiza una página del PDF como imagen y le aplica OCR. Se usa como
    respaldo cuando la página no tiene una capa de texto real (es decir,
    es una foto o un escaneo de la cédula).

    La resolución de render y el tamaño de lienzo de EasyOCR se mantienen
    bajos a propósito: este equipo tiene poca RAM libre (12 GB en total,
    compartida con Chrome, VS Code, etc.), y un procesamiento sin este
    límite puede hacer que el proceso muera sin ni siquiera alcanzar a
    mostrar un error de Python.
    """
    lector_ocr = _obtener_lector_ocr()
    archivo_temp = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    archivo_temp.close()
    try:
        pix = pagina.get_pixmap(dpi=100)
        pix.save(archivo_temp.name)
        fragmentos = lector_ocr.readtext(archivo_temp.name, canvas_size=700, mag_ratio=1.0, detail=0)
        return " ".join(fragmentos)
    finally:
        os.remove(archivo_temp.name)
        gc.collect()


def leer_fechas_desde_cedulas(ruta_pdf, documentos_conocidos):
    """
    Recorre el PDF de copias de cédula y empareja el número de documento con
    su fecha de expedición. Primero intenta con el texto real de la página;
    si no tiene (es una foto o un escaneo), usa OCR sobre la imagen
    renderizada de esa página.

    `documentos_conocidos` es el conjunto de números de documento que ya se
    sacaron del PDF de autorización: en vez de depender de que el OCR lea
    bien la etiqueta "NÚMERO" (que a veces se confunde con el fondo de
    seguridad de la cédula), se busca cualquier número en el texto de la
    página que coincida con uno ya conocido.

    Retorna un diccionario {numero_documento: fecha_dd/mm/aaaa}.
    """
    resultado = {}
    numero_pendiente = None

    documento_pdf = pymupdf.open(ruta_pdf)
    for pagina in documento_pdf:
        texto = " ".join(pagina.get_text().split())
        if not texto:
            texto = _leer_pagina_con_ocr(pagina)
        if not texto:
            continue

        numeros_en_pagina = {re.sub(r"\D", "", n) for n in re.findall(r'\d[\d.,]{5,}\d', texto)}
        numero_en_pagina = next((n for n in numeros_en_pagina if n in documentos_conocidos), None)

        fecha_en_pagina = None
        fecha_candidata = None
        fecha_con_ciudad = None
        for m_fecha in PATRON_FECHA_MES_TEXTO.finditer(texto):
            inicio, fin = m_fecha.span()
            antes = texto[max(0, inicio - 25):inicio].upper()
            contexto = texto[max(0, inicio - 40):fin + 40].upper()
            if "NACIMIENTO" in antes:
                continue
            dia, mes_txt, anio = m_fecha.groups()
            mes = MESES.get(mes_txt.upper())
            if not mes:
                continue
            fecha_formateada = f"{int(dia):02d}/{mes:02d}/{anio}"
            # Se recuerda como candidata por si el OCR no logra leer bien la
            # etiqueta "EXPEDICIÓN" (le pasa con las cédulas del formato
            # nuevo, que además suelen traer una tercera fecha de
            # vencimiento). "22 FEB 2017, CALI" (con coma y ciudad después)
            # es un patrón típico de fecha de expedición, así que se
            # prefiere sobre solo tomar la última fecha encontrada.
            fecha_candidata = fecha_formateada
            if texto[fin:fin + 3].lstrip().startswith(","):
                fecha_con_ciudad = fecha_formateada
            if "EXPEDICI" in contexto:
                fecha_en_pagina = fecha_formateada
                break

        if fecha_en_pagina is None:
            fecha_en_pagina = fecha_con_ciudad or fecha_candidata

        # El número y la fecha de expedición suelen quedar en páginas
        # distintas (frente y reverso de la cédula), así que se recuerda
        # el número hasta encontrar su fecha, o viceversa.
        numero_actual = numero_en_pagina or numero_pendiente
        if numero_actual and fecha_en_pagina:
            resultado[numero_actual] = fecha_en_pagina
            numero_pendiente = None
        elif numero_en_pagina and not fecha_en_pagina:
            numero_pendiente = numero_en_pagina
        elif fecha_en_pagina and not numero_en_pagina and numero_pendiente:
            resultado[numero_pendiente] = fecha_en_pagina
            numero_pendiente = None

    return resultado


def conciliar(df_autorizaciones, fechas_cedulas):
    """
    Compara la fecha de expedición de cada persona contra la que se pudo leer
    de su copia de cédula (si la hay). Si difieren, se queda con la de la
    cédula (es el documento oficial) y lo marca para revisión. Si no hay
    copia legible, deja la de la autorización pero también marca para
    revisión, porque no se pudo verificar contra nada.
    """
    fechas_finales = []
    revisar = []
    motivos = []

    for _, fila in df_autorizaciones.iterrows():
        doc = fila["DOC"]
        fecha_autorizacion = fila["FECHA_EXPEDICION"]
        fecha_cedula = fechas_cedulas.get(doc)

        if fecha_cedula is None:
            fechas_finales.append(fecha_autorizacion)
            revisar.append("SI")
            motivos.append("No se encontró o no se pudo leer la copia de cédula de esta persona (ni por texto ni por OCR). Verifica la fecha de expedición manualmente.")
        elif fecha_cedula != fecha_autorizacion:
            fechas_finales.append(fecha_cedula)
            revisar.append("SI")
            motivos.append(f"Fecha de expedición corregida según la cédula (la autorización decía {fecha_autorizacion}).")
        else:
            fechas_finales.append(fecha_autorizacion)
            revisar.append("NO")
            motivos.append("")

    df_final = df_autorizaciones.copy()
    df_final["FECHA_EXPEDICION"] = fechas_finales
    df_final["REVISAR"] = revisar
    df_final["MOTIVO_REVISAR"] = motivos
    return df_final


def obtener_rutas():
    if len(sys.argv) > 2:
        return sys.argv[1], sys.argv[2]
    if len(sys.argv) > 1:
        return sys.argv[1], None
    ruta_autorizacion = input("Ruta del PDF de autorización para consulta de antecedentes: ").strip('"').strip()
    ruta_cedulas = input("Ruta del PDF con las copias de cédula (Enter para omitir): ").strip('"').strip()
    return ruta_autorizacion, (ruta_cedulas or None)


def main():
    ruta_autorizacion, ruta_cedulas = obtener_rutas()

    if not ruta_autorizacion or not os.path.isfile(ruta_autorizacion):
        print(f"No se encontró el archivo: {ruta_autorizacion}")
        sys.exit(1)

    print("Leyendo el PDF de autorización...")
    df = leer_autorizaciones(ruta_autorizacion)
    if df.empty:
        print("No se encontró ninguna persona en el PDF de autorización.")
        sys.exit(1)
    print(f"Se encontraron {len(df)} persona(s).")

    fechas_cedulas = {}
    if ruta_cedulas and os.path.isfile(ruta_cedulas):
        print("\nLeyendo el PDF de copias de cédula...")
        fechas_cedulas = leer_fechas_desde_cedulas(ruta_cedulas, set(df["DOC"]))
        print(f"Se pudo leer la fecha de expedición de {len(fechas_cedulas)} de {len(df)} persona(s) desde la cédula.")
    else:
        print("\nNo se indicó (o no se encontró) un PDF de copias de cédula; se usará solo la autorización.")

    print("\nConciliando información...")
    df_final = conciliar(df, fechas_cedulas)

    directorio_base = os.path.dirname(ruta_autorizacion)
    ruta_salida = os.path.join(directorio_base, "personas_preparadas.csv")

    if os.path.isfile(ruta_salida):
        marca_tiempo = datetime.now().strftime("%Y%m%d_%H%M%S")
        ruta_respaldo = os.path.join(directorio_base, f"personas_preparadas.bak_{marca_tiempo}.csv")
        shutil.copy2(ruta_salida, ruta_respaldo)
        print(f"\nYa existía un {os.path.basename(ruta_salida)}: se guardó una copia en {os.path.basename(ruta_respaldo)} antes de reemplazarlo.")

    df_final.to_csv(ruta_salida, index=False, encoding="utf-8-sig")

    total_revisar = int((df_final["REVISAR"] == "SI").sum())
    print(f"\nListo. Archivo generado:\n{ruta_salida}")
    if total_revisar:
        print(f"\n{total_revisar} persona(s) quedaron marcadas para revisar antes de correr las consultas:")
        for _, fila in df_final[df_final["REVISAR"] == "SI"].iterrows():
            print(f"   - {fila['PRIMER_NOMBRE']} {fila['PRIMER_APELLIDO']} ({fila['DOC']}): {fila['MOTIVO_REVISAR']}")
        print("\nAbre el CSV, corrige lo que haga falta y luego pásaselo a ejecutar_revision.py o a cualquiera de los scripts.")
    else:
        print("Todas las personas coincidieron entre las dos fuentes. No hay nada que revisar.")


if __name__ == "__main__":
    main()
