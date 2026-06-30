---
name: eval-resumen-opiniones-ca
description: >
  Usa este skill SIEMPRE que el usuario quiera generar un documento (PDF + HTML) con el
  resumen de las opiniones que un Career Advisor (CA) ha ido dejando sobre un advisee concreto.
  Actívalo ante "resumen de opiniones del CA", "documento de opiniones", "PDF de opiniones",
  "informe de CA de {persona}", "exportar opiniones de un advisee" o similares. El output es un
  .pdf y un .html por advisee, con una entrada cronológica por cada opinión (opinión del CA +
  resumen sobre el que opinó) y una sección final con los comentarios sueltos. Con caché
  automática si los datos no cambian.
---

# Skill: Resumen de Opiniones del CA por Advisee (PDF + HTML)

## Qué hace este skill

Genera, para un advisee concreto, un documento con todas las opiniones que su Career Advisor
ha registrado a lo largo del tiempo. **No requiere ninguna base de Notion adicional** — usa la
base ya existente:

| Base de Notion | Qué contiene |
|----------------|--------------|
| `Opiniones - {advisee}` | Opiniones del CA (campos `CA`, `Opinion`, `Resumen`, `Fecha`) |

Produce dos archivos por advisee en `config.CARPETA_WEB`, servidos por `/api/files/<archivo>`:

- `opiniones_ca_{slug}.pdf` — PDF con paleta Igeneris (reportlab), logo y pie de página
- `opiniones_ca_{slug}.html` — versión web con el `IGENERIS_CSS` del proyecto

---

## Dónde se usa (web)

En la web: **Mis advisees → {advisee} → Gestionar informe → Generar documento de opiniones**.
Ese botón se despliega en dos opciones:

- **Ver en web** → abre el `.html` en una pestaña nueva
- **Descargar PDF** → descarga el `.pdf`

Ambas llaman al endpoint `POST /api/generar-opiniones-ca` con `{ "evaluado": "<advisee>" }`,
que devuelve `{ "pdfUrl", "htmlUrl" }`. Solo el CA del advisee (o un admin) puede generarlo.

---

## Arquitectura del flujo

```
Notion
  └── "Opiniones - {advisee}"  → filas con CA / Opinion / Resumen / Fecha
          ↓
  obtener_datos_opiniones_ca(advisee, ca_nombre="")
  ├── resuelve el CA (obtener_ca_de_empleado) si no se pasa
  ├── obtener_opiniones_ca_por_advisee(ca, advisee)
  └── separa cada fila por si tiene Resumen o no:
        ├── CON Resumen → entries        (entrada cronológica de 2 columnas)
        └── SIN Resumen → comentarios     ("Comentarios y notas extra")
          ↓
  generar_html_opiniones_ca(datos)   → opiniones_ca_{slug}.html
  generar_pdf_opiniones_ca(datos)    → opiniones_ca_{slug}.pdf
  _escribir_cache(slug, huella)      → opiniones_ca_{slug}_cache.json
```

**Clave del reparto de filas**: en `Opiniones - {advisee}`, las filas que el CA guarda con un
resumen estructurado son las **entradas cronológicas**; las filas sin resumen (las notas sueltas
que se registran desde la web — "Registro de reuniones / Comentarios") van a la sección final
**"Comentarios y notas extra"**.

**Caché automática**: si la huella SHA-256 de los datos no ha cambiado y los archivos existen,
se reutilizan sin regenerar.

---

## Estructura del documento

### Cabecera
- Nombre del advisee (grande) + logo de Igeneris a la derecha (si existe el PNG, ver más abajo)
- `CA · {nombre del CA}`
- Fecha de generación

### Entradas cronológicas (una por fila con Resumen, de más antigua a más reciente)
- **Fecha** (en naranja Igeneris)
- Dos columnas:
  - Izquierda — `OPINIÓN CA`: el texto literal de la opinión del CA (`Opinion`)
  - Derecha — `SOBRE QUÉ HA OPINADO`: el `Resumen` tal cual está guardado en Notion
    (lo genera el skill `eval-resumen-evaluacion`; aquí se muestra sin reprocesar)

### Sección final "Comentarios y notas extra" (solo si hay filas sin Resumen)
- Lista de los textos de `Opinion` de las filas que no tienen `Resumen`

---

## Datos que se leen de Notion

`obtener_opiniones_ca_por_advisee(ca_nombre, advisee)` devuelve una lista de dicts:

```python
{
    "fecha":           "2024-01-12T...",   # ISO; se formatea como "12 ene 2024"
    "ca":              "Laura Martínez",
    "opinion":         "Texto literal de la opinión del CA",
    "resumen_advisee": "Resumen estructurado sobre el que opinó (texto plano)",
}
```

El reparto:
- `resumen_advisee` no vacío → **entry** (`opinion_ca` | `resumen`)
- `resumen_advisee` vacío y `opinion` no vacío → **comentario suelto**

---

## Punto de entrada principal

```python
from backend.skill_opiniones_ca import generar_resumen_opiniones_ca

slug = generar_resumen_opiniones_ca(advisee="Álvaro García")          # CA auto-resuelto
slug = generar_resumen_opiniones_ca("Álvaro García", ca_nombre="Laura Martínez")
```

**Parámetros:**
- `advisee` (str): nombre del advisee, debe coincidir con la base `Opiniones - {advisee}`
- `ca_nombre` (str, opcional): si se omite se resuelve con `obtener_ca_de_empleado(advisee)`

**Devuelve:** `slug` (str) — nombre base de archivo sin extensión:
- `opiniones_ca_{slug}.pdf`
- `opiniones_ca_{slug}.html`

**Lanza `ValueError`** si el advisee no tiene ninguna opinión registrada.
**Lanza `RuntimeError`** si `reportlab` no está instalado (no se puede generar el PDF).

---

## Funciones del módulo

| Función | Qué hace |
|---------|----------|
| `obtener_datos_opiniones_ca(advisee, ca_nombre="")` | Lee Notion y reparte las filas en `entries` / `comentarios_sueltos` |
| `generar_html_opiniones_ca(datos)` | Genera el `.html` con estilo Igeneris. Devuelve la ruta |
| `generar_pdf_opiniones_ca(datos)` | Genera el `.pdf` con reportlab. Devuelve la ruta |
| `generar_resumen_opiniones_ca(advisee, ca_nombre="")` | Punto de entrada. Orquesta todo con caché |
| `_formatear_fecha(iso)` | `'2024-01-12T...'` → `'12 ene 2024'` |
| `_huella_datos(datos)` | Huella SHA-256 para la caché |

---

## Logo del documento (opcional)

El PDF incrusta un logo en la cabecera si existe el archivo:

```
backend/assets/igeneris_logo.png
```

Si no existe, el documento se genera igual pero sin logo (solo el nombre del advisee en la
cabecera). Ya está copiado el logo del frontend a esa ruta.

---

## Dependencias

```python
# Requeridas
reportlab        # generar_pdf_opiniones_ca()  → pip install reportlab (ya en requirements.txt)

# Del propio proyecto
from . import config                 # config.CARPETA_WEB, config.IGENERIS_CSS, config.ZONA_HORARIA_MADRID, config.BASE_DIR
from .notion_service import obtener_ca_de_empleado, obtener_opiniones_ca_por_advisee
from .utils import slug_archivo
```

---

## Errores frecuentes

| Error | Causa | Solución |
|-------|-------|----------|
| `ValueError: No hay opiniones del CA...` | El advisee no tiene filas en `Opiniones - {advisee}` | Verificar que existe la base y tiene opiniones |
| `RuntimeError: Instala reportlab` | reportlab no instalado | `pip install reportlab` |
| El logo no sale en el documento | Falta `backend/assets/igeneris_logo.png` | Copiar el PNG del logo a esa ruta |
| Caché no se invalida | La huella solo cubre opiniones/comentarios/CA | Borrar `opiniones_ca_{slug}_cache.json` manualmente |

---

## Relación con otros skills

- Consume el campo `Resumen` que produce el skill `eval-resumen-evaluacion` (lo muestra tal cual
  en la columna "SOBRE QUÉ HA OPINADO", sin reprocesar).
- Comparte las bases `Opiniones - {advisee}` con el skill `eval-informes-rrhh`.
