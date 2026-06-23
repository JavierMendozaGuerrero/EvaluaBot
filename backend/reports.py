import hashlib
import html
import json
import logging
import os
import re
from datetime import datetime, timezone

from . import config
from .clients import Document, anthropic_client
from .notion_service import obtener_comentarios_personales, obtener_evaluaciones_por_evaluado
from .utils import slug_archivo


_NIVEL_LABEL = {"superior": "Superior", "igual": "Igual nivel", "inferior": "Subordinado"}


def _evaluaciones_para_prompt(evaluaciones):
    lineas = []
    for e in evaluaciones:
        nivel = _NIVEL_LABEL.get(e.get("relacion", ""), "")
        nivel_str = f" | Nivel evaluador: {nivel}" if nivel else ""
        lineas.append(
            f"- Evaluado: {e['evaluado']} | "
            f"Evaluador: {e.get('persona_que_evalua') or e.get('nombre') or 'Desconocido'}{nivel_str} | "
            f"Proyecto: {e.get('proyecto') or 'Sin proyecto'} | "
            f"Satisfacción: {e['satisfaccion']} | Mejor aspecto: {e['mejor_aspecto']} | "
            f"Peor aspecto: {e['peor_aspecto']} | Fecha: {e['fecha']}"
        )
    return "\n".join(lineas)


def _comentarios_para_prompt(comentarios, nombre):
    if not comentarios:
        return ""
    lineas = []
    for c in comentarios:
        lineas.append(f"- Autor: {c['autor']} | Fecha: {c['fecha']} | Comentario: {c['comentario']}")
    return "\n".join(lineas)


def generar_informe_claude(evaluaciones, comentarios_personales=None):
    if not anthropic_client:
        raise RuntimeError("Falta ANTHROPIC_API_KEY o no está instalado el paquete anthropic.")
    if not evaluaciones:
        raise RuntimeError("No hay evaluaciones en Notion para generar el informe.")

    seccion_personal = ""
    if comentarios_personales:
        nombre = evaluaciones[0].get("evaluado", "") if evaluaciones else ""
        bloque = _comentarios_para_prompt(comentarios_personales, nombre)
        seccion_personal = (
            f"\n\nCOMENTARIOS DE EVALUACIONES PERSONALES (reflexiones propias y menciones de compañeros):\n{bloque}"
        )

    prompt = (
        "Eres un consultor senior de People Analytics. Genera un informe profesional en español "
        "sobre las evaluaciones recibidas. Usa este formato exacto con títulos claros:\n"
        "1. Resumen ejecutivo\n2. Métricas principales\n3. Fortalezas detectadas\n"
        "4. Riesgos o áreas de mejora\n5. Recomendaciones accionables\n6. Conclusión\n\n"
        "Sé concreto, no inventes datos y menciona patrones repetidos si los hay. "
        "Si hay comentarios de evaluaciones personales, intégralos en el análisis.\n\n"
        f"EVALUACIONES:\n{_evaluaciones_para_prompt(evaluaciones)}"
        f"{seccion_personal}"
    )
    respuesta = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2200,
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(bloque.text for bloque in respuesta.content if bloque.type == "text").strip()


def _ruta_cache_informe(slug):
    return os.path.join(config.CARPETA_WEB, f"informe_{slug}_cache.json")


def _huella_evaluaciones(evaluaciones):
    normalizado = json.dumps(evaluaciones, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(normalizado.encode("utf-8")).hexdigest()


def cargar_cache_informe(slug):
    ruta = _ruta_cache_informe(slug)
    if not os.path.exists(ruta):
        return None
    with open(ruta, "r", encoding="utf-8") as f:
        return json.load(f)


def guardar_cache_informe(slug, huella, total):
    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    with open(_ruta_cache_informe(slug), "w", encoding="utf-8") as f:
        json.dump({"huella": huella, "total": total, "generado": datetime.now(timezone.utc).isoformat()}, f, ensure_ascii=False, indent=2)


def guardar_informe_html(informe, evaluaciones, evaluado):
    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cuerpo = "<br>".join(html.escape(linea) for linea in informe.splitlines())
    slug = slug_archivo(evaluado)
    ruta = os.path.join(config.CARPETA_WEB, f"informe_{slug}.html")
    app_url = config.APP_PUBLIC_URL
    contenido = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><title>Informe de evaluaciones</title>
<style>{config.IGENERIS_CSS}
.shell {{ max-width: 1120px; margin: 0 auto; }}
.top {{ padding-top: clamp(42px, 8vw, 92px); display: grid; grid-template-columns: 1fr auto; gap: 28px; align-items: start; }}
.summary {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 0; border-top: 1px solid var(--ink); border-bottom: 1px solid var(--line); margin: 42px 0; }}
.metric {{ padding: 22px; border-right: 1px solid var(--line); }}
.metric:last-child {{ border-right: 0; }}
.metric span {{ display: block; font-size: 12px; text-transform: uppercase; letter-spacing: .08em; color: var(--muted); margin-bottom: 8px; }}
.metric strong {{ font-size: clamp(24px, 4vw, 44px); letter-spacing: -0.04em; }}
.informe {{ max-width: 820px; font-size: 18px; border-top: 1px solid var(--ink); padding-top: 28px; }}
</style></head><body><main class="page shell">
<nav class="nav"><a class="brand" href="javascript:void(0)" onclick="window.close()">igeneris</a><div class="nav-links"><button class="secondary" onclick="window.close()">Cerrar</button></div></nav>
<div class="top"><div><p class="kicker">Informe de evaluaciones</p><h1>Informe de evaluaciones</h1><p>Generado el {fecha}</p></div><div class="actions"><button class="secondary" onclick="window.close()">Cerrar</button></div></div>
<section class="summary"><div class="metric"><span>Evaluado</span><strong>{html.escape(evaluado)}</strong></div><div class="metric"><span>Evaluaciones</span><strong>{len(evaluaciones)}</strong></div><div class="metric"><span>Fuente</span><strong>Notion</strong></div></section>
<article class="informe">{cuerpo}</article></main></body></html>"""
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(contenido)
    return ruta


def guardar_informe_word(informe, evaluaciones, evaluado):
    if Document is None:
        raise RuntimeError("Falta python-docx. Instálalo con: pip install python-docx")
    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    ruta = os.path.join(config.CARPETA_WEB, f"informe_{slug_archivo(evaluado)}.docx")
    documento = Document()
    documento.add_heading("Informe de evaluaciones", level=1)
    documento.add_paragraph(f"Generado el {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}. Evaluado: {evaluado}. Evaluaciones analizadas: {len(evaluaciones)}.")
    for bloque in informe.split("\n\n"):
        lineas = [linea.strip() for linea in bloque.splitlines() if linea.strip()]
        if not lineas:
            continue
        if re.match(r"^\d+\.\s+", lineas[0]):
            documento.add_heading(lineas[0], level=2)
            for linea in lineas[1:]:
                documento.add_paragraph(linea)
        else:
            documento.add_paragraph(" ".join(lineas))
    documento.save(ruta)
    return ruta


def generar_archivos_informe(evaluado=""):
    if not evaluado:
        raise RuntimeError("Selecciona una persona evaluada.")
    evaluaciones = obtener_evaluaciones_por_evaluado(evaluado)
    comentarios = obtener_comentarios_personales(evaluado)
    nombre = evaluado
    slug = slug_archivo(nombre)
    huella = _huella_evaluaciones(evaluaciones + comentarios)
    cache = cargar_cache_informe(slug)
    html_path = os.path.join(config.CARPETA_WEB, f"informe_{slug}.html")
    docx_path = os.path.join(config.CARPETA_WEB, f"informe_{slug}.docx")
    if cache and cache.get("huella") == huella and os.path.exists(html_path) and os.path.exists(docx_path):
        logging.info(f"Informe reutilizado desde caché para {nombre}; no se llama a Claude.")
        return len(evaluaciones), slug, True
    informe = generar_informe_claude(evaluaciones, comentarios_personales=comentarios)
    guardar_informe_html(informe, evaluaciones, nombre)
    guardar_informe_word(informe, evaluaciones, nombre)
    guardar_cache_informe(slug, huella, len(evaluaciones))
    return len(evaluaciones), slug, False


def guardar_trayectoria_react(evaluaciones, evaluado):
    if not evaluaciones:
        raise RuntimeError("No hay evaluaciones en Notion para generar la trayectoria.")
    os.makedirs(config.CARPETA_WEB, exist_ok=True)
    slug = slug_archivo(evaluado)
    app_url = config.APP_PUBLIC_URL
    ruta = os.path.join(config.CARPETA_WEB, f"trayectoria_{slug}.html")
    datos_json = (
        json.dumps(evaluaciones, ensure_ascii=False)
        .replace("&", "\\u0026")
        .replace("<", "\\u003c")
        .replace(">", "\\u003e")
    )
    contenido = f"""<!DOCTYPE html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Trayectoria</title>
<style>{config.IGENERIS_CSS}
.stage{{padding-top:40px}}
.slide{{display:grid;grid-template-columns:.85fr 1.15fr;gap:22px}}
.side,.main-card{{border-top:1px solid var(--ink);padding:24px}}
.side{{background:var(--soft)}}
.main-card{{background:var(--ink);color:white}}
.main-card p{{color:rgba(255,255,255,.78)}}
.score{{font-size:clamp(100px,20vw,220px);font-weight:1000;line-height:.8;letter-spacing:-.06em}}
.quote-grid{{display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:28px}}
.quote{{border-top:1px solid rgba(255,255,255,.45);padding-top:14px}}
.quote h2{{color:white}}
.pill{{border:1px solid var(--line);background:white;padding:11px 12px;cursor:pointer;text-align:left;margin-bottom:8px;width:100%}}
.pill.activo{{background:var(--ink);color:white}}
.nav-mini{{display:grid;grid-template-columns:1fr auto 1fr;gap:12px;margin-top:20px;align-items:center}}
.nav-mini button{{width:100%}}
@media(max-width:820px){{.slide{{grid-template-columns:1fr}}.quote-grid{{grid-template-columns:1fr}}}}
</style>
</head><body><div id="root"></div><script>
const evaluaciones = {datos_json};
const agrupadas = new Map();
for (const ev of evaluaciones) {{
  const persona = ev.evaluado || "General";
  if (!agrupadas.has(persona)) agrupadas.set(persona, []);
  agrupadas.get(persona).push(ev);
}}
for (const lista of agrupadas.values()) {{
  lista.sort((a, b) => String(a.fecha || "").localeCompare(String(b.fecha || "")));
}}

const personas = Array.from(agrupadas.keys()).sort();
let persona = personas[0] || "";
let indice = 0;

function escapeHtml(value) {{
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({{
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#039;"
  }}[char]));
}}

function fecha(value) {{
  return value ? String(value).slice(0, 10) : "Sin fecha";
}}

function nivelLabel(relacion) {{
  return {{ "superior": "Superior", "igual": "Igual nivel", "inferior": "Subordinado" }}[relacion] || "";
}}

function render() {{
  const lista = agrupadas.get(persona) || [];
  const actual = lista[indice];
  const botones = personas.map((nombre) => `
    <button class="pill ${{nombre === persona ? "activo" : ""}}" data-persona="${{escapeHtml(nombre)}}">${{escapeHtml(nombre)}}</button>
  `).join("");

  document.getElementById("root").innerHTML = `
    <main class="page">
      <nav class="nav">
        <a class="brand" href="javascript:void(0)" onclick="window.close()">igeneris</a>
        <button class="secondary" onclick="window.close()">Cerrar</button>
      </nav>
      <section class="hero">
        <div>
          <p class="kicker">Trayectoria</p>
          <h1>Tu trayectoria de evaluación.</h1>
        </div>
        <div class="panel">
          <p>Navega por fecha, proyecto y satisfacción.</p>
        </div>
      </section>
      ${{actual ? `
        <section class="stage slide">
          <aside class="side">
            <div>${{botones}}</div>
            <div class="nav-mini">
              <button id="prev" ${{indice === 0 ? "disabled" : ""}}>Anterior</button>
              <strong>${{indice + 1}} / ${{lista.length}}</strong>
              <button id="next" ${{indice >= lista.length - 1 ? "disabled" : ""}}>Siguiente</button>
            </div>
          </aside>
          <article class="main-card">
            <p>${{fecha(actual.fecha)}}${{nivelLabel(actual.relacion) ? ` &nbsp;·&nbsp; <span style="font-size:13px;opacity:.7">${{nivelLabel(actual.relacion)}}</span>` : ""}}</p>
            <div class="score">${{escapeHtml(actual.satisfaccion || "-")}}/5</div>
            <p>Proyecto: ${{escapeHtml(actual.proyecto || "Sin proyecto")}}</p>
            <div class="quote-grid">
              <div class="quote">
                <h2>Lo mejor</h2>
                <p>${{escapeHtml(actual.mejor_aspecto || "Sin respuesta")}}</p>
              </div>
              <div class="quote">
                <h2>A mejorar</h2>
                <p>${{escapeHtml(actual.peor_aspecto || "Sin respuesta")}}</p>
              </div>
            </div>
          </article>
        </section>
      ` : `<section class="panel"><p>No hay evaluaciones todavía.</p></section>`}}
    </main>
  `;

  for (const button of document.querySelectorAll("[data-persona]")) {{
    button.addEventListener("click", () => {{
      persona = button.dataset.persona;
      indice = 0;
      render();
    }});
  }}
  document.getElementById("prev")?.addEventListener("click", () => {{
    indice = Math.max(0, indice - 1);
    render();
  }});
  document.getElementById("next")?.addEventListener("click", () => {{
    indice = Math.min(lista.length - 1, indice + 1);
    render();
  }});
}}

render();
</script></body></html>"""
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(contenido)
    return ruta


def generar_archivo_trayectoria(evaluado=""):
    if not evaluado:
        raise RuntimeError("Selecciona una persona evaluada.")
    evaluaciones = obtener_evaluaciones_por_evaluado(evaluado)
    nombre = evaluado
    guardar_trayectoria_react(evaluaciones, nombre)
    return len(evaluaciones), slug_archivo(nombre)
