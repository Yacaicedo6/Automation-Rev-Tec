# Interfaz web (local)

Capa visual sobre los mismos scripts de `Automation-Rev-Tec` — no reemplaza
la lógica, solo evita usar la consola y Excel para revisar los datos antes
de correr las verificaciones. Corre **en tu propio computador**, no en la
nube: los PDF, las cédulas y los certificados nunca salen de tu equipo.

## Instalación

Con el entorno virtual del proyecto (`.venv`) ya creado y activado:

```bash
pip install -r webapp/requirements.txt
```

## Uso

```bash
streamlit run webapp/Inicio.py
```

Se abre sola en el navegador (`http://localhost:8501`). La app está
configurada (`webapp/.streamlit/config.toml`) para solo aceptar conexiones
desde este mismo equipo — no queda expuesta en la red.

### Páginas

1. **Preparar personas** — selecciona el PDF de autorización y, si lo
   tienes, el de copias de cédula. La app extrae los datos, concilia las
   fechas (con OCR si hace falta) y te deja **corregir directamente en una
   tabla editable** antes de guardar el CSV — en vez de abrir el CSV en
   Excel.
2. **Ejecutar verificaciones** — elige el CSV ya revisado y qué portales
   correr. Cada verificación sigue abriendo su propia ventana de Chrome
   (necesario para resolver preguntas de seguridad o corregir errores a
   mano cuando el portal lo pide); la app solo centraliza el progreso y el
   resumen final, y dispara el correo de aviso al terminar.

## Notas

- No implementa nada nuevo: importa `preparar_personas.py` directamente (es
  seguro, no abre navegador) y lanza los `automation_*.py` como procesos
  aparte (igual que `ejecutar_revision.py`), porque esos sí necesitan una
  ventana de Chrome visible.
- Si algún PDF usa una redacción de autorización que la app no reconoce,
  el mensaje de error es el mismo que en consola — avisa para agregar esa
  variante al patrón, como ya se ha hecho varias veces.
