# Plan de implementación — Preguntas previas al CA (evaluación anual)

> Estado: **propuesta para revisar** (no implementado). Documento de trabajo.
> Objetivo: que el CA piense críticamente antes de aprobar el informe, sin que Claude le ancle el juicio,
> manteniendo el control humano y dejando trazabilidad de su razonamiento.

---

## 1. Principios (lo que NO se negocia)

1. **Anti-anclaje**: el CA se compromete con su valoración de cada área **antes** de ver lo que escribió Claude.
2. **CA siempre con el control**: Claude propone, el CA decide. Ninguna respuesta de Claude sobrescribe sin que el CA lo apruebe.
3. **Rigor con evidencia**: el CA razona sobre el **dato en bruto** (las mismas fuentes citadas `[E/O/P/S/B#]`), no sobre un resumen.
4. **Trazabilidad**: la valoración del CA y las divergencias con Claude quedan registradas (log de auditoría interno).
5. **Fricción mínima**: 1-2 preguntas por área, guardar progreso, poder reanudar. Si cansa, se rellena a lo loco y se pierde el sentido.

---

## 2. Flujo UX (asistente paso a paso en el dashboard)

```
[0] Confirmación de identidad
    "Vas a evaluar a {nombre} — proyectos del año: AF, Patio. ¿Correcto?"  → 1 clic
        ↓
[1] Lectura de evidencia POCO A POCO   ← opción C, sin sobrecargar
    La evidencia en bruto NO se muestra toda de golpe: se revela por
    bloques cronológicos (p. ej. cuatrimestre a cuatrimestre: ene-abr,
    may-ago, sep-dic), avanzando con "continuar". Sin filtrar ni curar
    (el CA ve todo), pero digerido y, de paso, reforzando la lectura de
    la EVOLUCIÓN del año (febrero ≠ noviembre).
        ↓
[2] Loop por dimensión — TODAS: gestión, calidad, equipo, comunicación,
    cliente, liderazgo (si aplica) y contribution to the firm
    Para cada dimensión:
      2a. Se muestran los CRITERIOS DTI de esa área (la lente) + recordatorio de la evidencia.
      2b. 1-2 preguntas → el CA escribe su valoración.   [SE BLOQUEA al guardar]
      2c. SOLO entonces se revela lo que redactó Claude para esa dimensión (con sus citas).
      2d. ¿Coinciden? → siguiente.
          ¿Divergen?   → se muestran lado a lado con citas; el CA decide:
                          [la mía] · [la de Claude] · [fusión editada]   → queda registrado.
        ↓
[3] Contribution to the firm + Resultado (mismo patrón)
        ↓
[4] Objetivos '26 (el CA los rellena)
        ↓
[5] Vista final del borrador → el CA publica (informe_final) cuando está conforme
```

**Clave de orden**: el paso 2c (revelar a Claude) nunca ocurre antes del 2b (bloquear respuesta del CA).

---

## 3. Modelo de datos

Una "sesión de evaluación" por (CA, advisee, año):

```jsonc
{
  "advisee": "Alonso Ballesteros",
  "ca": "María Paniagua",
  "anio": 2025,
  "estado": "en_progreso | completada",
  "identidad_confirmada": true,
  "respuestas_ca": {
    "gestion_proyecto": { "texto": "...", "bloqueada_en": "2026-03-01T10:00:00Z" },
    "calidad_tecnica":  { ... }
    // ...
  },
  "divergencias": [
    {
      "dimension": "calidad_tecnica",
      "valoracion_ca": "Veo calidad media...",
      "valoracion_claude": "Calidad alta [E3]",
      "decision": "fusion",          // mia | claude | fusion
      "texto_final": "...",
      "resuelta_en": "2026-03-01T10:05:00Z"
    }
  ]
}
```

- `respuestas_ca` → **input** que se inyecta en la reconciliación + **registro** del juicio humano.
- `divergencias` → **log de auditoría interno** (lo ven CA/admin, NO el advisee).

**Persistencia**: propongo una BD nueva en Notion `Sesiones evaluación anual` (consistente con el resto del sistema) **o** un JSON local junto al informe (`sesion_anual_{slug}.json`). Decisión abierta (ver §8).

---

## 4. Backend (api_server.py — nuevos endpoints)

Todos protegidos por sesión y verificando que el advisee es del CA (como `servir_archivo_protegido`).

| Endpoint | Método | Qué hace |
|----------|--------|----------|
| `/api/eval-anual/iniciar` | POST | Crea/recupera la sesión. Devuelve identidad (nombre, proyectos). Dispara la generación de Claude en background (cacheada) pero **no** la devuelve. |
| `/api/eval-anual/evidencia` | GET | Devuelve la evidencia en bruto **por bloques** (`?bloque=1`, p. ej. cuatrimestre), para mostrarla poco a poco sin sobrecargar. |
| `/api/eval-anual/dimension` | GET | Para una dimensión: devuelve criterios DTI + (si ya está bloqueada la respuesta del CA) los bullets de Claude. |
| `/api/eval-anual/responder` | POST | Guarda y **bloquea** la respuesta del CA para una dimensión. A partir de aquí ya puede pedir la de Claude. |
| `/api/eval-anual/resolver` | POST | Registra la decisión sobre una divergencia (mía/claude/fusión + texto final). |
| `/api/eval-anual/estado` | GET | Progreso de la sesión (para reanudar). |
| `/api/eval-anual/finalizar` | POST | Marca completada y regenera el informe final incorporando las decisiones del CA. |

**Reaprovecha**: `obtener_datos_empleado_anual`, `_formatear_contexto` (las `fuentes`), `_criterios_para_prompt` (por dimensión), `interpretar_evaluaciones_anual`.

---

## 5. Frontend (React — dashboard)

Un componente `EvaluacionAnualWizard` con estado de pasos. Pantallas:

1. **Confirmar identidad** — tarjeta con nombre/proyectos + botón "Sí, es correcto".
2. **Evidencia** — lista de fichas (reutiliza el estilo del anexo de Fuentes).
3. **Dimensión** (repetida) — criterios + textarea de respuesta + botón "Guardar y continuar" (bloquea). Tras bloquear, aparece el bloque de Claude y, si difiere, la vista comparativa con los 3 botones.
4. **Resumen** — borrador completo + "Publicar informe final".

Estado guardado en backend tras cada paso → se puede cerrar y reanudar. Barra de progreso "3/7 áreas".

---

## 6. Reconciliación con Claude

- Claude genera el borrador **una vez** (ya cacheado). Sus bullets por dimensión son la "propuesta".
- La respuesta del CA es la "tesis humana" comprometida antes de ver la propuesta.
- En `finalizar`, el texto final de cada dimensión = lo que el CA aprobó (su decisión en 2d).
  - Opción ligera: la decisión es manual (el CA elige/edita).
  - Opción asistida (fase 2): una llamada extra a Claude que **fusiona** respetando lo que el CA marcó, sin inventar (mismas reglas de cita).

---

## 7. Dónde encaja con lo ya construido

- El **borrador** (`informe_anual_*`) y el **anexo de Fuentes** ya existen → la evidencia del paso [1] es justo ese anexo.
- El **panel de revisión** (avisos del verificador) sigue apareciendo en el borrador.
- El estado **borrador → publicado** ya existe (`/api/subir-informe-final`). `finalizar` puede reutilizarlo.
- Las **citas internas** ya no dependen de Notion → la evidencia es accesible para el CA sin permisos extra.

---

## 8. Decisiones

1. **Persistencia de la sesión** → **JSON local en Fase 1** (rápido), migrar a **Notion en Fase 2**
   (durable + auditable por admins). Pros/contras de cada uno valorados.
2. **Reconciliación** → **manual en Fase 1** (CA elige/reescribe), **fusión asistida por Claude en Fase 2**.
3. **Liderazgo / Contribution** → ✅ **también pasan por el loop** (todas las áreas, no solo las 5 de proyecto).
4. **Guardar y reanudar** → ✅ se puede **guardar a medias y seguir más tarde**; **publicar exige completar** el loop.
5. **Lectura de evidencia** → ✅ **poco a poco** (por bloques cronológicos), no toda de golpe.

---

## 9. Fases de entrega

- **Fase 1 (núcleo) — ✅ IMPLEMENTADA**: sesión + identidad + evidencia por bloques + loop por dimensión
  con bloqueo + comparación manual + log de divergencias. Persistencia JSON local.
- **Fase 2 (asistencia) — ✅ IMPLEMENTADA**: fusión asistida por Claude + log de auditoría persistido en
  Notion (best-effort) + vista del log en la web.
- **Fase 3 (refinos) — pendiente**: recordatorios, métricas agregadas de divergencia, panel admin.

### Qué se construyó (para probar en vivo)

**Backend**
- `backend/eval_anual_sesion.py` — lógica de sesión (JSON local `sesion_anual_{slug}.json`).
- `backend/notion_service.py` — `guardar_log_evaluacion_anual()` (+ BD "Log evaluacion anual asistida").
- `backend/api_server.py` — endpoints:
  - GET `/api/eval-anual/{estado,evidencia,dimension,log}`
  - POST `/api/eval-anual/{iniciar,confirmar-identidad,responder,fusionar,decidir,finalizar}`
  - Acceso: solo el CA del advisee o admin.

**Frontend** (`frontend/src/main.jsx`)
- `EvaluacionAnualWizard` — identidad → evidencia por periodos → loop (responder/bloquear → ver IA →
  mía/IA/fusión, con "Sugerir fusión con IA") → resumen → publicar.
- `EvalAnualLogPage` — vista del log de decisiones (CA vs IA).
- Lanzadores en `AdviseeDetail` ("Evaluación anual asistida", "Ver log de decisiones").

**Probado**: lógica del módulo end-to-end (con stub de Claude) y build del frontend (`vite build`).
**Falta**: prueba en vivo (servidor + Notion + Claude reales).

---

## 10. Riesgos

- **Fatiga del CA** (cobertura total): mitigar con 1-2 preguntas/área y guardado incremental.
- **Automation bias** en el paso 2c: mitigado por el bloqueo previo (2b).
- **Coste/latencia**: la generación de Claude es 1 vez (cacheada); la fusión asistida (fase 2) añade 1 llamada.
- **Evidencia sin clasificar por dimensión**: resuelto con opción C (evidencia completa una vez + lente de criterios por área).