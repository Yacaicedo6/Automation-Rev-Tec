"""
Trigger de la revisión técnico-administrativa.

Pide una sola vez la ruta del Excel con la información de los postulantes
(el "ANEXO TECNICO... INFORMACION ARTISTAS...") y ejecuta, en orden, cada uno
de los scripts de verificación de antecedentes, pasándoles esa misma ruta.
"""
import os
import subprocess
import sys
from pathlib import Path

DIRECTORIO_SCRIPTS = Path(__file__).resolve().parent

VERIFICACIONES = [
    ("RNMC - Policía (Medidas Correctivas)", "automation_RNMC.py"),
    ("Contraloría - antecedentes fiscales", "automation_Contraloria.py"),
    ("Procuraduría - antecedentes disciplinarios", "automation_Procuraduria.py"),
    ("Policía - antecedentes judiciales", "automation_Judicial.py"),
    ("Delitos sexuales - inhabilidad", "automation_DelitosSexuales.py"),
]


def pedir_ruta_excel():
    try:
        import tkinter as tk
        from tkinter import filedialog

        raiz = tk.Tk()
        raiz.withdraw()
        ruta = filedialog.askopenfilename(
            title="Selecciona el Excel con la información de los postulantes",
            filetypes=[("Archivos Excel", "*.xlsx")],
        )
        raiz.destroy()
        if ruta:
            return ruta
    except Exception:
        pass

    return input("Ruta del archivo Excel con la información de los postulantes: ").strip('"').strip()


def main():
    ruta_excel = pedir_ruta_excel()

    if not ruta_excel or not os.path.isfile(ruta_excel):
        print(f"No se encontró el archivo: {ruta_excel}")
        sys.exit(1)

    print(f"\nArchivo seleccionado:\n{ruta_excel}\n")
    print("Se ejecutarán las siguientes verificaciones, en orden:")
    for nombre, _ in VERIFICACIONES:
        print(f"  - {nombre}")

    respuesta = input("\n¿Continuar? (s/n): ").strip().lower()
    if respuesta != "s":
        print("Operación cancelada.")
        return

    resultados = []
    for nombre, archivo in VERIFICACIONES:
        ruta_script = DIRECTORIO_SCRIPTS / archivo

        print("\n" + "=" * 60)
        print(f"Iniciando: {nombre}")
        print("=" * 60)

        proceso = subprocess.run([sys.executable, str(ruta_script), ruta_excel])
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
