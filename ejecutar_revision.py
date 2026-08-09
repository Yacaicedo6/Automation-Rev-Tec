"""
Trigger de la revisión técnico-administrativa.

Pide una sola vez la ruta del archivo con la información de los postulantes
(el Excel "ANEXO TECNICO... INFORMACION ARTISTAS..." o, en convocatorias que
ya no traen Excel, el PDF de "Autorización para consulta de antecedentes") y
ejecuta, en orden, cada uno de los scripts de verificación, pasándoles esa
misma ruta.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

DIRECTORIO_SCRIPTS = Path(__file__).resolve().parent

# Pausa entre una verificación y la siguiente, para que Chrome/chromedriver
# terminen de liberar recursos antes de abrir el próximo navegador. Sin esta
# pausa, el primer intento del siguiente script puede fallar por lentitud
# (el portal parece no responder) justo cuando en realidad es el sistema
# arrancando un Chrome nuevo con el anterior recién cerrado.
PAUSA_ENTRE_VERIFICACIONES = 8

VERIFICACIONES = [
    ("RNMC - Policía (Medidas Correctivas)", "automation_RNMC.py"),
    ("Contraloría - antecedentes fiscales", "automation_Contraloria.py"),
    ("Procuraduría - antecedentes disciplinarios", "automation_Procuraduria.py"),
    ("Policía - antecedentes judiciales", "automation_Judicial.py"),
    ("Delitos sexuales - inhabilidad", "automation_DelitosSexuales.py"),
]


def pedir_ruta_datos():
    try:
        import tkinter as tk
        from tkinter import filedialog

        raiz = tk.Tk()
        raiz.withdraw()
        ruta = filedialog.askopenfilename(
            title="Selecciona el Excel, el PDF o el CSV con la información de los postulantes",
            filetypes=[
                ("Excel, PDF o CSV", "*.xlsx *.pdf *.csv"),
                ("Archivos Excel", "*.xlsx"),
                ("Archivos PDF", "*.pdf"),
                ("Archivos CSV", "*.csv"),
            ],
        )
        raiz.destroy()
        if ruta:
            return ruta
    except Exception:
        pass

    return input("Ruta del archivo (Excel, PDF o CSV ya preparado) con la información de los postulantes: ").strip('"').strip()


def main():
    ruta_datos = pedir_ruta_datos()

    if not ruta_datos or not os.path.isfile(ruta_datos):
        print(f"No se encontró el archivo: {ruta_datos}")
        sys.exit(1)

    print(f"\nArchivo seleccionado:\n{ruta_datos}\n")
    print("Se ejecutarán las siguientes verificaciones, en orden:")
    for nombre, _ in VERIFICACIONES:
        print(f"  - {nombre}")

    respuesta = input("\n¿Continuar? (s/n): ").strip().lower()
    if respuesta != "s":
        print("Operación cancelada.")
        return

    resultados = []
    for indice, (nombre, archivo) in enumerate(VERIFICACIONES):
        if indice > 0:
            print(f"\nEsperando {PAUSA_ENTRE_VERIFICACIONES}s antes de abrir el siguiente navegador...")
            time.sleep(PAUSA_ENTRE_VERIFICACIONES)

        ruta_script = DIRECTORIO_SCRIPTS / archivo

        print("\n" + "=" * 60)
        print(f"Iniciando: {nombre}")
        print("=" * 60)

        proceso = subprocess.run([sys.executable, str(ruta_script), ruta_datos])
        exito = proceso.returncode == 0
        resultados.append((nombre, exito))

        if not exito:
            print(f"\n'{nombre}' terminó con errores (código {proceso.returncode}).")
            print("Revisa el detalle arriba antes de seguir: puede que falten personas por procesar.")
            continuar = input("¿Continuar con la siguiente verificación de todas formas? (s/n): ").strip().lower()
            if continuar != "s":
                print("Ejecución detenida por el usuario.")
                break

    mostrar_resumen(resultados)


def mostrar_resumen(resultados):
    print("\n" + "=" * 60)
    print("Resumen de ejecución")
    print("=" * 60)
    for nombre, ok in resultados:
        estado = "completado" if ok else "terminó con errores"
        print(f"  [{estado}] {nombre}")

    pendientes = [nombre for nombre, _ in VERIFICACIONES if nombre not in [n for n, _ in resultados]]
    for nombre in pendientes:
        print(f"  [no ejecutado] {nombre}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n\nEjecución interrumpida por el usuario (Ctrl+C).")
        sys.exit(1)
