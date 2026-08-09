"""
Prepara la lista de personas para la revisión técnico-administrativa, a
partir del PDF de "Autorización para consulta de antecedentes" y, cuando
esté disponible, el PDF con las copias de cédula de los integrantes.

Genera un CSV limpio (con una columna REVISAR) que los 5 scripts de
verificación pueden leer directamente ya conciliado, en vez de que cada uno
vuelva a leer los PDFs por su cuenta.
"""
import os
import re
import sys
import pandas as pd
import PyPDF2

PATRON_AUTORIZACION_PDF = re.compile(
    r"El \(la\) suscrito\(a\)\s+(?P<nombre>.+?)\s*,\s+identificado\(a\) con\s+"
    r"(?P<tipo_doc>c[eé]dula de ciudadan[ií]a|tarjeta de identidad|c[eé]dula de extranjer[ií]a|pasaporte)\s+No\.\s+"
    r"(?P<doc>[\d.]+)\s*,\s+expedida en\s+.+?\s*,\s+con fecha de expedici[oó]n\s+"
    r"(?P<fecha>\d{1,2}/\d{1,2}/\d{4})",
    re.IGNORECASE,
)

PATRON_FECHA_MES_TEXTO = re.compile(r'(\d{1,2})-([A-Z]{3})-(\d{4})', re.IGNORECASE)
MESES = {'ENE': 1, 'FEB': 2, 'MAR': 3, 'ABR': 4, 'MAY': 5, 'JUN': 6,
         'JUL': 7, 'AGO': 8, 'SEP': 9, 'OCT': 10, 'NOV': 11, 'DIC': 12}


def leer_autorizaciones(ruta_pdf):
    """
    Extrae a las personas del PDF de "Autorización para consulta de
    antecedentes" (una autorización por persona, dentro del mismo archivo).
    """
    with open(ruta_pdf, 'rb') as f:
        lector = PyPDF2.PdfReader(f)
        texto_completo = ""
        for pagina in lector.pages:
            texto_completo += (pagina.extract_text() or "") + " "

    texto_normalizado = " ".join(texto_completo.split())

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
            "FECHA_EXPEDICION": coincidencia.group("fecha"),
        })

    df = pd.DataFrame(filas)
    return df.drop_duplicates(subset=["DOC"]) if not df.empty else df


def leer_fechas_desde_cedulas(ruta_pdf):
    """
    Recorre el PDF de copias de cédula y, en las páginas que sí tengan texto
    real (no son solo una foto/escaneo sin capa de texto), empareja el
    número de documento con su fecha de expedición.

    Retorna un diccionario {numero_documento: fecha_dd/mm/aaaa}. Las
    personas cuya copia es solo imagen no quedan en este diccionario, porque
    no hay forma de leerlas sin OCR.
    """
    resultado = {}
    numero_pendiente = None

    with open(ruta_pdf, 'rb') as f:
        lector = PyPDF2.PdfReader(f)
        for pagina in lector.pages:
            texto = " ".join((pagina.extract_text() or "").split())
            if not texto:
                continue

            m_numero = re.search(r'N[ÚU]MERO\s+([\d.,]{6,})', texto, re.IGNORECASE)
            numero_en_pagina = re.sub(r"\D", "", m_numero.group(1)) if m_numero else None

            fecha_en_pagina = None
            for m_fecha in PATRON_FECHA_MES_TEXTO.finditer(texto):
                inicio, fin = m_fecha.span()
                antes = texto[max(0, inicio - 25):inicio].upper()
                contexto = texto[max(0, inicio - 40):fin + 40].upper()
                if "NACIMIENTO" in antes:
                    continue
                if "EXPEDICI" in contexto:
                    dia, mes_txt, anio = m_fecha.groups()
                    mes = MESES.get(mes_txt.upper())
                    if mes:
                        fecha_en_pagina = f"{int(dia):02d}/{mes:02d}/{anio}"
                        break

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
            motivos.append("No se pudo leer la copia de cédula (imagen sin texto). Verifica la fecha de expedición manualmente.")
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
        fechas_cedulas = leer_fechas_desde_cedulas(ruta_cedulas)
        print(f"Se pudo leer la fecha de expedición de {len(fechas_cedulas)} de {len(df)} persona(s) desde la cédula.")
    else:
        print("\nNo se indicó (o no se encontró) un PDF de copias de cédula; se usará solo la autorización.")

    print("\nConciliando información...")
    df_final = conciliar(df, fechas_cedulas)

    directorio_base = os.path.dirname(ruta_autorizacion)
    ruta_salida = os.path.join(directorio_base, "personas_preparadas.csv")
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
