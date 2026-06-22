"""
Prueba del sistema de preguntas gestionadas desde Notion.
Ejecutar desde la raiz del proyecto: python test_preguntas.py
"""
import os
import sys
sys.stdout.reconfigure(encoding="utf-8")

# Cargar .env manualmente antes de importar nada del backend
def _cargar_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        print("ERROR: no se encontró .env")
        sys.exit(1)
    with open(env_path, encoding="utf-8-sig") as f:
        for linea in f:
            linea = linea.strip()
            if not linea or linea.startswith("#") or "=" not in linea:
                continue
            clave, _, valor = linea.partition("=")
            os.environ.setdefault(clave.strip(), valor.strip())

_cargar_env()

# ─── 1. tipo_relacion ────────────────────────────────────────────────────────
print("=== 1. tipo_relacion ===")
from backend.hierarchy import tipo_relacion, comparar_jerarquia

casos = [
    ("superior", "Top-Bottom"),
    ("inferior", "Bottom-Top"),
    ("igual",    "Same Level"),
]
ok = True
for entrada, esperado in casos:
    resultado = tipo_relacion(entrada)
    estado = "✓" if resultado == esperado else "✗"
    if resultado != esperado:
        ok = False
    print(f"  {estado}  tipo_relacion('{entrada}') → '{resultado}'  (esperado: '{esperado}')")

print()
print("=== 1b. comparar_jerarquia + tipo_relacion ===")
jerarquias = [
    ("Partner",  "Analyst",  "Top-Bottom"),
    ("Analyst",  "Partner",  "Bottom-Top"),
    ("Manager",  "Manager",  "Same Level"),
    ("Trainee",  "Sr. Associate", "Bottom-Top"),
]
for cargo_eval, cargo_evad, esperado in jerarquias:
    relacion = comparar_jerarquia(cargo_eval, cargo_evad)
    tipo     = tipo_relacion(relacion)
    estado   = "✓" if tipo == esperado else "✗"
    if tipo != esperado:
        ok = False
    print(f"  {estado}  {cargo_eval} → {cargo_evad}  relacion='{relacion}'  tipo='{tipo}'")

print()

# ─── 2. obtener_preguntas_desde_notion ───────────────────────────────────────
print("=== 2. obtener_preguntas_desde_notion ===")
from backend.notion_service import obtener_preguntas_desde_notion

TIPOS = ["Top-Bottom", "Bottom-Top", "Same Level"]
CLAVES = ["satisfaccion", "mejor_aspecto", "peor_aspecto"]

for tipo in TIPOS:
    preguntas = obtener_preguntas_desde_notion(tipo)
    tiene_todas = all(k in preguntas for k in CLAVES)
    estado = "✓" if tiene_todas else "✗"
    if not tiene_todas:
        ok = False
    print(f"  {estado}  [{tipo}]  claves encontradas: {list(preguntas.keys())}")
    for clave, texto in preguntas.items():
        print(f"       {clave}: {texto}")
    print()

# ─── 3. texto_pregunta_por_clave ─────────────────────────────────────────────
print("=== 3. texto_pregunta_por_clave (con relacion) ===")
from backend.slack_bot import texto_pregunta_por_clave

for relacion in ["superior", "inferior", "igual"]:
    print(f"  relacion='{relacion}':")
    for clave in CLAVES:
        texto = texto_pregunta_por_clave(clave, relacion=relacion)
        print(f"    {clave}: {texto}")
    print()

# ─── Resultado final ──────────────────────────────────────────────────────────
if ok:
    print("✅ Todas las pruebas pasaron correctamente.")
else:
    print("❌ Alguna prueba falló. Revisa los resultados arriba.")
    sys.exit(1)
