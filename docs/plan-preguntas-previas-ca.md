# Evaluación anual asistida — informe final "con ayuda de Claude"

> Estado: **implementado**. Este documento describe el comportamiento real del código
> (`backend/eval_anual_sesion.py`, endpoints en `backend/api_server.py`, wizard en `frontend/src/main.jsx`).
> Objetivo: que el CA co-redacte el informe anual debatiendo con la IA, apoyado en criterios y evidencia,
> manteniendo el control humano.

---

## 1. Principios

1. **CA con el control**: Claude propone, el CA decide y confirma cada área. Nada se cierra sin su OK.
2. **Todo con evidencia**: cada afirmación lleva su cita `[E/O/P/S/B#]` a la fuente real; nada inventado.
3. **Baremo objetivo**: se juzga contra los **criterios del cargo** (de Notion), no contra opiniones sueltas.
4. **Debate crítico**: la IA es un sparring exigente (defiende con datos, cuestiona sesgos, no cede por complacer).
5. **Barato en API**: generación inicial cacheada en la sesión; contexto estático del debate con *prompt caching*.

---

## 2. Flujo real (wizard `EvaluacionAnualWizard`)

```
[0] Identidad — "¿Es esta la persona que vas a evaluar?" (nombre + proyectos) → 1 clic
        ↓
[1] Loop por área — TODAS: las 5 de proyecto + liderazgo (si el cargo lo requiere)
    + contribution to the firm + resultado global
    Por cada área:
      1a. Panel "Criterios y nivel · {cargo}":
            - ANTES de opinar → SOLO los criterios de esa área (de Notion, según el cargo).
            - Evidencia que la IA consideró (las fuentes que citó), plegable.
      1b. El CA escribe sus puntos / su opinión (pregunta abierta).
      1c. Al enviar → aparece el DIAGNÓSTICO (a qué nivel está y qué le falta para subir, con citas)
          y la IA responde conversacionalmente (defiende/reta con criterios + evidencia).
          - Debate libre: se puede seguir hablando ("¿por qué X, no Y?").
          - Referencias al momento: las citas [E3] de los mensajes son CLICABLES (ficha inline),
            y también se puede preguntar "referencia de E3" y la IA responde con el texto literal.
      1d. "Confirmar área y continuar" → fija el texto acordado (la propuesta de la IA).
        ↓
[2] Plan de acción sugerido (año que viene)
    - La IA propone 3-5 objetivos accionables a partir de la evaluación acordada + los gaps.
    - Editable a mano + "pedir cambios a la IA" (regenera con tu instrucción).
    - Es SOLO una sugerencia en pantalla: NO se mete en el Word.
        ↓
[3] "Generar borrador" → informe_anual_{slug}.docx/html con lo acordado por área
    (huecos de notas/retribución en blanco). El CA lo revisa, rellena y lo sube como informe final.
```

**Nota de orden**: los criterios se ven antes de opinar (lente objetiva), pero el **diagnóstico** (juicio de
nivel) solo aparece **tras enviar tu opinión** — para no anclarte, y de paso ahorrar API (se genera solo entonces).

---

## 3. Modelo de datos — `sesion_anual_{slug}.json` (JSON local en `config.CARPETA_WEB`)

```jsonc
{
  "advisee": "Alonso Ballesteros",
  "ca": "María Paniagua",
  "cargo": "Manager",            // leído de Notion (Lista de empleados → columna Cargo)
  "anio": 2025,
  "estado": "en_progreso | completada",
  "identidad_confirmada": true,
  "emp_data": { ... },           // las 5 fuentes + objetivos (obtener_datos_empleado_anual)
  "comentarios": { ... },        // interpretar_evaluaciones_anual (cacheado, 1 sola vez)
  "areas": {
    "gestion_proyecto": {
      "conversacion": [{ "rol": "ca|ia", "texto": "..." }],
      "propuesta": "bullets con citas",
      "confirmada": true,
      "texto_final": "...",
      "criterios": [{ "nivel": "Manager", "criterios": ["..."] }],  // de Notion
      "diagnostico": "está a nivel X porque [E2]; le falta Y/Z para subir"
    }
    // ... una entrada por área
  },
  "plan_accion": "1. …\n2. …",   // sugerencia del año que viene (solo se muestra)
  "creada_en": "...", "actualizada_en": "...", "completada_en": "..."
}
```

---

## 4. Backend — `backend/eval_anual_sesion.py`

| Función | Qué hace |
|---------|----------|
| `iniciar_sesion` | Crea/recupera la sesión; coge el **cargo real de Notion** (`_cargo_de` → `buscar_empleado_y_cargo`). No llama a Claude aún. |
| `confirmar_identidad` | Marca la identidad confirmada. |
| `obtener_area` | Evidencia citada + **criterios de Notion** (`_criterios_area`) + pregunta. El **diagnóstico** solo si ya existe (tras opinar). |
| `responder_area` | Guarda el turno del CA; genera el diagnóstico en la 1ª opinión; llama a la IA (`_claude_conversa_area`) y devuelve mensaje + propuesta + diagnóstico. |
| `confirmar_area` | Fija `texto_final` = propuesta acordada; marca el área confirmada. |
| `obtener_plan_accion` / `pedir_cambios_plan` / `guardar_plan_accion` | Plan de acción sugerido: genera (lazy), ajusta por instrucción del CA, o guarda la edición manual. |
| `finalizar_sesion` | Exige todas las áreas confirmadas; genera el borrador con lo acordado (huecos en blanco); log de auditoría en Notion (best-effort). |
| `estado_sesion` / `eliminar_sesion` | Progreso (para reanudar) / borrar la sesión y sus borradores. |

Helpers clave: `_criterios_area` (criterios por área **desde Notion** `obtener_criterios_evaluacion(grupo)`,
con emparejamiento de dimensión `_match_dim_label` y fallback al diccionario hardcodeado), `_generar_diagnostico`
(nivel + gaps), `_claude_conversa_area` (debate, con **prompt caching** del bloque estático), `_generar_plan_accion`.

**Reaprovecha del skill**: `obtener_datos_empleado_anual`, `_formatear_contexto` (fuentes), `interpretar_evaluaciones_anual`,
`guardar_informe_anual_word/html`, `_grupo_por_cargo`, `_nivel_cargo`, `_CRITERIOS_DTI`.

---

## 5. Endpoints — `backend/api_server.py` (`/api/eval-anual/*`)

Todos exigen sesión y que el advisee sea del CA (o admin) vía `_exigir_acceso_advisee`.

| Método | Ruta | Función |
|--------|------|---------|
| GET  | `/estado` | `estado_sesion` |
| GET  | `/area?clave=` | `obtener_area` |
| GET  | `/plan` | `obtener_plan_accion` |
| POST | `/iniciar` | `iniciar_sesion` |
| POST | `/confirmar-identidad` | `confirmar_identidad` |
| POST | `/responder-area` | `responder_area` |
| POST | `/confirmar-area` | `confirmar_area` |
| POST | `/plan-cambios` | `pedir_cambios_plan` |
| POST | `/plan-guardar` | `guardar_plan_accion` |
| POST | `/finalizar` | `finalizar_sesion` (+ urls del borrador) |
| POST | `/eliminar` | `eliminar_sesion` |

---

## 6. Frontend — `frontend/src/main.jsx`

- **`EvaluacionAnualWizard`** con pasos `identidad → loop → resumen(+plan) → hecho`. Textos vía i18n (`eaw.*`).
  - Panel "Criterios y nivel" (criterios siempre; diagnóstico tras opinar).
  - Chat por área con **citas clicables** (ficha inline de la fuente) y pista de referencia.
  - Paso final con el **plan de acción** editable + "pedir cambios a la IA".
  - Botón **"Info completa"** (arriba, junto al nombre): descarga el PDF con todas las fuentes.
- Se llega desde `AdviseeDetail` → "Realizar informe final" → "Con ayuda de Claude".

---

## 7. Coste / API

- La generación inicial (`interpretar_evaluaciones_anual`) se hace **una vez** y se cachea en la sesión.
- El **diagnóstico** por área se genera **una vez**, y solo **tras la primera opinión** del CA.
- El debate usa **prompt caching**: instrucciones + criterios + diagnóstico + evidencia + valoración van en un
  bloque `cache_control` (estático por área) → los turnos siguientes casi no pagan esos tokens. Fallback sin caché
  si el modelo/SDK no lo soportara.
- El **plan de acción** es 1 llamada al final (+1 por cada "pedir cambios").

---

## 8. Notas / pendiente

- **Ejemplos de guía**: aún no hay en Notion → el diagnóstico usa solo criterios (sin ejemplos).
- **Emparejamiento criterio↔área**: por solape de palabras; si en Notion las dimensiones tienen nombres muy
  distintos a las etiquetas del informe, puede fallar (afinar `_match_dim_label`).
- **Verificación en vivo**: la calidad real (criterios de Notion, diagnóstico y debate de Claude) se confirma
  con servidor + Notion + Claude reales. La lógica está probada con stubs y los builds pasan.