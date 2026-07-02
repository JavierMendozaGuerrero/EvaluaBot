"""
Flujo de revisión para Career Advisors (CA).

El bot envía un DM a cada empleado con un mensaje de notificación.
El usuario responde en el hilo: sí → bot pide nombre del advisee → muestra todas
las evaluaciones desde la última revisión del CA → pide opinión → guarda en
Notion → pregunta si hay otro advisee.
"""

import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from . import config
from .clients import notion, slack_app
from .i18n import t, boton_idioma_slack
from .notion_service import (
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
    toggle_idioma_slack,
    buscar_empleado_en_lista,
    buscar_empleado_y_cargo,
    obtener_advisees,
    obtener_comentarios_personales,
    obtener_config_calendario,
    obtener_ejemplos_guia,
    obtener_evaluaciones_por_evaluado,
    obtener_nombre_por_id_usuario,
    obtener_objetivos_persona,
    obtener_preguntas_seguimiento_ca,
    obtener_slack_ids_empleados,
    siguiente_envio_calendario,
    sugerir_empleados_parecidos,
)
from .skill_resumen_evaluacion import generar_resumen_evaluacion
from .utils import normalizar_nombre
from .anonimato import cargar_config as _cargar_anonimato, evaluadores_visibles_para_advisee as _evaluadores_visibles_para_advisee

# ---------------------------------------------------------------------------
# Estado compartido
# ---------------------------------------------------------------------------

ca_dm_activas: set = set()             # user_ids con evaluación CA activa
ca_dm_ts: dict = {}                    # user_id -> ts del mensaje inicial (raíz del hilo)
ca_dm_canal: dict = {}                 # user_id -> dm_channel_id
ca_hora_dm: dict = {}                  # user_id -> timestamp de envío
ca_ultimo_recordatorio_dm: dict = {}   # user_id -> timestamp del último recordatorio
conversaciones_ca: dict = {}           # user_id -> estado de conversación
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


def _obtener_o_crear_bbdd_ca(advisee: str) -> str:
    titulo = f"{PREFIJO_BBDD}{advisee.strip()}"
    with _lock:
        if titulo in _cache_bbdd:
            return _cache_bbdd[titulo]

    parent = _parent_bbdd_referencia()
    parent_ca = _parent_bbdd_en_pagina(config.NOTION_CA_TRACKING_PAGE_NAME, crear=True)
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
            _asegurar_propiedades_ca(db_id)
            with _lock:
                _cache_bbdd[titulo] = db_id
            return db_id

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

def _resumen_advisee(advisee: str, desde_fecha: str | None, anonimo: bool = True) -> str:
    try:
        evaluaciones = obtener_evaluaciones_por_evaluado(advisee)
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
            f"Valoración: {ev.get('q1', '?')} | "
            f"Ejemplo: {ev.get('q2', '?')}"
        )

    n = len(lineas)
    cabecera = f"*{advisee}* – {n} evaluación{'es' if n != 1 else ''}"
    if desde_fecha:
        cabecera += f" desde {desde_fecha[:10]}"
    resumen = cabecera + ":\n" + "\n".join(lineas)

    # Añadir comentarios de evaluaciones personales
    try:
        comentarios = obtener_comentarios_personales(advisee)
        if desde_fecha:
            comentarios = [c for c in comentarios if c.get("fecha", "") > desde_fecha]
        if comentarios:
            lineas_personales = []
            for c in sorted(comentarios, key=lambda x: x.get("fecha", "")):
                autor_c = "Anónimo" if anonimo else c['autor']
                lineas_personales.append(
                    f"• [{c['fecha']}] *{autor_c}* → _{c['comentario']}_"
                )
            resumen += f"\n\n*Comentarios personales ({len(lineas_personales)}):*\n" + "\n".join(lineas_personales)
    except Exception:
        logging.exception("Error leyendo comentarios personales de '%s'", advisee)

    # Añadir objetivos (solo títulos y KPIs como recordatorio)
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
# Helpers
# ---------------------------------------------------------------------------

def _es_si(texto: str) -> bool:
    return normalizar_nombre(texto) in {"si", "sí", "s", "yes", "y", "claro", "sip", "vale"}


def _es_no(texto: str) -> bool:
    return normalizar_nombre(texto) in {"no", "n", "nope", "nel"}


def _es_confirmar(texto: str) -> bool:
    return normalizar_nombre(texto) in {"si", "sí", "s", "ok", "okay", "confirmar", "guardar", "correcto",
                                        "yes", "y", "save", "confirm", "correct"}


def _es_modificar(texto: str) -> bool:
    return normalizar_nombre(texto) in {"modificar", "cambiar", "editar", "repetir",
                                        "modify", "change", "edit", "repeat"}


_OPCIONES_MODIFICACION_CA = {
    "1": "advisee", "advisee": "advisee",
    "2": "opinion", "opinion": "opinion",
}


def _texto_menu_modificacion_ca(idioma="es") -> str:
    return t("bc.mod_which", idioma)


def _bloques_menu_modificacion_ca(idioma="es") -> list:
    """Menú '¿Qué respuesta quieres modificar?' (CA) como botones."""
    return [
        {"type": "section", "text": {"type": "mrkdwn", "text": t("bc.mod_which_bold", idioma)}},
        {"type": "actions", "elements": [
            {"type": "button", "text": {"type": "plain_text", "text": "Advisee"}, "value": "1", "action_id": "mod_ca_1"},
            {"type": "button", "text": {"type": "plain_text", "text": t("bc.opinion_label", idioma)}, "value": "2", "action_id": "mod_ca_2"},
        ]},
    ]


def _clave_modificacion_ca(texto: str) -> str | None:
    return _OPCIONES_MODIFICACION_CA.get(normalizar_nombre(texto))


def _texto_pregunta_ca_por_clave(clave: str, idioma="es") -> str:
    if clave == "advisee":
        return t("bc.ask_advisee_name", idioma)
    if clave == "opinion":
        return obtener_preguntas_seguimiento_ca().get("opinion", "")
    return t("bc.enter_new_answer", idioma)


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

def _bloques_dm_ca(idioma):
    """Bloques del DM inicial de las evaluaciones CA, con botón de cambio de idioma en la cabecera."""
    return [
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": t("bc.pending_intro", idioma)},
            "accessory": boton_idioma_slack(idioma, "lang_toggle_ca"),
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": t("bp.example_label", idioma)},
            "accessory": {
                "type": "button",
                "text": {"type": "plain_text", "text": t("bp.see_example", idioma)},
                "action_id": "ca_ver_ejemplo",
            },
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": t("bp.send_to_start", idioma)},
        },
        {"type": "divider"},
    ]


def enviar_pregunta_inicial_ca() -> None:
    try:
        if config.APP_MODE != "produccion" and config.SLACK_TEST_USER_ID:
            slack_ids = [config.SLACK_TEST_USER_ID]
            logging.info(f"Modo prueba CA: enviando solo a {config.SLACK_TEST_USER_ID}")
        else:
            slack_ids = obtener_slack_ids_empleados()
            if not slack_ids:
                logging.warning("No se encontraron Slack IDs para envío CA")
                return

        with _lock:
            ca_dm_activas.clear()

        for user_id in slack_ids:
            try:
                ca_nombre, ca_aliases = _identidad_usuario_slack(user_id, logging)
                advisees = obtener_advisees(ca_nombre, ca_aliases=ca_aliases)
                if not advisees:
                    logging.info(f"[CA] {user_id} ({ca_nombre}) no tiene advisees, omitiendo")
                    continue

                resp_dm = slack_app.client.conversations_open(users=[user_id])
                dm_channel = resp_dm["channel"]["id"]
                _idi = idioma_por_slack_id(user_id)
                resp = slack_app.client.chat_postMessage(
                    channel=dm_channel,
                    text=t("bc.pending_fallback", _idi),
                    blocks=_bloques_dm_ca(_idi),
                )
                with _lock:
                    ca_dm_activas.add(user_id)
                    ca_dm_canal[user_id] = dm_channel
                    ca_dm_ts[user_id] = resp["ts"]
                    ca_hora_dm[user_id] = time.time()
                    conversaciones_ca.pop(user_id, None)
                logging.info(f"Mensaje CA enviado por DM a {user_id}, ts={resp['ts']}")
            except Exception:
                logging.exception(f"Error enviando DM CA a {user_id}")
    except Exception:
        logging.exception("Error en enviar_pregunta_inicial_ca")


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

    with _lock:
        estado = conversaciones_ca.get(conv_key)
        if estado is None:
            estado = {"modo": "pre_inicial", "ca_nombre": None, "idioma": idioma_por_slack_id(user_id)}
            conversaciones_ca[conv_key] = estado
        _idi = estado.get("idioma", "es")

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
                payload["advisee"] = estado.get("advisee_actual", "?")
                payload["resumen_bruto"] = estado.get("resumen_bruto", "")
                estado["modo"] = "esperando_opinion"
                accion = "llamar_claude"
            elif _es_no(texto):
                estado["resumen_actual"] = estado.get("resumen_bruto", "")
                estado["modo"] = "esperando_opinion"
                accion = "pedir_opinion_sin_claude"
            else:
                accion = "aclarar_permiso_claude"

        elif modo == "esperando_opinion":
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
                estado["modo"] = "seleccionando_modificacion_ca"
                accion = "pedir_modificacion_ca"
            elif _es_no(texto):
                estado["modo"] = "esperando_otro"
                accion = "cancelar_opinion"
            else:
                accion = "mostrar_confirmacion_ca"

        elif modo == "seleccionando_modificacion_ca":
            payload["advisee"] = estado.get("advisee_actual", "?")
            payload["ca_nombre"] = estado.get("ca_nombre")
            payload["opinion"] = estado.get("opinion_actual", "")
            campo = _clave_modificacion_ca(texto)
            if campo:
                estado["campo_modificando"] = campo
                estado["modo"] = "modificando_respuesta_ca"
                accion = "pedir_valor_modificacion_ca"
            else:
                accion = "pedir_modificacion_ca"

        elif modo == "modificando_respuesta_ca":
            payload["advisee"] = estado.get("advisee_actual", "?")
            payload["ca_nombre"] = estado.get("ca_nombre")
            payload["opinion"] = estado.get("opinion_actual", "")
            campo = estado.get("campo_modificando")
            if campo and texto:
                if campo == "advisee":
                    empleado = buscar_empleado_en_lista(texto)
                    if not empleado:
                        accion = "pedir_valor_modificacion_ca"
                        payload["error_advisee"] = texto
                    else:
                        ca_nombre, ca_aliases = _identidad_usuario_slack(user_id, logger)
                        permitido, permitidos = _advisee_permitido_para_ca(ca_nombre, ca_aliases, empleado)
                        if not permitido:
                            accion = "pedir_valor_modificacion_ca"
                            payload["error_advisee_no_asociado"] = empleado
                            payload["advisees_permitidos"] = permitidos
                        else:
                            estado["ca_nombre"] = ca_nombre
                            estado["advisee_actual"] = empleado
                            payload["advisee"] = empleado
                            estado.pop("campo_modificando", None)
                            estado["modo"] = "confirmacion_ca"
                            accion = "mostrar_confirmacion_ca"
                elif campo == "opinion":
                    estado["opinion_actual"] = texto
                    payload["opinion"] = texto
                    estado.pop("campo_modificando", None)
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
        ca_nombre_l, ca_aliases_l = _identidad_usuario_slack(user_id, logger)
        advisees_l = obtener_advisees(ca_nombre_l, ca_aliases=ca_aliases_l)
        guardados = estado.get("advisees_guardados", set())
        advisees_l = [a for a in advisees_l if a not in guardados]
        estado["lista_advisees"] = advisees_l
        # Si ya has opinado sobre todos tus advisees, cierra la evaluación con el mensaje de siempre.
        if not advisees_l and guardados:
            with _lock:
                if conv_key in conversaciones_ca:
                    conversaciones_ca[conv_key]["modo"] = "terminado"
            reply(prefijo + t("bc.all_advisees_done", _idi))
            return
        texto_header = prefijo + t("bc.which_advisee", _idi)
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
                "text": {"type": "plain_text", "text": t("bc.btn_finish", _idi), "emoji": True},
                "action_id": "ca_advisee_no",
            })
            blocks = [
                {"type": "section", "text": {"type": "mrkdwn", "text": texto_header}},
                {"type": "actions", "elements": elementos},
            ]
            slack_app.client.chat_postMessage(
                channel=channel,
                thread_ts=thread_ts,
                text=texto_header,
                blocks=blocks,
            )
        else:
            reply(texto_header)

    if accion == "pedir_advisee":
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
                    conversaciones_ca[conv_key]["advisee_actual"] = advisee
                    conversaciones_ca[conv_key]["ca_nombre"] = ca_nombre
            desde_fecha = _fecha_ultima_opinion(ca_nombre, advisee)
            hace_4_semanas = (datetime.now(timezone.utc) - timedelta(weeks=4)).isoformat()
            desde_fecha = max(desde_fecha, hace_4_semanas) if desde_fecha else hace_4_semanas
            _cfg_anon = _cargar_anonimato()
            resumen = _resumen_advisee(advisee, desde_fecha, anonimo=not _evaluadores_visibles_para_advisee(advisee, _cfg_anon))
            sin_novedades = "no hay evaluaciones nuevas" in resumen or "No hay evaluaciones registradas" in resumen
            with _lock:
                if conv_key in conversaciones_ca:
                    if sin_novedades:
                        conversaciones_ca[conv_key]["modo"] = "esperando_otro"
                        conversaciones_ca[conv_key]["resumen_actual"] = ""
                    else:
                        conversaciones_ca[conv_key]["modo"] = "esperando_permiso_claude"
                        conversaciones_ca[conv_key]["resumen_bruto"] = resumen
            if sin_novedades:
                _reply_lista_advisees(f"{resumen}\n\n")
            else:
                reply(resumen)
                slack_app.client.chat_postMessage(
                    channel=channel,
                    thread_ts=thread_ts,
                    text=t("bc.claude_summary_q", _idi),
                    blocks=[
                        {
                            "type": "section",
                            "text": {"type": "mrkdwn", "text": t("bc.claude_summary_q_full", _idi)},
                        },
                        {
                            "type": "actions",
                            "block_id": f"blq_claude_{user_id}",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": t("bc.yes", _idi)},
                                    "value": "si",
                                    "action_id": "permiso_claude_si",
                                    "style": "primary",
                                },
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": t("bc.no", _idi)},
                                    "value": "no",
                                    "action_id": "permiso_claude_no",
                                },
                            ],
                        },
                    ],
                )

    elif accion == "mostrar_confirmacion_ca":
        texto_conf = t("bc.conf_summary", _idi, advisee=payload.get('advisee', '?'), opinion=payload.get('opinion', '?'))
        slack_app.client.chat_postMessage(
            channel=channel,
            thread_ts=thread_ts,
            text=texto_conf,
            blocks=[
                {"type": "section", "text": {"type": "mrkdwn", "text": texto_conf}},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": t("bc.btn_save_yes", _idi), "emoji": True},
                            "style": "primary",
                            "action_id": "ca_confirmar",
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": t("bm.edit_btn", _idi), "emoji": True},
                            "action_id": "ca_modificar",
                        },
                    ],
                },
            ],
        )

    elif accion == "pedir_modificacion_ca":
        slack_app.client.chat_postMessage(
            channel=channel, thread_ts=thread_ts,
            text=t("bc.mod_which", _idi),
            blocks=_bloques_menu_modificacion_ca(_idi),
        )

    elif accion == "pedir_valor_modificacion_ca":
        campo = estado.get("campo_modificando")
        if payload.get("error_advisee_no_asociado"):
            permitidos = payload.get("advisees_permitidos") or []
            opciones = "\n".join(f"- {item}" for item in permitidos) if permitidos else t("bc.no_associated_advisees", _idi)
            reply(t("bc.error_advisee_not_associated", _idi, advisee=payload['error_advisee_no_asociado'], opciones=opciones))
        elif payload.get("error_advisee"):
            sugerencias = sugerir_empleados_parecidos(payload["error_advisee"])
            if sugerencias:
                opciones = "\n".join(f"- {n}" for n in sugerencias)
                reply(t("bc.error_advisee_suggest", _idi, advisee=payload['error_advisee'], opciones=opciones))
            else:
                reply(t("bc.error_advisee_no_suggest", _idi, advisee=payload['error_advisee']))
        else:
            reply(_texto_pregunta_ca_por_clave(campo, _idi) if campo else _texto_menu_modificacion_ca(_idi))

    elif accion == "llamar_claude":
        advisee = payload["advisee"]
        resumen_bruto = payload.get("resumen_bruto", "")
        _, cargo = buscar_empleado_y_cargo(advisee)
        try:
            resumen_claude = generar_resumen_evaluacion(advisee, cargo or "", resumen_bruto)
            with _lock:
                if conv_key in conversaciones_ca:
                    conversaciones_ca[conv_key]["resumen_actual"] = resumen_claude
            _preg = obtener_preguntas_seguimiento_ca().get("opinion_con_claude", "")
            reply(f"📊 *Resumen generado por Claude:*\n\n{resumen_claude}\n\n{_preg}")
        except Exception:
            logging.exception("Error generando resumen Claude para '%s'", advisee)
            with _lock:
                if conv_key in conversaciones_ca:
                    conversaciones_ca[conv_key]["resumen_actual"] = resumen_bruto
            _preg = obtener_preguntas_seguimiento_ca().get("opinion_con_claude", "")
            reply(f"⚠️ No se pudo generar el resumen con Claude.\n\n{_preg}")

    elif accion == "pedir_opinion_sin_claude":
        reply(obtener_preguntas_seguimiento_ca().get("opinion_sin_claude", ""))

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
            guardados = estado.setdefault("advisees_guardados", set())
            guardados.add(payload["advisee"])
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
    ejemplos = obtener_ejemplos_guia()
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


@slack_app.action("lang_toggle_ca")
def _handle_lang_toggle_ca(ack, body, logger):
    ack()
    try:
        user_id = body.get("user", {}).get("id", "")
        nuevo = toggle_idioma_slack(user_id)
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


@slack_app.action("ca_ver_ejemplo")
def _handle_ca_ver_ejemplo(ack, body, logger):
    ack()
    trigger_id = body.get("trigger_id")
    if not trigger_id:
        return
    try:
        _idi = idioma_por_slack_id(body.get("user", {}).get("id", ""))
        slack_app.client.views_open(trigger_id=trigger_id, view=_build_ejemplo_ca_view(_idi))
    except Exception:
        logger.exception("Error abriendo modal de ejemplo CA")


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
    # Producción: 4 semanas desde la fecha configurada en Notion
    while True:
        cal = obtener_config_calendario()
        fecha = cal.get("proyecto_ca")
        if not fecha:
            logging.info("[CA] Sin 'Proyecto y CA' en Calendario evaluaciones de Notion. Reintentando en 1h.")
            time.sleep(3600)
            continue
        siguiente = siguiente_envio_calendario(fecha, 4)
        espera = max(60, (siguiente - datetime.now(timezone.utc)).total_seconds())
        logging.info(f"[CA] Próximo envío: {siguiente.isoformat()} (en {espera/3600:.1f}h)")
        time.sleep(espera)
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


@slack_app.action(re.compile(r"^mod_ca_\d+$"))
def _handle_mod_ca_opcion(ack, body, logger):
    """Botón del menú '¿Qué respuesta quieres modificar?' (CA): reinyecta el número."""
    ack()
    try:
        val = ""
        for a in body.get("actions", []):
            val = a.get("value", "") or val
        evento = {
            "user": body["user"]["id"],
            "channel": body["channel"]["id"],
            "thread_ts": body["message"].get("thread_ts") or body["message"]["ts"],
            "text": val,
        }
        manejar_mensaje_ca(evento, logger)
    except Exception:
        logger.exception("Error procesando opción de modificación CA")


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
