"""
Flujo de revisión para Career Advisors (CA).

El bot envía un DM a cada empleado con un mensaje de notificación.
El usuario responde en el hilo: sí → bot pide nombre del advisee → muestra todas
las evaluaciones desde la última revisión del CA → pide opinión → guarda en
Notion → pregunta si hay otro advisee.
"""

import json
import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from . import config
from .clients import notion, slack_app
from .conversation_back import boton_atras, fila_atras, limpiar_historial, pop_historial, push_historial, tiene_historial
from .slack_lists import añadir_pendiente, enlace_lista_pendientes, quitar_pendiente
from .eval_tracking import registrar_envio_por_slack_id, marcar_completada_por_slack_id
from .excepciones import ErrorIA
from .i18n import t, botones_idioma_slack, texto_error_ia
from .notion_service import (
    _buscar_bbdd_en_pagina_id,
    _coincide_parent_bbdd,
    _crear_pagina_en_bbdd,
    _data_source_id,
    _extraer_titulo_bbdd,
    _parent_bbdd_en_pagina,
    _parent_bbdd_referencia,
    _query_bbdd,
    _tipo_objeto_busqueda_bbdd,
    _usa_data_sources,
    idioma_por_slack_id,
    guardar_idioma_por_slack_id,
    invalidar_cache_empleados,
    buscar_empleado_en_lista,
    buscar_empleado_y_cargo,
    excluir_feedback_confidencial,
    obtener_advisees,
    obtener_comentarios_personales,
    esperar_hasta_proximo_envio,
    obtener_ejemplos_guia,
    obtener_evaluaciones_por_evaluado,
    obtener_nombre_por_id_usuario,
    obtener_objetivos_persona,
    obtener_preguntas_seguimiento_ca,
    obtener_slack_id_por_nombre,
    obtener_slack_ids_empleados,
    sugerir_empleados_parecidos,
)
from .project_evals import obtener_evaluaciones_proyecto_por_evaluado
from .skill_resumen_evaluacion import generar_resumen_evaluacion
from .slack_carga import AnimacionCargando
from .utils import normalizar_nombre
from .anonimato import cargar_config as _cargar_anonimato, evaluadores_visibles_para_advisee as _evaluadores_visibles_para_advisee

# ---------------------------------------------------------------------------
# Estado compartido
# ---------------------------------------------------------------------------

ca_dm_activas: set = set()             # user_ids con evaluación CA activa
ca_dm_ts: dict = {}                    # user_id -> ts del mensaje inicial (raíz del hilo)
ca_dm_ts_anterior: dict = {}           # user_id -> ts de la CA anterior (caducada)
ca_dm_canal: dict = {}                 # user_id -> dm_channel_id
ca_hora_dm: dict = {}                  # user_id -> timestamp de envío
ca_ultimo_recordatorio_dm: dict = {}   # user_id -> timestamp del último recordatorio
conversaciones_ca: dict = {}           # user_id -> estado de conversación
# canal DM -> {"ts", "texto"}: último mensaje del hilo con botones vivos, para
# retirárselos cuando llega un mensaje nuevo o se pulsa otro botón (ver helpers
# más abajo). Los mensajes de "Ver evaluaciones" no se registran aquí.
_ca_botones_transitorios: dict = {}
_lock = threading.Lock()
_cache_bbdd: dict = {}
_cache_nombre_usuario: dict = {}
_cache_lista_empleados: dict = {"db_id": None, "nombres": None}

PREFIJO_BBDD = "Opiniones - "

_PALABRAS_NUMERO_CA = {
    "uno": 1, "dos": 2, "tres": 3, "cuatro": 4, "cinco": 5,
    "seis": 6, "siete": 7, "ocho": 8, "nueve": 9, "diez": 10,
}


def _resolver_numero_advisee(texto, estado):
    t = texto.strip().lower()
    idx = _PALABRAS_NUMERO_CA.get(t)
    if idx is None and t.isdigit():
        idx = int(t)
    if idx is not None:
        lista = estado.get("lista_advisees", [])
        if 1 <= idx <= len(lista):
            return lista[idx - 1]
    return texto


_PROPS_CA = {
    "Name":    {"title": {}},
    "Fecha":   {"date": {}},
    "CA":      {"rich_text": {}},
    "Opinion": {"rich_text": {}},
    "Resumen": {"rich_text": {}},
}


# ---------------------------------------------------------------------------
# Notion: base de datos de opiniones del CA
# ---------------------------------------------------------------------------

def _asegurar_propiedades_ca(database_id: str) -> None:
    try:
        if _usa_data_sources():
            bbdd = notion.data_sources.retrieve(data_source_id=database_id)
            faltantes = {k: v for k, v in _PROPS_CA.items() if k not in bbdd.get("properties", {})}
            if faltantes:
                notion.data_sources.update(data_source_id=database_id, properties=faltantes)
        else:
            bbdd = notion.databases.retrieve(database_id=database_id)
            faltantes = {k: v for k, v in _PROPS_CA.items() if k not in bbdd.get("properties", {})}
            if faltantes:
                notion.databases.update(database_id=database_id, properties=faltantes)
    except Exception:
        logging.exception(f"Error asegurando propiedades de BD CA {database_id}")


_lock_bbdd_ca = threading.Lock()


def _obtener_o_crear_bbdd_ca(advisee: str) -> str:
    titulo = f"{PREFIJO_BBDD}{advisee.strip()}"
    # Lock mantenido durante toda la búsqueda/creación para que dos hilos
    # concurrentes no creen dos BDs para el mismo advisee.
    with _lock_bbdd_ca:
        return _obtener_o_crear_bbdd_ca_locked(titulo)


def _obtener_o_crear_bbdd_ca_locked(titulo: str) -> str:
    with _lock:
        if titulo in _cache_bbdd:
            return _cache_bbdd[titulo]

    parent = _parent_bbdd_referencia()
    parent_ca = _parent_bbdd_en_pagina(config.NOTION_CA_TRACKING_PAGE_NAME, crear=True)

    # 1) Escanear los hijos de la página de seguimiento CA: consistencia inmediata
    #    frente al lag de indexación de notion.search (que puede no devolver BDs
    #    migradas o recién creadas y provocar duplicados).
    db_id = None
    if parent_ca.get("type") == "page_id":
        db_id = _buscar_bbdd_en_pagina_id(parent_ca["page_id"], titulo)

    # 2) Fallback: búsqueda global. Si falla, se propaga: no crear a ciegas.
    if not db_id:
        resultado = notion.search(
            query=titulo,
            filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
            page_size=100,
        )
        for bbdd in resultado.get("results", []):
            if _extraer_titulo_bbdd(bbdd) == titulo and (
                _coincide_parent_bbdd(bbdd, parent) or _coincide_parent_bbdd(bbdd, parent_ca)
            ):
                db_id = _data_source_id(bbdd)
                break

    if db_id:
        _asegurar_propiedades_ca(db_id)
        with _lock:
            _cache_bbdd[titulo] = db_id
        return db_id

    # 3) Crear solo si de verdad no existe.
    if _usa_data_sources():
        nueva = notion.databases.create(
            parent=parent_ca,
            title=[{"type": "text", "text": {"content": titulo}}],
            initial_data_source={
                "title": [{"type": "text", "text": {"content": titulo}}],
                "properties": _PROPS_CA,
            },
        )
        nueva = notion.databases.retrieve(database_id=nueva["id"])
    else:
        nueva = notion.databases.create(
            parent=parent_ca,
            title=[{"type": "text", "text": {"content": titulo}}],
            properties=_PROPS_CA,
        )

    db_id = _data_source_id(nueva)
    with _lock:
        _cache_bbdd[titulo] = db_id
    logging.info(f"Base de datos CA creada: {titulo}")
    return db_id


def guardar_nota_ca_web(ca_nombre: str, advisee: str, nota: str) -> tuple[bool, str]:
    """Guarda una nota del CA sobre un advisee registrada desde la web."""
    return _guardar_opinion(ca_nombre, advisee, nota)


def _guardar_opinion(ca_nombre: str, advisee: str, opinion: str, resumen: str = "") -> tuple[bool, str]:
    try:
        db_id = _obtener_o_crear_bbdd_ca(advisee)
        fecha_str = datetime.now(config.ZONA_HORARIA_MADRID).strftime("%Y-%m-%d %H:%M")
        _crear_pagina_en_bbdd(
            db_id,
            {
                "Name":    {"title":     [{"text": {"content": f"Opinion {fecha_str}"}}]},
                "Fecha":   {"date":      {"start": datetime.now(timezone.utc).isoformat()}},
                "CA":      {"rich_text": [{"text": {"content": ca_nombre}}]},
                "Opinion": {"rich_text": [{"text": {"content": opinion[:2000]}}]},
                "Resumen": {"rich_text": [{"text": {"content": resumen[:2000]}}]},
            },
        )
        return True, ""
    except Exception as exc:
        logging.exception(f"Error guardando opinion CA '{ca_nombre}'")
        return False, str(exc)


# ---------------------------------------------------------------------------
# Fecha de la última opinión del CA sobre un advisee
# ---------------------------------------------------------------------------

def _fecha_ultima_opinion(ca_nombre: str, advisee: str) -> str | None:
    titulo = f"{PREFIJO_BBDD}{advisee.strip()}"
    try:
        resultado = notion.search(
            query=titulo,
            filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
            page_size=10,
        )
        db_id = None
        for bbdd in resultado.get("results", []):
            if _extraer_titulo_bbdd(bbdd) == titulo:
                db_id = _data_source_id(bbdd)
                break
        if not db_id:
            return None

        filas = _query_bbdd(db_id, page_size=100).get("results", [])
        fechas = []
        for fila in filas:
            props = fila.get("properties", {})
            ca_texto = "".join(
                p.get("plain_text", "")
                for p in (props.get("CA", {}).get("rich_text") or props.get("Evaluador", {}).get("rich_text") or [])
            ).strip()
            if normalizar_nombre(ca_texto) == normalizar_nombre(ca_nombre):
                fecha = (props.get("Fecha", {}).get("date") or {}).get("start", "")
                if fecha:
                    fechas.append(fecha)
        return max(fechas) if fechas else None
    except Exception:
        logging.exception(f"Error buscando ultima opinion de '{ca_nombre}' sobre '{advisee}'")
        return None


def _ca_guardo_desde(ca_nombre: str, desde_ts: float) -> bool:
    """True si el CA guardó al menos una opinión en Notion desde el timestamp dado."""
    desde_fecha = datetime.fromtimestamp(desde_ts, tz=timezone.utc).strftime("%Y-%m-%d")
    try:
        advisees = obtener_advisees(ca_nombre)
        for advisee in advisees:
            try:
                db_id = _obtener_o_crear_bbdd_ca(advisee)
            except Exception:
                continue
            resultado = _query_bbdd(db_id, page_size=100)
            for fila in resultado.get("results", []):
                props = fila.get("properties", {})
                ca_texto = "".join(
                    p.get("plain_text", "")
                    for p in (props.get("CA", {}).get("rich_text") or [])
                ).strip()
                if normalizar_nombre(ca_texto) != normalizar_nombre(ca_nombre):
                    continue
                fecha = (props.get("Fecha", {}).get("date") or {}).get("start", "")[:10]
                if fecha >= desde_fecha:
                    return True
        return False
    except Exception:
        logging.exception(f"Error comprobando opiniones CA de '{ca_nombre}'")
        return False


# ---------------------------------------------------------------------------
# Resumen de evaluaciones
# ---------------------------------------------------------------------------

# --- Textos por tipo de evaluación (cada uno devuelve "" si no hay nada nuevo) ---

def _texto_evals_proyecto(advisee: str, desde_fecha: str | None, anonimo: bool = True) -> str:
    """Evaluaciones de proyecto: 'Resultados Evaluaciones al final de proyecto' (campo Evaluado).

    A diferencia de las mensuales, NO se filtran por fecha: son evaluaciones de fin de
    proyecto (poco frecuentes y a menudo anteriores a la última revisión del CA), así que
    se muestran siempre todas las recibidas.
    """
    try:
        evals = obtener_evaluaciones_proyecto_por_evaluado(advisee)
    except Exception:
        logging.exception("Error leyendo evaluaciones de proyecto de '%s'", advisee)
        return ""
    if not evals:
        return ""
    lineas = []
    for ev in sorted(evals, key=lambda e: e.get("fecha", "")):
        fecha = (ev.get("fecha") or "?")[:10]
        quien = "Anónimo" if anonimo else (ev.get("evaluador") or "?")
        proyecto = ev.get("proyecto") or "?"
        linea = f"• [{fecha}] *{quien}* en {proyecto}"
        if ev.get("tipo"):
            linea += f" ({ev['tipo']})"
        if ev.get("respuestas"):
            linea += f" – {ev['respuestas']}"
        lineas.append(linea)
    return "\n".join(lineas)


def _texto_evals_mensuales(advisee: str, desde_fecha: str | None, anonimo: bool = True) -> str:
    """Evaluaciones mensuales del bot de Slack: 'Evaluaciones - {nombre}' (Resultados Evaluaciones Mensuales)."""
    try:
        evaluaciones = excluir_feedback_confidencial(obtener_evaluaciones_por_evaluado(advisee))
    except Exception:
        logging.exception("Error leyendo evaluaciones mensuales de '%s'", advisee)
        return ""
    if desde_fecha:
        evaluaciones = [e for e in evaluaciones if (e.get("fecha") or "") > desde_fecha]
    if not evaluaciones:
        return ""
    lineas = []
    for ev in sorted(evaluaciones, key=lambda e: e.get("fecha", "")):
        fecha = ev.get("fecha", "")[:10] if ev.get("fecha") else "?"
        quien = "Anónimo" if anonimo else ev.get("persona_que_evalua", "?")
        lineas.append(
            f"• [{fecha}] *{quien}* en {ev.get('proyecto', '?')} – "
            f"Valoración: {ev.get('q1', '?')}/5 | Ejemplo: {ev.get('q2', '?')}"
        )
    return "\n".join(lineas)


def _texto_seguimiento_personal(advisee: str, desde_fecha: str | None, anonimo: bool = True) -> str:
    """Comentarios de evaluaciones de seguimiento personal."""
    try:
        comentarios = obtener_comentarios_personales(advisee)
    except Exception:
        logging.exception("Error leyendo comentarios personales de '%s'", advisee)
        return ""
    if desde_fecha:
        comentarios = [c for c in comentarios if c.get("fecha", "") > desde_fecha]
    if not comentarios:
        return ""
    lineas = []
    for c in sorted(comentarios, key=lambda x: x.get("fecha", "")):
        autor_c = c["autor"]  # seguimiento personal: autor = propio advisee, nunca anonimo
        lineas.append(f"• [{c['fecha']}] *{autor_c}* → _{c['comentario']}_")
    return "\n".join(lineas)


def _texto_objetivos(advisee: str) -> str:
    """Objetivos (títulos y KPIs) como recordatorio."""
    try:
        objetivos = obtener_objetivos_persona(advisee)
    except Exception:
        logging.exception("Error leyendo objetivos de '%s'", advisee)
        return ""
    if not objetivos:
        return ""
    lineas = []
    for obj in objetivos:
        linea = f"• *{obj.get('titulo', '')}*"
        if obj.get("kpis"):
            linea += f"\n  _KPIs: {obj['kpis']}_"
        lineas.append(linea)
    return "\n".join(lineas)


def _resumen_advisee(advisee: str, desde_fecha: str | None, anonimo: bool = True) -> str:
    """Resumen combinado para la WEB (obtener_resumen_advisee_para_ca). Sin cambios de comportamiento.

    En el bot de Slack NO se usa esto: allí cada tipo se muestra en su propio desplegable
    (ver _texto_evals_proyecto / _texto_evals_mensuales / _texto_seguimiento_personal).
    """
    try:
        evaluaciones = excluir_feedback_confidencial(obtener_evaluaciones_por_evaluado(advisee))
    except RuntimeError:
        return f"No hay evaluaciones registradas para *{advisee}*."
    except Exception:
        logging.exception(f"Error leyendo evaluaciones de '{advisee}'")
        return f"Error al leer evaluaciones de *{advisee}*."

    if not evaluaciones:
        return f"No hay evaluaciones registradas para *{advisee}*."

    if desde_fecha:
        nuevas = [e for e in evaluaciones if (e.get("fecha") or "") > desde_fecha]
        if not nuevas:
            return (
                f"*{advisee}*: no hay evaluaciones nuevas desde tu última revisión "
                f"({desde_fecha[:10]})."
            )
        evaluaciones = nuevas

    ordenadas = sorted(evaluaciones, key=lambda e: e.get("fecha", ""))
    lineas = []
    for ev in ordenadas:
        fecha = ev.get("fecha", "")[:10] if ev.get("fecha") else "?"
        quien = "Anónimo" if anonimo else ev.get('persona_que_evalua', '?')
        lineas.append(
            f"• [{fecha}] *{quien}* en {ev.get('proyecto', '?')} – "
            f"Valoración: {ev.get('q1', '?')}/5 | "
            f"Ejemplo: {ev.get('q2', '?')}"
        )

    n = len(lineas)
    cabecera = f"*{advisee}* – {n} evaluación{'es' if n != 1 else ''}"
    if desde_fecha:
        cabecera += f" desde {desde_fecha[:10]}"
    resumen = cabecera + ":\n" + "\n".join(lineas)

    try:
        comentarios = obtener_comentarios_personales(advisee)
        if desde_fecha:
            comentarios = [c for c in comentarios if c.get("fecha", "") > desde_fecha]
        if comentarios:
            lineas_personales = []
            for c in sorted(comentarios, key=lambda x: x.get("fecha", "")):
                autor_c = c['autor']  # seguimiento personal: nunca anonimo (autor = advisee)
                lineas_personales.append(
                    f"• [{c['fecha']}] *{autor_c}* → _{c['comentario']}_"
                )
            resumen += f"\n\n*Comentarios personales ({len(lineas_personales)}):*\n" + "\n".join(lineas_personales)
    except Exception:
        logging.exception("Error leyendo comentarios personales de '%s'", advisee)

    try:
        objetivos = obtener_objetivos_persona(advisee)
        if objetivos:
            lineas_obj = []
            for obj in objetivos:
                titulo_o = obj.get("titulo", "")
                kpis_o = obj.get("kpis", "")
                linea = f"• *{titulo_o}*"
                if kpis_o:
                    linea += f"\n  _KPIs: {kpis_o}_"
                lineas_obj.append(linea)
            resumen += f"\n\n📌 *Objetivos de {advisee}:*\n" + "\n".join(lineas_obj)
    except Exception:
        logging.exception("Error leyendo objetivos de '%s'", advisee)

    return resumen


# ---------------------------------------------------------------------------
# Mensaje desplegable con las evaluaciones recibidas del advisee
# ---------------------------------------------------------------------------

def _chunk_mrkdwn(texto: str, limite: int = 2900) -> list:
    """Trocea un texto largo en cachos < límite para respetar el máximo de un bloque section (3000)."""
    texto = texto or ""
    if len(texto) <= limite:
        return [texto]
    trozos, actual = [], ""
    for linea in texto.split("\n"):
        if len(actual) + len(linea) + 1 > limite and actual:
            trozos.append(actual)
            actual = ""
        actual += (linea + "\n")
    if actual.strip():
        trozos.append(actual)
    return trozos or [texto[:limite]]


# tipo de evaluación -> clave i18n de su cabecera
_HEADER_KEY_POR_TIPO = {
    "proyecto": "bc.evals_proyecto_header",
    "mensual": "bc.evals_mensual_header",
    "personal": "bc.evals_personal_header",
}


def _header_por_tipo(tipo: str, advisee: str, idioma: str) -> str:
    return t(_HEADER_KEY_POR_TIPO.get(tipo, "bc.evals_received_header"), idioma, advisee=advisee)


def _valor_desplegable(tipo: str, advisee: str) -> str:
    """Codifica el tipo + advisee en el 'value' del botón (para reconstruir al pulsar)."""
    return json.dumps({"t": tipo, "a": advisee})


# ---------------------------------------------------------------------------
# Botones "de un solo uso": retirar los de mensajes obsoletos
# ---------------------------------------------------------------------------
# Mismo patrón que la evaluación mensual (slack_bot.py): recordamos el último
# mensaje con botones de cada hilo (clave = canal DM, porque la CA transcurre en
# un DM 1:1) para poder retirarle los botones cuando llega un mensaje nuevo o se
# pulsa otro botón. Los mensajes de "Ver evaluaciones" NO se registran ni se
# cierran al pulsarlos: sus botones se conservan siempre.

def _recordar_botones_ca(channel: str, resp, texto: str) -> None:
    """Guarda el mensaje interactivo recién enviado como el último con botones vivos."""
    try:
        ts = resp.get("ts") if isinstance(resp, dict) else resp["ts"]
    except Exception:
        ts = None
    if channel and ts:
        _ca_botones_transitorios[channel] = {"ts": ts, "texto": texto or " "}


def _olvidar_botones_ca(channel: str) -> None:
    """Descarta el puntero al último mensaje con botones sin editarlo (ya lo hizo el handler)."""
    _ca_botones_transitorios.pop(channel, None)


def _quitar_botones_anteriores_ca(channel: str, client=None) -> None:
    """Retira los botones del último mensaje interactivo del hilo (si sigue vivo)."""
    info = _ca_botones_transitorios.pop(channel, None)
    if not info:
        return
    cli = client or slack_app.client
    try:
        cli.chat_update(
            channel=channel,
            ts=info["ts"],
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": info["texto"]}}],
            text=info["texto"],
        )
    except Exception:
        pass


def _cerrar_botones_click_ca(body, client=None) -> None:
    """Quita los botones del mensaje que se acaba de pulsar (conserva su texto)."""
    msg = body.get("message", {}) or {}
    ch = (body.get("channel") or {}).get("id")
    ts = msg.get("ts")
    _ca_botones_transitorios.pop(ch, None)
    if not ch or not ts:
        return
    bloques = [b for b in (msg.get("blocks") or []) if b.get("type") != "actions"]
    if not bloques:
        bloques = [{"type": "section", "text": {"type": "mrkdwn", "text": msg.get("text") or " "}}]
    cli = client or slack_app.client
    try:
        cli.chat_update(
            channel=ch,
            ts=ts,
            blocks=bloques,
            text=msg.get("text") or " ",
        )
    except Exception:
        pass


def _bloques_resumen_colapsado(tipo: str, advisee: str, idioma: str) -> list:
    """Vista plegada de un tipo: solo la cabecera + botón para desplegar."""
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": _header_por_tipo(tipo, advisee, idioma)}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": t("bc.btn_show_evals", idioma), "emoji": True},
                    "value": _valor_desplegable(tipo, advisee),
                    "action_id": "ca_ver_evaluaciones",
                },
            ],
        },
    ]


def _bloques_resumen_vacio(tipo: str, advisee: str, idioma: str) -> list:
    """Cabecera de un tipo sin evaluaciones nuevas (sin botón)."""
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": _header_por_tipo(tipo, advisee, idioma)}},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": t("bc.sin_evals_tipo", idioma)}]},
    ]


def _build_resumen_ca_view(tipo: str, advisee: str, resumen: str, idioma: str) -> dict:
    """Modal con las evaluaciones de un tipo recibidas por el advisee (se abre al
    pulsar 'Ver evaluaciones', en vez de desplegarse dentro del hilo)."""
    secciones = [
        {"type": "section", "text": {"type": "mrkdwn", "text": trozo}}
        for trozo in _chunk_mrkdwn(resumen)
    ] if resumen else [
        {"type": "section", "text": {"type": "mrkdwn", "text": t("bc.sin_evals_tipo", idioma)}}
    ]
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": _header_por_tipo(tipo, advisee, idioma)}},
        {"type": "divider"},
        *secciones,
    ]
    return {
        "type": "modal",
        "callback_id": "ca_resumen_ver",
        "title": {"type": "plain_text", "text": t("bc.evals_modal_title", idioma)},
        "close": {"type": "plain_text", "text": t("bm.close", idioma)},
        "blocks": blocks[:100],
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _es_si(texto: str) -> bool:
    return normalizar_nombre(texto) in {"si", "sí", "s", "yes", "y", "claro", "sip", "vale", "sim"}


def _es_no(texto: str) -> bool:
    return normalizar_nombre(texto) in {"no", "n", "nope", "nel", "nao", "não"}


def _es_confirmar(texto: str) -> bool:
    return normalizar_nombre(texto) in {"si", "sí", "s", "ok", "okay", "confirmar", "guardar", "correcto",
                                        "yes", "y", "save", "confirm", "correct",
                                        "sim", "gravar", "correto"}


def _es_modificar(texto: str) -> bool:
    return normalizar_nombre(texto) in {"modificar", "cambiar", "editar", "repetir",
                                        "modify", "change", "edit", "repeat",
                                        "alterar", "mudar"}


def _texto_pregunta_modificacion_ca(idioma="es") -> str:
    """Modificar solo permite reescribir la opinión; el advisee no se puede cambiar."""
    return obtener_preguntas_seguimiento_ca(idioma).get("opinion", "") or t("bc.enter_new_answer", idioma)


def _mensaje_advisee_no_encontrado(nombre: str, idioma="es") -> str:
    sugerencias = sugerir_empleados_parecidos(nombre)
    if sugerencias:
        opciones = "\n".join(f"- {item}" for item in sugerencias)
        return t("bc.not_found_suggest", idioma, nombre=nombre, opciones=opciones)
    return t("bc.not_found", idioma, nombre=nombre)


def _nombre_desde_notion(user_id: str) -> str | None:
    with _lock:
        if user_id in _cache_nombre_usuario:
            return _cache_nombre_usuario[user_id]
    try:
        resultado = notion.search(
            query="Lista de empleados",
            filter={"value": _tipo_objeto_busqueda_bbdd(), "property": "object"},
            page_size=10,
        )
        db_id = None
        for bbdd in resultado.get("results", []):
            if _extraer_titulo_bbdd(bbdd) == "Lista de empleados":
                db_id = _data_source_id(bbdd)
                break
        if not db_id:
            return None

        filas = _query_bbdd(db_id, page_size=100).get("results", [])
        for fila in filas:
            props = fila.get("properties", {})
            prop_id = props.get("ID_usuario", {})
            id_usuario = "".join(
                p.get("plain_text", "")
                for p in (prop_id.get("rich_text") or prop_id.get("title") or [])
            ).strip()
            if id_usuario != user_id:
                continue
            prop_nombre = props.get("Nombre", {})
            nombre = "".join(
                p.get("plain_text", "")
                for p in (prop_nombre.get("rich_text") or prop_nombre.get("title") or [])
            ).strip()
            if nombre:
                with _lock:
                    _cache_nombre_usuario[user_id] = nombre
                return nombre
        return None
    except Exception:
        logging.exception(f"Error buscando nombre para '{user_id}' en Lista empleados")
        return None


def _nombre_real(user_id: str, logger) -> str:
    nombre = _nombre_desde_notion(user_id)
    if nombre:
        return nombre
    try:
        resp = slack_app.client.users_info(user=user_id)
        user = resp.get("user", {})
        profile = user.get("profile", {})
        nombre = (
            (user.get("real_name") or "").strip()
            or (profile.get("real_name") or "").strip()
            or (profile.get("display_name") or "").strip()
            or (user.get("name") or "").strip()
        )
        return nombre if nombre else user_id
    except Exception as exc:
        logger.error(f"users_info falló para {user_id}: {exc}")
        return user_id


def _identidad_usuario_slack(user_id: str, logger) -> tuple[str, list[str]]:
    aliases = [user_id]
    nombre_notion = _nombre_desde_notion(user_id)
    if nombre_notion:
        aliases.append(nombre_notion)
    try:
        resp = slack_app.client.users_info(user=user_id)
        user = resp.get("user", {})
        profile = user.get("profile", {})
        aliases.extend([
            user.get("real_name", ""),
            user.get("name", ""),
            profile.get("real_name", ""),
            profile.get("display_name", ""),
            profile.get("email", ""),
        ])
    except Exception as exc:
        logger.error(f"users_info fallo para {user_id}: {exc}")

    limpios = []
    vistos = set()
    for alias in aliases:
        alias = (alias or "").strip()
        clave_alias = normalizar_nombre(alias)
        if alias and clave_alias not in vistos:
            vistos.add(clave_alias)
            limpios.append(alias)

    nombre = nombre_notion or (limpios[0] if limpios else user_id)
    return nombre, limpios


def _advisee_permitido_para_ca(ca_nombre: str, ca_aliases: list[str], advisee: str) -> tuple[bool, list[str]]:
    permitidos = obtener_advisees(ca_nombre, ca_aliases=ca_aliases)
    advisee_norm = normalizar_nombre(advisee)
    return any(normalizar_nombre(nombre) == advisee_norm for nombre in permitidos), permitidos


# ---------------------------------------------------------------------------
# Envío del mensaje inicial por DM
# ---------------------------------------------------------------------------

def _bloques_dm_ca(idioma, enlace_pendientes=None):
    """Bloques del DM inicial de las evaluaciones CA, con botón de cambio de idioma en la cabecera."""
    bloques = [
        botones_idioma_slack("lang_set_ca"),
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": t("bc.pending_intro", idioma)},
        },
        {"type": "context", "elements": [{"type": "mrkdwn", "text": t("bot.no_inteligente", idioma)}]},
        {"type": "section", "text": {"type": "mrkdwn", "text": t("bot.example_q", idioma)}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": t("bm.yes_btn", idioma), "emoji": True},
                    "style": "primary",
                    "action_id": "ca_ejemplo_si",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": t("bm.no_btn", idioma), "emoji": True},
                    "action_id": "ca_ejemplo_no",
                },
            ],
        },
    ]
    if enlace_pendientes:
        bloques.append({"type": "section", "text": {"type": "mrkdwn", "text": t("bc.pendientes_link", idioma, url=enlace_pendientes)}})
    bloques.append({"type": "divider"})
    return bloques


def enviar_pregunta_inicial_ca() -> None:
    try:
        invalidar_cache_empleados()  # leer el idioma actual de Notion, no una copia cacheada
        if config.APP_MODE != "produccion" and config.SLACK_TEST_USER_IDS:
            slack_ids = config.SLACK_TEST_USER_IDS
            logging.info(f"Modo prueba CA: enviando solo a {slack_ids}")
        else:
            slack_ids = obtener_slack_ids_empleados()
            if not slack_ids:
                logging.warning("No se encontraron Slack IDs para envío CA")
                return

        with _lock:
            activas_previas = set(ca_dm_activas)
            ca_dm_activas.clear()

        enlace_pendientes = enlace_lista_pendientes()
        for user_id in slack_ids:
            try:
                ca_nombre, ca_aliases = _identidad_usuario_slack(user_id, logging)
                advisees = obtener_advisees(ca_nombre, ca_aliases=ca_aliases)
                if not advisees:
                    logging.info(f"[CA] {user_id} ({ca_nombre}) no tiene advisees, omitiendo")
                    continue

                _idi = idioma_por_slack_id(user_id)
                if user_id in activas_previas:
                    _editar_dm_inicial_ca_caducada(user_id, _idi)
                resp_dm = slack_app.client.conversations_open(users=[user_id])
                dm_channel = resp_dm["channel"]["id"]
                resp = slack_app.client.chat_postMessage(
                    channel=dm_channel,
                    text=t("bc.pending_fallback", _idi),
                    blocks=_bloques_dm_ca(_idi, enlace_pendientes),
                )
                with _lock:
                    ca_dm_activas.add(user_id)
                    ca_dm_canal[user_id] = dm_channel
                    if ca_dm_ts.get(user_id):
                        ca_dm_ts_anterior[user_id] = ca_dm_ts[user_id]
                    ca_dm_ts[user_id] = resp["ts"]
                    ca_hora_dm[user_id] = time.time()
                    conversaciones_ca.pop(user_id, None)
                añadir_pendiente("ca", user_id, t("bc.pendientes_titulo", _idi))
                registrar_envio_por_slack_id(user_id, "ca")
                logging.info(f"Mensaje CA enviado por DM a {user_id}, ts={resp['ts']}")
            except Exception:
                logging.exception(f"Error enviando DM CA a {user_id}")
    except Exception:
        logging.exception("Error en enviar_pregunta_inicial_ca")


def _editar_dm_inicial_ca(user_id, idioma=None):
    """Sustituye el mensaje inicial (raíz del hilo) de la evaluación CA por el
    resumen de 'completada'. Se llama al marcar la evaluación como completada."""
    ts = ca_dm_ts.get(user_id)
    canal = ca_dm_canal.get(user_id)
    if not ts or not canal:
        return
    idioma = idioma or idioma_por_slack_id(user_id)
    texto = t("bc.dm_completada", idioma)
    try:
        slack_app.client.chat_update(
            channel=canal, ts=ts, text=texto,
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": texto}}],
        )
    except Exception:
        logging.exception("No se pudo editar el DM inicial CA de %s", user_id)


def _editar_dm_inicial_ca_caducada(user_id, idioma=None):
    """Marca como caducado el DM inicial de la evaluación CA anterior de user_id,
    que quedó sin responder al llegar una nueva. No se toca si ya fue completada
    (en ese caso ya la sustituyó _editar_dm_inicial_ca)."""
    ts = ca_dm_ts.get(user_id)
    canal = ca_dm_canal.get(user_id)
    if not ts or not canal:
        return
    idioma = idioma or idioma_por_slack_id(user_id)
    texto = t("bc.dm_expirada", idioma)
    try:
        slack_app.client.chat_update(
            channel=canal, ts=ts, text=texto,
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": texto}}],
        )
    except Exception:
        logging.exception("No se pudo marcar como caducado el DM inicial CA de %s", user_id)


def _enviar_lista_advisees(user_id, channel, thread_ts, estado, idioma, logger, prefijo=""):
    """Muestra la lista de advisees pendientes con botones. Se usa tanto al avanzar
    normalmente como al reenviar esta pregunta tras pulsar 'Atrás'."""
    ca_nombre_l, ca_aliases_l = _identidad_usuario_slack(user_id, logger)
    advisees_l = obtener_advisees(ca_nombre_l, ca_aliases=ca_aliases_l)
    guardados = estado.get("advisees_guardados", set())
    advisees_l = [a for a in advisees_l if a not in guardados]
    estado["lista_advisees"] = advisees_l
    # Si ya has opinado sobre todos tus advisees, cierra la evaluación con el mensaje de siempre.
    if not advisees_l and guardados:
        with _lock:
            if user_id in conversaciones_ca:
                conversaciones_ca[user_id]["modo"] = "terminado"
            ca_dm_activas.discard(user_id)
        quitar_pendiente("ca", user_id)
        marcar_completada_por_slack_id(user_id, "ca")
        _editar_dm_inicial_ca(user_id, idioma)
        _quitar_botones_anteriores_ca(channel)
        slack_app.client.chat_postMessage(
            channel=channel, thread_ts=thread_ts, text=prefijo + t("bc.all_advisees_done", idioma),
        )
        return
    texto_header = prefijo + t("bc.which_advisee", idioma)
    if advisees_l:
        elementos = [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": nombre},
                "value": nombre,
                "action_id": f"ca_advisee_{i}",
            }
            for i, nombre in enumerate(advisees_l)
        ]
        elementos.append({
            "type": "button",
            "text": {"type": "plain_text", "text": t("bc.btn_finish", idioma), "emoji": True},
            "action_id": "ca_advisee_no",
        })
        blocks = [
            {"type": "section", "text": {"type": "mrkdwn", "text": texto_header}},
            {"type": "actions", "elements": elementos},
        ] + fila_atras("atras_ca", "bc.back_btn", estado, idioma)
        resp = slack_app.client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=texto_header,
            blocks=blocks,
        )
        _recordar_botones_ca(channel, resp, texto_header)
    else:
        _quitar_botones_anteriores_ca(channel)
        slack_app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=texto_header)


def _enviar_pregunta_permiso_claude(channel, thread_ts, idioma, estado):
    blocks = [
        {"type": "section", "text": {"type": "mrkdwn", "text": t("bc.claude_summary_q_full", idioma)}},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": t("bc.yes", idioma)},
                    "value": "si",
                    "action_id": "permiso_claude_si",
                    "style": "primary",
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": t("bc.no", idioma)},
                    "value": "no",
                    "action_id": "permiso_claude_no",
                },
            ],
        },
    ] + fila_atras("atras_ca", "bc.back_btn", estado, idioma)
    resp = slack_app.client.chat_postMessage(
        channel=channel, thread_ts=thread_ts, text=t("bc.claude_summary_q", idioma), blocks=blocks,
    )
    _recordar_botones_ca(channel, resp, t("bc.claude_summary_q", idioma))


def _enviar_pregunta_opinion(channel, thread_ts, idioma, estado):
    preguntas = obtener_preguntas_seguimiento_ca(idioma)
    if estado.get("opinion_via_claude"):
        # El resumen de Claude es largo y Slack lo colapsa con "Mostrar más", dejando la
        # petición de opinión escondida al final. Por eso enviamos el resumen en un mensaje
        # y la petición ("✍️ Añade a continuación...") en otro aparte, siempre visible.
        resumen = f"{t('bc.claude_summary_header', idioma)}\n\n{estado.get('resumen_actual', '')}"
        slack_app.client.chat_postMessage(
            channel=channel, thread_ts=thread_ts, text=resumen,
            blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": resumen}}],
        )
        texto = f"✍️ {preguntas.get('opinion_con_claude', '')}"
    else:
        texto = f"✍️ {preguntas.get('opinion_con_claude', '')}"
    bloques = [{"type": "section", "text": {"type": "mrkdwn", "text": texto}}] + fila_atras("atras_ca", "bc.back_btn", estado, idioma)
    resp = slack_app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=texto, blocks=bloques)
    _recordar_botones_ca(channel, resp, texto)


def _enviar_confirmacion_ca(channel, thread_ts, idioma, estado):
    texto_conf = t("bc.conf_summary", idioma, advisee=estado.get("advisee_actual", "?"), opinion=estado.get("opinion_actual", "?"))
    elementos = [
        {
            "type": "button",
            "text": {"type": "plain_text", "text": t("bc.btn_save_yes", idioma), "emoji": True},
            "style": "primary",
            "action_id": "ca_confirmar",
        },
        {
            "type": "button",
            "text": {"type": "plain_text", "text": t("bm.edit_btn", idioma), "emoji": True},
            "action_id": "ca_modificar",
        },
    ]
    if tiene_historial(estado):
        elementos.append(boton_atras("atras_ca", "bc.back_btn", idioma))
    bloques = [
        {"type": "section", "text": {"type": "mrkdwn", "text": texto_conf}},
        {"type": "actions", "elements": elementos},
    ]
    resp = slack_app.client.chat_postMessage(
        channel=channel,
        thread_ts=thread_ts,
        text=texto_conf,
        blocks=bloques,
    )
    _recordar_botones_ca(channel, resp, texto_conf)


def _enviar_pregunta_valor_modificacion_ca(channel, thread_ts, idioma, estado):
    texto = _texto_pregunta_modificacion_ca(idioma)
    bloques = [{"type": "section", "text": {"type": "mrkdwn", "text": texto}}] + fila_atras("atras_ca", "bc.back_btn", estado, idioma)
    resp = slack_app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=texto, blocks=bloques)
    _recordar_botones_ca(channel, resp, texto)


def _reenviar_pregunta_actual_ca(user_id, channel, thread_ts, estado, logger):
    idi = estado.get("idioma", "es")
    modo = estado.get("modo")
    if modo in ("esperando_advisee", "esperando_otro"):
        _enviar_lista_advisees(user_id, channel, thread_ts, estado, idi, logger)
    elif modo == "esperando_permiso_claude":
        _enviar_pregunta_permiso_claude(channel, thread_ts, idi, estado)
    elif modo == "esperando_opinion":
        _enviar_pregunta_opinion(channel, thread_ts, idi, estado)
    elif modo == "confirmacion_ca":
        _enviar_confirmacion_ca(channel, thread_ts, idi, estado)
    elif modo == "modificando_respuesta_ca":
        _enviar_pregunta_valor_modificacion_ca(channel, thread_ts, idi, estado)


# ---------------------------------------------------------------------------
# Lógica de conversación – llamada desde slack_bot.py
# ---------------------------------------------------------------------------

def manejar_mensaje_ca(event, logger) -> None:
    user_id = event.get("user")
    thread_ts = event.get("thread_ts")
    channel = event.get("channel")
    texto = (event.get("text") or "").strip()

    with _lock:
        es_activo = user_id in ca_dm_activas
    if not es_activo:
        return

    # Ha llegado un mensaje nuevo al hilo: cualquier mensaje con botones anterior queda
    # obsoleto, así que le retiramos los botones para que no se puedan volver a pulsar.
    _quitar_botones_anteriores_ca(channel)

    conv_key = user_id

    def reply(text):
        slack_app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=text)

    if normalizar_nombre(texto) == "sos":
        with _lock:
            estado_anterior = conversaciones_ca.pop(conv_key, {})
            guardados = estado_anterior.get("advisees_guardados", set())
            if guardados:
                conversaciones_ca[conv_key] = {"modo": "pre_inicial", "ca_nombre": None, "advisees_guardados": guardados, "idioma": idioma_por_slack_id(user_id)}
        reply(t("bm.eval_cancelled", idioma_por_slack_id(user_id)))
        return

    accion = None
    payload = {}

    # Idioma del CA (receptor), leído fresco para reflejar cambios recientes en Notion
    # y no quedar congelado en el estado de una conversación previa.
    _idi = idioma_por_slack_id(user_id)
    with _lock:
        estado = conversaciones_ca.get(conv_key)
        if estado is None:
            estado = {"modo": "pre_inicial", "ca_nombre": None, "idioma": _idi}
            conversaciones_ca[conv_key] = estado
        else:
            estado["idioma"] = _idi

        modo = estado["modo"]

        if modo == "pre_inicial":
            estado["modo"] = "esperando_advisee"
            accion = "pedir_advisee"

        elif modo == "esperando_advisee":
            if _es_no(texto):
                estado["modo"] = "terminado"
                accion = "terminar"
            else:
                payload["advisee"] = _resolver_numero_advisee(texto, estado)
                payload["ca_nombre"] = estado.get("ca_nombre")
                accion = "validar_y_mostrar"

        elif modo == "esperando_permiso_claude":
            if _es_si(texto):
                push_historial(estado)
                payload["advisee"] = estado.get("advisee_actual", "?")
                payload["resumen_bruto"] = estado.get("resumen_bruto", "")
                estado["modo"] = "esperando_opinion"
                estado["opinion_via_claude"] = True
                accion = "llamar_claude"
            elif _es_no(texto):
                push_historial(estado)
                estado["resumen_actual"] = estado.get("resumen_bruto", "")
                estado["modo"] = "esperando_opinion"
                estado["opinion_via_claude"] = False
                accion = "pedir_opinion_sin_claude"
            else:
                accion = "aclarar_permiso_claude"

        elif modo == "esperando_opinion":
            push_historial(estado)
            payload["advisee"] = estado.get("advisee_actual", "?")
            payload["ca_nombre"] = estado.get("ca_nombre")
            payload["opinion"] = texto
            estado["opinion_actual"] = texto
            estado["modo"] = "confirmacion_ca"
            accion = "mostrar_confirmacion_ca"

        elif modo == "confirmacion_ca":
            payload["advisee"] = estado.get("advisee_actual", "?")
            payload["ca_nombre"] = estado.get("ca_nombre")
            payload["opinion"] = estado.get("opinion_actual", "")
            if _es_confirmar(texto):
                estado["modo"] = "esperando_otro"
                accion = "guardar_y_preguntar_otro"
            elif _es_modificar(texto):
                push_historial(estado)
                estado["modo"] = "modificando_respuesta_ca"
                accion = "pedir_valor_modificacion_ca"
            elif _es_no(texto):
                limpiar_historial(estado)
                estado["modo"] = "esperando_otro"
                accion = "cancelar_opinion"
            else:
                accion = "mostrar_confirmacion_ca"

        elif modo == "modificando_respuesta_ca":
            payload["advisee"] = estado.get("advisee_actual", "?")
            payload["ca_nombre"] = estado.get("ca_nombre")
            payload["opinion"] = estado.get("opinion_actual", "")
            if texto:
                push_historial(estado)
                estado["opinion_actual"] = texto
                payload["opinion"] = texto
                estado["modo"] = "confirmacion_ca"
                accion = "mostrar_confirmacion_ca"
            else:
                accion = "pedir_valor_modificacion_ca"

        elif modo == "esperando_otro":
            if _es_no(texto):
                estado["modo"] = "terminado"
                accion = "terminar"
            else:
                payload["advisee"] = _resolver_numero_advisee(texto, estado)
                payload["ca_nombre"] = estado.get("ca_nombre")
                accion = "validar_y_mostrar"

        elif modo == "terminado":
            accion = "ya_terminado"

    def _reply_lista_advisees(prefijo=""):
        _enviar_lista_advisees(user_id, channel, thread_ts, estado, _idi, logger, prefijo)

    if accion == "pedir_advisee":
        # Primer mensaje del hilo: barra de carga mientras leemos los advisees de Notion.
        with AnimacionCargando(channel, thread_ts, _idi):
            _reply_lista_advisees()

    elif accion == "validar_y_mostrar":
        advisee = payload["advisee"]
        ca_nombre, ca_aliases = _identidad_usuario_slack(user_id, logger)
        advisee_encontrado = buscar_empleado_en_lista(advisee)
        permitido = False
        if advisee_encontrado:
            permitido, _ = _advisee_permitido_para_ca(ca_nombre, ca_aliases, advisee_encontrado)
        if not advisee_encontrado or not permitido:
            reply(t("bc.advisee_not_in_list", _idi, advisee=advisee))
            return
        else:
            advisee = advisee_encontrado
            with _lock:
                if conv_key in conversaciones_ca:
                    push_historial(conversaciones_ca[conv_key])
                    conversaciones_ca[conv_key]["advisee_actual"] = advisee
                    conversaciones_ca[conv_key]["ca_nombre"] = ca_nombre
            # Buscar datos en Notion (última opinión, evaluaciones, comentarios y objetivos)
            # puede tardar; mostramos la barra de carga animada mientras tanto.
            with AnimacionCargando(channel, thread_ts, _idi):
                desde_fecha = _fecha_ultima_opinion(ca_nombre, advisee)
                hace_4_semanas = (datetime.now(timezone.utc) - timedelta(weeks=4)).isoformat()
                desde_fecha = max(desde_fecha, hace_4_semanas) if desde_fecha else hace_4_semanas
                _anon = not _evaluadores_visibles_para_advisee(advisee, _cargar_anonimato())
                txt_proyecto = _texto_evals_proyecto(advisee, desde_fecha, _anon)
                txt_mensual = _texto_evals_mensuales(advisee, desde_fecha, _anon)
                txt_personal = _texto_seguimiento_personal(advisee, desde_fecha, _anon)
                txt_objetivos = _texto_objetivos(advisee)
            # Un desplegable por cada tipo de evaluación que tenga contenido.
            tipos = [("proyecto", txt_proyecto), ("mensual", txt_mensual), ("personal", txt_personal)]
            sin_novedades = not any(txt for _, txt in tipos)
            # Resumen combinado (solo para el resumen que genera Claude, no para la web).
            _partes = []
            if txt_proyecto:
                _partes.append(f"*Evaluaciones de proyecto:*\n{txt_proyecto}")
            if txt_mensual:
                _partes.append(f"*Evaluaciones mensuales:*\n{txt_mensual}")
            if txt_personal:
                _partes.append(f"*Seguimiento personal:*\n{txt_personal}")
            if txt_objetivos:
                _partes.append(f"📌 *Objetivos de {advisee}:*\n{txt_objetivos}")
            resumen = "\n\n".join(_partes)
            with _lock:
                if conv_key in conversaciones_ca:
                    if sin_novedades:
                        conversaciones_ca[conv_key]["modo"] = "esperando_otro"
                        conversaciones_ca[conv_key]["resumen_actual"] = ""
                    else:
                        conversaciones_ca[conv_key]["modo"] = "esperando_permiso_claude"
                        conversaciones_ca[conv_key]["resumen_bruto"] = resumen
                        conversaciones_ca[conv_key].setdefault("resumenes_ver", {})[advisee] = {
                            "proyecto": txt_proyecto, "mensual": txt_mensual, "personal": txt_personal,
                        }
            if sin_novedades:
                _reply_lista_advisees(t("bc.no_new_evals", _idi, advisee=advisee) + "\n\n")
            else:
                # Intro: anunciamos que vamos a mostrar toda la información del advisee.
                slack_app.client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=t("bc.info_intro", _idi, advisee=advisee),
                )
                # Siempre los tres tipos separados; los que no tengan nada salen como "sin novedades".
                for _tipo, _txt in tipos:
                    _blocks = _bloques_resumen_colapsado(_tipo, advisee, _idi) if _txt else _bloques_resumen_vacio(_tipo, advisee, _idi)
                    slack_app.client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text=_header_por_tipo(_tipo, advisee, _idi),
                        blocks=_blocks,
                    )
                if txt_objetivos:
                    slack_app.client.chat_postMessage(
                        channel=channel,
                        thread_ts=thread_ts,
                        text=f"📌 Objetivos de {advisee}",
                        blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": f"📌 *Objetivos de {advisee}:*\n{txt_objetivos}"}}],
                    )
                _enviar_pregunta_permiso_claude(channel, thread_ts, _idi, estado)

    elif accion == "mostrar_confirmacion_ca":
        _enviar_confirmacion_ca(channel, thread_ts, _idi, estado)

    elif accion == "pedir_valor_modificacion_ca":
        _enviar_pregunta_valor_modificacion_ca(channel, thread_ts, _idi, estado)

    elif accion == "llamar_claude":
        advisee = payload["advisee"]
        resumen_bruto = payload.get("resumen_bruto", "")
        # Memo en conversación: si ya generamos el resumen de Claude para este MISMO texto en
        # bruto (p. ej. el CA vuelve atrás y reenvía), reutilízalo en vez de re-llamar a la API
        # (y evita releer el cargo en Notion). Solo cachea resúmenes reales de Claude, no el
        # texto en bruto de reserva.
        _cache = (estado.get("resumen_claude_cache") or {})
        if _cache.get("bruto") == resumen_bruto and _cache.get("texto"):
            with _lock:
                if conv_key in conversaciones_ca:
                    conversaciones_ca[conv_key]["resumen_actual"] = _cache["texto"]
            _enviar_pregunta_opinion(channel, thread_ts, _idi, estado)
            return
        _, cargo = buscar_empleado_y_cargo(advisee)
        resumen_claude = None
        motivo_fallo = ""
        # Mientras Claude "piensa", mostramos una barra de carga animada en el hilo.
        with AnimacionCargando(channel, thread_ts, _idi):
            try:
                resumen_claude = generar_resumen_evaluacion(advisee, cargo or "", resumen_bruto, _idi)
            except ErrorIA as err:
                # Sin saldo, IA saturada…: el CA no puede hacer nada con un "no se pudo",
                # pero sí con el motivo y a quién avisar.
                motivo_fallo = texto_error_ia(err.codigo, str(err), _idi)
            except Exception:
                logging.exception("Error generando resumen Claude para '%s'", advisee)
        with _lock:
            if conv_key in conversaciones_ca:
                conversaciones_ca[conv_key]["resumen_actual"] = resumen_claude or resumen_bruto
                if resumen_claude:
                    conversaciones_ca[conv_key]["resumen_claude_cache"] = {"bruto": resumen_bruto, "texto": resumen_claude}
        if resumen_claude:
            _enviar_pregunta_opinion(channel, thread_ts, _idi, estado)
        else:
            _preg = obtener_preguntas_seguimiento_ca(_idi).get("opinion_con_claude", "")
            # El texto estaba fijo en español aunque el CA fuese de en/pt.
            aviso = motivo_fallo or t("bc.claude_summary_fail", _idi)
            texto_fallo = f"⚠️ {aviso}\n\n{_preg}"
            bloques = [{"type": "section", "text": {"type": "mrkdwn", "text": texto_fallo}}] + fila_atras("atras_ca", "bc.back_btn", estado, _idi)
            _resp = slack_app.client.chat_postMessage(channel=channel, thread_ts=thread_ts, text=texto_fallo, blocks=bloques)
            _recordar_botones_ca(channel, _resp, texto_fallo)

    elif accion == "pedir_opinion_sin_claude":
        _enviar_pregunta_opinion(channel, thread_ts, _idi, estado)

    elif accion == "aclarar_permiso_claude":
        reply(t("bc.clarify_claude", _idi))

    elif accion == "cancelar_opinion":
        _reply_lista_advisees(t("bc.opinion_not_saved", _idi))

    elif accion == "guardar_y_preguntar_otro":
        ca_nombre, ca_aliases = _identidad_usuario_slack(user_id, logger)
        if payload.get("ca_nombre"):
            ca_aliases.append(payload["ca_nombre"])
        permitido, permitidos = _advisee_permitido_para_ca(ca_nombre, ca_aliases, payload["advisee"])
        if not permitido:
            opciones = "\n".join(f"- {item}" for item in permitidos) if permitidos else t("bc.no_associated_advisees", _idi)
            reply(t("bc.cannot_save_not_associated", _idi, advisee=payload['advisee'], opciones=opciones))
            return
        resumen = estado.get("resumen_actual", "")
        ok, error = _guardar_opinion(ca_nombre, payload["advisee"], payload["opinion"], resumen)
        if ok:
            with _lock:
                guardados = estado.setdefault("advisees_guardados", set())
                guardados.add(payload["advisee"])
                limpiar_historial(estado)
            _reply_lista_advisees(t("bc.opinion_saved", _idi))
        else:
            _reply_lista_advisees(t("bc.opinion_save_error", _idi, error=error[:300]))

    elif accion == "terminar":
        reply(t("bc.thanks_end", _idi))

    elif accion == "ya_terminado":
        reply(t("bc.already_concluded", _idi))


@slack_app.action(re.compile(r"^ca_advisee_\d+$"))
def _handle_ca_elegir_advisee(ack, body, client, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        advisee_name = body["actions"][0]["value"]
        msg = body.get("message", {})
        channel = body["channel"]["id"]
        thread_ts = msg.get("thread_ts") or msg.get("ts", "")
        _idi = idioma_por_slack_id(user_id)
        try:
            client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": t("bc.advisee_selected", _idi, name=advisee_name)}}],
                text=t("bc.advisee_selected_plain", _idi, name=advisee_name),
            )
            _olvidar_botones_ca(channel)
        except Exception:
            logger.warning("No se pudo actualizar el mensaje de selección de advisee")
        evento = {
            "user": user_id,
            "channel": channel,
            "thread_ts": thread_ts,
            "text": advisee_name,
        }
        manejar_mensaje_ca(evento, logger)
    except Exception:
        logger.exception("Error procesando selección de advisee")


@slack_app.action("ca_advisee_no")
def _handle_ca_advisee_no(ack, body, client, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        msg = body.get("message", {})
        channel = body["channel"]["id"]
        thread_ts = msg.get("thread_ts") or msg.get("ts", "")
        _idi = idioma_por_slack_id(user_id)
        try:
            client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": t("bc.finished_update", _idi)}}],
                text=t("bc.finished_update", _idi),
            )
            _olvidar_botones_ca(channel)
        except Exception:
            pass
        evento = {
            "user": user_id,
            "channel": channel,
            "thread_ts": thread_ts,
            "text": "sos",
        }
        manejar_mensaje_ca(evento, logger)
    except Exception:
        logger.exception("Error procesando ca_advisee_no")


def _leer_tipo_advisee(body):
    """Extrae (tipo, advisee) del value JSON del botón; tolera formatos antiguos."""
    valor = body["actions"][0].get("value", "")
    try:
        datos = json.loads(valor)
        return datos.get("t", ""), datos.get("a", "")
    except Exception:
        return "", valor


@slack_app.action("ca_ver_evaluaciones")
def _handle_ca_ver_evaluaciones(ack, body, client, logger):
    """Abre en una ventana (modal) las evaluaciones de ese tipo recibidas por el advisee.
    El mensaje del hilo conserva su botón, para poder volver a consultar la info."""
    ack()
    trigger_id = body.get("trigger_id")
    if not trigger_id:
        return
    try:
        user_id = body["user"]["id"]
        tipo, advisee = _leer_tipo_advisee(body)
        _idi = idioma_por_slack_id(user_id)
        with _lock:
            estado = conversaciones_ca.get(user_id, {})
            textos = (estado.get("resumenes_ver", {}) or {}).get(advisee, {})
        resumen = textos.get(tipo, "") if isinstance(textos, dict) else (textos or "")
        client.views_open(
            trigger_id=trigger_id,
            view=_build_resumen_ca_view(tipo, advisee, resumen, _idi),
        )
    except Exception:
        logger.exception("Error abriendo modal de evaluaciones del advisee")


# ---------------------------------------------------------------------------
# Ciclos de recordatorio y envío
# ---------------------------------------------------------------------------

_RECORDATORIO_CA_SEGUNDOS = 7 * 24 * 60 * 60  # 1 semana


def ciclo_recordatorios_ca() -> None:
    while True:
        time.sleep(30)
        ahora = time.time()
        with _lock:
            pendientes = [
                uid for uid in list(ca_dm_activas)
                if ahora - max(
                    ca_hora_dm.get(uid, ahora),
                    ca_ultimo_recordatorio_dm.get(uid, 0) or ca_hora_dm.get(uid, ahora),
                ) >= _RECORDATORIO_CA_SEGUNDOS
            ]
        for uid in pendientes:
            try:
                ca_nombre = obtener_nombre_por_id_usuario(uid)
                if not ca_nombre:
                    try:
                        resp = slack_app.client.users_info(user=uid)
                        u = resp.get("user", {})
                        p = u.get("profile", {})
                        ca_nombre = u.get("real_name") or p.get("real_name") or p.get("display_name") or u.get("name") or uid
                    except Exception:
                        ca_nombre = uid
                if _ca_guardo_desde(ca_nombre, ca_hora_dm.get(uid, 0)):
                    with _lock:
                        ca_dm_activas.discard(uid)
                    continue
                dm_channel = ca_dm_canal.get(uid)
                if not dm_channel:
                    continue
                slack_app.client.chat_postMessage(
                    channel=dm_channel,
                    text=t("bc.reminder", idioma_por_slack_id(uid)),
                )
                with _lock:
                    ca_ultimo_recordatorio_dm[uid] = time.time()
            except Exception:
                logging.exception(f"Error enviando recordatorio CA DM a {uid}")


def notificar_acceso_informe_final_web(advisee: str) -> bool:
    """Avisa por Slack al advisee de que su CA le ha dado acceso al informe final. Para uso desde la web."""
    slack_id = obtener_slack_id_por_nombre(advisee)
    if not slack_id:
        logging.warning("No se encontró Slack ID para '%s', no se puede notificar acceso a informe final", advisee)
        return False
    try:
        dm = slack_app.client.conversations_open(users=[slack_id])
        channel = dm["channel"]["id"]
        slack_app.client.chat_postMessage(
            channel=channel,
            text=t("bc.informe_final_disponible", idioma_por_slack_id(slack_id)),
        )
        logging.info("Notificación de informe final disponible enviada a '%s'", advisee)
        return True
    except Exception as e:
        if "user_not_found" in str(e):
            logging.warning(
                "Slack ID '%s' de '%s' no encontrado en el workspace. Comprueba el campo ID_usuario en Notion.",
                slack_id, advisee,
            )
        else:
            logging.exception("Error notificando acceso a informe final a '%s'", advisee)
        return False


def obtener_resumen_advisee_para_ca(ca_nombre: str, advisee: str) -> tuple[str, bool]:
    """Devuelve (resumen_texto, sin_novedades) para un advisee. Para uso desde la web."""
    desde_fecha = _fecha_ultima_opinion(ca_nombre, advisee)
    hace_4_semanas = (datetime.now(timezone.utc) - timedelta(weeks=4)).isoformat()
    desde_fecha = max(desde_fecha, hace_4_semanas) if desde_fecha else hace_4_semanas
    _cfg_anon = _cargar_anonimato()
    resumen = _resumen_advisee(advisee, desde_fecha, anonimo=not _evaluadores_visibles_para_advisee(advisee, _cfg_anon))
    sin_novedades = "no hay evaluaciones nuevas" in resumen or "No hay evaluaciones registradas" in resumen
    return resumen, sin_novedades


# ---------------------------------------------------------------------------
# Ejemplo de guía — modal CA
# ---------------------------------------------------------------------------

def _build_ejemplo_ca_view(idioma="es") -> dict:
    ejemplos = obtener_ejemplos_guia(idioma)
    ejemplo = ejemplos.get("CA", t("bm.no_example", idioma))
    return {
        "type": "modal",
        "callback_id": "ejemplo_ca_ver",
        "title": {"type": "plain_text", "text": t("bm.guide_example_title", idioma)},
        "close": {"type": "plain_text", "text": t("bm.close", idioma)},
        "blocks": [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": t("bc.guide_example_header", idioma)},
            },
            {"type": "divider"},
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": ejemplo[:3000] if ejemplo else t("bm.no_example", idioma)},
            },
        ],
    }


@slack_app.action(re.compile(r"^lang_set_ca_(es|en|pt)$"))
def _handle_lang_set_ca(ack, body, logger):
    ack()
    try:
        user_id = body.get("user", {}).get("id", "")
        idioma_elegido = body["actions"][0]["value"]
        nuevo = guardar_idioma_por_slack_id(user_id, idioma_elegido)
        channel = (body.get("channel") or {}).get("id") or (body.get("container") or {}).get("channel_id")
        ts = (body.get("message") or {}).get("ts") or (body.get("container") or {}).get("message_ts")
        if channel and ts:
            slack_app.client.chat_update(
                channel=channel,
                ts=ts,
                text=t("bc.pending_fallback", nuevo),
                blocks=_bloques_dm_ca(nuevo),
            )
    except Exception:
        logger.exception("Error cambiando idioma (CA)")


def _vista_modal_cargando() -> dict:
    """Modal ligero de carga: se abre al instante para no agotar el trigger_id de Slack."""
    return {
        "type": "modal",
        "title": {"type": "plain_text", "text": "Ejemplo"},
        "close": {"type": "plain_text", "text": "Cerrar"},
        "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": "⏳ Cargando… / Loading… / A carregar…"}}],
    }


@slack_app.action("ca_ver_ejemplo")
def _handle_ca_ver_ejemplo(ack, body, logger):
    ack()
    trigger_id = body.get("trigger_id")
    if not trigger_id:
        return
    # Abrir modal de carga YA (sin lecturas de Notion) para no agotar el trigger_id (~3s).
    try:
        resp = slack_app.client.views_open(trigger_id=trigger_id, view=_vista_modal_cargando())
    except Exception:
        logger.exception("Error abriendo modal de ejemplo CA")
        return
    try:
        _idi = idioma_por_slack_id(body.get("user", {}).get("id", ""))
        slack_app.client.views_update(view_id=resp["view"]["id"], view=_build_ejemplo_ca_view(_idi))
    except Exception:
        logger.exception("Error actualizando modal de ejemplo CA")


def _arrancar_ca_desde_boton(body, logger, con_ejemplo):
    """Botones Sí/No del DM inicial CA. 'Sí' publica el ejemplo en el hilo;
    ambos arrancan la evaluación inyectando el evento que antes generaba el primer
    mensaje del usuario. Si la conversación ya está en marcha, 'Sí' solo muestra
    el ejemplo y 'No' no hace nada."""
    user_id = body.get("user", {}).get("id", "")
    channel = (body.get("channel") or {}).get("id") or (body.get("container") or {}).get("channel_id")
    msg = body.get("message") or {}
    thread_ts = msg.get("thread_ts") or msg.get("ts")
    if not (user_id and channel and thread_ts):
        return
    with _lock:
        es_activo = user_id in ca_dm_activas and thread_ts == ca_dm_ts.get(user_id)
        estado = conversaciones_ca.get(user_id)
        ya_empezada = estado is not None and estado.get("modo", "pre_inicial") != "pre_inicial"
    if not es_activo:
        return
    idioma = idioma_por_slack_id(user_id)
    if con_ejemplo:
        with AnimacionCargando(channel, thread_ts, idioma):
            ejemplo = obtener_ejemplos_guia(idioma).get("CA") or t("bm.no_example", idioma)
        slack_app.client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=f"{t('bc.guide_example_header', idioma)}\n\n{ejemplo[:2900]}",
        )
    if ya_empezada:
        return
    manejar_mensaje_ca({"user": user_id, "channel": channel, "thread_ts": thread_ts, "text": ""}, logger)


@slack_app.action("ca_ejemplo_si")
def _handle_ca_ejemplo_si(ack, body, logger):
    ack()
    try:
        _arrancar_ca_desde_boton(body, logger, con_ejemplo=True)
    except Exception:
        logger.exception("Error arrancando evaluación CA desde el botón Sí")


@slack_app.action("ca_ejemplo_no")
def _handle_ca_ejemplo_no(ack, body, logger):
    ack()
    try:
        _arrancar_ca_desde_boton(body, logger, con_ejemplo=False)
    except Exception:
        logger.exception("Error arrancando evaluación CA desde el botón No")


def ciclo_envio_ca() -> None:
    if config.APP_MODE != "produccion":
        try:
            enviar_pregunta_inicial_ca()
        except Exception:
            logging.exception("Error en ciclo CA")
        while True:
            time.sleep(config.INTERVALO_PRUEBA_DIAS * 24 * 60 * 60)
            try:
                enviar_pregunta_inicial_ca()
            except Exception:
                logging.exception("Error en ciclo CA")
        return
    # Producción: cada 4 semanas, pero una semana DESPUÉS de proyecto (offset_dias).
    # esperar_hasta_proximo_envio relee el calendario mientras espera, así un cambio de
    # fecha en caliente se aplica sin reiniciar.
    while True:
        esperar_hasta_proximo_envio("proyecto_ca", 4, offset_dias=config.CA_OFFSET_DIAS, etiqueta="[CA]")
        try:
            enviar_pregunta_inicial_ca()
        except Exception:
            logging.exception("Error en ciclo CA producción")


# ---------------------------------------------------------------------------
# Action handler – botones Sí / No para permiso Claude
# ---------------------------------------------------------------------------

@slack_app.action("ca_confirmar")
def _handle_ca_confirmar(ack, body, logger):
    ack()
    _cerrar_botones_click_ca(body)
    try:
        evento = {
            "user": body["user"]["id"],
            "channel": body["channel"]["id"],
            "thread_ts": body["message"].get("thread_ts") or body["message"]["ts"],
            "text": "sí",
        }
        manejar_mensaje_ca(evento, logger)
    except Exception:
        logger.exception("Error procesando confirmación CA interactiva")


@slack_app.action("ca_modificar")
def _handle_ca_modificar(ack, body, logger):
    ack()
    _cerrar_botones_click_ca(body)
    try:
        evento = {
            "user": body["user"]["id"],
            "channel": body["channel"]["id"],
            "thread_ts": body["message"].get("thread_ts") or body["message"]["ts"],
            "text": "modificar",
        }
        manejar_mensaje_ca(evento, logger)
    except Exception:
        logger.exception("Error procesando modificación CA interactiva")


@slack_app.action("atras_ca")
def _handle_ca_atras(ack, body, client, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        channel = body["channel"]["id"]
        msg = body.get("message", {})
        thread_ts = msg.get("thread_ts") or msg.get("ts", "")
        idi = idioma_por_slack_id(user_id)
        try:
            client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": t("bc.back_done", idi)}}],
                text=t("bc.back_done", idi),
            )
            _olvidar_botones_ca(channel)
        except Exception:
            logger.warning("No se pudo actualizar el mensaje al volver atrás (CA)")

        with _lock:
            estado = conversaciones_ca.get(user_id)
            if not estado or not pop_historial(estado):
                return
        _reenviar_pregunta_actual_ca(user_id, channel, thread_ts, estado, logger)
    except Exception:
        logger.exception("Error procesando atrás en opiniones CA")


@slack_app.action(re.compile(r"^permiso_claude_(si|no)$"))
def _handle_permiso_claude(ack, body, client, logger):
    ack()
    try:
        user_id = body["user"]["id"]
        valor = body["actions"][0]["value"]
        channel = body["channel"]["id"]
        msg = body.get("message", {})
        thread_ts = msg.get("thread_ts") or msg.get("ts", "")
        _idi = idioma_por_slack_id(user_id)
        texto_sel = t("bc.claude_yes_update", _idi) if valor == "si" else t("bc.claude_no_update", _idi)
        try:
            client.chat_update(
                channel=channel,
                ts=msg["ts"],
                blocks=[{"type": "section", "text": {"type": "mrkdwn", "text": texto_sel}}],
                text=texto_sel,
            )
            _olvidar_botones_ca(channel)
        except Exception:
            logger.warning("No se pudo actualizar el mensaje de permiso Claude")
        evento = {
            "user": user_id,
            "channel": channel,
            "thread_ts": thread_ts,
            "text": "sí" if valor == "si" else "no",
        }
        manejar_mensaje_ca(evento, logger)
    except Exception:
        logger.exception("Error procesando permiso Claude interactivo")
