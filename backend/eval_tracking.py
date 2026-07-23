"""
Registro de cumplimiento de evaluaciones: cuántas evaluaciones se le han ASIGNADO a cada
persona (como evaluador) y cuántas ha REALIZADO, por ciclo de 4 semanas y por tipo.

Cada fila = "a la persona P se le asignó una evaluación de tipo T en el ciclo C".
Granularidad: mensual/personal/ca = 1 asignación por ciclo; proyecto = 1 por activación;
extra = 1 por solicitud. `Completada` se marca al finalizar el flujo correspondiente.

Estructura en Notion:
  TO-SEE/
    Evaluaciones recibidas y completadas   (BD plana de asignaciones)

Todas las operaciones son best-effort: envueltas en try/except para no romper los flujos de
Slack ni las peticiones web si Notion falla.
"""

import logging
import threading
from datetime import datetime, timedelta, timezone

from . import config
from .clients import notion
from .notion_service import (
    _buscar_bbdd_en_pagina_id,
    _parent_bbdd_en_pagina,
    _query_bbdd,
    _usa_data_sources,
    obtener_config_calendario,
    obtener_frecuencias_evaluaciones,
    obtener_nombre_por_id_usuario,
    siguiente_envio_calendario,
)
from .project_evals import _crear_bbdd, _crear_pagina_en_bbdd
from .utils import normalizar_nombre

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

_NOMBRE_BBDD = "Evaluaciones recibidas y completadas"

# Tipos válidos (deben coincidir con las opciones del select y con el frontend)
TIPOS = ["mensual", "personal", "ca", "proyecto", "extra"]

_PROPS = {
    "Persona": {"title": {}},
    "Tipo": {"select": {"options": [{"name": tp} for tp in TIPOS]}},
    "Fecha_envio": {"date": {}},
    "Ciclo": {"rich_text": {}},
    "Detalle": {"rich_text": {}},
    "Completada": {"checkbox": {}},
    "Fecha_completada": {"date": {}},
    "Fecha_recordatorio": {"date": {}},  # último recordatorio enviado (evals web pendientes)
}

_SEMANAS_CICLO = 4

# Las evaluaciones que se piden por Slack son opcionales; las de cerrar proyecto son
# obligatorias. No hay columna en Notion que lo diga: la distinción es exactamente
# esta división por tipo, y de ella salen las dos categorías del panel de cumplimiento.
_SLACK_TIPOS = ("mensual", "personal", "ca")

# Orden de presentación dentro de cada categoría. Un tipo que no esté aquí no se pierde:
# se pinta detrás, ordenado alfabéticamente.
_ORDEN_TIPOS = ("personal", "mensual", "ca", "proyecto", "extra")

# Primero las opcionales de Slack y luego las obligatorias de proyecto.
_ORDEN_CATEGORIAS = ("slack", "proyecto")


def _categoria_de_tipo(tipo: str) -> str:
    return "slack" if tipo in _SLACK_TIPOS else "proyecto"


# ---------------------------------------------------------------------------
# Caché de la BD
# ---------------------------------------------------------------------------

_lock_bbdd = threading.Lock()
_cache_bbdd_id: dict = {"db_id": None}


def _obtener_o_crear_bbdd() -> str | None:
    # El lock se mantiene durante TODA la búsqueda/creación para que dos flujos
    # concurrentes (mensual, personal, CA...) no creen bases de datos duplicadas.
    with _lock_bbdd:
        if _cache_bbdd_id["db_id"]:
            return _cache_bbdd_id["db_id"]

        parent = _parent_bbdd_en_pagina(config.NOTION_TOSEE_PAGE_NAME, crear=True)
        if parent.get("type") != "page_id":
            return None
        parent_id = parent["page_id"]

        db_id = _buscar_bbdd_en_pagina_id(parent_id, _NOMBRE_BBDD)
        if not db_id:
            try:
                db_id = _crear_bbdd(parent_id, _NOMBRE_BBDD, _PROPS)
                logging.info("BD '%s' creada en Notion", _NOMBRE_BBDD)
            except Exception:
                logging.exception("Error creando BD '%s'", _NOMBRE_BBDD)
                return None

        _asegurar_prop_recordatorio(db_id)
        _cache_bbdd_id["db_id"] = db_id
        return db_id


def _asegurar_prop_recordatorio(db_id: str) -> None:
    """Añade la columna 'Fecha_recordatorio' si la BD ya existía sin ella (BDs antiguas)."""
    try:
        if _usa_data_sources():
            bd = notion.data_sources.retrieve(data_source_id=db_id)
            if "Fecha_recordatorio" not in bd.get("properties", {}):
                notion.data_sources.update(data_source_id=db_id, properties={"Fecha_recordatorio": {"date": {}}})
        else:
            bd = notion.databases.retrieve(database_id=db_id)
            if "Fecha_recordatorio" not in bd.get("properties", {}):
                notion.databases.update(database_id=db_id, properties={"Fecha_recordatorio": {"date": {}}})
    except Exception:
        logging.exception("Error asegurando la propiedad 'Fecha_recordatorio' en la BD de tracking")


# ---------------------------------------------------------------------------
# Cálculo de ciclo (4 semanas)
# ---------------------------------------------------------------------------

def clave_ciclo_actual() -> str:
    """Devuelve la clave 'YYYY-MM-DD' del inicio del ciclo de 4 semanas en curso.

    Usa la fecha base del calendario ('proyecto_ca'); si no hay calendario, cae a una
    ventana de 4 semanas anclada a la fecha actual.
    """
    try:
        cal = obtener_config_calendario()
        base = cal.get("proyecto_ca")
        if base:
            siguiente = siguiente_envio_calendario(base, _SEMANAS_CICLO)
            inicio = siguiente - timedelta(weeks=_SEMANAS_CICLO)
            return inicio.date().isoformat()
    except Exception:
        logging.exception("Error calculando el ciclo actual; usando fallback")
    # Fallback: inicio de la ventana de 4 semanas terminada en hoy
    return (datetime.now(timezone.utc) - timedelta(weeks=_SEMANAS_CICLO)).date().isoformat()


# ---------------------------------------------------------------------------
# Caducidad de una asignación
# ---------------------------------------------------------------------------
#
# Una asignación caduca cuando llegaría la siguiente evaluación de su tipo, que es lo que
# en Slack edita el DM anterior a "caducado". Se calcula con la frecuencia del tipo, NO con
# el ciclo de 4 semanas: cada tipo tiene la suya y es editable en Notion, así que una
# asignación puede seguir viva en el ciclo siguiente (mensual = 30 días) o caducar dentro
# del suyo (personal = 14). La caducidad no se persiste: la fila se queda con
# Completada=False, que para el cumplimiento es la verdad (asignada y no realizada).

def deadline_asignacion(fecha_envio: str, tipo: str, frecuencias: dict | None = None) -> str:
    """Deadline (YYYY-MM-DD) = fecha de envío + frecuencia (días) del tipo.

    Devuelve "" si no se puede calcular (sin fecha de envío o sin frecuencia del tipo).
    """
    if frecuencias is None:
        frecuencias = _frecuencias()
    dias = frecuencias.get(tipo)
    if not fecha_envio or not dias:
        return ""
    try:
        return (datetime.fromisoformat(fecha_envio[:10]) + timedelta(days=int(dias))).date().isoformat()
    except Exception:
        logging.exception("No se pudo calcular el deadline de la asignación (%s, %s)", fecha_envio, tipo)
        return ""


def _caducada(deadline: str) -> bool:
    """True si el deadline ya pasó. Sin deadline calculable no caduca: ante la duda es
    mejor mostrar una tarea de más que ocultar una real."""
    return bool(deadline) and deadline < datetime.now(timezone.utc).date().isoformat()


def _frecuencias() -> dict:
    try:
        return obtener_frecuencias_evaluaciones()
    except Exception:
        logging.exception("No se pudieron leer las frecuencias; ninguna asignación caducará")
        return {}


# ---------------------------------------------------------------------------
# Utilidades de lectura de propiedades
# ---------------------------------------------------------------------------

def _titulo(props: dict, prop: str) -> str:
    return "".join(p.get("plain_text", "") for p in (props.get(prop) or {}).get("title", [])).strip()


def _rich(props: dict, prop: str) -> str:
    return "".join(p.get("plain_text", "") for p in (props.get(prop) or {}).get("rich_text", [])).strip()


def _select(props: dict, prop: str) -> str:
    sel = (props.get(prop) or {}).get("select") or {}
    return (sel.get("name") or "").strip()


def _checkbox(props: dict, prop: str) -> bool:
    return bool((props.get(prop) or {}).get("checkbox"))


def _dia(props: dict, prop: str) -> str:
    """Devuelve el día 'YYYY-MM-DD' de una propiedad date, o ""."""
    return (((props.get(prop) or {}).get("date") or {}).get("start") or "")[:10]


def _fecha(props: dict, prop: str):
    """Devuelve un datetime tz-aware de una propiedad date, o None."""
    val = ((props.get(prop) or {}).get("date") or {}).get("start")
    if not val:
        return None
    try:
        d = datetime.fromisoformat(val.replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _iter_filas(db_id: str, filter=None):
    cursor = None
    while True:
        kwargs: dict = {"page_size": 100}
        if filter is not None:
            kwargs["filter"] = filter
        if cursor:
            kwargs["start_cursor"] = cursor
        resp = _query_bbdd(db_id, **kwargs)
        for fila in resp.get("results", []):
            yield fila
        if not resp.get("has_more"):
            break
        cursor = resp.get("next_cursor")


# ---------------------------------------------------------------------------
# Escritura: registrar envío / marcar completada
# ---------------------------------------------------------------------------

def registrar_envio(persona: str, tipo: str, detalle: str = "") -> None:
    """Registra que a `persona` se le asignó una evaluación de tipo `tipo` en el ciclo actual.

    Idempotencia suave: si ya existe una fila pendiente (persona, tipo, ciclo) no duplica.
    """
    if not persona or tipo not in TIPOS:
        return
    db_id = _obtener_o_crear_bbdd()
    if not db_id:
        return
    ciclo = clave_ciclo_actual()
    try:
        if _buscar_fila(db_id, persona, tipo, ciclo, completada=False):
            return  # ya hay una asignación pendiente para este ciclo
        _crear_pagina_en_bbdd(db_id, {
            "Persona": {"title": [{"type": "text", "text": {"content": persona}}]},
            "Tipo": {"select": {"name": tipo}},
            "Fecha_envio": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
            "Ciclo": {"rich_text": [{"type": "text", "text": {"content": ciclo}}]},
            "Detalle": {"rich_text": [{"type": "text", "text": {"content": (detalle or "")[:2000]}}]},
            "Completada": {"checkbox": False},
        })
    except Exception:
        logging.exception("Error registrando envío de evaluación '%s' a '%s'", tipo, persona)


def registrar_envio_por_slack_id(user_id: str, tipo: str, detalle: str = "") -> None:
    """Como registrar_envio pero resolviendo el nombre a partir del Slack user_id."""
    try:
        nombre = obtener_nombre_por_id_usuario(user_id)
    except Exception:
        nombre = None
    if nombre:
        registrar_envio(nombre, tipo, detalle)


def marcar_completada(persona: str, tipo: str) -> None:
    """Marca como completada la asignación pendiente más reciente de (persona, tipo).

    Si no hay ninguna pendiente y tampoco consta ya una completada de este ciclo (envío no
    registrado), crea una fila ya completada para no perder la 'realizada'.
    """
    if not persona or tipo not in TIPOS:
        return
    db_id = _obtener_o_crear_bbdd()
    if not db_id:
        return
    ciclo = clave_ciclo_actual()
    ahora = datetime.now(timezone.utc).isoformat()
    try:
        fila = _buscar_pendiente_reciente(db_id, persona, tipo)
        if fila:
            notion.pages.update(page_id=fila["id"], properties={
                "Completada": {"checkbox": True},
                "Fecha_completada": {"date": {"start": ahora}},
            })
            return
        # No hay pendiente: puede que ya se marcara antes en este ciclo. No duplicar.
        if _buscar_fila(db_id, persona, tipo, ciclo, completada=True):
            return
        # No había envío registrado en absoluto: auto-cura creando una fila completada.
        _crear_pagina_en_bbdd(db_id, {
            "Persona": {"title": [{"type": "text", "text": {"content": persona}}]},
            "Tipo": {"select": {"name": tipo}},
            "Fecha_envio": {"date": {"start": ahora}},
            "Ciclo": {"rich_text": [{"type": "text", "text": {"content": ciclo}}]},
            "Completada": {"checkbox": True},
            "Fecha_completada": {"date": {"start": ahora}},
        })
    except Exception:
        logging.exception("Error marcando completada la evaluación '%s' de '%s'", tipo, persona)


def marcar_completada_por_slack_id(user_id: str, tipo: str) -> None:
    """Como marcar_completada pero resolviendo el nombre a partir del Slack user_id."""
    try:
        nombre = obtener_nombre_por_id_usuario(user_id)
    except Exception:
        nombre = None
    if nombre:
        marcar_completada(nombre, tipo)


# ---------------------------------------------------------------------------
# Recordatorios de evaluaciones web pendientes (proyecto / extra)
# ---------------------------------------------------------------------------

def pendientes_para_recordatorio(tipos: tuple, umbral_dias: int) -> list:
    """Filas pendientes (Completada=False) de los `tipos` dados cuyo envío es anterior a
    `umbral_dias` y cuyo último recordatorio (si lo hubo) también lo es.

    Devuelve [{page_id, persona, tipo, detalle}] listo para notificar por Slack.
    """
    db_id = _obtener_o_crear_bbdd()
    if not db_id:
        return []
    limite = datetime.now(timezone.utc) - timedelta(days=umbral_dias)
    pendientes = []
    try:
        for fila in _iter_filas(db_id, filter={"property": "Completada", "checkbox": {"equals": False}}):
            props = fila.get("properties", {})
            tipo = _select(props, "Tipo")
            if tipo not in tipos:
                continue
            fecha_envio = _fecha(props, "Fecha_envio")
            if not fecha_envio or fecha_envio > limite:
                continue  # aún no han pasado `umbral_dias` desde el envío
            fecha_rec = _fecha(props, "Fecha_recordatorio")
            if fecha_rec and fecha_rec > limite:
                continue  # ya se recordó hace menos de `umbral_dias`
            persona = _titulo(props, "Persona")
            if not persona:
                continue
            pendientes.append({
                "page_id": fila.get("id", ""),
                "persona": persona,
                "tipo": tipo,
                "detalle": _rich(props, "Detalle"),
            })
    except Exception:
        logging.exception("Error obteniendo pendientes para recordatorio")
    return pendientes


def marcar_recordatorio_enviado(page_id: str) -> None:
    """Sella la fecha del último recordatorio enviado en la fila dada."""
    if not page_id:
        return
    try:
        notion.pages.update(page_id=page_id, properties={
            "Fecha_recordatorio": {"date": {"start": datetime.now(timezone.utc).isoformat()}},
        })
    except Exception:
        logging.exception("Error marcando recordatorio enviado en fila '%s'", page_id)


def _buscar_fila(db_id: str, persona: str, tipo: str, ciclo: str, completada: bool | None = None):
    """Devuelve la primera fila que coincide con (persona, tipo, ciclo). None si no hay.

    `completada` acota por estado: False = solo pendientes, True = solo completadas,
    None = cualquiera.
    """
    objetivo = normalizar_nombre(persona)
    and_filters = [
        {"property": "Tipo", "select": {"equals": tipo}},
        {"property": "Ciclo", "rich_text": {"equals": ciclo}},
    ]
    if completada is not None:
        and_filters.append({"property": "Completada", "checkbox": {"equals": completada}})
    for fila in _iter_filas(db_id, filter={"and": and_filters}):
        if normalizar_nombre(_titulo(fila.get("properties", {}), "Persona")) == objetivo:
            return fila
    return None


def _buscar_pendiente_reciente(db_id: str, persona: str, tipo: str):
    """Fila pendiente de (persona, tipo) con el envío más reciente, sea del ciclo que sea.

    El ciclo se sella al enviar, pero la frecuencia del tipo no tiene por qué coincidir con
    la ventana de 4 semanas: una asignación de 30 días enviada en el ciclo C sigue viva en
    C+1, y es la que la persona acaba de contestar. Buscar solo en el ciclo actual no la
    encontraba, creaba una fila nueva ya completada y dejaba la vieja pendiente para
    siempre (fantasma en las tareas de la web).
    """
    objetivo = normalizar_nombre(persona)
    mejor, mejor_envio = None, ""
    for fila in _iter_filas(db_id, filter={"and": [
        {"property": "Tipo", "select": {"equals": tipo}},
        {"property": "Completada", "checkbox": {"equals": False}},
    ]}):
        props = fila.get("properties", {})
        if normalizar_nombre(_titulo(props, "Persona")) != objetivo:
            continue
        envio = _dia(props, "Fecha_envio")
        if mejor is None or envio > mejor_envio:
            mejor, mejor_envio = fila, envio
    return mejor


# ---------------------------------------------------------------------------
# Lectura para el frontend
# ---------------------------------------------------------------------------

def resumen_ciclo_actual() -> dict:
    """Devuelve { nombre: {"enviadas": n, "realizadas": m} } para el ciclo en curso."""
    db_id = _obtener_o_crear_bbdd()
    if not db_id:
        return {}
    ciclo = clave_ciclo_actual()
    resumen: dict = {}
    try:
        for fila in _iter_filas(db_id, filter={"property": "Ciclo", "rich_text": {"equals": ciclo}}):
            props = fila.get("properties", {})
            persona = _titulo(props, "Persona")
            if not persona:
                continue
            entrada = resumen.setdefault(persona, {"enviadas": 0, "realizadas": 0})
            entrada["enviadas"] += 1
            if _checkbox(props, "Completada"):
                entrada["realizadas"] += 1
    except Exception:
        logging.exception("Error calculando el resumen de cumplimiento del ciclo")
    return resumen


def _orden_tipo(tipo: str):
    """Los tipos conocidos van en el orden de _ORDEN_TIPOS; el resto detrás, alfabéticos."""
    return (_ORDEN_TIPOS.index(tipo), "") if tipo in _ORDEN_TIPOS else (len(_ORDEN_TIPOS), tipo)


def detalle_por_persona(nombre: str) -> list:
    """Devuelve el desglose por año > mes > categoría > tipo para una persona.

    [{"anio": 2026, "meses": [
        {"mes": 1, "categorias": [
            {"categoria": "slack", "tipos": [
                {"tipo": "personal", "enviadas": 2, "realizadas": 1}, ...]}, ...]}, ...]}, ...]
    Ordenado de más reciente a más antiguo (años y meses descendentes).

    Se agrupa por `Fecha_envio`, no por `Ciclo`: un ciclo es una ventana de 4 semanas
    anclada a una fecha de Notion (ver clave_ciclo_actual), así que cruza meses y no
    sirve para agrupar por mes natural. `Ciclo` queda como reserva por si alguna fila
    antigua no tuviera fecha de envío.
    """
    db_id = _obtener_o_crear_bbdd()
    if not db_id or not nombre:
        return []
    objetivo = normalizar_nombre(nombre)
    # {anio: {mes: {categoria: {tipo: {"enviadas": n, "realizadas": n}}}}}
    arbol: dict = {}
    try:
        for fila in _iter_filas(db_id):
            props = fila.get("properties", {})
            if normalizar_nombre(_titulo(props, "Persona")) != objetivo:
                continue
            dia = _dia(props, "Fecha_envio") or _rich(props, "Ciclo")
            try:
                anio, mes = int(dia[:4]), int(dia[5:7])
            except ValueError:
                # registrar_envio siempre escribe ambas, así que esto no debería pasar.
                logging.warning("Fila de cumplimiento de '%s' sin fecha usable (%r); se omite", nombre, dia)
                continue
            tipo = _select(props, "Tipo") or "otro"
            entrada = (
                arbol.setdefault(anio, {}).setdefault(mes, {})
                .setdefault(_categoria_de_tipo(tipo), {})
                .setdefault(tipo, {"enviadas": 0, "realizadas": 0})
            )
            entrada["enviadas"] += 1
            if _checkbox(props, "Completada"):
                entrada["realizadas"] += 1
    except Exception:
        logging.exception("Error leyendo el detalle de cumplimiento de '%s'", nombre)
    return [
        {"anio": anio, "meses": [
            {"mes": mes, "categorias": [
                {"categoria": cat, "tipos": [
                    {"tipo": tp, **contadores}
                    for tp, contadores in sorted(tipos.items(), key=lambda kv: _orden_tipo(kv[0]))
                ]}
                for cat, tipos in sorted(cats.items(), key=lambda kv: _ORDEN_CATEGORIAS.index(kv[0]))
            ]}
            for mes, cats in sorted(meses.items(), reverse=True)
        ]}
        for anio, meses in sorted(arbol.items(), reverse=True)
    ]


def pendientes_slack_de_persona(persona: str) -> list:
    """Tareas de Slack (mensual/personal/ca) vivas de `persona`: filas con Completada=False
    cuyo deadline no ha pasado. Devuelve [{tipo, deadline}] en orden estable.

    Las caducadas se excluyen aquí, no en Notion: en Slack el DM ya se editó a "caducado" y
    la persona no puede contestarlas, así que no son tareas; pero la fila sigue con
    Completada=False porque para el cumplimiento cuenta como asignada y no realizada.

    Igualmente, una pendiente vieja no es tarea si hay un envío POSTERIOR del mismo tipo ya
    completado: la persona ya hizo la evaluación vigente, y la fila vieja queda solo como
    registro de un ciclo no realizado. Sin esto, la caja de tareas de la web mostraba
    "fantasmas" tras completar por Slack (`marcar_completada` cierra solo la más reciente).
    """
    db_id = _obtener_o_crear_bbdd()
    if not db_id or not persona:
        return []
    objetivo = normalizar_nombre(persona)
    por_tipo: dict = {}  # tipo -> (momento envío, día 'YYYY-MM-DD') del pendiente más reciente
    _sin_fecha = datetime.min.replace(tzinfo=timezone.utc)
    try:
        for fila in _iter_filas(db_id, filter={"property": "Completada", "checkbox": {"equals": False}}):
            props = fila.get("properties", {})
            # El checkbox se re-comprueba en Python: el filtro de Notion es solo eficiencia.
            if _checkbox(props, "Completada"):
                continue
            if normalizar_nombre(_titulo(props, "Persona")) != objetivo:
                continue
            tipo = _select(props, "Tipo")
            if tipo not in _SLACK_TIPOS:
                continue
            momento = _fecha(props, "Fecha_envio") or _sin_fecha
            if tipo not in por_tipo or momento > por_tipo[tipo][0]:
                por_tipo[tipo] = (momento, _dia(props, "Fecha_envio"))
    except Exception:
        logging.exception("Error leyendo pendientes de Slack de '%s'", persona)

    # Descarta tipos cuyo envío completado más reciente es igual o posterior al pendiente:
    # la evaluación vigente ya está hecha y la fila pendiente es de un ciclo anterior.
    if por_tipo:
        try:
            condiciones = [{"property": "Completada", "checkbox": {"equals": True}}]
            momentos_reales = [m for m, _ in por_tipo.values() if m != _sin_fecha]
            if momentos_reales:
                # Acota la consulta: solo interesan completadas desde el pendiente más antiguo.
                condiciones.append({"property": "Fecha_envio", "date": {"on_or_after": min(momentos_reales).date().isoformat()}})
            for fila in _iter_filas(db_id, filter={"and": condiciones}):
                props = fila.get("properties", {})
                if not _checkbox(props, "Completada"):
                    continue
                if normalizar_nombre(_titulo(props, "Persona")) != objetivo:
                    continue
                tipo = _select(props, "Tipo")
                if tipo not in por_tipo:
                    continue
                momento = _fecha(props, "Fecha_envio")
                if momento and momento >= por_tipo[tipo][0]:
                    por_tipo.pop(tipo)
        except Exception:
            logging.exception("Error comprobando completadas recientes de '%s'", persona)

    frecuencias = _frecuencias()
    vivas = []
    for tp in _SLACK_TIPOS:
        if tp not in por_tipo:
            continue
        # Basta con mirar el envío más reciente: si ese caducó, los anteriores también.
        deadline = deadline_asignacion(por_tipo[tp][1], tp, frecuencias)
        if not _caducada(deadline):
            vivas.append({"tipo": tp, "deadline": deadline})
    return vivas
