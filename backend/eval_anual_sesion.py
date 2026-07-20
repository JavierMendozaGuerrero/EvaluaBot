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

import functools
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone

from . import config
from .clients import anthropic_client
from .excepciones import ErrorIA
from .i18n import normalizar_idioma
from .ia import MSG_NO_DISPONIBLE, turno_analisis_anual
from .notion_service import (
    guardar_log_evaluacion_anual,
    guardar_plan_accion_en_notion,
    buscar_empleado_y_cargo,
    obtener_borrador_estructurado,
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

# Un lock por evaluado. Toda la sesión vive en un JSON que se lee entero, se modifica y
# se reescribe entero: sin esto, dos peticiones a la vez sobre la misma persona (un F5
# del CA basta) leen la misma copia y la última en guardar borra lo que hizo la otra.
# También evita pagar dos análisis idénticos de ~60s (ver _asegurar_comentarios).
# Es un lock en memoria: vale para varios hilos, no para varios procesos. Hoy la API
# corre en un único uvicorn, así que llega; con más de un worker haría falta un lock
# de fichero (portalocker) o mover la sesión a una base de datos.
_locks_sesion: dict[str, threading.RLock] = {}
_locks_io: dict[str, threading.Lock] = {}
_lock_registro = threading.Lock()


def _lock_de(slug: str) -> threading.RLock:
    """Lock de OPERACIÓN: se retiene durante toda la operación (hasta ~60s si hay IA)."""
    with _lock_registro:
        lock = _locks_sesion.get(slug)
        if lock is None:
            # RLock y no Lock: hay funciones bloqueadas que llaman a otras que también
            # lo cogen (p. ej. _leer -> _guardar al redactar), y un Lock se autobloquearía.
            lock = _locks_sesion[slug] = threading.RLock()
        return lock


def _lock_io_de(slug: str) -> threading.Lock:
    """Lock de FICHERO: se retiene solo lo que dura el open/replace (milisegundos).

    Es distinto del de operación a propósito. Los endpoints de solo lectura no cogen el
    de operación (si no, consultar el estado esperaría a un análisis de ~60s), así que un
    lector puede cruzarse con el os.replace de un escritor. En Windows eso da
    PermissionError: no se puede sustituir un fichero que otro tiene abierto. Este lock
    serializa únicamente el acceso al fichero, sin que nadie espere por la IA.

    Orden para no bloquearse: primero el de operación, luego el de IO. Nunca al revés.
    """
    with _lock_registro:
        lock = _locks_io.get(slug)
        if lock is None:
            lock = _locks_io[slug] = threading.Lock()
        return lock


def _con_lock_sesion(fn):
    """Serializa por evaluado toda la función, de la lectura al guardado.

    Se aplica a las que MODIFICAN la sesión. Las de solo lectura se quedan fuera a
    propósito: `_guardar` ya es atómico (nunca se lee un JSON a medias), y meterlas aquí
    haría que consultar el estado se quedara esperando los ~60s de un análisis en curso.
    """
    @functools.wraps(fn)
    def envoltorio(advisee, *args, **kwargs):
        with _lock_de(slug_archivo(advisee)):
            return fn(advisee, *args, **kwargs)
    return envoltorio


def _ruta_sesion(slug: str) -> str:
    return os.path.join(config.CARPETA_WEB, f"sesion_anual_{slug}.json")


def _cargar_json(ruta: str, slug: str) -> dict | None:
    """Lee el JSON reintentando los fallos pasajeros.

    Devolver None equivale a decirle al CA "no hay sesión iniciada", así que solo debe
    pasar cuando de verdad no la hay. Un lector puede cruzarse con el os.replace de un
    escritor: en Windows eso da PermissionError (no se puede sustituir un fichero que
    está abierto). Es cuestión de milisegundos, así que se reintenta antes de rendirse.
    """
    for intento in range(4):
        try:
            # El lock de IO cubre el cruce entre hilos; los reintentos quedan como red
            # por si el fichero lo toca algo de fuera de este proceso (un backup, otro
            # arranque del bot compartiendo la carpeta).
            with _lock_io_de(slug):
                with open(ruta, encoding="utf-8") as f:
                    return json.load(f)
        except FileNotFoundError:
            # El replace de un escritor puede pillarnos justo en medio.
            if intento == 3:
                return None
        except (OSError, json.JSONDecodeError):
            if intento == 3:
                logging.exception("No se pudo leer la sesión anual %s", slug)
                return None
        time.sleep(0.05 * (intento + 1))
    return None


def _leer(slug: str) -> dict | None:
    ruta = _ruta_sesion(slug)
    if not os.path.exists(ruta):
        return None
    sesion = _cargar_json(ruta, slug)
    if sesion is None:
        return None
    # Las sesiones creadas antes de la redacción guardaron los datos en bruto, con nombres.
    # Redactar aquí (además de al crearlas) las limpia al abrirlas, y el primer _guardar
    # deja el fichero ya redactado. Es idempotente: redactar lo redactado no hace nada.
    if sesion.get("emp_data") and not _esta_redactado(sesion["emp_data"]):
        sesion["emp_data"] = _redactar_emp_data(sesion["emp_data"])
        # Lo que Claude redactó salió de los datos en bruto, así que puede citar a alguien
        # por su nombre. Se tira y se regenera desde los datos ya limpios.
        sesion["comentarios"] = None
        _guardar(slug, sesion)
        logging.info("Sesión anual %s: datos redactados y comentarios invalidados.", slug)
    return sesion


def _guardar(slug: str, data: dict) -> None:
    """Escribe la sesión de forma atómica: o queda la versión nueva, o la vieja.

    Antes se abría el fichero en modo "w", que lo trunca a cero y luego escribe. Si el
    proceso moría o se reiniciaba en ese hueco, el JSON quedaba a medias; `_leer` no
    puede parsearlo, devuelve None, y el CA pierde la sesión entera ("No hay sesión
    iniciada") sin forma de recuperarla. Escribir a un temporal y renombrar evita ese
    hueco: os.replace es atómico, así que el fichero final nunca se ve a medio escribir.
    """
    data["actualizada_en"] = _ahora()
    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    ruta = _ruta_sesion(slug)
    # El temporal va en la misma carpeta: os.replace solo es atómico dentro del mismo
    # sistema de ficheros, y /tmp puede estar en otro.
    tmp = f"{ruta}.{os.getpid()}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())  # sin esto, un corte de luz deja el rename hecho y el contenido no
        # El lock de IO evita el cruce con un lector (en Windows, os.replace falla con
        # PermissionError si otro tiene el destino abierto). Los reintentos quedan como
        # red por si el fichero lo toca algo ajeno a este proceso.
        for intento in range(4):
            try:
                with _lock_io_de(slug):
                    os.replace(tmp, ruta)
                break
            except PermissionError:
                if intento == 3:
                    raise
                time.sleep(0.05 * (intento + 1))
    except Exception:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


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
    """Genera (una vez) y cachea en la sesión lo que redactó Claude.

    Quien llama ya viene con el lock del evaluado (`_con_lock_sesion`), así que dos
    peticiones a la vez sobre la misma persona no lanzan dos análisis: la segunda espera
    y se encuentra los comentarios ya hechos en la relectura de abajo.
    """
    if sesion.get("comentarios"):
        return sesion["comentarios"]
    # Releer: mientras esperábamos el lock, otra petición ha podido dejarlos hechos.
    # Sin esto, un F5 del CA cuesta otro análisis de ~60s y otra llamada de pago.
    fresca = _leer(slug)
    if fresca and fresca.get("comentarios"):
        sesion["comentarios"] = fresca["comentarios"]
        return sesion["comentarios"]
    with turno_analisis_anual():
        comentarios = sk.interpretar_evaluaciones_anual(sesion["emp_data"], cargo=sesion.get("cargo", ""),
                                                        idioma=normalizar_idioma(sesion.get("idioma", "es")))
    sesion["comentarios"] = comentarios
    _guardar(slug, sesion)
    return comentarios


def _fuente_a_item(cid: str, s: dict) -> dict:
    return {
        "cid": cid, "tipo": s.get("tipo", ""), "label": s.get("label", ""),
        "evaluador": s.get("evaluador", ""), "texto": s.get("texto", ""), "fecha": s.get("fecha", ""),
    }


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
        items.append(_fuente_a_item(cid, fuentes[cid]))
    items.sort(key=lambda x: x["fecha"] or "")
    return items



def _fuentes_no_citadas(fuentes: dict, evidencia: list[dict]) -> list[dict]:
    """Las fuentes que no están en la evidencia del área.

    El panel enseñaba solo lo citado, así que una evaluación que Claude decidiera ignorar
    desaparecía de la vista del CA sin dejar rastro: el CA no podía ni saber que existía,
    ni discrepar. Aquí van el resto para que las tenga delante y juzgue él.
    """
    citadas = {e["cid"] for e in evidencia}
    return sorted(
        (_fuente_a_item(cid, s) for cid, s in fuentes.items() if cid not in citadas),
        key=lambda x: x["fecha"] or "",
    )


# ── Aportaciones del CA ───────────────────────────────────────────────────────
# El CA sabe cosas que no están en Notion (una conversación, un marrón que nadie evaluó).
# Antes se descartaban: sin cita, fuera del informe. Ahora la IA se las admite cuando lo que
# cuenta es un hecho observable y situado (no una etiqueta); entonces queda registrado como
# fuente [C#] firmada por él y es citable como cualquier otra.
# `fecha` y `proyecto` son opcionales a propósito: solo etiquetan la fuente. Exigirlos
# convertía la conversación en un formulario ("dame cuándo, dónde y qué pasó"), y lo que
# hace falta es que el CA cuente el hecho, no que rellene campos.

_CID_APORTACION_RE = re.compile(r"\[(C\d+)\]")


def _aportaciones(sesion: dict) -> list[dict]:
    return sesion.setdefault("emp_data", {}).setdefault("aportaciones_ca", [])


def _evidencia_area(sesion: dict, comentarios: dict, fuentes: dict, clave: str) -> list[dict]:
    """Evidencia del área: lo que Claude citó + las aportaciones que el CA registró en ella.

    Las aportaciones no salen de _evidencia_de_area porque esa lee las citas de la
    valoración inicial de Claude, escrita antes de que el CA abriera la boca."""
    items = _evidencia_de_area(comentarios, fuentes, clave)
    vistos = {e["cid"] for e in items}
    for ap in _aportaciones(sesion):
        cid = ap.get("cid")
        if ap.get("area") == clave and cid in fuentes and cid not in vistos:
            vistos.add(cid)
            items.append(_fuente_a_item(cid, fuentes[cid]))
    items.sort(key=lambda x: x["fecha"] or "")
    return items


def _registrar_aportaciones(sesion: dict, clave: str, propuestas: list) -> dict:
    """Da de alta las aportaciones que la IA aceptó en este turno y devuelve el remapeo
    de ids {cid_de_claude: cid_asignado}.

    Los ids los asigna el backend, no el modelo: Claude propone uno (necesita citarlo en su
    misma respuesta) pero puede equivocarse o colisionar con uno ya usado, así que aquí se
    reasignan en orden y se reescriben los tokens de sus textos."""
    if not isinstance(propuestas, list):
        return {}
    registro = _aportaciones(sesion)
    usados = {ap.get("cid") for ap in registro}
    siguiente = 1 + max((int(c[1:]) for c in usados if c and c[1:].isdigit()), default=0)
    remapeo: dict[str, str] = {}
    for p in propuestas:
        if not isinstance(p, dict):
            continue
        texto = (p.get("texto") or "").strip()
        if not texto:
            continue
        cid_ia = (p.get("cid") or "").strip()
        cid = f"C{siguiente}"
        siguiente += 1
        if cid_ia and cid_ia != cid:
            remapeo[cid_ia] = cid
        registro.append({
            "cid": cid,
            "area": clave,
            "texto": texto,
            "proyecto": (p.get("proyecto") or "").strip(),
            "fecha": (p.get("fecha") or "").strip(),
            "autor": sesion.get("ca", ""),
            "registrada_en": _ahora(),
        })
    if remapeo:
        logging.info("Sesión anual %s: aportaciones del CA remapeadas %s",
                     sesion.get("advisee", ""), remapeo)
    return remapeo


def _aplicar_remapeo(texto: str, remapeo: dict) -> str:
    """Sustituye los [C#] que usó Claude por los ids realmente asignados. En una sola
    pasada: encadenar reemplazos podría pisar un id recién escrito (C4→C3 y luego C3→C2)."""
    if not remapeo or not texto:
        return texto
    return _CID_APORTACION_RE.sub(lambda m: f"[{remapeo.get(m.group(1), m.group(1))}]", texto)


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


def _criterios_nivel_panel(sesion: dict, area: dict) -> list[dict]:
    """Criterios que ve el CA en el panel «Criterios y nivel»: solo los del nivel del
    cargo actual (el rango completo se conserva en area['criterios'] para otros usos)."""
    nivel_actual = sk._nivel_cargo(sesion.get("cargo", ""))
    criterios_full = area.get("criterios", [])
    if not nivel_actual:
        return criterios_full
    return [c for c in criterios_full if sk._nivel_canonico(c.get("nivel", "")) == nivel_actual]


_TXT_NO_EVALUABLE = "No se ha podido evaluar este criterio por falta de información suficiente."


# Los prompts de este módulo están escritos en español, así que en español no hace falta
# decir nada. Para EN/PT se añade este bloque al final del system, igual que hace
# sk.interpretar_evaluaciones_anual: el informe lo redacta el CA en SU idioma, y lo que
# Claude proponga tiene que salir ya en ese idioma (los datos de Notion vienen en español).
_INSTRUCCION_IDIOMA = {
    "en": (
        "\n\nLANGUAGE: Write your entire reply in English. The source data (evaluations, criteria, "
        "objectives, the CA's own notes) may be in Spanish: translate the meaning, do not copy "
        "Spanish text. Keep any JSON keys and citation tags like [E3] exactly as specified."
    ),
    "pt": (
        "\n\nIDIOMA: Escreve toda a tua resposta em português europeu. Os dados de origem (avaliações, "
        "critérios, objetivos, as notas do próprio CA) podem estar em espanhol: traduz o significado, "
        "não copies texto em espanhol. Mantém as chaves do JSON e as etiquetas de citação como [E3] "
        "exatamente como se indica."
    ),
}


def _instruccion_idioma(idioma: str) -> str:
    """Bloque para el system prompt que fija el idioma de redacción. Vacío en español."""
    return _INSTRUCCION_IDIOMA.get(normalizar_idioma(idioma), "")


def _generar_resumen_area(cargo: str, etiqueta: str, bullets: list[str], evidencia: list,
                          conversacion: list, propuesta: str, idioma: str = "es") -> list[dict]:
    """Claude valora el área criterio a criterio (los MISMOS bullets del panel).

    Devuelve [{"criterio", "valoracion", "evaluable"}], una entrada por bullet y en el
    mismo orden. El texto de cada criterio se toma tal cual de la fuente (Notion o
    fallback): Claude solo aporta la valoración. Si un criterio no se puede valorar,
    se dice explícitamente (no se omite ni se agrupa)."""
    if not anthropic_client:
        raise ErrorIA(MSG_NO_DISPONIBLE, "ia_no_configurada", definitivo=True)
    lista = "\n".join(f"{i}. {b}" for i, b in enumerate(bullets, 1))
    ev_txt = "\n".join(f"[{e['cid']}] {e['label']} — {e['texto']}" for e in evidencia) or "(sin evidencia)"
    conv_txt = "\n".join(f"{'CA' if m['rol'] == 'ca' else 'IA'}: {m['texto']}" for m in conversacion) \
        or "(sin conversación)"
    system = (
        f"Eres el director de RRHH de IGENERIS. La persona tiene el cargo: {cargo or 'no especificado'}. "
        f"Estás cerrando el área «{etiqueta}» de su evaluación anual. Recibes los CRITERIOS de su nivel "
        "(numerados), la EVIDENCIA (con citas [X#]) y la CONVERSACIÓN mantenida con su Career Advisor.\n\n"
        "Para CADA criterio, valora cómo lo ha hecho la persona EN ESE CRITERIO CONCRETO: 1-3 frases "
        "directas y honestas, citando la evidencia [X#] que lo respalde e incorporando lo acordado en la "
        "conversación. NO inventes: si para un criterio no hay información suficiente (ni en la evidencia "
        "ni en la conversación), márcalo como no evaluable en lugar de rellenarlo. Las fuentes [C#] son "
        "aportaciones que el CA hizo en la conversación y que ya quedaron registradas: son evidencia "
        "citable como el resto.\n\n"
        'Devuelve SOLO un JSON válido: {"valoraciones": [{"i": <número del criterio>, '
        '"evaluable": true|false, "valoracion": "texto"}]} con EXACTAMENTE una entrada por criterio y en '
        'el mismo orden. Si "evaluable" es false, deja "valoracion" vacía o indica brevemente qué '
        "información faltaría."
        + _instruccion_idioma(idioma)
        + config.INSTRUCCION_ANTIINYECCION
    )
    user = (f"CRITERIOS ({len(bullets)}):\n{lista}\n\nEVIDENCIA:\n{ev_txt}\n\n"
            f"CONVERSACIÓN:\n{conv_txt}\n\nPROPUESTA ACORDADA DEL ÁREA:\n{propuesta or '(sin propuesta)'}")
    try:
        resp = anthropic_client.messages.create(
            model="claude-sonnet-4-6", max_tokens=2000, temperature=0, system=system,
            messages=[{"role": "user", "content": user}],
        )
        txt = "".join(b.text for b in resp.content if b.type == "text").strip()
        # El modelo envuelve el JSON en prosa o en un fence según le da: recortar a mano
        # solo el fence dejaba fuera el caso del preámbulo (ver sk._extraer_json_objeto).
        data = sk._extraer_json_objeto(txt)
        if data is None:
            raise ValueError(f"respuesta sin JSON: {txt[:300]!r}")
    except ErrorIA:
        # Ya trae el motivo (sin saldo, IA saturada…) escrito para el CA: que lo vea.
        raise
    except Exception:
        logging.exception("Fallo generando el resumen final del área")
        raise RuntimeError("No se pudo generar la sugerencia final; reinténtalo.")
    por_indice = {}
    for v in data.get("valoraciones", []):
        try:
            por_indice[int(v.get("i"))] = v
        except (TypeError, ValueError):
            continue
    out = []
    for i, criterio in enumerate(bullets, 1):
        v = por_indice.get(i) or {}
        texto = (v.get("valoracion") or "").strip()
        evaluable = bool(v.get("evaluable")) and bool(texto)
        if not evaluable:
            texto = _TXT_NO_EVALUABLE + (f" ({texto})" if texto else "")
        out.append({"criterio": criterio, "valoracion": texto, "evaluable": evaluable})
    return out


def _resumen_a_texto(resumen: list | None) -> str:
    """Aplana el resumen por criterios a texto (para reutilizarlo en otros prompts)."""
    if not resumen:
        return ""
    return "\n".join(f"- {r.get('criterio', '')}: {r.get('valoracion', '')}" for r in resumen)


# ── API del módulo ────────────────────────────────────────────────────────────

def _cargo_de(advisee: str) -> str:
    """Cargo del empleado desde 'Lista de empleados' (columna Cargo) en Notion."""
    try:
        _, cargo = buscar_empleado_y_cargo(advisee)
        return (cargo or "").strip()
    except Exception:
        logging.exception("No se pudo leer el cargo de '%s'", advisee)
        return ""


def _esta_redactado(emp_data: dict) -> bool:
    """True si estos datos ya pasaron por _redactar_emp_data (marca 'anonimizado')."""
    filas = list(emp_data.get("evaluaciones", [])) + list(emp_data.get("evals_proyecto", []))
    return all(f.get("anonimizado") for f in filas) if filas else True


def _redactar_emp_data(emp_data: dict) -> dict:
    """Quita de los datos del advisee todo lo que el CA no puede ver, con las mismas reglas
    que los PDFs de fuentes (ver skill_pdfs_fuentes._solo_top_down):

      - Evals mensuales: solo las de un superior. Las de iguales, las bottom-to-top y las
        de dirección desconocida no llegan aquí.
      - Nombres de evaluador y el 'tipo' de las evals de proyecto (delata el nivel): fuera.

    Se aplica AQUÍ, al entrar el dato en la sesión, y no al pintarlo: la sesión se guarda en
    disco y Claude redacta desde ella, así que un filtro en la vista dejaría los nombres en
    el contexto del modelo —que además tiene instrucciones de citar la fuente literal— y
    bastaría con pedírselos por el chat. Lo que no entra aquí no puede escaparse después.

    Opiniones del CA, seguimiento personal y evals extra se dejan intactos: en esos tres el
    autor no es información protegida (el CA leyéndose a sí mismo, o el advisee hablando de
    sí mismo). El informe anual que genera el admin no pasa por aquí.
    """
    datos = dict(emp_data)
    datos["evaluaciones"] = [
        {**ev, "persona_que_evalua": "", "nombre": "", "anonimizado": True}
        for ev in emp_data.get("evaluaciones", [])
        if ev.get("relacion") == "superior"
    ]
    datos["evals_proyecto"] = [
        {**pe, "evaluador": "", "tipo": "", "anonimizado": True}
        for pe in emp_data.get("evals_proyecto", [])
    ]
    return datos


@_con_lock_sesion
def iniciar_sesion(advisee: str, cargo: str = "", idioma: str = "es") -> dict:
    """Crea o recupera la sesión. Devuelve identidad + progreso. NO genera Claude todavía.

    `idioma` es el del CA que redacta: manda en todo lo que él ve y en el Word que sale
    (la plantilla, la fecha y el idioma en que Claude propone los textos). Se fija al
    crear la sesión y ya no cambia, para que el informe no salga a medias en dos idiomas.
    """
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not cargo:
        cargo = _cargo_de(advisee)  # cargo real desde Notion (Lista de empleados)
    if sesion is None:
        emp_data = _redactar_emp_data(sk.obtener_datos_empleado_anual(advisee))
        if not emp_data.get("opiniones_ca") and not emp_data.get("evaluaciones") \
           and not emp_data.get("evals_proyecto") and not emp_data.get("seguimiento") \
           and not emp_data.get("barbecho"):
            raise ValueError(f"No hay datos de evaluación para '{advisee}'.")
        sesion = {
            "advisee": advisee,
            "ca": emp_data.get("ca", ""),
            "cargo": cargo,
            "idioma": normalizar_idioma(idioma),
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


def iniciar_manual(advisee: str, cargo: str = "", idioma: str = "es") -> dict:
    """Prepara el informe para rellenarlo MANUALMENTE en la web (sin Claude).

    Construye un borrador en blanco (comentarios vacíos, huecos del CA vacíos) listo para
    editar como un Word y guardar en Notion. Si ya hay una sesión (p. ej. asistida a medias),
    reutiliza su borrador para no perder lo escrito. Devuelve {borrador}.

    `idioma`: el del CA, igual que en `iniciar_sesion` (aquí manda en la plantilla y la fecha)."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not cargo:
        cargo = _cargo_de(advisee)
    if sesion is None:
        try:
            emp_data = _redactar_emp_data(sk.obtener_datos_empleado_anual(advisee))
        except Exception:
            logging.exception("Sin datos de evaluación para '%s'; borrador manual en blanco", advisee)
            emp_data = {"empleado": advisee, "ca": "", "objetivos": []}
        sesion = {
            "advisee": advisee,
            "ca": emp_data.get("ca", ""),
            "cargo": cargo,
            "idioma": normalizar_idioma(idioma),
            "anio": datetime.now(timezone.utc).year - 1,
            "estado": "completada",         # habilita obtener_borrador/guardar sin pasar por áreas
            "identidad_confirmada": True,
            "emp_data": emp_data,
            "areas": {},
            "comentarios": None,
            "modo": "manual",
            "creada_en": _ahora(),
        }
    elif cargo and cargo != sesion.get("cargo"):
        sesion["cargo"] = cargo
    if not sesion.get("borrador"):
        sesion["borrador"] = _restaurar_borrador_notion(advisee) or _construir_borrador(sesion)
    _guardar(slug, sesion)
    return {"borrador": sesion["borrador"]}


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


@_con_lock_sesion
def confirmar_identidad(advisee: str) -> dict:
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    sesion["identidad_confirmada"] = True
    _guardar(slug, sesion)
    return {"ok": True}


@_con_lock_sesion
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


@_con_lock_sesion
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
    evidencia = _evidencia_area(sesion, comentarios, fuentes, clave)
    no_citadas = _fuentes_no_citadas(fuentes, evidencia)
    area = sesion.setdefault("areas", {}).setdefault(
        clave, {"conversacion": [], "propuesta": "", "confirmada": False})

    # Criterios del área (siempre, sin API). Se muestran ANTES de que el CA opine.
    if not area.get("criterios"):
        area["criterios"] = _criterios_area(sesion.get("cargo", ""), clave, secciones[clave],
                                            idioma=normalizar_idioma(sesion.get("idioma", "es")),
                                            nombre=sesion.get("advisee", ""))
        if area["criterios"]:
            _guardar(slug, sesion)

    return {
        "clave": clave,
        "etiqueta": secciones[clave],
        "cargo": sesion.get("cargo", ""),
        "evidencia": evidencia,
        # El resto de fuentes disponibles, que Claude no citó en esta área. Van aparte para
        # que el CA vea que existen y pueda usarlas aunque el modelo las descartara.
        "evidencia_no_citada": no_citadas,
        # En el panel solo se muestran los criterios del cargo actual; el rango completo
        # (area["criterios"]) se conserva para la comparación posterior.
        "criterios": _criterios_nivel_panel(sesion, area),
        "pregunta": _pregunta_area(secciones[clave]),
        "conversacion": area.get("conversacion", []),
        "propuesta": area.get("propuesta", ""),
        "confirmada": area.get("confirmada", False),
        # Sugerencia final del área (criterio a criterio); solo existe si el CA la pidió.
        "resumen": area.get("resumen_final") or [],
    }


def _claude_conversa_area(etiqueta: str, evidencia: list, claude_bullets: str, conversacion: list,
                          criterios: list | None = None, diagnostico: str = "", cargo: str = "",
                          siguiente_cid: str = "C1", idioma: str = "es") -> dict:
    """Llama a Claude para reaccionar a los puntos del CA y proponer los bullets del área.

    Devuelve además `aportaciones`: lo que el CA ha aportado de su cosecha y la IA ha dado
    por suficientemente concreto para admitirlo como fuente nueva (ver _registrar_aportaciones)."""
    ev_txt = "\n".join(f"[{e['cid']}] {e['label']} — {e['texto']}" for e in evidencia) or "(sin evidencia)"
    conv_txt = "\n".join(f"{'CA' if m['rol'] == 'ca' else 'IA'}: {m['texto']}" for m in conversacion)
    crit_txt = "\n".join(f"[{c['nivel']}]: " + " / ".join(c['criterios']) for c in (criterios or [])) \
        or "(sin criterios en Notion)"
    if not anthropic_client:
        return {"mensaje": "(IA no disponible) Tomo nota de tus puntos.", "propuesta": claude_bullets,
                "aportaciones": []}

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
        "responde con el TEXTO LITERAL de esa fuente (tipo, proyecto, fecha y contenido).\n"
        "- ANONIMATO: las evaluaciones te llegan sin el nombre de quien las escribió, a propósito. Si el CA "
        "pregunta quién dijo algo, dile que esa información es anónima por diseño. NUNCA deduzcas ni "
        "aventures una identidad a partir del proyecto, la fecha o el contenido.\n"
        "- APORTACIONES DEL CA: el CA trabaja con la persona y sabe cosas que nadie registró. Si te cuenta "
        "algo que no está en la EVIDENCIA, NO lo descartes por no estar registrado: es información válida "
        "que hay que concretar. El listón para admitirla es que sea un HECHO OBSERVABLE y situado, no una "
        "etiqueta: 'no avisó de que el modelo iba tarde hasta la víspera de la entrega' sirve; 'le falta "
        "proactividad' no. Situado quiere decir que se entienda dónde y cuándo pasó, lo bastante como para "
        "que quien estuviera allí lo reconozca.\n"
        "  CÓMO pedirle que concrete: NO le pases un cuestionario ni le enumeres campos que rellenar; eso "
        "es un interrogatorio y le corta. Reacciona a lo que te ha contado como alguien a quien le interesa: "
        "dile primero qué parte de lo que ha dicho YA te sirve y por qué (nombrando lo concreto que haya "
        "dicho), y luego, en una o dos frases, dile qué le falta a eso para poder sostenerse en un informe. "
        "Explícale el porqué, no el formato, y deja que él elija cómo contarlo. Si de lo que ya ha dicho se "
        "deduce el cuándo o el dónde, NO se lo vuelvas a preguntar.\n"
        "  Cuando el hecho esté lo bastante situado, ADMÍTELA: devuélvela en 'aportaciones' con un id nuevo "
        f"(el siguiente libre es [{siguiente_cid}]; si admites varias en el mismo turno, numera hacia "
        "arriba) y cítala en la propuesta con ese id, igual que cualquier otra fuente. Queda registrada como "
        "aportación firmada por el CA y sale en el informe con su texto. Admitirla no es darle la razón: "
        "sigue debatiendo el fondo — que un hecho sea concreto no lo hace representativo de todo el año.\n"
        "  Las aportaciones ya admitidas te llegan en la EVIDENCIA como [C#]: cítalas, pero NO las repitas "
        "en 'aportaciones' (ya tienen id).\n"
        "- NO inventes: cada afirmación de la propuesta debe llevar su cita [X#], sea de la evidencia o de "
        "una aportación admitida del CA. Lo que no tenga cita, fuera.\n\n"
        'Devuelve SOLO un JSON válido: {"mensaje": "tu respuesta conversacional", '
        '"propuesta": "los bullets finales del área, uno por línea, cada uno con su cita", '
        '"aportaciones": [{"cid": "C#", "fecha": "mes o periodo, si se sabe", '
        '"proyecto": "proyecto o contexto, si se sabe", '
        '"texto": "el hecho, en una frase, tal y como lo cuenta el CA"}]}. '
        '"fecha" y "proyecto" son para etiquetar la fuente: rellénalos con lo que el CA haya dicho y '
        'déjalos vacíos si no lo ha dicho. No los pidas solo para rellenarlos. '
        'Si en este turno no admites ninguna aportación nueva, "aportaciones" es [].\n\n'
        # Se le pide hablar como una persona, y sin esto se pone a hablar directamente: suelta
        # el mensaje en prosa y se deja el JSON. Entonces el CA no ve la respuesta, ve un error.
        "FORMATO (obligatorio): responde SOLO con el JSON. Tu primer carácter debe ser '{' y el último "
        "'}'. Lo que le dices al CA va DENTRO del campo \"mensaje\", nunca suelto fuera del JSON."
        + _instruccion_idioma(idioma)
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
        except ErrorIA as err:
            if err.definitivo:
                # Sin saldo o API mal configurada: reintentar sin caché falla igual y gasta otra llamada.
                raise
            # Si el prompt caching no está soportado, reintenta sin caché (misma calidad, sin ahorro)
            logging.warning("Prompt caching no disponible; reintento sin caché")
            resp = _crear(instrucciones + "\n\n" + contexto_estatico)
        t = "".join(b.text for b in resp.content if b.type == "text").strip()
        # Ídem: el JSON puede venir envuelto en prosa además de en un fence.
        data = sk._extraer_json_objeto(t)
        if data is None:
            raise ValueError(f"respuesta sin JSON: {t[:300]!r}")
        return {"mensaje": (data.get("mensaje") or "").strip(),
                "propuesta": (data.get("propuesta") or "").strip(),
                "aportaciones": data.get("aportaciones") or []}
    except ErrorIA:
        # No es "reformula y reinténtalo": el CA necesita saber que es la API (y a quién avisar).
        raise
    except Exception:
        logging.exception("Fallo en la conversación del área")
        return {"mensaje": "He tenido un problema al responder; reformula o reinténtalo.",
                "propuesta": claude_bullets, "aportaciones": []}


@_con_lock_sesion
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
    evidencia = _evidencia_area(sesion, comentarios, fuentes, clave)

    area = sesion.setdefault("areas", {}).setdefault(
        clave, {"conversacion": [], "propuesta": "", "confirmada": False})
    if "criterios" not in area:
        area["criterios"] = _criterios_area(sesion.get("cargo", ""), clave, secciones[clave],
                                            idioma=normalizar_idioma(sesion.get("idioma", "es")),
                                            nombre=sesion.get("advisee", ""))
    area["conversacion"].append({"rol": "ca", "texto": texto.strip()})
    # La conversación avanza → la sugerencia final generada antes queda obsoleta.
    # El CA la vuelve a pedir con el botón cuando dé el área por hablada.
    area.pop("resumen_final", None)

    n_libre = 1 + max((int(ap["cid"][1:]) for ap in _aportaciones(sesion)
                       if ap.get("cid", "C")[1:].isdigit()), default=0)
    res = _claude_conversa_area(
        secciones[clave], evidencia, claude_bullets, area["conversacion"],
        criterios=area.get("criterios"), diagnostico=area.get("diagnostico", ""),
        cargo=sesion.get("cargo", ""), siguiente_cid=f"C{n_libre}",
        idioma=normalizar_idioma(sesion.get("idioma", "es")))
    # Lo que el CA ha aportado y la IA ha admitido pasa a ser fuente [C#] de la sesión: a
    # partir del turno siguiente entra en la evidencia y llega al informe con su cita.
    remapeo = _registrar_aportaciones(sesion, clave, res.get("aportaciones"))
    res["mensaje"] = _aplicar_remapeo(res["mensaje"], remapeo)
    res["propuesta"] = _aplicar_remapeo(res["propuesta"], remapeo)
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
            "conversacion": area["conversacion"]}


@_con_lock_sesion
def generar_resumen_area(advisee: str, clave: str) -> dict:
    """Genera BAJO DEMANDA (botón del CA) la sugerencia final del área, desglosada
    criterio a criterio con los MISMOS bullets que muestra el panel «Criterios y nivel»."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    secciones = dict(_secciones(sesion.get("cargo", "")))
    if clave not in secciones:
        raise ValueError(f"Sección desconocida: {clave}")
    area = sesion.get("areas", {}).get(clave)
    if not area or not area.get("conversacion"):
        raise ValueError("Primero conversa con la IA sobre esta área.")
    if not area.get("criterios"):
        area["criterios"] = _criterios_area(sesion.get("cargo", ""), clave, secciones[clave],
                                            idioma=normalizar_idioma(sesion.get("idioma", "es")),
                                            nombre=sesion.get("advisee", ""))
    bullets = [b for c in _criterios_nivel_panel(sesion, area) for b in (c.get("criterios") or [])]
    if not bullets:
        raise ValueError("No existen criterios para este puesto en esta área.")

    comentarios = _asegurar_comentarios(slug, sesion)
    _, fuentes = _emp_y_fuentes(sesion)
    evidencia = _evidencia_area(sesion, comentarios, fuentes, clave)
    resumen = _generar_resumen_area(sesion.get("cargo", ""), secciones[clave], bullets,
                                    evidencia, area.get("conversacion", []), area.get("propuesta", ""),
                                    idioma=normalizar_idioma(sesion.get("idioma", "es")))
    area["resumen_final"] = resumen
    _guardar(slug, sesion)
    return {"resumen": resumen}


@_con_lock_sesion
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

def _evidencia_y_criterios(sesion: dict, con_evals_en_bruto: bool = False) -> tuple[str, str]:
    """Bloque de evidencia (evals acordadas y/o en bruto) + criterios del puesto y siguiente.
    Compartido por la generación del plan y por el chat de dudas.

    `con_evals_en_bruto` añade las evaluaciones originales AUNQUE ya haya valoraciones
    acordadas por área. Lo usa el chat: para generar el plan basta el resumen acordado,
    pero el CA que pregunta sobre un plan ya hecho quiere justamente lo que el resumen
    dejó fuera ("¿de dónde sale este objetivo?"), y sin las evals en bruto el modelo no
    tiene nada concreto que citar y contesta que le falta información.
    """
    cargo = sesion.get("cargo", "")
    secciones = _secciones(cargo)
    areas = sesion.get("areas", {})
    bloques = []
    for clave, etiqueta in secciones:
        a = areas.get(clave, {})
        final = a.get("texto_final") or a.get("propuesta") or ""
        diag = _resumen_a_texto(a.get("resumen_final")) or a.get("diagnostico") or ""
        if final or diag:
            bloques.append(f"### {etiqueta}\nVALORACIÓN ACORDADA: {final or '—'}\nDIAGNÓSTICO/GAPS: {diag or '—'}")

    partes = []
    if bloques:
        partes.append("EVALUACIÓN POR ÁREA (acordada con el CA):\n" + "\n\n".join(bloques))
    if con_evals_en_bruto or not bloques:
        contexto, _ = sk._formatear_contexto(sesion.get("emp_data") or {})
        partes.append("RESULTADOS DE TODAS LAS EVALUACIONES:\n" + (contexto or "(sin evaluaciones)"))
    evidencia = "\n\n".join(partes)

    emp_nombre = (sesion.get("emp_data") or {}).get("empleado", "") or sesion.get("advisee", "")
    criterios = sk._criterios_para_prompt(cargo, normalizar_idioma(sesion.get("idioma", "es")), emp_nombre)
    criterios_section = f"\n\nCRITERIOS DEL PUESTO Y DEL SIGUIENTE NIVEL:\n{criterios}" if criterios else ""
    return evidencia, criterios_section


def chatear_plan(advisee: str, mensajes: list) -> dict:
    """Chat (Haiku, barato) para que el CA resuelva dudas sobre el plan de acción.

    `mensajes` = [{"rol": "user"|"assistant", "texto": ...}, ...], con la última pregunta
    al final. Reutiliza la misma evidencia que el plan y la cachea (prompt caching) para
    abaratar los mensajes siguientes."""
    if not anthropic_client:
        raise ErrorIA(MSG_NO_DISPONIBLE, "ia_no_configurada", definitivo=True)
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        # Igual que obtener_plan_accion/guardar_plan_accion: el CA puede abrir esta pantalla
        # sin haber pasado por el asistente. Sin esto, cada pregunta le salía como respuesta
        # del bot un "No hay sesión iniciada" que no le dice nada.
        iniciar_sesion(advisee)
        sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    msgs = [
        {"role": "assistant" if m.get("rol") == "assistant" else "user", "content": m.get("texto", "").strip()}
        for m in (mensajes or []) if m.get("texto", "").strip()
    ]
    if not msgs:
        raise ValueError("Escribe una pregunta.")
    plan = sesion.get("plan_accion", "")
    evidencia, criterios_section = _evidencia_y_criterios(sesion, con_evals_en_bruto=True)
    system_text = (
        "Eres un asistente de RRHH de IGENERIS. Ayudas al Career Advisor a entender y afinar el PLAN DE "
        "ACCIÓN de su advisee para el año que viene. Respondes con base en la evidencia proporcionada "
        "(evaluaciones mensuales y de proyecto, seguimiento, criterios del puesto y el plan). Relaciona "
        "el plan con la evidencia: si te preguntan de dónde sale un objetivo, busca en las evaluaciones "
        "qué lo justifica y cítalo. Las evaluaciones son anónimas a propósito: habla de lo que dicen, no "
        "de quién las escribió. Sé breve, concreto y directo. No inventes hechos que no estén en la "
        "evidencia; si de verdad no está, dilo."
        + _instruccion_idioma(sesion.get("idioma", "es"))
        + config.INSTRUCCION_ANTIINYECCION
        + f"\n\nCARGO: {sesion.get('cargo') or 'no especificado'}{criterios_section}\n\n{evidencia}"
        + f"\n\nPLAN DE ACCIÓN ACTUAL:\n{plan or '(todavía no hay plan generado)'}"
    )
    try:
        resp = anthropic_client.messages.create(
            model="claude-haiku-4-5", max_tokens=1200, temperature=0.3,
            system=[{"type": "text", "text": system_text, "cache_control": {"type": "ephemeral"}}],
            messages=msgs,
        )
        return {"respuesta": "".join(b.text for b in resp.content if b.type == "text").strip()}
    except ErrorIA:
        # El chat muestra este mensaje como respuesta del asistente: que diga el motivo real.
        raise
    except Exception:
        logging.exception("Fallo en el chat del plan de acción")
        raise ValueError("No se pudo responder ahora mismo. Inténtalo de nuevo.")


def _generar_plan_accion(sesion: dict, instruccion: str = "", plan_previo: str = "") -> str:
    """Claude propone un plan de acción para el año que viene en UNA sola llamada.

    Se basa en los resultados de todas las evaluaciones y en los criterios del puesto
    (y del siguiente nivel). Si el CA ya hizo el informe final, usa las valoraciones
    acordadas por área; si no (plan creado desde cero), usa las evaluaciones en bruto.
    NO usa los objetivos del año (para minimizar el gasto de API)."""
    if not anthropic_client:
        return plan_previo or ""
    cargo = sesion.get("cargo", "")
    evidencia, criterios_section = _evidencia_y_criterios(sesion)

    system = (
        "Eres el director de RRHH de IGENERIS. A partir de los resultados de las evaluaciones del empleado "
        "y de los criterios de su puesto (y del siguiente nivel), propón un PLAN DE ACCIÓN SUGERIDO para el "
        "año que viene: entre 3 y 5 objetivos concretos y accionables. Para cada objetivo: un título breve + "
        "qué hacer / cómo lograrlo, atado a los gaps detectados y a la ruta de crecimiento (consolidar su "
        "nivel o subir al siguiente). Realista, específico y medible cuando se pueda. Es una SUGERENCIA para "
        "el CA. Devuelve texto plano como lista numerada (1., 2., …), sin preámbulos."
        + _instruccion_idioma(sesion.get("idioma", "es"))
        + config.INSTRUCCION_ANTIINYECCION
    )
    user = f"CARGO: {cargo or 'no especificado'}{criterios_section}\n\n{evidencia}"
    if plan_previo and instruccion:
        user += f"\n\nPLAN ACTUAL:\n{plan_previo}\n\nAJUSTE QUE PIDE EL CA: {instruccion}"
    try:
        resp = anthropic_client.messages.create(
            model="claude-sonnet-4-6", max_tokens=1200, temperature=0.3, system=system,
            messages=[{"role": "user", "content": user}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    except ErrorIA:
        # Devolver el plan anterior (o vacío) aquí haría creer que ese es el plan sugerido:
        # mejor que el CA vea por qué no se ha podido generar.
        raise
    except Exception:
        logging.exception("Fallo generando el plan de acción")
        return plan_previo or ""


@_con_lock_sesion
def obtener_plan_accion(advisee: str, forzar: bool = False) -> dict:
    """Genera (lazy) y devuelve el plan de acción sugerido por Claude.

    `forzar` regenera aunque ya hubiera un plan (para 'Crear plan de acción nuevo').
    Si no hay sesión (el CA no ha hecho el informe final), la inicia. El plan se genera
    en UNA sola llamada a Claude a partir de las evaluaciones (en bruto o acordadas) y de
    los criterios del puesto — como en el informe final pero sin recorrer todo el asistente."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        iniciar_sesion(advisee)
        sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    if forzar or not sesion.get("plan_accion"):
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


@_con_lock_sesion
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


@_con_lock_sesion
def guardar_plan_accion(advisee: str, texto: str) -> dict:
    """Guarda el plan editado a mano por el CA. Si aún no existe sesión de evaluación
    anual (el CA crea un plan nuevo sin haber hecho el informe final), la inicia primero
    para no romper el asistente si más adelante hace el informe."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        iniciar_sesion(advisee)
        sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    sesion["plan_accion"] = (texto or "").strip()
    _guardar(slug, sesion)

    # El plan vigente se publica en Notion (TO-SEE → Planes de acción → 'Plan de acción -
    # {Nombre}') para que no dependa de la carpeta local. Solo aquí: lo que genera la IA
    # (crear plan nuevo / pedir cambios) es borrador hasta que el CA pulsa Guardar.
    # Si Notion falla no se pierde el guardado local, así que solo se avisa.
    notion_url = ""
    try:
        notion_url = guardar_plan_accion_en_notion(
            advisee, sesion["plan_accion"], ca_nombre=sesion.get("ca", "")
        ).get("url", "")
    except Exception:
        logging.exception("No se pudo guardar el plan de acción en Notion para '%s'", advisee)
    return {"ok": True, "notionUrl": notion_url}


# ── Borrador editable del informe final (en la web) ───────────────────────────

def _dims_informe(cargo: str) -> list[tuple[str, str]]:
    """Dimensiones de la tabla CALIFICACIÓN de la plantilla oficial (proyecto + liderazgo si aplica)."""
    dims = list(sk._DIMS_PDF)
    if any(c in (cargo or "").strip().lower() for c in sk._REQUIERE_LIDERAZGO):
        dims += list(sk._DIMS_LIDERAZGO)
    return dims


def _construir_borrador(sesion: dict) -> dict:
    """Borrador editable que replica 1:1 la plantilla oficial del informe final.

    Los comentarios por dimensión van prellenados con lo acordado en la sesión; los
    campos reservados al CA (notas, retribución, promoción, salarios, deadlines) se
    dejan VACÍOS, igual que en la plantilla. El CA los rellena en la web si quiere.

    Va en el idioma del CA (`sesion["idioma"]`), y ese idioma viaja DENTRO del borrador:
    es lo que se guarda en Notion, así que cuando el advisee descarga su Word (que se
    regenera desde Notion, sin sesión) sale igual que lo dejó el CA."""
    ahora = datetime.now(timezone.utc)
    idioma = normalizar_idioma(sesion.get("idioma", "es"))
    areas = sesion.get("areas", {})
    dimensiones = []
    for clave, etiqueta in _dims_informe(sesion.get("cargo", "")):
        a = areas.get(clave, {})
        dimensiones.append({
            # `clave` es la que identifica la fila en todo el sistema; `etiqueta` es solo
            # lo que se pinta, por eso puede traducirse sin romper nada guardado.
            "clave": clave, "etiqueta": sk._dim_label(clave, etiqueta, idioma), "nota": "",
            "comentarios": a.get("texto_final") or a.get("propuesta") or "",
        })
    objetivos = [{"texto": (o.get("titulo") or o.get("descripcion") or "").strip(), "deadline": ""}
                 for o in sesion["emp_data"].get("objetivos", [])]
    while len(objetivos) < 3:
        objetivos.append({"texto": "", "deadline": ""})
    return {
        "empleado": sesion["advisee"],
        "idioma": idioma,
        "anio": ahora.year - 1,
        "anioSiguiente": ahora.year,
        "fecha": f"{sk._mes_label(ahora.month - 1, idioma)} {ahora.year}",
        "caActual": sesion.get("ca", ""),
        "caSiguiente": "",
        "cargo": sesion.get("cargo", ""),
        "salarioActual": "",
        "dimensiones": dimensiones,
        "retribucion": {"notaProyectos": "", "variable60": "", "notaContribucion": "",
                        "variable": "", "objetivosCorporativos": "", "totalVariable": ""},
        "resultadoEval": {"promocion": "", "cargoSiguiente": "", "nuevoSalarioFijo": ""},
        "objetivos": objetivos,
    }


def _merge_borrador(base: dict, data: dict) -> dict:
    """Aplica al borrador SOLO los campos editables conocidos (ignora claves extrañas)."""
    out = json.loads(json.dumps(base))  # copia profunda
    for campo in ("caSiguiente", "salarioActual"):
        if campo in data:
            out[campo] = str(data.get(campo) or "").strip()
    editadas = {d.get("clave"): d for d in data.get("dimensiones") or [] if isinstance(d, dict)}
    for dim in out.get("dimensiones", []):
        ed = editadas.get(dim["clave"])
        if not ed:
            continue
        if "nota" in ed:
            dim["nota"] = str(ed.get("nota") or "").strip()
        if "comentarios" in ed:
            dim["comentarios"] = str(ed.get("comentarios") or "")
    for bloque in ("retribucion", "resultadoEval"):
        entrada = data.get(bloque)
        if isinstance(entrada, dict):
            for k in out[bloque]:
                if k in entrada:
                    out[bloque][k] = str(entrada.get(k) or "").strip()
    if isinstance(data.get("objetivos"), list):
        out["objetivos"] = [{"texto": str(o.get("texto") or ""), "deadline": str(o.get("deadline") or "").strip()}
                            for o in data["objetivos"][:20] if isinstance(o, dict)]
        while len(out["objetivos"]) < 3:
            out["objetivos"].append({"texto": "", "deadline": ""})
    return out


@_con_lock_sesion
def obtener_borrador(advisee: str) -> dict:
    """Borrador editable del informe final. Se construye al finalizar la sesión;
    aquí solo se recupera (o se reconstruye si falta en sesiones antiguas)."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    if sesion.get("estado") != "completada":
        raise ValueError("Genera primero el borrador (todas las áreas deben estar confirmadas).")
    if not sesion.get("borrador"):
        sesion["borrador"] = _restaurar_borrador_notion(advisee) or _construir_borrador(sesion)
        _guardar(slug, sesion)
    return {"borrador": sesion["borrador"]}


@_con_lock_sesion
def guardar_borrador(advisee: str, data: dict) -> dict:
    """Guarda las ediciones del CA sobre el borrador web."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    base = sesion.get("borrador") or _construir_borrador(sesion)
    sesion["borrador"] = _merge_borrador(base, data or {})
    _guardar(slug, sesion)
    return {"ok": True, "borrador": sesion["borrador"]}


def _idioma_borrador(borrador: dict | None, sesion: dict | None = None) -> str:
    """Idioma en el que se redactó el informe, mirando primero DENTRO del borrador.

    Los informes guardados antes de que esto existiera no traen `idioma`: caen a la sesión
    si la hay y, si no, al español, que es exactamente como se generaron en su día."""
    for origen in (borrador, sesion):
        if origen and origen.get("idioma"):
            return normalizar_idioma(origen["idioma"])
    return "es"


def generar_docx_borrador(advisee: str, nombre_archivo: str) -> str:
    """Genera el .docx del informe final (plantilla oficial) desde el borrador editado.

    Devuelve la ruta del archivo escrito en CARPETA_WEB."""
    slug = slug_archivo(advisee)
    sesion = _leer(slug)
    if not sesion:
        raise ValueError("No hay sesión iniciada.")
    borrador = sesion.get("borrador")
    if not borrador:
        raise ValueError("No hay borrador generado para esta persona.")
    _, fuentes = _emp_y_fuentes(sesion)
    comentarios = {"_fuentes": fuentes}
    for dim in borrador.get("dimensiones", []):
        comentarios[dim["clave"]] = dim.get("comentarios", "")
    valores_ca = {
        "caSiguiente": borrador.get("caSiguiente", ""),
        "salarioActual": borrador.get("salarioActual", ""),
        "notas": {d["clave"]: d.get("nota", "") for d in borrador.get("dimensiones", [])},
        "retribucion": borrador.get("retribucion", {}),
        "resultadoEval": borrador.get("resultadoEval", {}),
        "objetivos": borrador.get("objetivos", []),
    }
    sk.guardar_informe_anual_word(sesion["emp_data"], comentarios, cargo=sesion.get("cargo", ""),
                                  idioma=_idioma_borrador(borrador, sesion),
                                  valores_ca=valores_ca, nombre_archivo=nombre_archivo)
    return os.path.join(config.CARPETA_WEB, nombre_archivo)


def _restaurar_borrador_notion(advisee: str) -> dict | None:
    """Recupera de Notion el borrador en curso (si se perdió la caché local). None si no hay."""
    try:
        return obtener_borrador_estructurado(advisee)
    except Exception:
        logging.exception("No se pudo restaurar el borrador de '%s' desde Notion", advisee)
        return None


def fuentes_para_revision(advisee: str) -> dict:
    """Mapa de fuentes citables del advisee, para la copia de revisión del CA.

    Primero mira la sesión local (gratis, y es el caso normal: el CA acaba de hacer la eval).
    Si ya no está, las reconstruye desde Notion, que tarda unos segundos. Devuelve {} si no se
    puede: el informe sale sin anexo, que es un degradado aceptable, nunca un error."""
    try:
        sesion = _leer(slug_archivo(advisee))
        if sesion and sesion.get("emp_data"):
            return _emp_y_fuentes(sesion)[1]
        _, fuentes = sk._formatear_contexto(sk.obtener_datos_empleado_anual(advisee))
        return fuentes
    except Exception:
        logging.exception("No se pudieron reunir las fuentes de '%s' para la revisión del CA", advisee)
        return {}


def word_desde_borrador(borrador: dict, nombre_archivo: str, fuentes: dict | None = None) -> str:
    """Genera el .docx oficial a partir de un borrador (dict), SIN depender de la sesión local.

    Se usa para regenerar el Word del advisee desde lo guardado en Notion (fuente de verdad).
    Devuelve la ruta del archivo escrito en CARPETA_WEB.

    `fuentes` SOLO se pasa para la copia de revisión del CA (`revision_informe_*`), que es la
    única que lleva anexo de Fuentes/Evidencia. Por defecto va vacío: el documento del advisee
    no puede llevarlo, porque las fuentes revelan quién dijo qué de él. El `incluir_fuentes`
    de abajo es explícito (y no solo el dict vacío) para que siga siendo cierto si algún día el
    borrador guardado en Notion llega a traer fuentes dentro."""
    comentarios = {"_fuentes": fuentes or {}}
    for dim in borrador.get("dimensiones", []):
        comentarios[dim.get("clave", "")] = dim.get("comentarios", "")
    valores_ca = {
        "caSiguiente": borrador.get("caSiguiente", ""),
        "salarioActual": borrador.get("salarioActual", ""),
        "notas": {d.get("clave", ""): d.get("nota", "") for d in borrador.get("dimensiones", [])},
        "retribucion": borrador.get("retribucion", {}),
        "resultadoEval": borrador.get("resultadoEval", {}),
        "objetivos": borrador.get("objetivos", []),
    }
    emp_data = {"empleado": borrador.get("empleado", ""), "ca": borrador.get("caActual", "")}
    sk.guardar_informe_anual_word(emp_data, comentarios, cargo=borrador.get("cargo", ""),
                                  idioma=_idioma_borrador(borrador),
                                  valores_ca=valores_ca, nombre_archivo=nombre_archivo,
                                  incluir_fuentes=bool(fuentes))
    return os.path.join(config.CARPETA_WEB, nombre_archivo)


@_con_lock_sesion
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
    idioma = _idioma_borrador(None, sesion)
    sk.guardar_informe_anual_word(emp_data, comentarios_final, cargo=cargo, idioma=idioma)
    sk.guardar_informe_anual_html(emp_data, comentarios_final, cargo=cargo, idioma=idioma)

    # Borrador editable en la web: se (re)construye con lo recién acordado.
    # (Si el CA reabre áreas y vuelve a finalizar, el borrador se regenera.)
    sesion["borrador"] = _construir_borrador(sesion)
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