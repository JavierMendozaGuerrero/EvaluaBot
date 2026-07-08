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
from .notion_service import (
    guardar_log_evaluacion_anual,
    buscar_empleado_y_cargo,
    obtener_criterios_evaluacion,
)
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


_STOP = {"de", "del", "la", "el", "con", "al", "a", "los", "las", "y", "the", "of", "to"}


def _sig_words(s: str) -> set:
    return {w for w in (s or "").lower().split() if w not in _STOP}


def _match_dim_label(etiqueta: str, criterios: dict) -> str | None:
    """Empareja la etiqueta del área con la dimensión de Notion por solape de palabras."""
    objetivo = _sig_words(etiqueta)
    mejor, mejor_score = None, 0
    for dim_label in criterios:
        score = len(objetivo & _sig_words(dim_label))
        if score > mejor_score:
            mejor, mejor_score = dim_label, score
    return mejor if mejor_score > 0 else None


def _criterios_area(cargo: str, clave: str, etiqueta: str, idioma: str = "es", nombre: str = "") -> list[dict]:
    """Criterios de esa área para el cargo y superiores, DESDE NOTION (fallback hardcoded).

    El grupo (Negocio/Palantir/MiddleOffice) sale de la columna Área de Notion (por nombre);
    si no consta, se infiere del cargo. Devuelve [{"nivel": "Manager", "criterios": [...]}, ...].
    """
    grupo = sk._grupo_empleado(nombre, cargo)
    nivel = sk._nivel_cargo(cargo)
    try:
        crit_notion = obtener_criterios_evaluacion(grupo, idioma) or {}
    except Exception:
        crit_notion = {}

    def _seleccionar(niveles_dict: dict) -> list[dict]:
        # Normaliza las etiquetas de nivel (ES de Notion o inglesas del fallback)
        # a la escala canonica para poder ordenarlas y seleccionarlas.
        por_canon: dict = {}
        for label, crits in niveles_dict.items():
            canon = sk._nivel_canonico(label)
            if canon and crits:
                por_canon[canon] = (label, crits)
        orden = sk._ORDEN_CARGO
        idx = orden.index(nivel) if nivel and nivel in orden else -1
        presentes = [c for c in orden if c in por_canon]
        sel = presentes[max(0, idx - 1):] if idx >= 0 else presentes
        return [{"nivel": por_canon[c][0], "criterios": por_canon[c][1]} for c in sel]

    dim_label = _match_dim_label(etiqueta, crit_notion) if crit_notion else None
    if dim_label:
        return _seleccionar(crit_notion[dim_label])
    # Fallback al diccionario hardcodeado (por clave)
    dim_crit = sk._CRITERIOS_DTI.get(clave, {})
    return _seleccionar(dim_crit) if dim_crit else []


def _generar_diagnostico(cargo: str, etiqueta: str, criterios: list, evidencia: list) -> str:
    """Claude analiza: a qué nivel está en esta área y qué le falta para subir (con citas)."""
    if not anthropic_client or not evidencia:
        return ""
    crit_txt = "\n".join(f"[{c['nivel']}]: " + " / ".join(c['criterios']) for c in criterios) \
        or "(sin criterios en Notion para esta área)"
    ev_txt = "\n".join(f"[{e['cid']}] {e['label']} — {e['texto']}" for e in evidencia)
    system = (
        f"Eres el director de RRHH de IGENERIS. La persona tiene el cargo: {cargo or 'no especificado'}. "
        f"Área: «{etiqueta}». Con los CRITERIOS por nivel (de Notion) y la EVIDENCIA (con citas), evalúa de "
        "forma BREVE, directa y honesta: (1) a qué nivel está en esta área y POR QUÉ, citando la evidencia "
        "concreta [X#]; (2) qué le falta (gaps concretos) para consolidar su nivel o para subir al siguiente. "
        "Devuelve texto plano, 2-4 frases, con las citas [X#] correspondientes. NO inventes: solo lo que "
        "la evidencia respalde."
        + config.INSTRUCCION_ANTIINYECCION
    )
    user = f"CRITERIOS POR NIVEL:\n{crit_txt}\n\nEVIDENCIA:\n{ev_txt}"
    try:
        resp = anthropic_client.messages.create(
            model="claude-sonnet-4-6", max_tokens=500, temperature=0, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception:
        logging.exception("Fallo generando el diagnóstico del área")
        return ""


# ── API del módulo ────────────────────────────────────────────────────────────

def _cargo_de(advisee: str) -> str:
    """Cargo del empleado desde 'Lista de empleados' (columna Cargo) en Notion."""
    try:
        _, cargo = buscar_empleado_y_cargo(advisee)
        return (cargo or "").strip()
    except Exception:
        logging.exception("No se pudo leer el cargo de '%s'", advisee)
        return ""


def iniciar_sesion(advisee: str, cargo: str = "") -> dict:
    """Crea o recupera la sesión. Devuelve identidad + progreso. NO genera Claude todavía."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not cargo:
        cargo = _cargo_de(advisee)  # cargo real desde Notion (Lista de empleados)
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


def eliminar_sesion(advisee: str) -> dict:
    """Borra por completo la sesión anual (conversaciones, áreas confirmadas y
    borradores generados) para poder empezar de cero. La siguiente llamada a
    iniciar_sesion() reconstruye la sesión desde los datos de Notion."""
    slug = slug_archivo(advisee)
    ruta = _ruta_sesion(slug)
    try:
        if os.path.exists(ruta):
            os.remove(ruta)
    except Exception:
        logging.exception("No se pudo eliminar la sesión anual %s", slug)
        raise
    # Limpia también los borradores ya generados, si los hubiera.
    for nombre_fichero in (f"informe_anual_{slug}.html", f"informe_anual_{slug}.docx"):
        f = os.path.join(config.CARPETA_WEB, nombre_fichero)
        try:
            if os.path.exists(f):
                os.remove(f)
        except Exception:
            logging.exception("No se pudo eliminar el borrador %s", nombre_fichero)
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
    evidencia = _evidencia_de_area(comentarios, fuentes, clave)
    area = sesion.setdefault("areas", {}).setdefault(
        clave, {"conversacion": [], "propuesta": "", "confirmada": False})

    # Criterios del área (siempre, sin API). Se muestran ANTES de que el CA opine.
    # El DIAGNÓSTICO (nivel + gaps) NO se genera aquí: solo tras la opinión inicial del CA.
    if not area.get("criterios"):
        area["criterios"] = _criterios_area(sesion.get("cargo", ""), clave, secciones[clave], nombre=sesion.get("advisee", ""))
        if area["criterios"]:
            _guardar(slug, sesion)

    # En el panel solo se muestran los criterios del cargo actual; el rango completo
    # (area["criterios"]) se conserva para el diagnostico/comparacion posterior.
    nivel_actual = sk._nivel_cargo(sesion.get("cargo", ""))
    criterios_full = area.get("criterios", [])
    criterios_mostrar = (
        [c for c in criterios_full if sk._nivel_canonico(c.get("nivel", "")) == nivel_actual]
        if nivel_actual else criterios_full
    )

    return {
        "clave": clave,
        "etiqueta": secciones[clave],
        "cargo": sesion.get("cargo", ""),
        "evidencia": evidencia,
        "criterios": criterios_mostrar,
        "diagnostico": area.get("diagnostico", ""),  # vacío hasta que el CA mande su opinión
        "pregunta": _pregunta_area(secciones[clave]),
        "conversacion": area.get("conversacion", []),
        "propuesta": area.get("propuesta", ""),
        "confirmada": area.get("confirmada", False),
    }


def _claude_conversa_area(etiqueta: str, evidencia: list, claude_bullets: str, conversacion: list,
                          criterios: list | None = None, diagnostico: str = "", cargo: str = "") -> dict:
    """Llama a Claude para reaccionar a los puntos del CA y proponer los bullets del área."""
    ev_txt = "\n".join(f"[{e['cid']}] {e['label']} — {e['texto']}" for e in evidencia) or "(sin evidencia)"
    conv_txt = "\n".join(f"{'CA' if m['rol'] == 'ca' else 'IA'}: {m['texto']}" for m in conversacion)
    crit_txt = "\n".join(f"[{c['nivel']}]: " + " / ".join(c['criterios']) for c in (criterios or [])) \
        or "(sin criterios en Notion)"
    if not anthropic_client:
        return {"mensaje": "(IA no disponible) Tomo nota de tus puntos.", "propuesta": claude_bullets}

    instrucciones = (
        "Eres el director de RRHH de IGENERIS: riguroso, exigente y directo. Co-rediges con el CA el área "
        f"«{etiqueta}» del informe anual. Tu papel NO es complacer: eres un SPARRING CRÍTICO que ayuda al "
        "CA a pensar mejor y a que el informe sea justo y esté respaldado por datos. Sé conversacional pero "
        "breve e incisivo.\n\n"
        "REGLAS:\n"
        "- DESAFÍA activamente: cuestiona las valoraciones del CA. Si algo que dice NO está respaldado por la "
        "evidencia, dilo sin rodeos y pídele que lo justifique. Señala posibles sesgos: recencia (dar más "
        "peso a lo último), efecto halo, basarse en un solo evaluador, o generalizar sin base. Haz de abogado "
        "del diablo cuando toque.\n"
        "- DEFIENDE con datos: cuando propongas algo, respáldalo con su cita [X#]. Si el CA propone una "
        "alternativa, contrástala con la evidencia; si no está respaldada, NO la aceptes solo por complacer.\n"
        "- CEDE solo si el CA aporta un argumento sólido o evidencia, o si es una decisión que le corresponde "
        "a él como CA (y en ese caso dilo claramente).\n"
        "- CRITERIOS Y NIVEL: apóyate en los CRITERIOS por cargo (de Notion) y en el DIAGNÓSTICO de nivel. "
        "Juzga contra el criterio del cargo de la persona, no contra tu opinión; y cuando ayude, recuérdale "
        "al CA a qué nivel está y qué le falta para subir.\n"
        "- REFERENCIAS: si el CA pregunta por una referencia (p. ej. 'referencia de E3', '¿qué dice [O1]?'), "
        "responde con el TEXTO LITERAL de esa fuente (tipo, proyecto/evaluador, fecha y contenido).\n"
        "- NO inventes: cada afirmación de la propuesta debe llevar su cita [X#] de la evidencia.\n\n"
        'Devuelve SOLO un JSON válido: {"mensaje": "tu respuesta conversacional", '
        '"propuesta": "los bullets finales del área, uno por línea, cada uno con su cita"}.'
        + config.INSTRUCCION_ANTIINYECCION
    )
    # La evidencia, criterios, diagnóstico y tu valoración son ESTÁTICOS durante todo el debate del área
    # → se cachean (prompt caching): los turnos siguientes casi no pagan esos tokens (mismo modelo, menos API).
    contexto_estatico = (
        f"ÁREA: {etiqueta}\nCARGO DE LA PERSONA: {cargo or 'no especificado'}\n\n"
        f"CRITERIOS POR NIVEL (Notion):\n{crit_txt}\n\n"
        f"DIAGNÓSTICO DE NIVEL:\n{diagnostico or '(sin diagnóstico)'}\n\n"
        f"EVIDENCIA:\n{ev_txt}\n\n"
        f"TU VALORACIÓN INICIAL:\n{claude_bullets or '(sin información)'}"
    )
    def _crear(system_arg):
        return anthropic_client.messages.create(
            model="claude-sonnet-4-6", max_tokens=1500, temperature=0,
            system=system_arg,
            messages=[{"role": "user", "content": f"CONVERSACIÓN:\n{conv_txt}"}],
        )

    system_cacheado = [
        {"type": "text", "text": instrucciones},
        {"type": "text", "text": contexto_estatico, "cache_control": {"type": "ephemeral"}},
    ]
    try:
        try:
            resp = _crear(system_cacheado)
        except Exception:
            # Si el prompt caching no está soportado, reintenta sin caché (misma calidad, sin ahorro)
            logging.warning("Prompt caching no disponible; reintento sin caché")
            resp = _crear(instrucciones + "\n\n" + contexto_estatico)
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
    if "criterios" not in area:
        area["criterios"] = _criterios_area(sesion.get("cargo", ""), clave, secciones[clave], nombre=sesion.get("advisee", ""))
    # El DIAGNÓSTICO (nivel + gaps) se genera aquí: solo cuando el CA manda su opinión inicial.
    if "diagnostico" not in area:
        area["diagnostico"] = _generar_diagnostico(
            sesion.get("cargo", ""), secciones[clave], area.get("criterios") or [], evidencia)
    area["conversacion"].append({"rol": "ca", "texto": texto.strip()})

    res = _claude_conversa_area(
        secciones[clave], evidencia, claude_bullets, area["conversacion"],
        criterios=area.get("criterios"), diagnostico=area.get("diagnostico", ""),
        cargo=sesion.get("cargo", ""))
    area["conversacion"].append({"rol": "ia", "texto": res["mensaje"]})
    area["propuesta"] = res["propuesta"] or area.get("propuesta", "")
    # Si el CA reabre un área ya confirmada y sigue hablando, la propuesta se
    # actualiza pero texto_final (lo que va al informe) se quedó congelado en
    # confirmar_area(). Desconfirmamos para que finalizar_sesion() exija volver
    # a confirmarla explícitamente antes de generar el borrador — así no se cuela
    # una edición sin que el CA la haya validado.
    area["confirmada"] = False
    _guardar(slug, sesion)
    return {"mensaje": res["mensaje"], "propuesta": area["propuesta"],
            "conversacion": area["conversacion"], "diagnostico": area.get("diagnostico", "")}


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


# ── Plan de acción sugerido (paso final) ──────────────────────────────────────

def _generar_plan_accion(sesion: dict, instruccion: str = "", plan_previo: str = "") -> str:
    """Claude propone un plan de acción para el año que viene a partir de la evaluación acordada."""
    if not anthropic_client:
        return plan_previo or ""
    secciones = _secciones(sesion.get("cargo", ""))
    areas = sesion.get("areas", {})
    bloques = []
    for clave, etiqueta in secciones:
        a = areas.get(clave, {})
        final = a.get("texto_final") or a.get("propuesta") or ""
        diag = a.get("diagnostico") or ""
        if final or diag:
            bloques.append(f"### {etiqueta}\nVALORACIÓN ACORDADA: {final or '—'}\nDIAGNÓSTICO/GAPS: {diag or '—'}")
    resumen = "\n\n".join(bloques) or "(sin datos por área)"
    system = (
        "Eres el director de RRHH de IGENERIS. A partir de la evaluación anual YA ACORDADA (valoración y "
        "gaps por área) y del cargo de la persona, propón un PLAN DE ACCIÓN SUGERIDO para el año que viene: "
        "entre 3 y 5 objetivos concretos y accionables. Para cada objetivo: un título breve + qué hacer / "
        "cómo lograrlo, atado a los GAPS detectados y a la ruta de crecimiento (consolidar su nivel o subir "
        "al siguiente). Realista, específico y medible cuando se pueda. Es una SUGERENCIA para el CA. "
        "Devuelve texto plano como lista numerada (1., 2., …), sin preámbulos."
        + config.INSTRUCCION_ANTIINYECCION
    )
    user = f"CARGO: {sesion.get('cargo') or 'no especificado'}\n\nEVALUACIÓN POR ÁREA:\n{resumen}"
    if plan_previo and instruccion:
        user += f"\n\nPLAN ACTUAL:\n{plan_previo}\n\nAJUSTE QUE PIDE EL CA: {instruccion}"
    try:
        resp = anthropic_client.messages.create(
            model="claude-sonnet-4-6", max_tokens=1200, temperature=0.3, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception:
        logging.exception("Fallo generando el plan de acción")
        return plan_previo or ""


def obtener_plan_accion(advisee: str) -> dict:
    """Genera (lazy) y devuelve el plan de acción sugerido."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    if not sesion.get("plan_accion"):
        sesion["plan_accion"] = _generar_plan_accion(sesion)
        _guardar(slug, sesion)
    return {"plan": sesion.get("plan_accion", "")}


def obtener_plan_guardado(advisee: str) -> dict:
    """Devuelve el plan YA guardado SIN generarlo (para mostrarlo fuera del asistente). Cero API."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        return {"plan": "", "tieneSesion": False}
    return {"plan": sesion.get("plan_accion", ""), "tieneSesion": True}


def pedir_cambios_plan(advisee: str, instruccion: str) -> dict:
    """El CA pide a la IA que ajuste el plan de acción."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    if not (instruccion or "").strip():
        raise ValueError("Dime qué cambio quieres en el plan.")
    nuevo = _generar_plan_accion(sesion, instruccion=instruccion.strip(),
                                 plan_previo=sesion.get("plan_accion", ""))
    sesion["plan_accion"] = nuevo
    _guardar(slug, sesion)
    return {"plan": nuevo}


def guardar_plan_accion(advisee: str, texto: str) -> dict:
    """Guarda el plan editado a mano por el CA."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    sesion["plan_accion"] = (texto or "").strip()
    _guardar(slug, sesion)
    return {"ok": True}


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