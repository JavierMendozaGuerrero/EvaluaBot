"""
Sesión de evaluación anual asistida — flujo conversacional por área.

Flujo:
  1. Confirmación de identidad (¿evalúas a X?).
  2. Por cada área (gestión de proyecto, calidad, ... + liderazgo si aplica + contribution + resultado):
       a. Se muestra la EVIDENCIA que Claude consideró de esa área (las fuentes que citó).
       b. El CA escribe sus puntos / su opinión (pregunta abierta).
       c. Claude compara con lo que él pondría y responde conversacionalmente
          ("yo pondría esto [bullets con citas], ¿qué opinas?"). Se puede seguir hablando.
       d. El CA confirma el área → se fija el texto final acordado.
  3. Finalizar → genera el borrador con los huecos; Claude rellena las áreas con lo acordado.

Persistencia: JSON local junto al informe (`sesion_anual_{slug}.json`).
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


# ── Secciones del recorrido ───────────────────────────────────────────────────

def _secciones(cargo: str) -> list[tuple[str, str]]:
    """Áreas por las que pasa el CA, en orden. Todas."""
    secs = list(sk._DIMS_PROYECTOS)
    if any(c in cargo.strip().lower() for c in sk._REQUIERE_LIDERAZGO):
        secs += list(sk._DIMS_LIDERAZGO)
    secs.append(("contribution_to_firm", "Contribution to the firm"))
    secs.append(("resultado", "Resultado global"))
    return secs


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


def _evidencia_de_area(comentarios: dict, fuentes: dict, clave: str) -> list[dict]:
    """Evidencia que Claude consideró para un área = las fuentes que citó en sus bullets."""
    val = comentarios.get(clave)
    textos = list(val.values()) if isinstance(val, dict) else ([val] if isinstance(val, str) else [])
    ids = []
    for t in textos:
        ids += sk._CITE_RE.findall(t or "")
    vistos, items = set(), []
    for cid in ids:
        if cid in vistos or cid not in fuentes:
            continue
        vistos.add(cid)
        s = fuentes[cid]
        items.append({
            "cid": cid, "tipo": s.get("tipo", ""), "label": s.get("label", ""),
            "evaluador": s.get("evaluador", ""), "texto": s.get("texto", ""), "fecha": s.get("fecha", ""),
        })
    items.sort(key=lambda x: x["fecha"] or "")
    return items


def _pregunta_area(etiqueta: str) -> str:
    return (f"¿Qué puntos principales quieres que salgan en el informe sobre «{etiqueta}»? "
            f"Cuéntame tu opinión y qué destacarías.")


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
            "areas": {},          # {clave: {conversacion:[{rol,texto}], propuesta, confirmada, texto_final}}
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
    areas = sesion.get("areas", {})
    confirmadas = sum(1 for c, _ in secciones if areas.get(c, {}).get("confirmada"))
    return {
        "advisee": sesion["advisee"],
        "ca": sesion.get("ca", ""),
        "cargo": sesion.get("cargo", ""),
        "anio": sesion.get("anio"),
        "estado": sesion.get("estado"),
        "identidadConfirmada": sesion.get("identidad_confirmada", False),
        "proyectos": _proyectos_de(sesion["emp_data"]),
        "secciones": [{"clave": c, "etiqueta": e, "confirmada": bool(areas.get(c, {}).get("confirmada"))}
                      for c, e in secciones],
        "totalSecciones": len(secciones),
        "seccionesConfirmadas": confirmadas,
    }


def confirmar_identidad(advisee: str) -> dict:
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    sesion["identidad_confirmada"] = True
    _guardar(slug, sesion)
    return {"ok": True}


def obtener_area(advisee: str, clave: str) -> dict:
    """Datos de un área: evidencia que Claude consideró + pregunta abierta + conversación."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    secciones = dict(_secciones(sesion.get("cargo", "")))
    if clave not in secciones:
        raise ValueError(f"Sección desconocida: {clave}")
    comentarios = _asegurar_comentarios(slug, sesion)
    _, fuentes = _emp_y_fuentes(sesion)
    area = sesion.get("areas", {}).get(clave, {})
    return {
        "clave": clave,
        "etiqueta": secciones[clave],
        "evidencia": _evidencia_de_area(comentarios, fuentes, clave),
        "pregunta": _pregunta_area(secciones[clave]),
        "conversacion": area.get("conversacion", []),
        "propuesta": area.get("propuesta", ""),
        "confirmada": area.get("confirmada", False),
    }


def _claude_conversa_area(etiqueta: str, evidencia: list, claude_bullets: str, conversacion: list) -> dict:
    """Llama a Claude para reaccionar a los puntos del CA y proponer los bullets del área."""
    ev_txt = "\n".join(f"[{e['cid']}] {e['label']} — {e['texto']}" for e in evidencia) or "(sin evidencia)"
    conv_txt = "\n".join(f"{'CA' if m['rol'] == 'ca' else 'IA'}: {m['texto']}" for m in conversacion)
    if not anthropic_client:
        return {"mensaje": "(IA no disponible) Tomo nota de tus puntos.", "propuesta": claude_bullets}
    system = (
        f"Eres el director de RRHH de IGENERIS. Estás co-redactando con el CA el área «{etiqueta}» del "
        "informe anual. Tienes la EVIDENCIA (con citas [E3]/[O1]/[P2]/[S1]/[B1]) y TU VALORACIÓN basada "
        "en ella. El CA te da sus puntos. Responde de forma conversacional y BREVE: di qué pondrías tú, "
        "señala dónde coincides o difieres con el CA, y pregúntale su opinión para cerrar el área. "
        "NO inventes: cada afirmación de la propuesta debe llevar su cita [X#] de la evidencia. "
        'Devuelve SOLO un JSON válido: {"mensaje": "tu respuesta conversacional", '
        '"propuesta": "los bullets finales del área, uno por línea, cada uno con su cita"}.'
    )
    user = (
        f"ÁREA: {etiqueta}\n\nEVIDENCIA:\n{ev_txt}\n\n"
        f"TU VALORACIÓN INICIAL:\n{claude_bullets or '(sin información)'}\n\n"
        f"CONVERSACIÓN:\n{conv_txt}"
    )
    try:
        resp = anthropic_client.messages.create(
            model="claude-sonnet-4-6", max_tokens=1500, temperature=0, system=system,
            messages=[{"role": "user", "content": user}],
        )
        t = "".join(b.text for b in resp.content if b.type == "text").strip()
        if t.startswith("```"):
            t = t.split("```", 2)[1]
            if t.startswith("json"):
                t = t[4:]
            t = t.rsplit("```", 1)[0]
        data = json.loads(t.strip())
        return {"mensaje": (data.get("mensaje") or "").strip(),
                "propuesta": (data.get("propuesta") or "").strip()}
    except Exception:
        logging.exception("Fallo en la conversación del área")
        return {"mensaje": "He tenido un problema al responder; reformula o reinténtalo.",
                "propuesta": claude_bullets}


def responder_area(advisee: str, clave: str, texto: str) -> dict:
    """El CA aporta sus puntos; Claude compara con su versión y responde conversacionalmente."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    secciones = dict(_secciones(sesion.get("cargo", "")))
    if clave not in secciones:
        raise ValueError(f"Sección desconocida: {clave}")
    if not (texto or "").strip():
        raise ValueError("Escribe tus puntos antes de enviar.")

    comentarios = _asegurar_comentarios(slug, sesion)
    _, fuentes = _emp_y_fuentes(sesion)
    claude_bullets = _claude_texto(comentarios, clave)
    evidencia = _evidencia_de_area(comentarios, fuentes, clave)

    area = sesion.setdefault("areas", {}).setdefault(
        clave, {"conversacion": [], "propuesta": "", "confirmada": False})
    area["conversacion"].append({"rol": "ca", "texto": texto.strip()})

    res = _claude_conversa_area(secciones[clave], evidencia, claude_bullets, area["conversacion"])
    area["conversacion"].append({"rol": "ia", "texto": res["mensaje"]})
    area["propuesta"] = res["propuesta"] or area.get("propuesta", "")
    _guardar(slug, sesion)
    return {"mensaje": res["mensaje"], "propuesta": area["propuesta"], "conversacion": area["conversacion"]}


def confirmar_area(advisee: str, clave: str) -> dict:
    """Cierra un área: fija el texto final = la propuesta acordada."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    area = sesion.get("areas", {}).get(clave)
    if not area or not area.get("conversacion"):
        raise ValueError("Primero conversa con la IA sobre esta área.")
    area["confirmada"] = True
    area["texto_final"] = area.get("propuesta", "")
    _guardar(slug, sesion)
    return _resumen_estado(sesion)


def estado_sesion(advisee: str) -> dict:
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    return _resumen_estado(sesion)


def finalizar_sesion(advisee: str) -> dict:
    """Exige todas las áreas confirmadas. Genera el borrador con lo acordado (huecos en blanco)."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    secciones = _secciones(sesion.get("cargo", ""))
    areas = sesion.get("areas", {})
    faltan = [e for c, e in secciones if not areas.get(c, {}).get("confirmada")]
    if faltan:
        raise ValueError("Faltan áreas por confirmar: " + ", ".join(faltan))

    _, fuentes = _emp_y_fuentes(sesion)
    comentarios_final: dict = {"_fuentes": fuentes, "_avisos_verificacion": [], "_bullets_descartados": []}
    for clave, _ in secciones:
        comentarios_final[clave] = areas[clave].get("texto_final", "")

    emp_data = sesion["emp_data"]
    cargo = sesion.get("cargo", "")
    sk.guardar_informe_anual_word(emp_data, comentarios_final, cargo=cargo)
    sk.guardar_informe_anual_html(emp_data, comentarios_final, cargo=cargo)

    sesion["estado"] = "completada"
    sesion["completada_en"] = _ahora()
    _guardar(slug, sesion)

    # Log de auditoría en Notion (best-effort)
    try:
        guardar_log_evaluacion_anual(advisee, sesion.get("ca", ""), sesion.get("anio"),
                                     _entradas_log(sesion, secciones))
    except Exception:
        logging.exception("No se pudo persistir el log de evaluación anual en Notion")

    return {"ok": True, "estado": "completada"}


def _entradas_log(sesion: dict, secciones: list) -> list[dict]:
    areas = sesion.get("areas", {})
    out = []
    for clave, etiqueta in secciones:
        a = areas.get(clave)
        if not a:
            continue
        ca_msgs = "\n".join(m["texto"] for m in a.get("conversacion", []) if m["rol"] == "ca")
        out.append({
            "clave": clave, "etiqueta": etiqueta,
            "caTexto": ca_msgs, "claudeTexto": a.get("propuesta", ""),
            "eleccion": "acordado", "textoFinal": a.get("texto_final", ""),
            "divergencia": False, "en": _ahora(),
        })
    return out