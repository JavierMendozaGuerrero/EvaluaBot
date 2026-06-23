"""
Flujo de revisión para Career Advisors (CA).

El bot envía un DM a cada empleado con un mensaje de notificación.
El usuario responde en el hilo: sí → bot pide nombre del advisee → muestra todas
las evaluaciones desde la última revisión del CA → pide opinión → guarda en
Notion → pregunta si hay otro advisee.
"""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from . import config
from .clients import notion, slack_app
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
    buscar_empleado_en_lista,
    obtener_advisees,
    obtener_comentarios_personales,
    obtener_config_calendario,
    obtener_evaluaciones_por_evaluado,
    obtener_slack_ids_empleados,
    siguiente_envio_calendario,
    sugerir_empleados_parecidos,
)
from .utils import normalizar_nombre

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


# ---------------------------------------------------------------------------
# Resumen de evaluaciones
# ---------------------------------------------------------------------------

def _resumen_advisee(advisee: str, desde_fecha: str | None) -> str:
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
        lineas.append(
            f"• [{fecha}] *{ev.get('persona_que_evalua', '?')}* en {ev.get('proyecto', '?')} – "
            f"Satisfacción {ev.get('satisfaccion', '?')}/5 | "
            f"Mejor: {ev.get('mejor_aspecto', '?')} | "
            f"Peor: {ev.get('peor_aspecto', '?')}"
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
                lineas_personales.append(
                    f"• [{c['fecha']}] *{c['autor']}* → _{c['comentario']}_"
                )
            resumen += f"\n\n*Comentarios personales ({len(lineas_personales)}):*\n" + "\n".join(lineas_personales)
    except Exception:
        logging.exception("Error leyendo comentarios personales de '%s'", advisee)

    return resumen


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _es_si(texto: str) -> bool:
    return normalizar_nombre(texto) in {"si", "sí", "s", "yes", "y", "claro", "sip", "vale"}


def _es_no(texto: str) -> bool:
    return normalizar_nombre(texto) in {"no", "n", "nope", "nel"}


def _es_confirmar(texto: str) -> bool:
    return normalizar_nombre(texto) in {"si", "sí", "s", "ok", "okay", "confirmar", "guardar", "correcto"}


def _es_modificar(texto: str) -> bool:
    return normalizar_nombre(texto) in {"modificar", "cambiar", "editar", "repetir"}


_OPCIONES_MODIFICACION_CA = {
    "1": "advisee", "advisee": "advisee",
    "2": "opinion", "opinion": "opinion",
}


def _texto_menu_modificacion_ca() -> str:
    return (
        "¿Qué respuesta quieres modificar?\n"
        "1. Advisee\n2. Opinión\n\n"
        "Responde con el número o el nombre del campo."
    )


def _clave_modificacion_ca(texto: str) -> str | None:
    return _OPCIONES_MODIFICACION_CA.get(normalizar_nombre(texto))


def _texto_pregunta_ca_por_clave(clave: str) -> str:
    if clave == "advisee":
        return "¿Cuál es el nombre de tu advisee?"
    if clave == "opinion":
        return "¿Qué opinas de las evaluaciones?"
    return "Escribe la nueva respuesta."


def _mensaje_advisee_no_encontrado(nombre: str) -> str:
    sugerencias = sugerir_empleados_parecidos(nombre)
    if sugerencias:
        opciones = "\n".join(f"- {item}" for item in sugerencias)
        return (
            f"*{nombre}* no aparece tal cual en la lista de empleados.\n"
            "¿Querías decir alguno de estos nombres? Responde copiando el nombre exacto:\n"
            f"{opciones}"
        )
    return (
        f"*{nombre}* no aparece tal cual en la lista de empleados. "
        "Escribe nombre y apellido como aparece en la lista."
    )


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
                resp_dm = slack_app.client.conversations_open(users=[user_id])
                dm_channel = resp_dm["channel"]["id"]
                resp = slack_app.client.chat_postMessage(
                    channel=dm_channel,
                    text=(
                        "📋 *Evaluaciones CA* — Si eres Career Advisor de alguien, tienes una revisión pendiente.\n"
                        "Responde en el hilo de este mensaje para comenzar.\n"
                        "_Si quieres cancelar en cualquier momento, escribe SOS en el hilo._"
                    ),
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
            conversaciones_ca.pop(conv_key, None)
        reply("Evaluación CA cancelada. Si quieres volver a empezar, espera a la próxima evaluación.")
        return

    accion = None
    payload = {}

    with _lock:
        estado = conversaciones_ca.get(conv_key)
        if estado is None:
            estado = {"modo": "pre_inicial", "ca_nombre": None}
            conversaciones_ca[conv_key] = estado

        modo = estado["modo"]

        if modo == "pre_inicial":
            estado["modo"] = "inicial"
            accion = "hacer_primera_pregunta"

        elif modo == "inicial":
            if _es_si(texto):
                estado["modo"] = "esperando_advisee"
                accion = "pedir_advisee"
            elif _es_no(texto):
                estado["modo"] = "terminado"
                accion = "terminar_sin_ca"
            else:
                accion = "aclarar_inicial"

        elif modo == "esperando_advisee":
            payload["advisee"] = texto
            payload["ca_nombre"] = estado.get("ca_nombre")
            accion = "validar_y_mostrar"

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
            if _es_si(texto):
                estado["modo"] = "esperando_advisee"
                accion = "pedir_siguiente_advisee"
            elif _es_no(texto):
                estado["modo"] = "terminado"
                accion = "terminar"
            else:
                accion = "aclarar_otro"

        elif modo == "terminado":
            accion = "ya_terminado"

    if accion == "hacer_primera_pregunta":
        reply("¿Eres Career Advisor de alguien? (`sí` / `no`)")

    elif accion == "pedir_advisee":
        reply("¿Cuál es el nombre de tu advisee?")

    elif accion == "terminar_sin_ca":
        reply("¡Perfecto, gracias! 👋")

    elif accion == "aclarar_inicial":
        reply("Responde `sí` o `no`. ¿Eres Career Advisor de alguien?")

    elif accion == "validar_y_mostrar":
        advisee = payload["advisee"]
        advisee_encontrado = buscar_empleado_en_lista(advisee)
        if not advisee_encontrado:
            reply(_mensaje_advisee_no_encontrado(advisee))
        else:
            advisee = advisee_encontrado
            ca_nombre, ca_aliases = _identidad_usuario_slack(user_id, logger)
            permitido, permitidos = _advisee_permitido_para_ca(ca_nombre, ca_aliases, advisee)
            if not permitido:
                opciones = "\n".join(f"- {item}" for item in permitidos) if permitidos else "- No tienes advisees asociados en Lista CA."
                reply(
                    f"*{advisee}* no aparece asociado a ti como advisee en `Lista CA`.\n"
                    "Solo puedes hacer evaluaciones CA de las personas que sean tus advisees.\n"
                    f"Tus advisees actuales:\n{opciones}"
                )
                return
            with _lock:
                if conv_key in conversaciones_ca:
                    conversaciones_ca[conv_key]["advisee_actual"] = advisee
                    conversaciones_ca[conv_key]["ca_nombre"] = ca_nombre
            desde_fecha = _fecha_ultima_opinion(ca_nombre, advisee)
            hace_4_semanas = (datetime.now(timezone.utc) - timedelta(weeks=4)).isoformat()
            desde_fecha = max(desde_fecha, hace_4_semanas) if desde_fecha else hace_4_semanas
            resumen = _resumen_advisee(advisee, desde_fecha)
            sin_novedades = "no hay evaluaciones nuevas" in resumen or "No hay evaluaciones registradas" in resumen
            with _lock:
                if conv_key in conversaciones_ca:
                    conversaciones_ca[conv_key]["modo"] = "esperando_otro" if sin_novedades else "esperando_opinion"
                    conversaciones_ca[conv_key]["resumen_actual"] = "" if sin_novedades else resumen
            if sin_novedades:
                reply(f"{resumen}\n\n¿Tienes otro advisee? (`sí` / `no`)")
            else:
                reply(f"{resumen}\n\n*¿Qué opinas de esto?*")

    elif accion == "mostrar_confirmacion_ca":
        reply(
            f"*Resumen de tu valoración:*\n"
            f"• Advisee: *{payload.get('advisee', '?')}*\n"
            f"• Opinión: {payload.get('opinion', '?')}\n\n"
            "Responde `sí` para guardar en Notion o `modificar` para cambiar una respuesta concreta."
        )

    elif accion == "pedir_modificacion_ca":
        reply(_texto_menu_modificacion_ca())

    elif accion == "pedir_valor_modificacion_ca":
        campo = estado.get("campo_modificando")
        if payload.get("error_advisee_no_asociado"):
            permitidos = payload.get("advisees_permitidos") or []
            opciones = "\n".join(f"- {item}" for item in permitidos) if permitidos else "- No tienes advisees asociados en Lista CA."
            reply(
                f"*{payload['error_advisee_no_asociado']}* existe en la lista de empleados, "
                "pero no aparece asociado a ti en `Lista CA`.\n"
                f"Tus advisees actuales:\n{opciones}\n\n"
                "Escribe uno de esos nombres."
            )
        elif payload.get("error_advisee"):
            sugerencias = sugerir_empleados_parecidos(payload["error_advisee"])
            if sugerencias:
                opciones = "\n".join(f"- {n}" for n in sugerencias)
                reply(
                    f"*{payload['error_advisee']}* no está en la lista de empleados.\n"
                    f"¿Querías decir alguno de estos? Copia el nombre exacto:\n{opciones}"
                )
            else:
                reply(
                    f"*{payload['error_advisee']}* no está en la lista de empleados. "
                    "Escríbelo sin tildes, primera letra del nombre y primer apellido en mayúscula, solo primer apellido."
                )
        else:
            reply(_texto_pregunta_ca_por_clave(campo) if campo else _texto_menu_modificacion_ca())

    elif accion == "cancelar_opinion":
        reply("De acuerdo, no se guardará esta opinión.\n\n¿Tienes otro advisee? (`sí` / `no`)")

    elif accion == "guardar_y_preguntar_otro":
        ca_nombre, ca_aliases = _identidad_usuario_slack(user_id, logger)
        if payload.get("ca_nombre"):
            ca_aliases.append(payload["ca_nombre"])
        permitido, permitidos = _advisee_permitido_para_ca(ca_nombre, ca_aliases, payload["advisee"])
        if not permitido:
            opciones = "\n".join(f"- {item}" for item in permitidos) if permitidos else "- No tienes advisees asociados en Lista CA."
            reply(
                f"No puedo guardar esta opinión: *{payload['advisee']}* no aparece asociado a ti en `Lista CA`.\n"
                f"Tus advisees actuales:\n{opciones}"
            )
            return
        resumen = estado.get("resumen_actual", "")
        ok, error = _guardar_opinion(ca_nombre, payload["advisee"], payload["opinion"], resumen)
        if ok:
            reply("✅ Opinión guardada en Notion.\n\n¿Tienes otro advisee? (`sí` / `no`)")
        else:
            reply(f"⚠️ No se pudo guardar en Notion: `{error[:300]}`\n\n¿Tienes otro advisee? (`sí` / `no`)")

    elif accion == "pedir_siguiente_advisee":
        reply("¿Cuál es el nombre de tu próximo advisee?")

    elif accion == "terminar":
        reply("¡Perfecto, gracias por tu tiempo! 🎉")

    elif accion == "aclarar_otro":
        reply("Responde `sí` si tienes otro advisee, o `no` para terminar.")

    elif accion == "ya_terminado":
        reply("Esta evaluación ya ha concluido. 👋")


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
                uid for uid in ca_dm_activas
                if (
                    ahora - max(ca_hora_dm.get(uid, ahora), ca_ultimo_recordatorio_dm.get(uid, 0) or ca_hora_dm.get(uid, ahora)) >= _RECORDATORIO_CA_SEGUNDOS
                    and conversaciones_ca.get(uid, {}).get("modo") not in ("terminado",)
                )
            ]
        for uid in pendientes:
            try:
                dm_channel = ca_dm_canal.get(uid)
                if not dm_channel:
                    continue
                slack_app.client.chat_postMessage(
                    channel=dm_channel,
                    text="*📋 Recuerda realizar tu revisión de Career Advisor.* Abre el hilo del mensaje CA y responde.",
                )
                with _lock:
                    ca_ultimo_recordatorio_dm[uid] = time.time()
            except Exception:
                logging.exception(f"Error enviando recordatorio CA DM a {uid}")


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
