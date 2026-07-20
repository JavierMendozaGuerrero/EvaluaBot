"""Compara las respuestas del servidor API viejo (http.server) contra el nuevo
(FastAPI) para las rutas GET de solo lectura, y reporta cualquier diferencia.

Por qué solo GET: las rutas POST/DELETE mutan datos en Notion o disparan efectos
secundarios (mensajes de Slack, generación de ficheros). Repetir la misma petición
contra dos servidores duplicaría esas escrituras/notificaciones. Las rutas GET son
idempotentes -- seguro repetirlas.

## Cómo montar la comparación

1. Servidor VIEJO (implementación http.server, ya solo existe en el historial de git
   desde que api_server.py se convirtió en shim de FastAPI). Para levantarlo:

       git worktree add ../evaluabot-old-api HEAD~1   # o el commit anterior a la migración
       cd ../evaluabot-old-api
       $env:PUERTO_WEB="8000"
       python bot.py     # (o el entrypoint real del proyecto)

2. Servidor NUEVO (esta rama, FastAPI):

       $env:PUERTO_WEB="8001"
       python bot.py

   Ambos procesos deben apuntar a las MISMAS credenciales de Notion/Slack (mismo
   .env) para que las respuestas sean comparables.

3. Crea (o usa) un usuario de prueba dedicado en Notion -- NO uses un empleado real,
   para no mezclar datos reales con pruebas. Usa ese usuario como TEST_USERNAME /
   TEST_PASSWORD abajo, y un TEST_EVALUADO cuyo nombre exista en la base de Notion.

4. Ejecuta:

       python scripts/diff_api_routes.py \\
           --old http://localhost:8000 \\
           --new http://localhost:8001 \\
           --username "Test EvaluaBot" \\
           --password "..." \\
           --evaluado "Test EvaluaBot"

El script hace login por separado contra cada servidor (las sesiones son en memoria
por proceso, un token de uno no sirve en el otro), pega a cada ruta GET con
parámetros representativos, y compara los JSON ignorando campos que cambian entre
ejecuciones (timestamps, ETags, tokens).
"""

import argparse
import json
import sys
from urllib.parse import urlencode

import httpx

IGNORAR_CLAVES = {"etag", "token", "timestamp", "generatedAt", "fechaAlta", "docxUrl", "htmlUrl", "pdfUrl", "docxAnualUrl"}


def _limpiar(valor):
    """Elimina recursivamente claves volátiles antes de comparar."""
    if isinstance(valor, dict):
        return {k: _limpiar(v) for k, v in valor.items() if k not in IGNORAR_CLAVES}
    if isinstance(valor, list):
        return [_limpiar(v) for v in valor]
    return valor


def login(base_url, username, password):
    r = httpx.post(f"{base_url}/api/login", json={"username": username, "password": password}, timeout=20)
    r.raise_for_status()
    return r.json()["token"]


# Rutas GET de solo lectura y los parámetros representativos que necesitan.
# `{evaluado}` / `{advisee}` / `{nombre}` se sustituyen por --evaluado en tiempo de ejecución.
RUTAS_GET = [
    "/api/health",
    "/api/me",
    "/api/evaluados",
    "/api/mis-advisees",
    "/api/mi-perfil",
    "/api/paises",
    "/api/opiniones-ca?advisee={evaluado}",
    "/api/objetivos?nombre={evaluado}",
    "/api/tareas-slack",
    "/api/acceso-advisee-individual?advisee={evaluado}",
    "/api/informe-final?evaluado={evaluado}",
    "/api/evaluaciones-proyecto-activas",
    "/api/todos-empleados",
    "/api/proyectos-manager",
    "/api/evaluaciones-extra-pendientes",
    "/api/evaluaciones-extra-recibidas?evaluado={evaluado}",
    "/api/estado-ciclo-slack",
    "/api/resumen-evaluaciones-advisee?advisee={evaluado}",
    "/api/criterios-evaluacion?grupo=negocio",
    "/api/eval-anual/estado?evaluado={evaluado}",
    "/api/eval-anual/plan?evaluado={evaluado}",
    "/api/eval-anual/plan-guardado?evaluado={evaluado}",
    # Rutas solo-admin: solo tienen sentido si el usuario de prueba es admin.
    "/api/cumplimiento-evaluaciones",
    "/api/feedback-confidencial-todos",
    "/api/anonimato-evaluadores",
    "/api/evaluados-anual",
]


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--old", required=True, help="Base URL del servidor viejo, ej. http://localhost:8000")
    parser.add_argument("--new", required=True, help="Base URL del servidor nuevo, ej. http://localhost:8001")
    parser.add_argument("--username", required=True)
    parser.add_argument("--password", required=True)
    parser.add_argument("--evaluado", required=True, help="Nombre de un empleado/advisee de PRUEBA existente en Notion")
    args = parser.parse_args()

    print("Iniciando sesión en ambos servidores...")
    token_old = login(args.old, args.username, args.password)
    token_new = login(args.new, args.username, args.password)

    fallos = []
    for plantilla in RUTAS_GET:
        ruta = plantilla.format(evaluado=args.evaluado)
        headers_old = {"Authorization": f"Bearer {token_old}"}
        headers_new = {"Authorization": f"Bearer {token_new}"}
        try:
            r_old = httpx.get(f"{args.old}{ruta}", headers=headers_old, timeout=20)
            r_new = httpx.get(f"{args.new}{ruta}", headers=headers_new, timeout=20)
        except httpx.HTTPError as e:
            fallos.append((ruta, f"error de red: {e}"))
            continue

        if r_old.status_code != r_new.status_code:
            fallos.append((ruta, f"status distinto: viejo={r_old.status_code} nuevo={r_new.status_code}"))
            continue

        try:
            cuerpo_old = _limpiar(r_old.json())
            cuerpo_new = _limpiar(r_new.json())
        except json.JSONDecodeError:
            if r_old.content != r_new.content:
                fallos.append((ruta, "cuerpo no-JSON distinto"))
            continue

        if cuerpo_old != cuerpo_new:
            fallos.append((ruta, f"cuerpo distinto:\n  viejo={cuerpo_old}\n  nuevo={cuerpo_new}"))
        else:
            print(f"  OK  {ruta}")

    print()
    if fallos:
        print(f"{len(fallos)} diferencia(s) encontradas:")
        for ruta, detalle in fallos:
            print(f"  FAIL {ruta}: {detalle}")
        sys.exit(1)
    print(f"Todas las {len(RUTAS_GET)} rutas coinciden.")


if __name__ == "__main__":
    main()
