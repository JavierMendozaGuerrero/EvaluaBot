"""
Sesión de evaluación anual asistida (preguntas previas al CA) — Fase 1.

Flujo (ver docs/plan-preguntas-previas-ca.md):
  1. Confirmación de identidad.
  2. Lectura de la evidencia en bruto POR BLOQUES (cuatrimestres), sin sobrecargar.
  3. Loop por dimensión (las 5 + liderazgo si aplica + contribution + resultado):
       a. El CA escribe su valoración → SE BLOQUEA (anti-anclaje).
       b. Solo entonces se revela lo que redactó Claude.
       c. El CA decide: su versión / la de Claude / fusión → queda registrado.
  4. Publicar (exige completar el loop).

Persistencia: JSON local junto al informe (`sesion_anual_{slug}.json`). Migrable a Notion en Fase 2.
"""

import json
import logging
import os
from datetime import datetime, timezone

from . import config
from .clients import anthropic_client
from .notion_service import guardar_log_evaluacion_anual
from .utils import slug_archivo
from . import skill_informes_anual as sk


# ── Definición de secciones del loop ──────────────────────────────────────────

def _secciones(cargo: str) -> list[tuple[str, str]]:
    """Dimensiones por las que pasa el CA, en orden. Todas (no solo las de proyecto)."""
    secs = list(sk._DIMS_PROYECTOS)
    if any(c in cargo.strip().lower() for c in sk._REQUIERE_LIDERAZGO):
        secs += list(sk._DIMS_LIDERAZGO)
    secs.append(("contribution_to_firm", "Contribution to the firm"))
    secs.append(("resultado", "Resultado global"))
    return secs


def _criterios_dimension(cargo: str, clave: str) -> list[str]:
    """Criterios DTI de esa área para el cargo y superiores (la 'lente' del CA)."""
    dim_crit = sk._CRITERIOS_DTI.get(clave, {})
    if not dim_crit:
        return []
    nivel = sk._nivel_cargo(cargo)
    if not nivel or nivel not in sk._ORDEN_CARGO:
        return list(dim_crit.get("analyst", []))
    idx = sk._ORDEN_CARGO.index(nivel)
    out: list[str] = []
    for lvl in sk._ORDEN_CARGO[max(0, idx - 1):]:
        out.extend(dim_crit.get(lvl, []))
    return out


# ── Persistencia (JSON local) ─────────────────────────────────────────────────

def _ruta_sesion(slug: str) -> str:
    return os.path.join(config.CARPETA_WEB, f"sesion_anual_{slug}.json")


def _leer(slug: str) -> dict | None:
    ruta = _ruta_sesion(slug)
    if not os.path.exists(ruta):
        return None
    try:
        with open(ruta, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        logging.exception("No se pudo leer la sesión anual %s", slug)
        return None


def _guardar(slug: str, data: dict) -> None:
    data["actualizada_en"] = _ahora()
    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    with open(_ruta_sesion(slug), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _proyectos_de(emp_data: dict) -> list[str]:
    vistos, out = set(), []
    for ev in emp_data.get("evaluaciones", []) + emp_data.get("evals_proyecto", []):
        p = (ev.get("proyecto") or "").strip()
        if p and p.lower() not in vistos:
            vistos.add(p.lower()); out.append(p)
    return out


def _claude_texto(comentarios: dict, clave: str) -> str:
    """Aplana lo que redactó Claude para una sección a texto legible."""
    val = comentarios.get(clave)
    if isinstance(val, dict):
        partes = []
        for nivel, label in sk._LABELS_NIVEL:
            t = (val.get(nivel) or "").strip()
            if t:
                partes.append(f"{label}:\n{t}")
        return "\n\n".join(partes)
    return val or ""


_BLOQUES = [(1, 4, "Enero – Abril"), (5, 8, "Mayo – Agosto"), (9, 12, "Septiembre – Diciembre")]


def _indice_bloque(fecha: str) -> int:
    try:
        mes = int(fecha[5:7])
        for i, (ini, fin, _) in enumerate(_BLOQUES):
            if ini <= mes <= fin:
                return i
    except Exception:
        pass
    return len(_BLOQUES)  # sin fecha → bloque final


def _bloques_evidencia(fuentes: dict) -> list[dict]:
    """Agrupa la evidencia en bloques cronológicos (cuatrimestres). Omite bloques vacíos."""
    grupos: dict[int, list] = {}
    for cid, src in fuentes.items():
        idx = _indice_bloque(src.get("fecha", ""))
        item = {
            "cid": cid, "tipo": src.get("tipo", ""), "label": src.get("label", ""),
            "evaluador": src.get("evaluador", ""), "texto": src.get("texto", ""),
            "fecha": src.get("fecha", ""),
        }
        grupos.setdefault(idx, []).append(item)
    bloques = []
    etiquetas = [e for _, _, e in _BLOQUES] + ["Sin fecha"]
    for idx in sorted(grupos):
        items = sorted(grupos[idx], key=lambda x: x["fecha"] or "9999")
        bloques.append({"etiqueta": etiquetas[idx], "items": items})
    return bloques


def _emp_y_fuentes(sesion: dict) -> tuple[dict, dict]:
    emp_data = sesion["emp_data"]
    _, fuentes = sk._formatear_contexto(emp_data)
    return emp_data, fuentes


def _asegurar_comentarios(slug: str, sesion: dict) -> dict:
    """Genera (una vez) y cachea en la sesión lo que redactó Claude."""
    if sesion.get("comentarios"):
        return sesion["comentarios"]
    comentarios = sk.interpretar_evaluaciones_anual(sesion["emp_data"], cargo=sesion.get("cargo", ""))
    sesion["comentarios"] = comentarios
    _guardar(slug, sesion)
    return comentarios


# ── API del módulo ────────────────────────────────────────────────────────────

def iniciar_sesion(advisee: str, cargo: str = "") -> dict:
    """Crea o recupera la sesión. Devuelve identidad + progreso. NO genera Claude todavía."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if sesion is None:
        emp_data = sk.obtener_datos_empleado_anual(advisee)
        if not emp_data.get("opiniones_ca") and not emp_data.get("evaluaciones") \
           and not emp_data.get("evals_proyecto") and not emp_data.get("seguimiento") \
           and not emp_data.get("barbecho"):
            raise ValueError(f"No hay datos de evaluación para '{advisee}'.")
        sesion = {
            "advisee": advisee,
            "ca": emp_data.get("ca", ""),
            "cargo": cargo,
            "anio": datetime.now(timezone.utc).year - 1,
            "estado": "en_progreso",
            "identidad_confirmada": False,
            "emp_data": emp_data,
            "respuestas_ca": {},      # {clave: {texto, bloqueada_en}}
            "decisiones": {},         # {clave: {eleccion, texto_final, claude_texto, en}}
            "comentarios": None,
            "creada_en": _ahora(),
        }
        _guardar(slug, sesion)
    elif cargo and cargo != sesion.get("cargo"):
        sesion["cargo"] = cargo
        _guardar(slug, sesion)

    return _resumen_estado(sesion)


def _resumen_estado(sesion: dict) -> dict:
    secciones = _secciones(sesion.get("cargo", ""))
    completadas = sum(1 for c, _ in secciones if c in sesion.get("decisiones", {}))
    return {
        "advisee": sesion["advisee"],
        "ca": sesion.get("ca", ""),
        "cargo": sesion.get("cargo", ""),
        "anio": sesion.get("anio"),
        "estado": sesion.get("estado"),
        "identidadConfirmada": sesion.get("identidad_confirmada", False),
        "proyectos": _proyectos_de(sesion["emp_data"]),
        "secciones": [{"clave": c, "etiqueta": e,
                       "respondida": c in sesion.get("respuestas_ca", {}),
                       "decidida": c in sesion.get("decisiones", {})}
                      for c, e in secciones],
        "totalSecciones": len(secciones),
        "seccionesDecididas": completadas,
        "nBloquesEvidencia": len(_bloques_evidencia(_emp_y_fuentes(sesion)[1])),
    }


def confirmar_identidad(advisee: str) -> dict:
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    sesion["identidad_confirmada"] = True
    _guardar(slug, sesion)
    return {"ok": True}


def obtener_evidencia(advisee: str, bloque: int = 0) -> dict:
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    _, fuentes = _emp_y_fuentes(sesion)
    bloques = _bloques_evidencia(fuentes)
    if not bloques:
        return {"bloque": 0, "totalBloques": 0, "etiqueta": "", "items": [], "hayMas": False}
    bloque = max(0, min(bloque, len(bloques) - 1))
    return {
        "bloque": bloque,
        "totalBloques": len(bloques),
        "etiqueta": bloques[bloque]["etiqueta"],
        "items": bloques[bloque]["items"],
        "hayMas": bloque < len(bloques) - 1,
    }


def obtener_dimension(advisee: str, clave: str) -> dict:
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    secciones = dict(_secciones(sesion.get("cargo", "")))
    if clave not in secciones:
        raise ValueError(f"Sección desconocida: {clave}")

    respuesta = sesion.get("respuestas_ca", {}).get(clave)
    bloqueada = respuesta is not None
    out = {
        "clave": clave,
        "etiqueta": secciones[clave],
        "criterios": _criterios_dimension(sesion.get("cargo", ""), clave),
        "pregunta": _pregunta_dimension(secciones[clave], sesion["advisee"]),
        "respuestaCa": respuesta.get("texto", "") if respuesta else "",
        "bloqueada": bloqueada,
        "decision": sesion.get("decisiones", {}).get(clave),
    }
    # Solo se revela a Claude DESPUÉS de que el CA haya bloqueado su respuesta (anti-anclaje)
    if bloqueada:
        comentarios = _asegurar_comentarios(slug, sesion)
        out["claude"] = _claude_texto(comentarios, clave)
    else:
        out["claude"] = None
    return out


def _pregunta_dimension(etiqueta: str, nombre: str) -> str:
    return (f"¿Cómo valoras a {nombre} en «{etiqueta}» este año? "
            f"Ten en cuenta la EVOLUCIÓN a lo largo del año (no es lo mismo el principio que el final) "
            f"y entre proyectos. Escribe tu valoración antes de ver la de la IA.")


def responder_dimension(advisee: str, clave: str, texto: str) -> dict:
    """Bloquea la respuesta del CA para una dimensión. A partir de aquí ya puede ver a Claude."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    if clave not in dict(_secciones(sesion.get("cargo", ""))):
        raise ValueError(f"Sección desconocida: {clave}")
    if not (texto or "").strip():
        raise ValueError("Escribe tu valoración antes de continuar.")
    sesion.setdefault("respuestas_ca", {})[clave] = {
        "texto": texto.strip(), "bloqueada_en": _ahora(),
    }
    _guardar(slug, sesion)
    # Devuelve ya la versión de Claude para comparar
    return obtener_dimension(advisee, clave)


def decidir_dimension(advisee: str, clave: str, eleccion: str, texto_final: str = "") -> dict:
    """Registra la decisión del CA tras comparar con Claude: 'mia' | 'claude' | 'fusion'."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    if eleccion not in ("mia", "claude", "fusion"):
        raise ValueError("Elección no válida.")
    respuesta = sesion.get("respuestas_ca", {}).get(clave)
    if not respuesta:
        raise ValueError("Primero responde y bloquea tu valoración.")
    comentarios = _asegurar_comentarios(slug, sesion)
    claude_texto = _claude_texto(comentarios, clave)

    if eleccion == "mia":
        final = respuesta["texto"]
    elif eleccion == "claude":
        final = claude_texto
    else:  # fusion
        final = (texto_final or "").strip()
        if not final:
            raise ValueError("Escribe el texto de la fusión.")

    sesion.setdefault("decisiones", {})[clave] = {
        "eleccion": eleccion, "texto_final": final,
        "ca_texto": respuesta["texto"], "claude_texto": claude_texto,
        "divergencia": eleccion != "claude",
        "en": _ahora(),
    }
    _guardar(slug, sesion)
    return {"ok": True}


def sugerir_fusion(advisee: str, clave: str) -> dict:
    """Fase 2: Claude propone una fusión de la valoración del CA + la suya (respetando la del CA)."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    respuesta = sesion.get("respuestas_ca", {}).get(clave)
    if not respuesta:
        raise ValueError("Primero responde y bloquea tu valoración.")
    comentarios = _asegurar_comentarios(slug, sesion)
    claude_texto = _claude_texto(comentarios, clave)
    if not anthropic_client:
        # Sin IA disponible: fusión trivial (texto del CA + el de la IA)
        return {"sugerencia": (respuesta["texto"] + "\n" + claude_texto).strip()}

    system = (
        "Eres editor de informes de RRHH. Te doy la VALORACIÓN DEL CA (su juicio, que manda) y la "
        "VALORACIÓN DE LA IA (basada en datos, con citas tipo [E3]). Redacta una versión final en "
        "bullets que RESPETE el criterio del CA e incorpore los matices y citas de la IA que lo "
        "apoyen. Mantén las citas [X#] que ya existan; NO inventes citas ni afirmaciones nuevas. "
        "Devuelve solo el texto final, sin preámbulos."
    )
    try:
        resp = anthropic_client.messages.create(
            model="claude-sonnet-4-6", max_tokens=1200, temperature=0, system=system,
            messages=[{"role": "user", "content": (
                f"VALORACIÓN DEL CA:\n{respuesta['texto']}\n\n"
                f"VALORACIÓN DE LA IA:\n{claude_texto or '(sin información)'}"
            )}],
        )
        sugerencia = "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception:
        logging.exception("Fallo al sugerir fusión")
        sugerencia = (respuesta["texto"] + "\n" + claude_texto).strip()
    return {"sugerencia": sugerencia}


def estado_sesion(advisee: str) -> dict:
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    return _resumen_estado(sesion)


def finalizar_sesion(advisee: str) -> dict:
    """Exige el loop completo. Genera el borrador con las decisiones del CA y marca completada."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    secciones = _secciones(sesion.get("cargo", ""))
    faltan = [e for c, e in secciones if c not in sesion.get("decisiones", {})]
    if faltan:
        raise ValueError("Faltan secciones por decidir: " + ", ".join(faltan))

    _, fuentes = _emp_y_fuentes(sesion)
    # Comentarios reconciliados: el texto final de cada sección es la decisión del CA
    comentarios_final: dict = {"_fuentes": fuentes, "_avisos_verificacion": [], "_bullets_descartados": []}
    for clave, _ in secciones:
        comentarios_final[clave] = sesion["decisiones"][clave]["texto_final"]

    emp_data = sesion["emp_data"]
    cargo = sesion.get("cargo", "")
    sk.guardar_informe_anual_word(emp_data, comentarios_final, cargo=cargo)
    sk.guardar_informe_anual_html(emp_data, comentarios_final, cargo=cargo)

    sesion["estado"] = "completada"
    sesion["completada_en"] = _ahora()
    _guardar(slug, sesion)

    # Fase 2: persistir el log de auditoría en Notion (best-effort, no rompe el flujo)
    try:
        entradas = log_auditoria(advisee).get("entradas", [])
        guardar_log_evaluacion_anual(advisee, sesion.get("ca", ""), sesion.get("anio"), entradas)
    except Exception:
        logging.exception("No se pudo persistir el log de evaluación anual en Notion")

    return {"ok": True, "estado": "completada"}


def log_auditoria(advisee: str) -> dict:
    """Log interno (CA/admin): respuestas del CA vs Claude y decisiones. No lo ve el advisee."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    secciones = _secciones(sesion.get("cargo", ""))
    entradas = []
    for clave, etiqueta in secciones:
        d = sesion.get("decisiones", {}).get(clave)
        if not d:
            continue
        entradas.append({
            "clave": clave, "etiqueta": etiqueta,
            "caTexto": d.get("ca_texto", ""), "claudeTexto": d.get("claude_texto", ""),
            "eleccion": d.get("eleccion"), "textoFinal": d.get("texto_final", ""),
            "divergencia": d.get("divergencia", False), "en": d.get("en"),
        })
    return {"advisee": advisee, "entradas": entradas}