"""
Administra las cuentas del equipo que pueden entrar al panel. Ya no hace
falta para el día a día (cualquiera se crea su cuenta sola desde /registro),
pero sigue sirviendo para dar/quitar permisos de administrador y para
emergencias desde la consola.

Uso (desde la carpeta panel/, con el entorno activado):
    python3 gestionar_usuarios.py agregar <usuario>
    python3 gestionar_usuarios.py quitar <usuario>
    python3 gestionar_usuarios.py listar
    python3 gestionar_usuarios.py cambiar-clave <usuario>
    python3 gestionar_usuarios.py hacer-admin <usuario>
    python3 gestionar_usuarios.py quitar-admin <usuario>

Las contraseñas nunca se guardan en texto plano -- solo su hash (bcrypt) en
panel/usuarios.json, que está en .gitignore a propósito.
"""
import getpass
import json
import sys
from pathlib import Path

import bcrypt

RUTA_USUARIOS = Path(__file__).resolve().parent / "usuarios.json"


CAMPOS_POR_DEFECTO = {
    "es_admin": False,
    "nombres": "",
    "apellidos": "",
    "identificacion": "",
    "correo": "",
    "celular": "",
    "acepto_terminos": False,
    "avisar_por_correo": False,
}


def _cargar():
    if not RUTA_USUARIOS.is_file():
        return {}
    datos = json.loads(RUTA_USUARIOS.read_text(encoding="utf-8"))
    # Compatibilidad con formatos viejos (usuario -> hash directo, o sin
    # los campos de nombre/identificación que se agregaron después).
    for usuario, valor in datos.items():
        if isinstance(valor, str):
            valor = {"hash": valor}
        for campo, por_defecto in CAMPOS_POR_DEFECTO.items():
            valor.setdefault(campo, por_defecto)
        datos[usuario] = valor
    return datos


def _guardar(usuarios):
    RUTA_USUARIOS.write_text(json.dumps(usuarios, indent=2, ensure_ascii=False), encoding="utf-8")


def _pedir_clave():
    while True:
        clave = getpass.getpass("Contraseña: ")
        if len(clave) < 6:
            print("Muy corta, usa al menos 6 caracteres.")
            continue
        confirmacion = getpass.getpass("Repite la contraseña: ")
        if clave != confirmacion:
            print("No coinciden, intenta de nuevo.")
            continue
        return clave


def agregar(usuario):
    usuarios = _cargar()
    if usuario in usuarios:
        print(f"'{usuario}' ya existe. Usa 'cambiar-clave' si quieres actualizar su contraseña.")
        return
    clave = _pedir_clave()
    hash_clave = bcrypt.hashpw(clave.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    usuarios[usuario] = {"hash": hash_clave, **CAMPOS_POR_DEFECTO}
    _guardar(usuarios)
    print(f"Usuario '{usuario}' creado.")


def cambiar_clave(usuario):
    usuarios = _cargar()
    if usuario not in usuarios:
        print(f"'{usuario}' no existe.")
        return
    clave = _pedir_clave()
    usuarios[usuario]["hash"] = bcrypt.hashpw(clave.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    _guardar(usuarios)
    print(f"Contraseña de '{usuario}' actualizada.")


def quitar(usuario):
    usuarios = _cargar()
    if usuario not in usuarios:
        print(f"'{usuario}' no existe.")
        return
    del usuarios[usuario]
    _guardar(usuarios)
    print(f"Usuario '{usuario}' eliminado.")


def hacer_admin(usuario, es_admin=True):
    usuarios = _cargar()
    if usuario not in usuarios:
        print(f"'{usuario}' no existe.")
        return
    usuarios[usuario]["es_admin"] = es_admin
    _guardar(usuarios)
    print(f"'{usuario}' ahora {'es' if es_admin else 'ya no es'} administrador.")


def listar():
    usuarios = _cargar()
    if not usuarios:
        print("No hay usuarios todavía.")
        return
    print(f"{len(usuarios)} usuario(s):")
    for usuario, datos in usuarios.items():
        etiqueta = " (administrador)" if datos.get("es_admin") else ""
        print(f"  - {usuario}{etiqueta}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    accion = sys.argv[1]
    if accion == "listar":
        listar()
    elif accion == "agregar" and len(sys.argv) > 2:
        agregar(sys.argv[2])
    elif accion == "quitar" and len(sys.argv) > 2:
        quitar(sys.argv[2])
    elif accion == "cambiar-clave" and len(sys.argv) > 2:
        cambiar_clave(sys.argv[2])
    elif accion == "hacer-admin" and len(sys.argv) > 2:
        hacer_admin(sys.argv[2], True)
    elif accion == "quitar-admin" and len(sys.argv) > 2:
        hacer_admin(sys.argv[2], False)
    else:
        print(__doc__)
        sys.exit(1)
