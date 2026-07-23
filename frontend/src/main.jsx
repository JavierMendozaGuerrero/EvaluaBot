import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles/globals.css";
import "./styles/components.css";
import "./styles.css";
import { t, tieneClave, setLang, setLangManual, getLang, subscribeLang, nombreMes } from "./i18n";

// El texto de cada documento legal se carga bajo demanda (import dinámico) al abrir
// la página, para no arrastrar el markdown en el bundle inicial.
const LEGAL_DOCS = {
  privacidad: { titulo: "Política de privacidad", load: () => import("./legal/privacidad.md?raw") },
  terminos: { titulo: "Términos y condiciones", load: () => import("./legal/terminos.md?raw") },
};

function getLegalDoc() {
  const hash = (window.location.hash || "").replace(/^#/, "").toLowerCase();
  return LEGAL_DOCS[hash] ? hash : null;
}

// Prioridad: variable de entorno explícita > (en dev) backend local en :8000 > mismo origen.
// En el build de producción (Docker/NAS) la web y la API se sirven juntas, así que las
// llamadas van relativas al mismo host y puerto (cadena vacía = mismo origen).
const API_BASE =
  import.meta.env.VITE_API_BASE_URL ??
  (import.meta.env.DEV ? `${window.location.protocol}//${window.location.hostname}:8000` : "");

function apiUrl(path) {
  return `${API_BASE}${path}`;
}

// ── Micro-interacciones ──
// Todo respeta prefers-reduced-motion: si el usuario lo pide, no hay animación.
function _prefiereMenosMovimiento() {
  try { return window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches; }
  catch { return false; }
}

// Cuenta un número de forma ascendente (easeOutCubic) desde el valor anterior al nuevo.
// Usa el timestamp de requestAnimationFrame, no relojes externos.
function useCountUp(target, duration = 700) {
  const objetivo = Math.round(target || 0);
  const [val, setVal] = useState(objetivo);
  const prev = React.useRef(objetivo);
  useEffect(() => {
    const desde = prev.current;
    prev.current = objetivo;
    if (desde === objetivo || _prefiereMenosMovimiento()) { setVal(objetivo); return; }
    let raf, inicio = null;
    const paso = (ts) => {
      if (inicio == null) inicio = ts;
      const p = Math.min((ts - inicio) / duration, 1);
      const e = 1 - Math.pow(1 - p, 3);
      setVal(Math.round(desde + (objetivo - desde) * e));
      if (p < 1) raf = requestAnimationFrame(paso);
    };
    raf = requestAnimationFrame(paso);
    return () => cancelAnimationFrame(raf);
  }, [objetivo, duration]);
  return val;
}

// Barra de progreso: el ancho y el % suben contando al aparecer o al cambiar el valor.
function ProgressBar({ pct, barWidth = "100%", height = 6, showPct = true, gap = 10 }) {
  const shown = useCountUp(pct);
  const track = (
    <div style={{ width: barWidth, height, background: "var(--border)", borderRadius: 3, overflow: "hidden", flex: barWidth === "100%" ? 1 : undefined }}>
      <div style={{ height: "100%", width: `${shown}%`, background: "#000", borderRadius: 3, transition: "width .1s linear" }} />
    </div>
  );
  if (!showPct) return track;
  return (
    <div style={{ display: "flex", alignItems: "center", gap }}>
      {track}
      <span style={{ fontSize: 11, fontWeight: 400, color: "rgba(0,0,0,.45)", whiteSpace: "nowrap" }}>{shown}%</span>
    </div>
  );
}

// Checkmark SVG que se traza a sí mismo (círculo + palomita) al montarse. La animación
// vive en styles.css (.draw-check-*), así que aquí sólo se dibuja el trazo.
function DrawCheck({ size = 26, color = "#166534" }) {
  return (
    <svg className="draw-check" viewBox="0 0 52 52" width={size} height={size} aria-hidden="true">
      <circle className="draw-check-circle" cx="26" cy="26" r="24" fill="none" stroke={color} strokeWidth="2.5" />
      <path className="draw-check-mark" fill="none" stroke={color} strokeWidth="3.5"
            strokeLinecap="round" strokeLinejoin="round" d="M14 27 l8 8 l16 -18" />
    </svg>
  );
}

// Bloque de éxito: check grande que se traza + mensaje. Momento de logro.
function SavedOk({ text, color = "#166534" }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12, padding: "8px 0", textAlign: "center" }}>
      <DrawCheck size={56} color={color} />
      <p className="fine" style={{ color, margin: 0, fontSize: 15 }}>{text}</p>
    </div>
  );
}

// Placeholder con brillo animado (skeleton) mientras carga contenido.
function Skeleton({ height = 16, width = "100%", radius = 6, style }) {
  return <div className="skeleton" style={{ height, width, borderRadius: radius, ...style }} />;
}
// Esqueleto para un formulario que carga: varias filas (etiqueta + campo).
function SkeletonForm({ rows = 4 }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 26 }}>
      {Array.from({ length: rows }).map((_, i) => (
        <div key={i} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <Skeleton height={13} width={`${45 + (i % 3) * 12}%`} />
          <Skeleton height={42} width="100%" radius={8} />
        </div>
      ))}
    </div>
  );
}
// Rejilla de cards-placeholder (skeleton) para listas de personas que cargan.
function SkeletonCards({ n = 8 }) {
  return Array.from({ length: n }).map((_, i) => (
    <div key={i} className="advisee-page-card" style={{ cursor: "default", pointerEvents: "none" }} aria-hidden="true">
      <div className="advisee-page-foto skeleton" />
      <Skeleton width="65%" height={14} />
    </div>
  ));
}

// ── Barra de carga global (top loading bar) ──
// count = peticiones en curso; total/done = peticiones de la "tanda" actual,
// para que el progreso sea proporcional a las que ya han terminado (done/total).
const _loading = { count: 0, total: 0, done: 0, listeners: new Set() };
function subscribeLoading(fn) {
  _loading.listeners.add(fn);
  return () => _loading.listeners.delete(fn);
}
function _emitLoading() {
  const snapshot = { count: _loading.count, total: _loading.total, done: _loading.done };
  _loading.listeners.forEach((fn) => fn(snapshot));
}
function startLoading() {
  if (_loading.count === 0) { _loading.total = 0; _loading.done = 0; } // nueva tanda
  _loading.count += 1;
  _loading.total += 1;
  _emitLoading();
}
function stopLoading() {
  _loading.count = Math.max(0, _loading.count - 1);
  _loading.done += 1;
  _emitLoading();
}

// Traduce el error que devuelve el backend. El backend manda `error` (texto en español,
// ya escrito para el usuario) y, cuando sabe qué ha pasado, un `code` estable: si ese
// código está traducido lo pintamos en el idioma del usuario, y si no cae al texto del
// backend, que sigue siendo útil. Nunca dejamos que se vea un error de código.
function mensajeDeError(data) {
  const clave = data && data.code ? `err.${data.code}` : "";
  if (clave && tieneClave(clave)) return t(clave);
  return (data && data.error) || t("common.err_generic");
}

async function apiRequest(path, { token, method = "GET", body } = {}) {
  startLoading();
  try {
    const response = await fetch(apiUrl(path), {
      method,
      headers: {
        "Content-Type": "application/json",
        ...(token ? { Authorization: `Bearer ${token}` } : {}),
      },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(mensajeDeError(data));
    }
    return data;
  } finally {
    stopLoading();
  }
}

// Abre un archivo protegido (HTML/PDF) en una pestaña nueva SIN poner el token en
// la URL: lo pide con la cabecera Authorization y lo muestra desde un blob local.
// Antes se hacía window.open(`...?token=${token}`), lo que filtraba el token de
// sesión en el historial, la caché, los logs del servidor y la cabecera Referer.
async function openAuthedFile(path, token) {
  // Abrimos la pestaña de forma síncrona (gesto del usuario) para no ser bloqueados
  // por el bloqueador de pop-ups; luego le cargamos el blob cuando llega.
  const win = window.open("", "_blank");
  try {
    const res = await fetch(apiUrl(path), { headers: { Authorization: `Bearer ${token}` } });
    if (!res.ok) throw new Error(String(res.status));
    const url = URL.createObjectURL(await res.blob());
    if (win) win.location = url; else window.open(url, "_blank", "noopener,noreferrer");
    setTimeout(() => URL.revokeObjectURL(url), 60000);
  } catch {
    if (win) win.close();
  }
}

const _CACHE_TTL = 5 * 60 * 1000;

function _getCached(key) {
  try {
    const raw = sessionStorage.getItem(`ebc_${key}`);
    if (!raw) return undefined;
    const { d, t } = JSON.parse(raw);
    return Date.now() - t < _CACHE_TTL ? d : undefined;
  } catch { return undefined; }
}

function _setCache(key, data) {
  try { sessionStorage.setItem(`ebc_${key}`, JSON.stringify({ d: data, t: Date.now() })); } catch {}
}

function clearApiCache() {
  try {
    Object.keys(sessionStorage)
      .filter((k) => k.startsWith("ebc_"))
      .forEach((k) => sessionStorage.removeItem(k));
  } catch {}
}

async function apiRequestCached(path, options, onFresh) {
  const cached = _getCached(path);
  if (cached !== undefined) {
    if (onFresh) {
      apiRequest(path, options)
        .then((fresh) => { _setCache(path, fresh); onFresh(fresh); })
        .catch(() => {});
    }
    return cached;
  }
  const data = await apiRequest(path, options);
  _setCache(path, data);
  return data;
}

function isStrongPassword(password) {
  return password.length >= 8 && /[A-Z]/.test(password) && /[^A-Za-z0-9]/.test(password);
}

function initials(nombre) {
  if (!nombre) return "?";
  return nombre.trim().split(/\s+/).map((w) => w[0].toUpperCase()).slice(0, 2).join("");
}

function getResetToken() {
  const queryToken = new URLSearchParams(window.location.search).get("reset");
  if (queryToken) return queryToken;

  const hashMatch = window.location.hash.match(/reset[=/]([^&/?#]+)/);
  if (hashMatch) return decodeURIComponent(hashMatch[1]);

  const pathMatch = window.location.pathname.match(/\/reset\/([^/]+)/);
  return pathMatch ? decodeURIComponent(pathMatch[1]) : "";
}

function PasswordInput({ value, onChange, placeholder = "", required = true, minLength }) {
  const [visible, setVisible] = useState(false);
  return (
    <div className="password-field">
      <input
        type={visible ? "text" : "password"}
        value={value}
        placeholder={placeholder}
        onChange={onChange}
        required={required}
        minLength={minLength}
      />
      <button
        type="button"
        className="password-toggle"
        onClick={() => setVisible(!visible)}
        aria-label={visible ? t("pw.hide") : t("pw.show")}
        title={visible ? t("pw.hide") : t("pw.show")}
      >
        <span className={`eye-icon ${visible ? "is-visible" : ""}`} aria-hidden="true" />
      </button>
    </div>
  );
}

function Footer() {
  return (
    <footer className="site-footer">
      <p className="site-footer-copy">© {new Date().getFullYear()} <strong>Igeneris</strong></p>
      <nav className="site-footer-links">
        <a href="#privacidad">{t("footer.privacy")}</a>
        <a href="#terminos">{t("footer.terms")}</a>
      </nav>
    </footer>
  );
}

function cambiarIdiomaGlobal(code) {
  // Actualiza la UI al instante y, si hay sesión, lo persiste en la columna Idioma de Notion (fuente de verdad).
  setLangManual(code);
  const tk = localStorage.getItem("evaluabot_token") || sessionStorage.getItem("evaluabot_token") || "";
  if (tk) {
    apiRequest("/api/set-idioma", { token: tk, method: "POST", body: { idioma: code } }).catch(() => {});
  }
}

// Rueda de idioma: aro dividido en 3 segmentos (ES arriba, EN abajo-dcha, PT abajo-izda).
// El activo se resalta en naranja; click en un segmento = elegir ese idioma.
const _LANG_SEGS = [
  { code: "es", label: "ES", title: "Español" },
  { code: "en", label: "EN", title: "English" },
  { code: "pt", label: "PT", title: "Português" },
];
// Banderas como SVG (no emoji): en Windows con Chrome/Edge los emojis de bandera
// no se renderizan y salen como letras. Con SVG se ven siempre igual.
const _FLAG_SVG = {
  es: "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 3 2'><rect width='3' height='2' fill='#AA151B'/><rect y='.5' width='3' height='1' fill='#F1BF00'/></svg>",
  pt: "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 30 20'><rect width='30' height='20' fill='#DA291C'/><rect width='12' height='20' fill='#046A38'/><circle cx='12' cy='10' r='3.4' fill='#FFD100' stroke='#fff' stroke-width='.5'/><circle cx='12' cy='10' r='1.5' fill='#DA291C'/></svg>",
  en: "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 60 30'><clipPath id='uk'><rect width='60' height='30'/></clipPath><g clip-path='url(#uk)'><rect width='60' height='30' fill='#012169'/><path d='M0,0 60,30 M60,0 0,30' stroke='#fff' stroke-width='6'/><path d='M0,0 60,30 M60,0 0,30' stroke='#C8102E' stroke-width='4'/><path d='M30,0 V30 M0,15 H60' stroke='#fff' stroke-width='10'/><path d='M30,0 V30 M0,15 H60' stroke='#C8102E' stroke-width='6'/></g></svg>",
};
const _flagUri = (code) => `data:image/svg+xml,${encodeURIComponent(_FLAG_SVG[code] || "")}`;
function LangToggle() {
  const [, force] = useState(0);
  const [hover, setHover] = useState(-1);
  useEffect(() => subscribeLang(() => force((n) => n + 1)), []);
  const lang = getLang();
  const idx = Math.max(0, _LANG_SEGS.findIndex((s) => s.code === lang));

  // Al elegir un idioma, la rueda gira para dejar ESE segmento arriba (camino mas corto).
  const [spin, setSpin] = useState(-120 * idx);
  const _primerRender = React.useRef(true);
  useEffect(() => {
    setSpin((prev) => {
      if (_primerRender.current) { _primerRender.current = false; return -120 * idx; }
      // Da al menos una vuelta completa y deja el idioma activo arriba.
      let target = -120 * idx;
      while (target > prev - 360) target -= 360;
      return target;
    });
  }, [idx]);

  const size = 72, cx = size / 2, cy = size / 2, R = size / 2 - 3, START = -150;
  const pol = (ang, rad) => {
    const a = (ang * Math.PI) / 180;
    return [cx + rad * Math.cos(a), cy + rad * Math.sin(a)];
  };
  // Porción de tarta (círculo completo, sin agujero): del centro al arco exterior.
  const sector = (a1, a2) => {
    const [ox1, oy1] = pol(a1, R), [ox2, oy2] = pol(a2, R);
    return `M ${cx} ${cy} L ${ox1} ${oy1} A ${R} ${R} 0 0 1 ${ox2} ${oy2} Z`;
  };

  return (
    <div style={{
      position: "fixed", top: 74, right: 14, zIndex: 300,
      opacity: hover >= 0 ? 1 : 0.5, transition: "opacity .18s",
      filter: "drop-shadow(0 1px 5px rgba(0,0,0,.15))",
    }}>
      <svg viewBox={`0 0 ${size} ${size}`} width={size} height={size} role="group" aria-label="Idioma">
        <g style={{
          transform: `rotate(${spin}deg)`, transformBox: "fill-box", transformOrigin: "center",
          transition: "transform .6s cubic-bezier(.2,.75,.2,1)",
        }}>
          {_LANG_SEGS.map((s, i) => {
            const a1 = START + i * 120, a2 = START + (i + 1) * 120;
            const activo = lang === s.code;
            const mid = a1 + 60;
            const [fx, fy] = pol(mid, R * 0.55);   // bandera centrada en el segmento
            const fw = 20, fh = 13;
            const fill = activo ? "var(--accent, #ff4d2e)" : (hover === i ? "#ffe7e0" : "#fff");
            return (
              <g key={s.code} onClick={() => cambiarIdiomaGlobal(s.code)}
                 onMouseEnter={() => setHover(i)} onMouseLeave={() => setHover(-1)}
                 style={{ cursor: "pointer" }}>
                <title>{s.title}</title>
                <path d={sector(a1, a2)} fill={fill} stroke="#111" strokeWidth="1.6"
                      style={{ transition: "fill .25s" }} />
                <g style={{ transform: `rotate(${-spin}deg)`, transformBox: "fill-box", transformOrigin: "center", transition: "transform .6s cubic-bezier(.2,.75,.2,1)" }}>
                  <image href={_flagUri(s.code)} x={fx - fw / 2} y={fy - fh / 2} width={fw} height={fh}
                         preserveAspectRatio="xMidYMid slice"
                         style={{ pointerEvents: "none" }} />
                  <rect x={fx - fw / 2} y={fy - fh / 2} width={fw} height={fh} rx="2" fill="none"
                        stroke="rgba(0,0,0,.4)" strokeWidth="0.7" style={{ pointerEvents: "none" }} />
                </g>
              </g>
            );
          })}
        </g>
      </svg>
    </div>
  );
}

function renderLegalInline(text) {
  // **negrita** y [texto](#hash) -> enlaces internos
  const parts = text.split(/(\*\*[^*]+\*\*|\[[^\]]+\]\([^)]+\))/g);
  return parts.map((part, i) => {
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) {
      return <strong key={i}>{part.slice(2, -2)}</strong>;
    }
    const link = part.match(/^\[([^\]]+)\]\(([^)]+)\)$/);
    if (link) {
      return <a key={i} href={link[2]}>{link[1]}</a>;
    }
    return <span key={i}>{part}</span>;
  });
}

function LegalContent({ texto }) {
  const lines = texto.split("\n");
  const blocks = [];
  let list = null;

  const flushList = () => {
    if (list) { blocks.push({ type: "ul", ordered: list.ordered, items: list.items }); list = null; }
  };

  lines.forEach((raw) => {
    const line = raw.trimEnd();
    if (/^-\s+/.test(line) || /^\d+\.\s+/.test(line)) {
      const ordered = /^\d+\.\s+/.test(line);
      const item = line.replace(/^(-|\d+\.)\s+/, "");
      if (!list || list.ordered !== ordered) { flushList(); list = { ordered, items: [] }; }
      list.items.push(item);
    } else if (line.startsWith("### ")) {
      flushList(); blocks.push({ type: "h3", text: line.slice(4) });
    } else if (line.startsWith("## ")) {
      flushList(); blocks.push({ type: "h2", text: line.slice(3) });
    } else if (line.startsWith("# ")) {
      flushList(); blocks.push({ type: "h1", text: line.slice(2) });
    } else if (line.trim() === "") {
      flushList();
    } else {
      flushList(); blocks.push({ type: "p", text: line });
    }
  });
  flushList();

  return (
    <div className="legal-content">
      {blocks.map((b, i) => {
        if (b.type === "h1") return <h1 key={i} className="legal-h1">{b.text}</h1>;
        if (b.type === "h2") return <h2 key={i} className="legal-h2">{renderLegalInline(b.text)}</h2>;
        if (b.type === "h3") return <h3 key={i} className="legal-h3">{renderLegalInline(b.text)}</h3>;
        if (b.type === "ul") {
          const Tag = b.ordered ? "ol" : "ul";
          return <Tag key={i} className="legal-list">{b.items.map((it, j) => <li key={j}>{renderLegalInline(it)}</li>)}</Tag>;
        }
        return <p key={i} className="legal-p">{renderLegalInline(b.text)}</p>;
      })}
    </div>
  );
}

// Contexto para navegar a la página inicial desde cualquier página anidada.
const GoHomeContext = React.createContext(null);

// Bloque de navegación de la esquina superior derecha: flecha de "Volver" y,
// justo debajo, un botón de "Inicio" (casita) que lleva a la página inicial.
// El botón de inicio solo aparece si hay un handler disponible en el contexto.
function NavBack({ onBack, style }) {
  const goHome = React.useContext(GoHomeContext);
  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6, ...style }}>
      <button className="link-button" onClick={onBack}>{t("common.back")}</button>
      {goHome && (
        <button
          type="button"
          className="link-button"
          onClick={goHome}
          title={t("common.home")}
          aria-label={t("common.home")}
          style={{ display: "inline-flex", alignItems: "center", justifyContent: "flex-end", gap: 5, padding: 0 }}
        >
          <svg viewBox="0 0 24 24" width="20" height="20" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round">
            <path d="M4 11 12 4l8 7" />
            <path d="M6 9.6V19h5v-5h2v5h5V9.6" />
          </svg>
          <span>{t("common.home")}</span>
        </button>
      )}
    </div>
  );
}

function LegalPage({ doc, onBack }) {
  const data = LEGAL_DOCS[doc];
  const [texto, setTexto] = useState(null);
  useEffect(() => { window.scrollTo(0, 0); }, [doc]);
  useEffect(() => {
    let vivo = true;
    setTexto(null);
    if (data) {
      data.load()
        .then((m) => { if (vivo) setTexto(m.default); })
        .catch(() => { if (vivo) setTexto(""); });
    }
    return () => { vivo = false; };
  }, [doc]);
  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <NavBack onBack={onBack} />
      </nav>
      <div className="legal-wrap">
        {!data ? <p>{t("legal.unavailable")}</p>
          : texto == null ? <SkeletonForm rows={8} />
          : <LegalContent texto={texto} />}
      </div>
      <Footer />
    </main>
  );
}

function AdminRoleSelect({ user, onChoose, onLogout }) {
  const persona = user?.persona || user?.username || "";
  return (
    <main className="page auth-page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <div className="nav-user">
          <div className="nav-user-info">
            <span className="nav-user-name">{persona}</span>
            <button className="link-button logout-btn" onClick={onLogout}>{t("common.logout")}</button>
          </div>
        </div>
      </nav>
      <div className="role-select-body">
        <p className="kicker">{t("role.welcome")}</p>
        <h2>{t("role.how_enter")}</h2>
        <div className="role-select-grid stagger">
          <button className="role-card" onClick={() => onChoose("admin")}>
            <span className="role-card-title">{t("role.admin_title")}</span>
            <span className="role-card-desc">{t("role.admin_desc")}</span>
          </button>
          <button className="role-card secondary" onClick={() => onChoose("personal")}>
            <span className="role-card-title">{t("role.personal_title")}</span>
            <span className="role-card-desc">{t("role.personal_desc")}</span>
          </button>
        </div>
      </div>
      <Footer />
    </main>
  );
}

function AdminPanel({ token, onBack }) {
  const [evaluados, setEvaluados] = useState([]);
  const [cargandoEvaluados, setCargandoEvaluados] = useState(true);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(null);
  const [informeFinal, setInformeFinal] = useState(null);
  const [statusMsg, setStatusMsg] = useState("");
  const [anonimato, setAnonimato] = useState(null);
  const [anonLoading, setAnonLoading] = useState(false);
  const [generandoFuente, setGenerandoFuente] = useState("");
  const [fuenteError, setFuenteError] = useState("");
  const [feedbackConfidencial, setFeedbackConfidencial] = useState(null);
  const [vistaGlobalConfidencial, setVistaGlobalConfidencial] = useState(false);
  const [feedbackGlobal, setFeedbackGlobal] = useState(null);
  const [buscarGlobalConfidencial, setBuscarGlobalConfidencial] = useState("");
  const [cumplimiento, setCumplimiento] = useState({});
  const [detalleCumplimiento, setDetalleCumplimiento] = useState(null);

  useEffect(() => {
    apiRequest("/api/evaluados", { token })
      .then((data) => setEvaluados(data.evaluados || []))
      .catch(() => {})
      .finally(() => setCargandoEvaluados(false));
    apiRequest("/api/anonimato-evaluadores", { token })
      .then((data) => setAnonimato(data))
      .catch(() => {});
    apiRequest("/api/cumplimiento-evaluaciones", { token })
      .then((data) => setCumplimiento(data.cumplimiento || {}))
      .catch(() => {});
  }, [token]);

  useEffect(() => {
    if (!selected?.nombre) return;
    setDetalleCumplimiento(null);
    apiRequest(`/api/cumplimiento-evaluaciones-detalle?nombre=${encodeURIComponent(selected.nombre)}`, { token })
      .then((data) => setDetalleCumplimiento(data.detalle || []))
      .catch(() => setDetalleCumplimiento([]));
  }, [token, selected?.nombre]);

  useEffect(() => {
    if (!vistaGlobalConfidencial) return;
    setFeedbackGlobal(null);
    apiRequest("/api/feedback-confidencial-todos", { token })
      .then((data) => setFeedbackGlobal(data.feedback || []))
      .catch(() => setFeedbackGlobal([]));
  }, [token, vistaGlobalConfidencial]);

  const feedbackGlobalFiltrado = (feedbackGlobal || []).filter((f) => {
    const q = buscarGlobalConfidencial.trim().toLowerCase();
    if (!q) return true;
    return (f.evaluado || "").toLowerCase().includes(q) || (f.proyecto || "").toLowerCase().includes(q);
  });

  async function toggleGlobalAnonimo() {
    if (!anonimato || anonLoading) return;
    setAnonLoading(true);
    try {
      const data = await apiRequest("/api/anonimato-evaluadores", {
        token, method: "POST", body: { global_anonimo: !anonimato.global_anonimo },
      });
      setAnonimato(data);
    } catch {}
    setAnonLoading(false);
  }

  async function toggleEvaluadoRevelado(nombre) {
    if (!anonimato || anonLoading) return;
    setAnonLoading(true);
    try {
      const revelados = anonimato.advisees_revelados || [];
      const nuevos = revelados.includes(nombre)
        ? revelados.filter((n) => n !== nombre)
        : [...revelados, nombre];
      const data = await apiRequest("/api/anonimato-evaluadores", {
        token, method: "POST", body: { advisees_revelados: nuevos },
      });
      setAnonimato(data);
    } catch {}
    setAnonLoading(false);
  }

  useEffect(() => {
    if (!selected) return;
    setInformeFinal(null);
    apiRequest(`/api/informe-final?evaluado=${encodeURIComponent(selected.nombre)}`, { token })
      .then((data) => setInformeFinal(data))
      .catch(() => setInformeFinal({ disponible: false, mensaje: t("admin.err_load_report") }));
  }, [token, selected?.nombre]);

  useEffect(() => {
    if (!selected) return;
    setFeedbackConfidencial(null);
    apiRequest(`/api/feedback-confidencial?evaluado=${encodeURIComponent(selected.nombre)}`, { token })
      .then((data) => setFeedbackConfidencial(data.feedback || []))
      .catch(() => setFeedbackConfidencial([]));
  }, [token, selected?.nombre]);

  async function selectEmpleado(item) {
    setStatusMsg("");
    try {
      const perfil = await apiRequest(`/api/perfil-empleado?nombre=${encodeURIComponent(item.value)}`, { token });
      setSelected({ nombre: item.value, foto: perfil.foto || item.foto || null, cargo: perfil.cargo || "" });
    } catch {
      setSelected({ nombre: item.value, foto: item.foto || null, cargo: "" });
    }
  }

  async function openFile(path, filename) {
    if (!filename.endsWith(".docx")) {
      openAuthedFile(path, token);
      return;
    }
    try {
      const response = await fetch(apiUrl(path), { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) throw new Error(t("admin.err_download"));
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setStatusMsg(err.message);
    }
  }

  async function descargarFuentePdf(endpoint, etiqueta) {
    setGenerandoFuente(endpoint);
    setFuenteError("");
    try {
      const data = await apiRequest(endpoint, { token, method: "POST", body: { evaluado: selected.nombre } });
      const path = data.pdfUrl;
      if (!path) throw new Error(t("ad.err_no_doc"));
      const response = await fetch(apiUrl(path), { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) {
        const d = await response.json().catch(() => ({}));
        throw new Error(d.error || t("admin.err_download"));
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${etiqueta}_${selected.nombre.replace(/\s+/g, "_")}.pdf`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setFuenteError(err.message);
    } finally {
      setGenerandoFuente("");
    }
  }

  const filtrados = evaluados.filter((e) =>
    e.label.toLowerCase().includes(search.toLowerCase())
  );

  if (selected) {
    return (
      <main className="page">
        <nav className="nav">
          <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
          <button className="link-button" onClick={() => { setSelected(null); setInformeFinal(null); setStatusMsg(""); setFuenteError(""); }}>{t("common.back")}</button>
        </nav>
        <div className="admin-employee-wrap">
          <div className="admin-employee-layout">
            <div className="admin-employee-profile">
              {selected.foto
                ? <img src={selected.foto} alt={selected.nombre} className="advisee-detail-foto" />
                : <div className="advisee-detail-foto advisee-foto-placeholder">{selected.nombre.charAt(0)}</div>
              }
              <h2 className="advisee-detail-nombre">{selected.nombre}</h2>
              {selected.cargo && <p className="fine" style={{ margin: 0 }}>{selected.cargo}</p>}
            </div>
            <div className="admin-employee-actions">
              <p className="kicker">{t("admin.reports")}</p>
              {informeFinal === null ? (
                <p className="fine">{t("common.loading")}</p>
              ) : informeFinal?.disponible ? (
                <>
                  {informeFinal.htmlUrl && (
                    <button onClick={() => openFile(informeFinal.htmlUrl, "informe_final.html")}>
                      {t("admin.view_final_report")}
                    </button>
                  )}
                  {informeFinal.docxUrl && (
                    <button className="secondary" onClick={() => openFile(informeFinal.docxUrl, "informe_final.docx")}>
                      {t("admin.download_word")}
                    </button>
                  )}
                </>
              ) : (
                <p className="fine">{informeFinal?.mensaje || t("admin.no_final_report")}</p>
              )}
              <div style={{ marginTop: 20 }}>
                <p className="kicker">{t("admin.available_info")}</p>
                <button className="secondary" disabled={!!generandoFuente}
                  onClick={() => descargarFuentePdf("/api/generar-pdf-evals-mensuales", "evals_mensuales")}>
                  {generandoFuente === "/api/generar-pdf-evals-mensuales" ? t("ad.generating") : t("admin.dl_monthly_evals")}
                </button>
                <button className="secondary" disabled={!!generandoFuente} style={{ marginTop: 8 }}
                  onClick={() => descargarFuentePdf("/api/generar-pdf-evals-proyecto", "evals_proyecto")}>
                  {generandoFuente === "/api/generar-pdf-evals-proyecto" ? t("ad.generating") : t("admin.dl_proj_evals")}
                </button>
                <button className="secondary" disabled={!!generandoFuente} style={{ marginTop: 8 }}
                  onClick={() => descargarFuentePdf("/api/generar-pdf-seguimiento", "seguimiento_personal")}>
                  {generandoFuente === "/api/generar-pdf-seguimiento" ? t("ad.generating") : t("admin.dl_personal_tracking")}
                </button>
                {fuenteError && <p className="fine error" style={{ marginTop: 8 }}>{fuenteError}</p>}
              </div>
              {anonimato && (
                <div style={{ marginTop: 20 }}>
                  <p className="kicker">Evaluadores</p>
                  {(() => {
                    const globalRevelado = !anonimato.global_anonimo;
                    const individualRevelado = (anonimato.advisees_revelados || []).includes(selected.nombre);
                    const visible = globalRevelado || individualRevelado;
                    return (
                      <button
                        className="secondary"
                        disabled={anonLoading || globalRevelado}
                        onClick={() => toggleEvaluadoRevelado(selected.nombre)}
                        title={globalRevelado ? "Revelado globalmente" : undefined}
                      >
                        {visible ? "Ocultar evaluadores a su CA" : "Revelar evaluadores a su CA"}
                      </button>
                    );
                  })()}
                </div>
              )}
              <div style={{ marginTop: 20 }}>
                <p className="kicker">{t("admin.confidential_feedback_title")}</p>
                <p className="fine">{t("admin.confidential_feedback_note")}</p>
                {feedbackConfidencial === null ? (
                  <p className="fine">{t("common.loading")}</p>
                ) : feedbackConfidencial.length === 0 ? (
                  <p className="fine">{t("admin.confidential_feedback_empty")}</p>
                ) : (
                  <div className="objetivos-list">
                    {feedbackConfidencial.map((f, i) => (
                      <article key={i} className="objetivo-item">
                        <p className="opinion-fecha fine">
                          {f.fecha ? f.fecha.slice(0, 10) : t("common.no_date")}
                          {f.proyecto ? ` · ${f.proyecto}` : ""}
                        </p>
                        {f.q1 && <p className="objetivo-texto"><strong>{f.q1}</strong></p>}
                        {f.q2 && <p className="objetivo-texto">{f.q2}</p>}
                      </article>
                    ))}
                  </div>
                )}
              </div>
              <div style={{ marginTop: 20 }}>
                <p className="kicker">{t("admin.eval_compliance_title")}</p>
                <p className="fine">{t("admin.eval_compliance_note")}</p>
                {detalleCumplimiento === null ? (
                  <p className="fine">{t("common.loading")}</p>
                ) : detalleCumplimiento.length === 0 ? (
                  <p className="fine">{t("admin.eval_compliance_empty")}</p>
                ) : (
                  <div className="objetivos-list">
                    {detalleCumplimiento.map((ciclo, i) => (
                      <article key={i} className="objetivo-item">
                        <p className="opinion-fecha fine">{t("admin.eval_cycle")} {ciclo.ciclo}</p>
                        <div className="eval-compliance-rows">
                          {["mensual", "personal", "ca", "proyecto", "extra"]
                            .filter((tp) => ciclo.tipos[tp])
                            .map((tp) => (
                              <div key={tp} className="eval-compliance-row">
                                <span>{t(`admin.eval_type_${tp}`)}</span>
                                <span className="eval-compliance-ratio">
                                  {ciclo.tipos[tp].realizadas}/{ciclo.tipos[tp].enviadas}
                                </span>
                              </div>
                            ))}
                        </div>
                      </article>
                    ))}
                  </div>
                )}
              </div>
              {statusMsg && (
                <p className="fine error">{statusMsg}</p>
              )}
            </div>
          </div>
        </div>
        <Footer />
      </main>
    );
  }

  if (vistaGlobalConfidencial) {
    return (
      <main className="page">
        <nav className="nav">
          <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
          <button className="link-button" onClick={() => setVistaGlobalConfidencial(false)} style={{ marginLeft: "auto" }}>{t("common.back")}</button>
        </nav>
        <div className="admin-search-wrap">
          <p className="kicker">{t("role.admin_title")}</p>
          <h2>{t("admin.confidential_feedback_title")}</h2>
          <p className="fine">{t("admin.confidential_feedback_all_note")}</p>
          <div className="admin-search-field" style={{ marginTop: 16 }}>
            <input
              type="text"
              placeholder={t("admin.confidential_feedback_search_ph")}
              value={buscarGlobalConfidencial}
              onChange={(e) => setBuscarGlobalConfidencial(e.target.value)}
            />
          </div>
          {feedbackGlobal === null ? (
            <p className="fine" style={{ marginTop: 20 }}>{t("common.loading")}</p>
          ) : feedbackGlobalFiltrado.length === 0 ? (
            <p className="fine" style={{ marginTop: 20 }}>{t("admin.confidential_feedback_empty")}</p>
          ) : (
            <div className="objetivos-list" style={{ marginTop: 20 }}>
              {feedbackGlobalFiltrado.map((f, i) => (
                <article key={i} className="objetivo-item">
                  <p className="opinion-fecha fine">
                    {f.fecha ? f.fecha.slice(0, 10) : t("common.no_date")}
                    {f.proyecto ? ` · ${f.proyecto}` : ""}
                  </p>
                  <p className="objetivo-titulo"><strong>{f.evaluado}</strong></p>
                  {f.q1 && <p className="objetivo-texto"><strong>{f.q1}</strong></p>}
                  {f.q2 && <p className="objetivo-texto">{f.q2}</p>}
                </article>
              ))}
            </div>
          )}
        </div>
        <Footer />
      </main>
    );
  }

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        {anonimato && (
          <button
            className="link-button"
            disabled={anonLoading}
            onClick={toggleGlobalAnonimo}
            style={{ color: "var(--accent)" }}
            onMouseEnter={(e) => e.currentTarget.style.color = "#0a0a0a"}
            onMouseLeave={(e) => e.currentTarget.style.color = "var(--accent)"}
          >
            › {anonimato.global_anonimo ? "Revelar todos los evaluadores" : "Ocultar todos los evaluadores"}
          </button>
        )}
        <button
          className="link-button"
          onClick={() => setVistaGlobalConfidencial(true)}
          style={{ color: "var(--accent)" }}
          onMouseEnter={(e) => e.currentTarget.style.color = "#0a0a0a"}
          onMouseLeave={(e) => e.currentTarget.style.color = "var(--accent)"}
        >
          › {t("admin.confidential_feedback_all_btn")}
        </button>
        <button className="link-button" onClick={onBack} style={{ marginLeft: "auto" }}>{t("common.back")}</button>
      </nav>
      <div className="admin-search-wrap">
        <p className="kicker">{t("role.admin_title")}</p>
        <h2>{t("admin.search_employee")}</h2>
        <div className="admin-search-field">
          <input
            type="text"
            placeholder={t("admin.search_placeholder")}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="advisees-page-grid stagger">
          {cargandoEvaluados && <SkeletonCards n={8} />}
          {!cargandoEvaluados && filtrados.map((e) => (
            <button
              key={e.value}
              className="advisee-page-card"
              onClick={() => selectEmpleado(e)}
            >
              {e.foto
                ? <img src={e.foto} alt={e.label} className="advisee-page-foto" />
                : <div className="advisee-page-foto advisee-foto-placeholder">{e.label.charAt(0)}</div>
              }
              <span className="advisee-page-nombre">
                {e.label}
                {cumplimiento[e.value] && (
                  <span
                    className="eval-count-badge"
                    title={t("admin.eval_count_tooltip")}
                  >
                    {cumplimiento[e.value].realizadas}/{cumplimiento[e.value].enviadas}
                  </span>
                )}
              </span>
            </button>
          ))}
          {filtrados.length === 0 && search && (
            <p className="fine" style={{ textAlign: "center", width: "100%" }}>{t("admin.no_results", { q: search })}</p>
          )}
        </div>
      </div>
      <Footer />
    </main>
  );
}

// Tarjeta de un objetivo. Con onEliminar muestra el botón de cerrarlo; los objetivos
// antiguos llegan sin él (ya están cerrados) y con la firma de quién los cerró.
function ObjetivoCard({ obj, onEliminar, eliminando, children }) {
  // Tipo y fecha en que se marcó el objetivo, en una sola línea (cualquiera puede faltar).
  const meta = [obj.tipo, formatearFecha(obj.fecha)].filter(Boolean).join(" · ");
  return (
    <article className="objetivo-item">
      {meta && <p className="opinion-fecha fine">{meta}</p>}
      <p className="objetivo-titulo"><strong>{obj.titulo}</strong></p>
      {obj.kpis && <p className="objetivo-texto fine"><em>{t("obj.kpis_label")}</em> {obj.kpis}</p>}
      {children}
      {obj.eliminado_por && (
        <p className="opinion-fecha fine">
          {t("goals.closed_by", { quien: obj.eliminado_por, fecha: formatearFecha(obj.fecha_eliminacion) })}
        </p>
      )}
      {onEliminar && (
        <div style={{ marginTop: "8px" }}>
          <button
            className="link-button objetivo-eliminar"
            disabled={eliminando}
            onClick={() => onEliminar(obj.page_id)}
          >
            <span aria-hidden="true">×</span>
            {eliminando ? t("common.deleting") : t("common.delete")}
          </button>
        </div>
      )}
    </article>
  );
}

function MisObjetivosPage({ token, persona, onBack }) {
  const [objetivos, setObjetivos] = useState([]);
  const [loading, setLoading] = useState(true);
  const [deleting, setDeleting] = useState(null);
  const [error, setError] = useState("");

  // Solo los vigentes: los que uno cierra pasan al histórico, que ve su CA.
  function recargar() {
    return apiRequest(`/api/objetivos?nombre=${encodeURIComponent(persona)}`, { token })
      .then((data) => setObjetivos(data.objetivos || []));
  }

  useEffect(() => {
    recargar().catch((err) => setError(err.message)).finally(() => setLoading(false));
  }, [token, persona]);

  async function eliminar(page_id) {
    if (!window.confirm(t("goals.confirm_delete"))) return;
    setError("");
    setDeleting(page_id);
    try {
      await apiRequest("/api/objetivos", { token, method: "DELETE", body: { page_id, nombre: persona } });
      await recargar();
    } catch (err) {
      setError(err.message);
    } finally {
      setDeleting(null);
    }
  }

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <NavBack onBack={onBack} />
      </nav>
      <section className="hero dashboard-hero">
        <div>
          <p className="kicker">{t("obj.personal_dev")}</p>
          <h1>{t("obj.my_goals_title")}</h1>
        </div>
      </section>
      <section className="objetivos-historial panel">
        {error && <p className="error">{error}</p>}
        {loading ? (
          <p>{t("common.loading")}</p>
        ) : objetivos.length ? (
          <div className="objetivos-list">
            {objetivos.map((obj) => (
              <ObjetivoCard
                key={obj.page_id}
                obj={obj}
                onEliminar={eliminar}
                eliminando={deleting === obj.page_id}
              />
            ))}
          </div>
        ) : (
          <p>{t("obj.none_yet")}</p>
        )}
      </section>
      <Footer />
    </main>
  );
}

function ObjetivosPage({ token, advisee, caName, onBack, vista = "form", onCambiarVista }) {
  const [objetivos, setObjetivos] = useState([]);
  const [antiguos, setAntiguos] = useState([]);
  const [form, setForm] = useState({ titulo: "", kpis: "", descripcion: "", tipo: "" });
  const [pendientes, setPendientes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [enviado, setEnviado] = useState(false);
  const esHistorial = vista === "historial";

  function recargar() {
    const url = `/api/objetivos?nombre=${encodeURIComponent(advisee.nombre)}`;
    const actuales = apiRequest(url, { token }).then((data) => setObjetivos(data.objetivos || []));
    // Los antiguos solo hacen falta en la vista del CA; el formulario de alta no los usa.
    if (!esHistorial) return actuales;
    return Promise.all([
      actuales,
      apiRequest(`${url}&antiguos=true`, { token }).then((data) => setAntiguos(data.objetivos || [])),
    ]);
  }

  useEffect(() => {
    recargar().catch((err) => setError(err.message)).finally(() => setLoading(false));
  }, [token, advisee.nombre, esHistorial]);

  function objetivoLimpio() {
    return {
      titulo: form.titulo.trim(),
      kpis: form.kpis.trim(),
      descripcion: form.descripcion.trim(),
      tipo: form.tipo.trim(),
    };
  }

  function añadirOtro() {
    if (!form.titulo.trim()) return;
    setPendientes((prev) => [...prev, objetivoLimpio()]);
    setForm({ titulo: "", kpis: "", descripcion: "", tipo: "" });
    setError("");
    setSuccess("");
  }

  function quitarPendiente(idx) {
    setPendientes((prev) => prev.filter((_, i) => i !== idx));
  }

  // Agrupa los objetivos ANTIGUOS por año y, dentro de cada año, por mes. Los actuales
  // van en lista plana: son pocos y están todos vigentes, agruparlos no aporta nada.
  // antiguos ya viene ordenado por fecha descendente desde el backend.
  const antiguosPorAnio = useMemo(() => {
    const anios = new Map(); // anio -> Map(mesIdx -> [obj])
    for (const obj of antiguos) {
      const fecha = obj.fecha || "";
      const anio = fecha.slice(0, 4) || t("common.no_date");
      const mesIdx = fecha.length >= 7 ? parseInt(fecha.slice(5, 7), 10) - 1 : -1;
      if (!anios.has(anio)) anios.set(anio, new Map());
      const meses = anios.get(anio);
      if (!meses.has(mesIdx)) meses.set(mesIdx, []);
      meses.get(mesIdx).push(obj);
    }
    return [...anios.entries()].map(([anio, meses]) => [anio, [...meses.entries()]]);
  }, [antiguos]);

  async function guardar(e) {
    e.preventDefault();
    // Guarda el bloque completo: los objetivos ya añadidos + el que esté en el
    // formulario (aunque no se haya pulsado "Añadir otro"), para no perder datos.
    const aGuardar = form.titulo.trim() ? [...pendientes, objetivoLimpio()] : pendientes;
    if (!aGuardar.length) return;
    setError("");
    setSuccess("");
    setSaving(true);
    try {
      for (const obj of aGuardar) {
        await apiRequest("/api/objetivos", {
          token,
          method: "POST",
          body: { nombre: advisee.nombre, ...obj },
        });
      }
      setForm({ titulo: "", kpis: "", descripcion: "", tipo: "" });
      setPendientes([]);
      // Muestra la animación de éxito y vuelve directamente a la página del advisee.
      setEnviado(true);
      setTimeout(() => onBack(), 1700);
    } catch (err) {
      setError(err.message);
      setSaving(false);
    }
  }

  async function eliminar(page_id) {
    if (!window.confirm(t("goals.confirm_delete"))) return;
    setDeleting(page_id);
    try {
      await apiRequest("/api/objetivos", { token, method: "DELETE", body: { page_id, nombre: advisee.nombre } });
      await recargar();
    } catch (err) {
      setError(err.message);
    } finally {
      setDeleting(null);
    }
  }

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <NavBack onBack={onBack} />
      </nav>
      <div className="profile-wrap" style={{ flex: 1 }}>
        <div className="dash-layout">

          {/* LEFT — perfil del advisee (mismo panel que la página de advisee) */}
          <aside className="dash-profile">
            <p className="eyebrow" style={{ color: "var(--fg)", textAlign: "center", fontWeight: 500, margin: 0 }}>{t("ad.eyebrow")}</p>
            <div className="profile-photo-wrap">
              {advisee.foto
                ? <img src={advisee.foto} alt={advisee.nombre} className="profile-photo" />
                : <div className="profile-photo-placeholder">{advisee.nombre.charAt(0)}</div>
              }
              <div className="profile-id">
                <h1 className="profile-name">{advisee.nombre}</h1>
                {advisee.cargo && <p className="profile-cargo">{advisee.cargo}</p>}
              </div>
            </div>
          </aside>

          {/* RIGHT — introducir objetivos (form) o historial */}
          <div className="dash-main">
            {esHistorial ? (
              <section className="objetivos-historial">
                <p className="kicker">{t("goals.kicker")}</p>
                <h2>{t("goals.of_person", { nombre: advisee.nombre })}</h2>
                {onCambiarVista && (
                  <button className="link-button objetivos-ir-a" onClick={() => onCambiarVista("form")}>
                    {t("goals.go_form", { nombre: advisee.nombre })}
                  </button>
                )}
                {error && <p className="error">{error}</p>}
                {loading ? (
                  <p>{t("common.loading")}</p>
                ) : (
                  <>
                    <h3 className="objetivos-seccion">{t("goals.current")}</h3>
                    {objetivos.length ? (
                      <div className="objetivos-list">
                        {objetivos.map((obj) => (
                          <ObjetivoCard
                            key={obj.page_id}
                            obj={obj}
                            onEliminar={eliminar}
                            eliminando={deleting === obj.page_id}
                          >
                            {obj.descripcion && <p className="objetivo-texto">{obj.descripcion}</p>}
                          </ObjetivoCard>
                        ))}
                      </div>
                    ) : (
                      <p>{t("goals.none_current", { nombre: advisee.nombre })}</p>
                    )}

                    <h3 className="objetivos-seccion">{t("goals.old")}</h3>
                    {antiguos.length ? (
                      <div className="objetivos-anios">
                        {antiguosPorAnio.map(([anio, meses], anioIdx) => (
                          <details key={anio} className="objetivos-anio" open={anioIdx === 0}>
                            <summary className="objetivos-anio-head"><span>{anio}</span></summary>
                            {meses.map(([mesIdx, items], mesPos) => (
                              <details key={mesIdx} className="objetivos-mes" open={mesPos === 0}>
                                <summary className="objetivos-mes-head">{mesIdx >= 0 ? nombreMes(mesIdx) : t("common.no_date")}</summary>
                                <div className="objetivos-list">
                                  {items.map((obj) => (
                                    <ObjetivoCard key={obj.page_id} obj={obj}>
                                      {obj.descripcion && <p className="objetivo-texto">{obj.descripcion}</p>}
                                    </ObjetivoCard>
                                  ))}
                                </div>
                              </details>
                            ))}
                          </details>
                        ))}
                      </div>
                    ) : (
                      <p>{t("goals.none_old")}</p>
                    )}
                  </>
                )}
              </section>
            ) : enviado ? (
              <div style={{ paddingTop: "clamp(24px, 6vw, 64px)" }}>
                <SavedOk text={t("goals.saved_title")} color="#166534" />
              </div>
            ) : (
              <form onSubmit={guardar}>
                <h2>{t("goals.new")}</h2>
                {onCambiarVista && (
                  // type="button": dentro de un <form> el defecto es submit y guardaría.
                  <button type="button" className="link-button objetivos-ir-a" onClick={() => onCambiarVista("historial")}>
                    {t("goals.go_history", { nombre: advisee.nombre })}
                  </button>
                )}
                {error && <p className="error">{error}</p>}
                {success && <p className="fine">{success}</p>}

                {pendientes.length > 0 && (
                  <div className="objetivos-pendientes">
                    {pendientes.map((obj, i) => (
                      <div key={i} className="objetivo-chip">
                        <div className="objetivo-chip-body">
                          <div className="objetivo-chip-titulo">{obj.titulo}</div>
                          {(obj.tipo || obj.kpis) && (
                            <div className="objetivo-chip-meta">
                              {[obj.tipo, obj.kpis].filter(Boolean).join(" · ")}
                            </div>
                          )}
                        </div>
                        <button
                          type="button"
                          className="objetivo-chip-remove"
                          aria-label={t("goals.remove_aria")}
                          onClick={() => quitarPendiente(i)}
                        >
                          ×
                        </button>
                      </div>
                    ))}
                  </div>
                )}

                <label>{t("goals.title_label")}</label>
                <input
                  type="text"
                  value={form.titulo}
                  onChange={(e) => setForm((f) => ({ ...f, titulo: e.target.value }))}
                  placeholder={t("goals.title_ph")}
                />
                <label style={{ marginTop: "12px" }}>{t("goals.type_label")}</label>
                <input
                  type="text"
                  value={form.tipo}
                  onChange={(e) => setForm((f) => ({ ...f, tipo: e.target.value }))}
                  placeholder={t("goals.type_ph")}
                />
                <label style={{ marginTop: "12px" }}>{t("goals.kpis_field_label")}</label>
                <input
                  type="text"
                  value={form.kpis}
                  onChange={(e) => setForm((f) => ({ ...f, kpis: e.target.value }))}
                  placeholder={t("goals.kpis_ph")}
                />
                <label style={{ marginTop: "12px" }}>{t("goals.desc_label")}</label>
                <textarea
                  className="objetivos-textarea"
                  value={form.descripcion}
                  onChange={(e) => setForm((f) => ({ ...f, descripcion: e.target.value }))}
                  rows={5}
                  placeholder={t("goals.desc_ph")}
                />
                <div className="actions">
                  <button type="button" className="secondary" onClick={añadirOtro} disabled={saving || !form.titulo.trim()}>
                    {t("goals.add_another")}
                  </button>
                  <button type="submit" disabled={saving || (!form.titulo.trim() && pendientes.length === 0)}>
                    {saving
                      ? t("common.saving")
                      : (pendientes.length + (form.titulo.trim() ? 1 : 0)) > 1
                        ? t("goals.save_many", { n: pendientes.length + (form.titulo.trim() ? 1 : 0) })
                        : t("goals.save_one")}
                  </button>
                </div>
              </form>
            )}
          </div>
        </div>
      </div>
      <Footer />
    </main>
  );
}

function AuthScreen({ onLogin }) {
  const resetToken = getResetToken();
  const [mode, setMode] = useState(resetToken ? "reset" : "login");
  const [form, setForm] = useState({ username: "", email: "", password: "", confirmPassword: "", newPassword: "", confirmNewPassword: "" });
  const [rememberMe, setRememberMe] = useState(false);
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [loading, setLoading] = useState(false);
  const [maskedEmail, setMaskedEmail] = useState("");
  const passwordToValidate = mode === "reset" ? form.newPassword : mode === "register" ? form.password : "";
  const passwordInvalid = Boolean(passwordToValidate) && !isStrongPassword(passwordToValidate);
  const passwordsMismatch = mode === "reset"
    ? Boolean(form.confirmNewPassword) && form.newPassword !== form.confirmNewPassword
    : mode === "register"
      ? Boolean(form.confirmPassword) && form.password !== form.confirmPassword
      : false;
  const passwordConfirmationMissing = mode === "reset"
    ? !form.confirmNewPassword
    : mode === "register"
      ? !form.confirmPassword
      : false;
  const canSubmit = !loading && !((mode === "reset" || mode === "register") && (!isStrongPassword(passwordToValidate) || passwordConfirmationMissing || passwordsMismatch));

  async function submit(event) {
    event.preventDefault();
    setError("");
    setMessage("");
    if ((mode === "reset" || mode === "register") && !isStrongPassword(passwordToValidate)) {
      setError(t("auth.err_weak_pw"));
      return;
    }
    if ((mode === "reset" && form.newPassword !== form.confirmNewPassword) || (mode === "register" && form.password !== form.confirmPassword)) {
      setError(t("auth.err_pw_mismatch"));
      return;
    }
    setLoading(true);
    try {
      if (mode === "register") {
        // El backend responde VERIFICACION_REQUERIDA:<email> (se captura abajo) y
        // pasamos a pedir el código; la cuenta no se crea hasta confirmarlo.
        await apiRequest("/api/register", { method: "POST", body: form });
        setMode("login");
      } else if (mode === "verify-code") {
        await apiRequest("/api/register/verify", { method: "POST", body: { email: form.email, code: form.verifyCode } });
        setMode("login");
        setMessage(t("auth.account_verified"));
      } else if (mode === "forgot") {
        await apiRequest("/api/password-reset/request", { method: "POST", body: { email: form.email } });
        setMessage(t("auth.forgot_sent"));
      } else if (mode === "reset") {
        await apiRequest("/api/password-reset/confirm", { method: "POST", body: { token: resetToken, password: form.newPassword, confirmPassword: form.confirmNewPassword } });
        localStorage.removeItem("evaluabot_token");
        window.history.replaceState({}, "", window.location.pathname);
        setMode("login");
        setMessage(t("auth.pw_updated"));
      } else {
        const data = await apiRequest("/api/login", { method: "POST", body: { ...form, remember: rememberMe } });
        if (rememberMe) {
          localStorage.setItem("evaluabot_token", data.token);
          sessionStorage.removeItem("evaluabot_token");
        } else {
          sessionStorage.setItem("evaluabot_token", data.token);
          localStorage.removeItem("evaluabot_token");
        }
        onLogin(data.token, data.user);
      }
    } catch (err) {
      if (err.message.startsWith("VERIFICACION_REQUERIDA:")) {
        setMaskedEmail(err.message.split(":")[1] || "");
        setMode("verify-code");
        setError("");
      } else {
        setError(err.message);
      }
    } finally {
      setLoading(false);
    }
  }

  const showBack = mode === "forgot" || mode === "reset" || mode === "verify-code";
  const backToLogin = () => { window.history.replaceState({}, "", window.location.pathname); setError(""); setMessage(""); setForm((f) => ({ ...f, verifyCode: "" })); setMode("login"); };
  const title = mode === "verify-code" ? t("auth.title_verify") : mode === "forgot" ? t("auth.title_forgot") : mode === "reset" ? t("auth.title_reset") : mode === "login" ? t("auth.title_login") : t("auth.title_register");
  const desc = mode === "forgot"
    ? t("auth.desc_forgot")
    : mode === "reset"
      ? t("auth.desc_reset")
      : "";

  return (
    <main className="page auth-page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
      </nav>
      <div style={{ flex: 1, display: "flex", alignItems: "center", justifyContent: "center", padding: "48px 24px" }}>
       <div className="auth-body" style={{ paddingTop: 0 }}>
        {showBack && (
          <button type="button" className="link-button" onClick={backToLogin} style={{ marginBottom: 22 }}>
            {t("auth.back_to_login")}
          </button>
        )}
        <p className="eyebrow">{t("auth.eyebrow")}</p>
        <h1 style={{ fontSize: 30, marginBottom: desc ? 12 : 22 }}>{title}</h1>
        {desc && <p className="fine" style={{ color: "rgba(0,0,0,.6)", marginBottom: 18 }}>{desc}</p>}
        {error && <p className="error" style={{ marginBottom: 12 }}>{error}</p>}
        {message && <p className="fine" style={{ marginBottom: 12 }}>{message}</p>}
        <form onSubmit={submit}>
          {mode === "verify-code" ? (
            <>
              <p className="fine">{t("auth.verify_intro_1")}<strong>{maskedEmail}</strong>{t("auth.verify_intro_2")}</p>
              <label>{t("auth.verify_code_label")}</label>
              <input
                type="text"
                inputMode="numeric"
                maxLength={6}
                value={form.verifyCode}
                onChange={(e) => setForm({ ...form, verifyCode: e.target.value.replace(/\D/g, "") })}
                required
                autoFocus
              />
            </>
          ) : mode === "forgot" ? (
            <>
              <label>Email</label>
              <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
            </>
          ) : mode === "reset" ? (
            <>
              <label>{t("auth.title_reset")}</label>
              <PasswordInput value={form.newPassword} onChange={(e) => setForm({ ...form, newPassword: e.target.value })} minLength={8} />
              <label>{t("auth.repeat_pw")}</label>
              <PasswordInput value={form.confirmNewPassword} onChange={(e) => setForm({ ...form, confirmNewPassword: e.target.value })} minLength={8} />
            </>
          ) : (
            <>
              <label>{mode === "login" ? t("auth.user_or_email") : t("auth.user")}</label>
              <input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required />
              {mode === "register" && (
                <>
                  <label>Email</label>
                  <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
                </>
              )}
              <label>{t("auth.password")}</label>
              <PasswordInput value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} minLength={mode === "register" ? 8 : undefined} />
              {mode === "register" && (
                <>
                  <label>{t("auth.repeat_pw")}</label>
                  <PasswordInput value={form.confirmPassword} onChange={(e) => setForm({ ...form, confirmPassword: e.target.value })} minLength={8} />
                </>
              )}
            </>
          )}
          {mode === "login" && (
            <label className="check-label">
              <input type="checkbox" className="check-input" checked={rememberMe} onChange={(e) => setRememberMe(e.target.checked)} />
              {t("auth.remember")}
            </label>
          )}
          {(mode === "register" || mode === "reset") && (
            <p className={(passwordInvalid || passwordsMismatch) ? "error fine" : "fine"} style={{ marginTop: 12 }}>
              {t("auth.pw_hint")}
            </p>
          )}
          <hr style={{ border: "none", borderTop: "1px solid var(--border)", margin: "20px 0" }} />
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button type="submit" className="btn-pill-primary" disabled={!canSubmit}>
              {loading ? t("auth.processing") : mode === "verify-code" ? t("auth.verify_btn") : mode === "forgot" ? t("auth.send_link") : mode === "reset" ? t("auth.save_pw") : mode === "login" ? t("auth.title_login") : t("auth.title_register")}
            </button>
            {mode === "login" && (
              <button type="button" className="btn-pill-ghost" onClick={() => { setError(""); setMessage(""); setMode("forgot"); }}>
                {t("auth.forgot_link")}
              </button>
            )}
            {showBack && (
              <button type="button" className="btn-pill-ghost" onClick={backToLogin}>
                {t("auth.back_word")}
              </button>
            )}
          </div>
        </form>
        {mode === "login" && (
          <p className="auth-legal">
            {t("auth.legal_1")}<a href="#privacidad">{t("auth.legal_privacy")}</a>{t("auth.legal_2")}<a href="#terminos">{t("auth.legal_terms")}</a>{t("auth.legal_3")}
          </p>
        )}
       </div>
      </div>
    </main>
  );
}

function renderMd(text) {
  return text.split("\n").map((line, li, arr) => {
    const parts = line.split(/(\*[^*]+\*|_[^_]+_)/g);
    return (
      <span key={li}>
        {parts.map((part, pi) => {
          if (part.startsWith("*") && part.endsWith("*") && part.length > 2)
            return <strong key={pi}>{part.slice(1, -1)}</strong>;
          if (part.startsWith("_") && part.endsWith("_") && part.length > 2)
            return <em key={pi}>{part.slice(1, -1)}</em>;
          return <span key={pi}>{part}</span>;
        })}
        {li < arr.length - 1 && <br />}
      </span>
    );
  });
}

function ChatEvalProyecto({ token, user, onComplete, onNavigate }) {
  const persona = user?.persona || user?.username || "";
  const storageKey = `evalproy_${(token || "").slice(-8)}`;
  const GRACE_MS = 2 * 24 * 60 * 60 * 1000;

  const _evalGuardadasIniciales = React.useMemo(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey) || "[]");
      return saved.filter(e => Date.now() - e.ts < GRACE_MS);
    } catch { return []; }
  }, []);
  const _enGraciaAlMontar = _evalGuardadasIniciales.length > 0;

  const [msgs, setMsgs] = React.useState(() => _enGraciaAlMontar
    ? [{ role: "bot", text: t("cep.grace_intro") }]
    : [{ role: "bot", text: t("cep.pending_intro") }]
  );
  const [step, setStep] = React.useState(_enGraciaAlMontar ? "terminado" : "intro");
  const [area, setArea] = React.useState(null);
  const [proyecto, setProyecto] = React.useState("");
  const [evaluadoNombre, setEvaluadoNombre] = React.useState("");
  const [relacion, setRelacion] = React.useState("igual");
  const [preguntas, setPreguntas] = React.useState([]);
  const [preguntaIdx, setPreguntaIdx] = React.useState(0);
  const [respuestas, setRespuestas] = React.useState({});
  const [evaluadosEnSesion, setEvaluadosEnSesion] = React.useState([]);
  const [inputVal, setInputVal] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [empleadosTodos, setEmpleadosTodos] = React.useState([]);
  const [sugerencias, setSugerencias] = React.useState([]);
  const [moEvaluables, setMoEvaluables] = React.useState([]);
  const [modificandoCampo, setModificandoCampo] = React.useState(null);
  // Evaluaciones guardadas en esta sesión con su page_id para el grace period (2 días)
  const [evaluacionesGuardadas, setEvaluacionesGuardadas] = React.useState(() => {
    try {
      const saved = JSON.parse(sessionStorage.getItem(storageKey) || "[]");
      return saved.filter(e => Date.now() - e.ts < GRACE_MS);
    } catch { return []; }
  });
  const [editandoPageId, setEditandoPageId] = React.useState(null);
  const bottomRef = React.useRef(null);

  React.useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);
  React.useEffect(() => {
    apiRequest("/api/todos-empleados", { token }).then(d => setEmpleadosTodos(d.empleados || [])).catch(() => {});
  }, [token]);

  const botSay = (text) => setMsgs(m => [...m, { role: "bot", text }]);
  const userSay = (text) => setMsgs(m => [...m, { role: "user", text }]);

  function buscarSugerencias(texto) {
    if (!texto || texto.length < 2) { setSugerencias([]); return; }
    const norm = texto.toLowerCase();
    setSugerencias(empleadosTodos.filter(e => e.toLowerCase().includes(norm)).slice(0, 5));
  }

  function getResumen(resp, preg) {
    const lines = [t("cep.resumen_head")];
    lines.push(t("cep.resumen_evaluado", { v: resp.evaluado || "" }));
    if (resp.proyecto) lines.push(t("cep.resumen_proyecto", { v: resp.proyecto }));
    for (const q of preg) {
      const label = q.texto.split("\n")[0].replace(/\*/g, "").slice(0, 55);
      lines.push(`- *${label}*: ${resp[q.clave] || ""}`);
    }
    lines.push(t("cep.resumen_satisf"));
    return lines.join("\n");
  }

  function handleComenzar() {
    userSay(t("cep.btn_comenzar"));
    botSay(t("cep.ask_area"));
    setStep("pedir_area");
  }

  async function handleArea(areaVal) {
    const LABELS = { negocio: t("cep.area_negocio"), middleoffice: "MiddleOffice", palantir: "Palantir" };
    userSay(LABELS[areaVal]);
    setArea(areaVal);
    if (areaVal === "middleoffice") {
      setLoading(true);
      try {
        const d = await apiRequest("/api/buscar-empleado-slack?area=middleoffice", { token });
        const lista = d.moEvaluables || [];
        setMoEvaluables(lista);
        setPreguntas(d.preguntas || []);
        setRespuestas({ proyecto: "" });
        botSay(lista.length ? t("cep.ask_who_list", { lista: lista.map(e => `- ${e}`).join("\n") }) : t("cep.ask_who"));
        setSugerencias(lista);
      } catch { botSay(t("cep.ask_who")); }
      finally { setLoading(false); }
      setStep("pedir_persona");
    } else {
      botSay(t("cep.ask_project"));
      setStep("pedir_proyecto");
    }
  }

  function handleProyecto() {
    const val = inputVal.trim();
    if (!val) return;
    userSay(val);
    setProyecto(val);
    setRespuestas({ proyecto: val });
    setInputVal("");
    setSugerencias([]);
    botSay(t("cep.project_ok", { val }));
    setStep("pedir_persona");
  }

  async function handlePersonaSubmit(nombreStr) {
    const nombre = (nombreStr || "").trim();
    if (!nombre) return;
    setSugerencias([]);
    setInputVal("");
    userSay(nombre);
    setLoading(true);
    try {
      const areaActual = area || "negocio";
      const d = await apiRequest(`/api/buscar-empleado-slack?nombre=${encodeURIComponent(nombre)}&area=${areaActual}`, { token });
      if (d.empleado) {
        const clave = `${(respuestas.proyecto || "").toLowerCase()}|${d.empleado.toLowerCase()}`;
        if (evaluadosEnSesion.includes(clave)) {
          botSay(t("cep.already_evaluated", { emp: d.empleado, proy: respuestas.proyecto || "?" }));
          return;
        }
        setEvaluadoNombre(d.empleado);
        setRelacion(d.relacion || "igual");
        const finalPregs = d.preguntas?.length ? d.preguntas : preguntas;
        setPreguntas(finalPregs);
        setPreguntaIdx(0);
        setRespuestas(r => ({ ...r, evaluado: d.empleado }));
        if (finalPregs.length) { botSay(finalPregs[0].texto); setStep("preguntas"); }
        else botSay(t("cep.no_questions"));
      } else if (d.sugerencias?.length) {
        setSugerencias(d.sugerencias);
        botSay(t("cep.not_found_suggest", { nombre, sug: d.sugerencias.map((s, i) => `${i + 1}. ${s}`).join("\n") }));
      } else {
        botSay(t("cep.not_found", { nombre }));
      }
    } catch { botSay(t("cep.err_temp_data")); }
    finally { setLoading(false); }
  }

  function avanzarPregunta(newResp, newPregs, nextIdx) {
    if (nextIdx < newPregs.length) {
      setPreguntaIdx(nextIdx);
      botSay(newPregs[nextIdx].texto);
    } else {
      setPreguntaIdx(0);
      botSay(getResumen(newResp, newPregs));
      setStep("confirmacion");
    }
  }

  function handleValoracion(val) {
    const q = preguntas[preguntaIdx];
    if (!q) return;
    userSay(val);
    const newResp = { ...respuestas, [q.clave]: val };
    setRespuestas(newResp);
    avanzarPregunta(newResp, preguntas, preguntaIdx + 1);
  }

  function handleRespuestaPregunta() {
    const val = inputVal.trim();
    if (!val) return;
    const q = preguntas[preguntaIdx];
    if (!q) return;
    userSay(val);
    setInputVal("");
    const newResp = { ...respuestas, [q.clave]: val };
    setRespuestas(newResp);
    avanzarPregunta(newResp, preguntas, preguntaIdx + 1);
  }

  async function handleConfirmar() {
    userSay(t("cep.save_yes"));
    setLoading(true);
    try {
      const respsClave = Object.fromEntries(Object.entries(respuestas).filter(([k, v]) => k !== "evaluado" && k !== "proyecto" && v));
      if (editandoPageId) {
        await apiRequest("/api/actualizar-evaluacion-slack", {
          token, method: "POST",
          body: { page_id: editandoPageId, evaluado: respuestas.evaluado, proyecto: respuestas.proyecto || "", area: area || "negocio", respuestas: respsClave },
        });
        const updated = evaluacionesGuardadas.map(e =>
          e.page_id === editandoPageId ? { ...e, respuestas: { ...respuestas }, ts: Date.now() } : e
        );
        setEvaluacionesGuardadas(updated);
        try { sessionStorage.setItem(storageKey, JSON.stringify(updated)); } catch {}
        setEditandoPageId(null);
        botSay(t("cep.updated"));
        setStep("preguntar_mas_modificaciones");
      } else {
        const data = await apiRequest("/api/guardar-evaluacion-slack", {
          token, method: "POST",
          body: { evaluado: respuestas.evaluado, proyecto: respuestas.proyecto || "", area: area || "negocio", respuestas: respsClave },
        });
        const nueva = {
          page_id: data.page_id,
          evaluado: respuestas.evaluado,
          proyecto: respuestas.proyecto || "",
          ts: Date.now(),
          respuestas: { ...respuestas },
          area: area || "negocio",
          preguntas: [...preguntas],
        };
        const updated = [...evaluacionesGuardadas, nueva];
        setEvaluacionesGuardadas(updated);
        try { sessionStorage.setItem(storageKey, JSON.stringify(updated)); } catch {}
        const clave = `${(respuestas.proyecto || "").toLowerCase()}|${(respuestas.evaluado || "").toLowerCase()}`;
        setEvaluadosEnSesion(prev => [...prev, clave]);
        botSay(t("cep.saved"));
        setStep("mas_personas");
      }
    } catch (e) { botSay(t("cep.err_save", { msg: e.message || "" })); }
    finally { setLoading(false); }
  }

  function handleElegirModificar(ev) {
    userSay(`✏️ ${ev.evaluado}${ev.proyecto ? ` — ${ev.proyecto}` : ""}`);
    setEditandoPageId(ev.page_id);
    setRespuestas(ev.respuestas);
    setArea(ev.area);
    setPreguntas(ev.preguntas || []);
    botSay(getResumen(ev.respuestas, ev.preguntas || []));
    setStep("confirmar");
  }

  function handleModificar() {
    userSay(t("cep.btn_modificar"));
    const items = [t("cep.mod_item_persona")];
    if (respuestas.proyecto) items.push(t("cep.mod_item_proyecto"));
    const base = respuestas.proyecto ? 3 : 2;
    preguntas.forEach((q, i) => items.push(`${base + i}. ${q.texto.split("\n")[0].replace(/\*/g, "").slice(0, 55)}`));
    botSay(t("cep.ask_which_mod", { items: items.join("\n") }));
    setStep("modificar_menu");
  }

  function handleModificarMenu() {
    const num = parseInt(inputVal.trim());
    if (isNaN(num)) { botSay(t("cep.reply_number")); return; }
    userSay(inputVal.trim());
    setInputVal("");
    let campo = null;
    if (num === 1) campo = "evaluado";
    else if (num === 2 && respuestas.proyecto) campo = "proyecto";
    else {
      const base = respuestas.proyecto ? 3 : 2;
      const idx = num - base;
      if (idx >= 0 && idx < preguntas.length) campo = preguntas[idx].clave;
    }
    if (!campo) { botSay(t("cep.reply_number_range", { max: 2 + (respuestas.proyecto ? 1 : 0) + preguntas.length - (respuestas.proyecto ? 0 : 1) })); return; }
    setModificandoCampo(campo);
    if (campo === "evaluado") botSay(t("cep.enter_person"));
    else if (campo === "proyecto") botSay(t("cep.enter_new_project"));
    else botSay(preguntas.find(q => q.clave === campo)?.texto || t("cep.enter_new_answer"));
    setStep("modificar_valor");
  }

  async function handleModificarValor(val) {
    const v = (val ?? inputVal).trim();
    if (!v) return;
    const campo = modificandoCampo;
    if (campo === "evaluado") {
      setSugerencias([]);
      setInputVal("");
      userSay(v);
      setLoading(true);
      try {
        const d = await apiRequest(`/api/buscar-empleado-slack?nombre=${encodeURIComponent(v)}&area=${area || "negocio"}`, { token });
        if (d.empleado) {
          setEvaluadoNombre(d.empleado);
          setRelacion(d.relacion || "igual");
          const finalPregs = d.preguntas?.length ? d.preguntas : preguntas;
          setPreguntas(finalPregs);
          const newResp = { ...respuestas, evaluado: d.empleado };
          setRespuestas(newResp);
          setModificandoCampo(null);
          botSay(getResumen(newResp, finalPregs));
          setStep("confirmacion");
        } else if (d.sugerencias?.length) {
          setSugerencias(d.sugerencias);
          botSay(t("cep.not_found_suggest2", { v, sug: d.sugerencias.map((s, i) => `${i + 1}. ${s}`).join("\n") }));
        } else {
          botSay(t("cep.not_found2", { v }));
        }
      } catch { botSay(t("cep.err_temp")); }
      finally { setLoading(false); }
    } else {
      const esVal = campo === "q1" || campo === "mo_contribucion";
      if (esVal && !["1","2","3","4","5"].includes(v)) { botSay(t("cep.reply_1_5")); return; }
      userSay(v);
      setInputVal("");
      const newResp = { ...respuestas, [campo]: v };
      if (campo === "proyecto") setProyecto(v);
      setRespuestas(newResp);
      setModificandoCampo(null);
      botSay(getResumen(newResp, preguntas));
      setStep("confirmacion");
    }
  }

  function handleMasPersonas(si) {
    userSay(si ? t("cep.yes") : t("cep.no"));
    if (si) {
      setEvaluadoNombre("");
      setRespuestas(r => ({ proyecto: r.proyecto }));
      setPreguntaIdx(0);
      setSugerencias([]);
      if (area === "middleoffice") {
        botSay(moEvaluables.length ? t("cep.ask_who_list", { lista: moEvaluables.map(e => `- ${e}`).join("\n") }) : t("cep.ask_who_short"));
        setSugerencias(moEvaluables);
      } else {
        botSay(proyecto ? t("cep.ask_other_member_proj", { proy: proyecto }) : t("cep.ask_other_member"));
      }
      setStep("pedir_persona");
    } else if (area === "middleoffice") {
      botSay(t("cep.thanks_close"));
      setStep("terminado");
    } else {
      botSay(t("cep.ask_other_project"));
      setStep("mas_proyectos");
    }
  }

  function handleMasProyectos(si) {
    userSay(si ? t("cep.yes") : t("cep.no"));
    if (si) {
      setProyecto(""); setEvaluadoNombre(""); setRespuestas({}); setPreguntaIdx(0); setSugerencias([]);
      botSay(t("cep.ask_project"));
      setStep("pedir_proyecto");
    } else {
      const modificables = evaluacionesGuardadas.filter(e => Date.now() - e.ts < GRACE_MS);
      if (modificables.length > 0) {
        botSay(t("cep.thanks_grace"));
      } else {
        botSay(t("cep.thanks_close"));
      }
      setStep("terminado");
      onComplete?.();
    }
  }

  const pregActual = preguntas[preguntaIdx];
  const esValoracion = pregActual?.clave === "q1" || pregActual?.clave === "mo_contribucion";
  const esModValoracion = modificandoCampo === "q1" || modificandoCampo === "mo_contribucion";

  function renderInput() {
    if (loading) return <div className="chat-input-area"><div className="chat-input-row"><span className="fine" style={{ color: "var(--muted)" }}>...</span></div></div>;
    if (step === "intro") return (
      <div className="chat-input-area"><div className="chat-btns"><button className="chat-btn primary" onClick={handleComenzar}>{t("cep.btn_comenzar")}</button></div></div>
    );
    if (step === "pedir_area") return (
      <div className="chat-input-area"><div className="chat-btns">
        <button className="chat-btn" onClick={() => handleArea("negocio")}>{t("cep.area_negocio")}</button>
        <button className="chat-btn" onClick={() => handleArea("middleoffice")}>MiddleOffice</button>
        <button className="chat-btn" onClick={() => handleArea("palantir")}>Palantir</button>
      </div></div>
    );
    if (step === "pedir_proyecto") return (
      <div className="chat-input-area"><div className="chat-input-row">
        <input className="chat-input" placeholder={t("cep.ph_project")} value={inputVal} onChange={e => setInputVal(e.target.value)} onKeyDown={e => e.key === "Enter" && handleProyecto()} autoFocus />
        <button className="chat-send-btn" onClick={handleProyecto}>→</button>
      </div></div>
    );
    if (step === "pedir_persona") return (
      <div className="chat-input-area">
        {sugerencias.length > 0 && <div className="chat-sugerencias">{sugerencias.map(s => <button key={s} className="chat-btn" onClick={() => { setSugerencias([]); handlePersonaSubmit(s); }}>{s}</button>)}</div>}
        <div className="chat-input-row">
          <input className="chat-input" placeholder={t("cep.ph_person")} value={inputVal} onChange={e => { setInputVal(e.target.value); buscarSugerencias(e.target.value); }} onKeyDown={e => e.key === "Enter" && handlePersonaSubmit(inputVal)} autoFocus />
          <button className="chat-send-btn" onClick={() => handlePersonaSubmit(inputVal)}>→</button>
        </div>
      </div>
    );
    if (step === "preguntas") {
      if (esValoracion) return (
        <div className="chat-input-area"><div className="chat-btns">{[1,2,3,4,5].map(n => <button key={n} className="chat-btn" onClick={() => handleValoracion(String(n))}>{n}</button>)}</div></div>
      );
      return (
        <div className="chat-input-area"><div className="chat-input-row">
          <textarea className="chat-input chat-textarea" placeholder={t("cep.ph_answer")} value={inputVal} onChange={e => setInputVal(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleRespuestaPregunta(); } }} rows={2} autoFocus />
          <button className="chat-send-btn" onClick={handleRespuestaPregunta}>→</button>
        </div></div>
      );
    }
    if (step === "confirmacion") return (
      <div className="chat-input-area"><div className="chat-btns">
        <button className="chat-btn primary" onClick={handleConfirmar}>{t("cep.save_yes")}</button>
        <button className="chat-btn" onClick={handleModificar}>{t("cep.btn_modificar")}</button>
      </div></div>
    );
    if (step === "modificar_menu") return (
      <div className="chat-input-area"><div className="chat-input-row">
        <input className="chat-input" placeholder={t("cep.ph_field_number")} value={inputVal} onChange={e => setInputVal(e.target.value)} onKeyDown={e => e.key === "Enter" && handleModificarMenu()} autoFocus />
        <button className="chat-send-btn" onClick={handleModificarMenu}>→</button>
      </div></div>
    );
    if (step === "modificar_valor") {
      if (sugerencias.length > 0) return (
        <div className="chat-input-area">
          <div className="chat-sugerencias">{sugerencias.map(s => <button key={s} className="chat-btn" onClick={() => { setSugerencias([]); handleModificarValor(s); }}>{s}</button>)}</div>
          <div className="chat-input-row">
            <input className="chat-input" placeholder={t("cep.ph_or_name")} value={inputVal} onChange={e => setInputVal(e.target.value)} onKeyDown={e => e.key === "Enter" && handleModificarValor(inputVal)} autoFocus />
            <button className="chat-send-btn" onClick={() => handleModificarValor(inputVal)}>→</button>
          </div>
        </div>
      );
      if (esModValoracion) return (
        <div className="chat-input-area"><div className="chat-btns">{[1,2,3,4,5].map(n => <button key={n} className="chat-btn" onClick={() => handleModificarValor(String(n))}>{n}</button>)}</div></div>
      );
      return (
        <div className="chat-input-area"><div className="chat-input-row">
          <input className="chat-input" placeholder={t("cep.ph_new_answer")} value={inputVal} onChange={e => setInputVal(e.target.value)} onKeyDown={e => e.key === "Enter" && handleModificarValor(inputVal)} autoFocus />
          <button className="chat-send-btn" onClick={() => handleModificarValor(inputVal)}>→</button>
        </div></div>
      );
    }
    if (step === "mas_personas") return (
      <div className="chat-input-area"><div className="chat-btns">
        <button className="chat-btn primary" onClick={() => handleMasPersonas(true)}>{t("cep.yes")}</button>
        <button className="chat-btn" onClick={() => handleMasPersonas(false)}>{t("cep.no")}</button>
      </div></div>
    );
    if (step === "mas_proyectos") return (
      <div className="chat-input-area"><div className="chat-btns">
        <button className="chat-btn primary" onClick={() => handleMasProyectos(true)}>{t("cep.yes")}</button>
        <button className="chat-btn" onClick={() => handleMasProyectos(false)}>{t("cep.no")}</button>
      </div></div>
    );
    if (step === "terminado") {
      const modificables = evaluacionesGuardadas.filter(e => Date.now() - e.ts < GRACE_MS);
      return (
        <div className="chat-input-area">
          <div className="chat-btns">
            <span className="fine" style={{ color: "var(--muted)" }}>{t("cep.completed")}</span>
            {modificables.length > 0 && (
              <button className="chat-btn" onClick={() => {
                botSay(t("cep.ask_whose_mod"));
                setStep("elegir_modificar");
              }}>{t("cep.btn_mod_answers")}</button>
            )}
          </div>
        </div>
      );
    }
    if (step === "elegir_modificar") {
      const modificables = evaluacionesGuardadas.filter(e => Date.now() - e.ts < GRACE_MS);
      return (
        <div className="chat-input-area"><div className="chat-btns">
          {modificables.map((ev, i) => (
            <button key={i} className="chat-btn" onClick={() => handleElegirModificar(ev)}>
              {ev.evaluado}{ev.proyecto ? ` — ${ev.proyecto}` : ""}
            </button>
          ))}
        </div></div>
      );
    }
    if (step === "preguntar_mas_modificaciones") {
      const modificables = evaluacionesGuardadas.filter(e => Date.now() - e.ts < GRACE_MS);
      return (
        <div className="chat-input-area"><div className="chat-btns">
          {modificables.length > 0 && (
            <button className="chat-btn primary" onClick={() => {
              botSay(t("cep.ask_whose_mod"));
              setStep("elegir_modificar");
            }}>{t("cep.yes")}</button>
          )}
          <button className="chat-btn" onClick={() => {
            botSay(t("cep.bye"));
            setStep("terminado");
          }}>{t("cep.no")}</button>
        </div></div>
      );
    }
    return null;
  }

  return (
    <div className="eval-chat-area">
      <div className="chat-msgs">
        {msgs.map((msg, i) => (
          <div key={i} className={`chat-msg-${msg.role}`}>
            {msg.role === "bot"
              ? <><span className="chat-avatar">🤖</span><div className="chat-bubble-bot">{renderMd(msg.text)}</div></>
              : <div className="chat-bubble-user">{msg.text}</div>
            }
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      {renderInput()}
    </div>
  );
}

function ChatEvalPersonal({ token, user, onComplete }) {
  const persona = user?.persona || user?.username || "";
  const [msgs, setMsgs] = React.useState([{
    role: "bot",
    text: "📝 *Seguimiento personal*\n\n_Esta evaluación es totalmente privada, solo podrá verla tu CA._\n_Si en algún momento quieres cancelar, cierra esta sección._\n\n*Pulsa el botón* para comenzar.",
  }]);
  const [step, setStep] = React.useState("intro");
  const [comentario, setComentario] = React.useState("");
  const [inputVal, setInputVal] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const bottomRef = React.useRef(null);

  React.useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);

  const botSay = (text) => setMsgs(m => [...m, { role: "bot", text }]);
  const userSay = (text) => setMsgs(m => [...m, { role: "user", text }]);

  function handleComenzar() {
    userSay("Comenzar");
    botSay("*Esta es tu oportunidad para:*\n\n*1.* Explicar cómo estás ayudando en _\"Contribution to the firm\"_\n*2.* Cómo te estás acercando a tus objetivos\n*3.* Señalar limitaciones o aspectos relevantes respecto al cumplimiento de los criterios de evaluación\n*4.* Si necesitas ayuda con algún tema o has tenido alguna dificultad que quieras comentar\n\nYa puedes escribir tu comentario.");
    setStep("esperando_comentario");
  }

  async function handleVerObjetivos() {
    try {
      const d = await apiRequest(`/api/objetivos?nombre=${encodeURIComponent(persona)}`, { token });
      const objs = d.objetivos || [];
      if (objs.length) {
        const lineas = objs.map(o => `• *${o.titulo}*${o.kpis ? `\n  _KPIs: ${o.kpis}_` : ""}`).join("\n");
        botSay(`📌 *Tus objetivos actuales:*\n\n${lineas}`);
      } else {
        botSay("📌 No tienes objetivos registrados actualmente.");
      }
    } catch (e) { botSay(`⚠️ No se pudieron cargar los objetivos: ${e.message || "Error desconocido"}`); }
  }

  function handleVerCriterios() {
    userSay("📊 Ver criterios");
    botSay("¿Para qué área quieres ver los criterios?");
    setStep("criterios_grupo");
  }

  async function handleCriteriosGrupo(grupo) {
    const labels = { negocio: "Negocio", palantir: "Palantir", middleoffice: "Middle Office" };
    userSay(labels[grupo] || grupo);
    setLoading(true);
    try {
      const d = await apiRequest(`/api/criterios-evaluacion?grupo=${encodeURIComponent(grupo)}`, { token });
      const criterios = d.criterios || {};
      const entries = Object.entries(criterios);
      if (!entries.length) {
        botSay("📊 No hay criterios disponibles para este área.");
      } else {
        const texto = `📊 *Criterios — ${labels[grupo] || grupo}*\n\n` +
          entries.map(([dim, niveles]) =>
            `*${dim}*\n` + Object.entries(niveles).map(([n, ts]) => `  _${n}:_ ${Array.isArray(ts) ? ts.join(". ") : ts}`).join("\n")
          ).join("\n\n");
        botSay(texto);
      }
    } catch (e) {
      botSay(`⚠️ No se pudieron cargar los criterios: ${e.message || "Error desconocido"}`);
    } finally {
      setLoading(false);
    }
    setStep("esperando_comentario");
  }

  function handleComentario() {
    const val = inputVal.trim();
    if (!val) return;
    userSay(val);
    setComentario(val);
    setInputVal("");
    botSay(`📋 Tu comentario:\n_${val}_\n\n¿Lo guardo?`);
    setStep("confirmacion");
  }

  async function handleConfirmar() {
    userSay(t("cep.save_yes"));
    setLoading(true);
    try {
      await apiRequest("/api/guardar-evaluacion-personal", { token, method: "POST", body: { comentario } });
      botSay("✅ Evaluación guardada. ¿Quieres añadir otro comentario?");
      setStep("preguntando_otro");
    } catch (e) { botSay(`⚠️ No se pudo guardar: ${e.message || "Error desconocido"}`); }
    finally { setLoading(false); }
  }

  function handleModificar() {
    userSay("✏️ Modificar");
    setComentario("");
    botSay("Escribe de nuevo tu comentario:");
    setStep("esperando_comentario");
  }

  function handleOtroSi() {
    userSay("✅ Sí");
    setComentario("");
    setInputVal("");
    botSay("¿Qué más me quieres contar? Responde con tu comentario.");
    setStep("esperando_comentario");
  }

  function handleOtroNo() {
    userSay("❌ No");
    botSay("Muchas gracias. Ya puedes cerrar esta sección 👋");
    setStep("terminado");
    onComplete?.();
  }

  function renderInput() {
    if (loading) return <div className="chat-input-area"><div className="chat-input-row"><span className="fine" style={{ color: "var(--muted)" }}>...</span></div></div>;
    if (step === "intro") return (
      <div className="chat-input-area"><div className="chat-btns"><button className="chat-btn primary" onClick={handleComenzar}>Comenzar</button></div></div>
    );
    if (step === "esperando_comentario") return (
      <div className="chat-input-area">
        <div className="chat-btns" style={{ marginBottom: "8px" }}>
          <button className="chat-btn" onClick={handleVerObjetivos}>📋 Ver mis objetivos</button>
          <button className="chat-btn" onClick={handleVerCriterios}>📊 Ver criterios</button>
        </div>
        <div className="chat-input-row">
          <textarea className="chat-input chat-textarea" placeholder="Escribe tu comentario..." value={inputVal} onChange={e => setInputVal(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleComentario(); } }} rows={3} autoFocus />
          <button className="chat-send-btn" onClick={handleComentario}>→</button>
        </div>
      </div>
    );
    if (step === "criterios_grupo") return (
      <div className="chat-input-area"><div className="chat-btns">
        <button className="chat-btn" onClick={() => handleCriteriosGrupo("negocio")}>Negocio</button>
        <button className="chat-btn" onClick={() => handleCriteriosGrupo("palantir")}>Palantir</button>
        <button className="chat-btn" onClick={() => handleCriteriosGrupo("middleoffice")}>Middle Office</button>
      </div></div>
    );
    if (step === "confirmacion") return (
      <div className="chat-input-area"><div className="chat-btns">
        <button className="chat-btn primary" onClick={handleConfirmar}>{t("cep.save_yes")}</button>
        <button className="chat-btn" onClick={handleModificar}>{t("cep.btn_modificar")}</button>
      </div></div>
    );
    if (step === "preguntando_otro") return (
      <div className="chat-input-area"><div className="chat-btns">
        <button className="chat-btn primary" onClick={handleOtroSi}>{t("cep.yes")}</button>
        <button className="chat-btn" onClick={handleOtroNo}>{t("cep.no")}</button>
      </div></div>
    );
    if (step === "terminado") return (
      <div className="chat-input-area"><span className="fine" style={{ color: "var(--muted)" }}>Evaluación completada ✅</span></div>
    );
    return null;
  }

  return (
    <div className="eval-chat-area">
      <div className="chat-msgs">
        {msgs.map((msg, i) => (
          <div key={i} className={`chat-msg-${msg.role}`}>
            {msg.role === "bot"
              ? <><span className="chat-avatar">🤖</span><div className="chat-bubble-bot">{renderMd(msg.text)}</div></>
              : <div className="chat-bubble-user">{msg.text}</div>
            }
          </div>
        ))}
        <div ref={bottomRef} />
      </div>
      {renderInput()}
    </div>
  );
}

function HistorialEvaluacionesPage({ token, evaluado, evaluador, proyecto, onBack }) {
  const [historial, setHistorial] = React.useState(null);
  const [error, setError] = React.useState(null);

  React.useEffect(() => {
    apiRequest(
      `/api/historial-evaluaciones?evaluado=${encodeURIComponent(evaluado)}&evaluador=${encodeURIComponent(evaluador)}&proyecto=${encodeURIComponent(proyecto || "")}`,
      { token }
    )
      .then(d => setHistorial(d.historial || []))
      .catch(() => setError(t("hist.err_load")));
  }, [token, evaluado, evaluador, proyecto]);

  function formatFecha(iso) {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleDateString(getLang() === "en" ? "en-GB" : "es-ES", { day: "2-digit", month: "short", year: "numeric" });
    } catch { return iso.slice(0, 10); }
  }

  const RELACION_LABELS = { superior: t("hist.rel_superior"), igual: t("hist.rel_equal"), inferior: t("hist.rel_lower") };

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <NavBack onBack={onBack} />
      </nav>
      <div className="historial-page">
        <p className="kicker">{t("hist.title")}</p>
        <h1 className="historial-title">{evaluado}</h1>
        <p className="fine historial-subtitle">{t("hist.project_label")} <strong>{proyecto || "—"}</strong></p>
        {error && <p className="historial-empty">{error}</p>}
        {historial === null && !error && <p className="fine" style={{ opacity: 0.5 }}>{t("common.loading")}</p>}
        {historial?.length === 0 && (
          <p className="historial-empty">{t("hist.empty")}</p>
        )}
        {historial?.length > 0 && (
          <div className="historial-tabla-wrap">
            <table className="historial-tabla">
              <thead>
                <tr>
                  <th>{t("hist.col_date")}</th>
                  <th>{t("hist.col_project")}</th>
                  <th>{t("hist.col_score")}</th>
                  <th>{t("hist.col_justif")}</th>
                  <th>{t("hist.col_relation")}</th>
                </tr>
              </thead>
              <tbody>
                {historial.map((ev, i) => (
                  <tr key={i}>
                    <td className="hist-fecha">{formatFecha(ev.fecha)}</td>
                    <td className="hist-proyecto">{ev.proyecto || <span style={{ opacity: 0.35 }}>—</span>}</td>
                    <td className="hist-valo">
                      {ev.q1
                        ? <span className={`hist-valo-badge valo-${ev.q1}`}>{ev.q1}</span>
                        : <span className="hist-valo-badge valo-x">—</span>
                      }
                    </td>
                    <td className="hist-texto">{ev.q2 || <span style={{ opacity: 0.35 }}>—</span>}</td>
                    <td className="hist-relacion">
                      {ev.relacion
                        ? <span className={`hist-rel-badge rel-${ev.relacion}`}>{RELACION_LABELS[ev.relacion] || ev.relacion}</span>
                        : <span style={{ opacity: 0.35 }}>—</span>
                      }
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
      <Footer />
    </main>
  );
}

const LABEL_QA = {
  fontSize: 11, letterSpacing: ".1em", textTransform: "uppercase",
  color: "var(--text-55)", fontWeight: 400, flexShrink: 0, whiteSpace: "nowrap", minWidth: 88,
};

function DetalleEvaluacionRealizadaPage({ ev, proyecto, onBack }) {
  // `ev.respuestas` viene del backend como "pregunta: respuesta" por línea
  // (ver _formatear_respuestas). Partimos por el primer ": " para no romper
  // respuestas que contengan ":".
  const lineas = (ev?.respuestas || "")
    .split("\n")
    .map((l) => l.trim())
    .filter(Boolean)
    .map((l) => {
      const idx = l.indexOf(": ");
      return idx === -1
        ? { pregunta: l, respuesta: "" }
        : { pregunta: l.slice(0, idx), respuesta: l.slice(idx + 2) };
    });

  function formatFecha(iso) {
    if (!iso) return "";
    try {
      return new Date(iso).toLocaleDateString(getLang() === "en" ? "en-GB" : "es-ES", { day: "2-digit", month: "short", year: "numeric" });
    } catch { return iso.slice(0, 10); }
  }

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <NavBack onBack={onBack} />
      </nav>
      <div className="historial-page">
        <p className="kicker">{proyecto || ev?.proyecto || ""}</p>
        <h1 className="historial-title">{(ev?.tipo || "").split(" ")[0]}{ev?.evaluado ? ` · ${ev.evaluado}` : ""}</h1>
        {ev?.fecha && <p className="fine historial-subtitle">{formatFecha(ev.fecha)}</p>}
        {lineas.length === 0 && <p className="historial-empty">{t("dash.finished_project_empty")}</p>}
        {lineas.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 20 }}>
            {lineas.map((l, i) => {
              const resp = (l.respuesta || "").trim();
              // Respuesta breve (numérica o categórica) → a la derecha de "RESPUESTA".
              // Respuesta abierta (texto largo) → debajo.
              const esNumerica = /^\d+([.,/]\d+)?$/.test(resp);
              const esCorta = resp.length <= 24 && resp.split(/\s+/).length <= 3;
              const inline = resp && (esNumerica || esCorta);
              return (
                <div key={i} style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", padding: "16px 18px", background: "var(--bg)" }}>
                  <div style={{ display: "flex", gap: 14, alignItems: "baseline" }}>
                    <span style={LABEL_QA}>{t("det.question")}</span>
                    <span style={{ fontSize: 15, fontWeight: 500, color: "#000", lineHeight: 1.5, minWidth: 0 }}>{l.pregunta}</span>
                  </div>
                  {inline ? (
                    <div style={{ display: "flex", gap: 14, alignItems: "baseline", marginTop: 12 }}>
                      <span style={LABEL_QA}>{t("det.answer")}</span>
                      <span style={{ fontSize: 15, fontWeight: 400, color: "#000", lineHeight: 1.5, minWidth: 0 }}>{resp}</span>
                    </div>
                  ) : (
                    <div style={{ marginTop: 12 }}>
                      <p style={{ ...LABEL_QA, margin: 0 }}>{t("det.answer")}</p>
                      <p style={{ fontSize: 15, fontWeight: 400, color: "#000", margin: "4px 0 0", whiteSpace: "pre-wrap", lineHeight: 1.5 }}>{resp || "—"}</p>
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </div>
      <Footer />
    </main>
  );
}

function EvaluacionesSlackSection({ token, user, onNavigate, onCompletada }) {
  // sessionStorage persiste dentro de la misma sesión del navegador (sobrevive navegar atrás/adelante).
  // Clave incluye los últimos 8 chars del token para que sea específica del usuario.
  const storageKey = `eval_completadas_${(token || "").slice(-8)}`;

  const [estadoCiclo, setEstadoCiclo] = React.useState(null);
  const [tipoActivo, setTipoActivo] = React.useState(null);
  const [completadas, setCompletadas] = React.useState(() => {
    try { return JSON.parse(sessionStorage.getItem(storageKey) || "{}"); } catch { return {}; }
  });
  // Ref para saber si el usuario ya empezó a interactuar antes de que llegue la API.
  // Si ya empezó, ignoramos la respuesta de la API para no cambiar ticks mid-conversación.
  const interactuoRef = React.useRef(false);

  React.useEffect(() => {
    apiRequest("/api/estado-ciclo-slack", { token })
      .then(d => {
        setEstadoCiclo(d);
        if (!interactuoRef.current) {
          // Merge: si la API dice que algo está hecho, se marca como hecho.
          // Lo que ya estaba marcado en sesión se mantiene (por si la API tiene lag o falla).
          setCompletadas(prev => {
            const apiComp = d.completadas || {};
            const merged = { ...prev };
            Object.entries(apiComp).forEach(([k, v]) => { if (v) merged[k] = true; });
            return merged;
          });
        }
      })
      .catch(() => setEstadoCiclo({ cicloActivo: true, completadas: {} }));
  }, [token]);

  const tipos = [
    { key: "proyecto", label: t("ess.tab_monthly"), disponible: true },
    { key: "personal", label: t("ess.tab_personal"), disponible: true },
  ];

  // Comprobar si hay evaluaciones mensuales en periodo de gracia (2 días)
  const proyectoEnGracia = React.useMemo(() => {
    try {
      const key = `evalproy_${(token || "").slice(-8)}`;
      const saved = JSON.parse(sessionStorage.getItem(key) || "[]");
      return saved.some(e => Date.now() - e.ts < 2 * 24 * 60 * 60 * 1000);
    } catch { return false; }
  }, [token]);

  function handleTabClick(key) {
    interactuoRef.current = true;
    setTipoActivo(key);
  }

  function marcarCompletada(key) {
    setCompletadas(c => {
      const next = { ...c, [key]: true };
      try { sessionStorage.setItem(storageKey, JSON.stringify(next)); } catch {}
      return next;
    });
    onCompletada?.(key);
  }

  return (
    <div>
      <p className="fine" style={{ marginBottom: "24px" }}>
        {t("ess.intro")}
      </p>
      <div className="eval-slack-layout">
        <nav className="eval-tipos">
          {tipos.map(tipo => {
            const enGracia = tipo.key === "proyecto" && completadas[tipo.key] && proyectoEnGracia;
            const bloqueada = !tipo.disponible || (completadas[tipo.key] && !enGracia);
            return (
            <button
              key={tipo.key}
              className={`eval-tipo-btn${tipoActivo === tipo.key ? " active" : ""}${completadas[tipo.key] && !enGracia ? " completada" : ""}`}
              onClick={() => { if (!bloqueada) handleTabClick(tipo.key); }}
              disabled={bloqueada}
              title={completadas[tipo.key] && !enGracia ? t("ess.tip_done") : enGracia ? t("ess.tip_editable") : !tipo.disponible ? t("ess.tip_soon") : ""}
            >
              <span>{tipo.label}</span>
              {completadas[tipo.key] && !enGracia
                ? <span className="eval-tick">✅</span>
                : enGracia
                  ? <span className="eval-tick" title={t("ess.editable")}>✏️</span>
                  : !tipo.disponible
                    ? <span className="eval-tick" style={{ fontSize: "11px", opacity: 0.4 }}>{t("ess.soon_short")}</span>
                    : null
              }
            </button>
            );
          })}
        </nav>
        <div style={{ minHeight: "500px", display: "flex", flexDirection: "column" }}>
          {tipoActivo === "proyecto"
            ? <ChatEvalProyecto key="proyecto" token={token} user={user} onComplete={() => marcarCompletada("proyecto")} onNavigate={onNavigate} />
            : tipoActivo === "personal"
              ? <ChatEvalPersonal key="personal" token={token} user={user} onComplete={() => marcarCompletada("personal")} />
              : <div className="eval-chat-area"><div className="eval-placeholder"><p className="fine">{t("ess.select_type")}</p></div></div>
          }
        </div>
      </div>
    </div>
  );
}

function DashNavItem({ label, onClick, disabled, external = false, download = false }) {
  const [hover, setHover] = useState(false);
  return (
    <div
      role="button"
      tabIndex={0}
      onClick={disabled ? undefined : onClick}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
      style={{
        display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8,
        padding: "6px 0", fontSize: 14, fontWeight: 400,
        cursor: disabled ? "default" : "pointer",
        color: disabled ? "rgba(0,0,0,.3)" : hover ? "var(--accent)" : "#000",
        transition: "color .15s", userSelect: "none",
      }}
    >
      <span><span className="dash-dot" />{label}</span>
      {external && (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13, flexShrink: 0 }}><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /><polyline points="15 3 21 3 21 9" /><line x1="10" y1="14" x2="21" y2="3" /></svg>
      )}
      {download && (
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13, flexShrink: 0 }}><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4" /><polyline points="7 10 12 15 17 10" /><line x1="12" y1="15" x2="12" y2="3" /></svg>
      )}
    </div>
  );
}

function DashCollapsible({ title, open, onToggle, children, badge = null, bodyMarginTop = 10, hint = null }) {
  return (
    <div>
      <div onClick={onToggle} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", cursor: "pointer", userSelect: "none" }}>
        <span className="eyebrow" style={{ marginBottom: 0, fontSize: "0.7rem" }}>
          {title}
        </span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          {badge != null && (
            <span style={{
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              minWidth: 20, height: 20, padding: "0 5px", borderRadius: 4,
              background: "rgba(242,60,20,.12)", color: "var(--accent)", fontWeight: 500,
              fontSize: 11, whiteSpace: "nowrap",
            }}>{badge}</span>
          )}
          <svg viewBox="0 0 24 24" fill="none" stroke="#000" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            style={{ width: 11, height: 11, flexShrink: 0, transform: open ? "rotate(180deg)" : "rotate(0deg)", transition: "transform .25s" }}>
            <polyline points="18 15 12 9 6 15" />
          </svg>
        </span>
      </div>
      {open && <div style={{ marginTop: bodyMarginTop }}>{children}</div>}
    </div>
  );
}

const DASH_DIVIDER = { border: "none", borderTop: "1px solid var(--border)", margin: "10px 0" };
const PAISES_PERMITIDOS = ["España", "México", "Portugal"];

function Dashboard({ token, user, onLogout, onNavigate, onBackToRoleSelect = null }) {
  const [evaluados, setEvaluados] = useState([]);
  const [evaluado, setEvaluado] = useState("");
  const [status, setStatus] = useState("");
  const [links, setLinks] = useState(null);
  const [advisees, setAdvisees] = useState([]);
  const [informeFinalEmpleado, setInformeFinalEmpleado] = useState(null);
  const [adminModo, setAdminModo] = useState("borrador");
  const [informeFinalAdmin, setInformeFinalAdmin] = useState(null);
  // En modo "Perfil personal" (onBackToRoleSelect activo) el admin debe verse y
  // comportarse igual que cualquier otro empleado, con los mismos botones del To-do.
  const isAdmin = Boolean(user?.is_admin) && !onBackToRoleSelect;
  const [perfil, setPerfil] = useState({ foto: "", cargo: "", pais: "" });
  const [editandoPais, setEditandoPais] = useState(false);
  const [paisSel, setPaisSel] = useState("");
  const [paisGuardando, setPaisGuardando] = useState(false);
  const [paisMsg, setPaisMsg] = useState("");
  const [misObjetivos, setMisObjetivos] = useState([]);
  const [objEliminando, setObjEliminando] = useState(null);
  const [objError, setObjError] = useState("");
  const [informesOpen, setInformesOpen] = useState(false);
  const [objOpen, setObjOpen] = useState(false);
  const [tareasOpen, setTareasOpen] = useState(false);
  const [projOpen, setProjOpen] = useState(false);
  const [extraEvalOpen, setExtraEvalOpen] = useState(false);
  const [terminadosOpen, setTerminadosOpen] = useState(false);
  const [proyectosTerminados, setProyectosTerminados] = useState(null); // null = aún no cargado
  const [terminadosCargando, setTerminadosCargando] = useState(false);
  const [proyTerminadoAbierto, setProyTerminadoAbierto] = useState(null); // nombre del proyecto expandido
  const [seccionActiva, setSeccionActiva] = useState(null);
  const [proyectosActivos, setProyectosActivos] = useState([]);
  const [proyectosManager, setProyectosManager] = useState(null);
  const [proyectosVersion, setProyectosVersion] = useState(0);
  const [proyectosProgreso, setProyectosProgreso] = useState({});
  const [tareasProyecto, setTareasProyecto] = useState([]);
  const [tareasSlack, setTareasSlack] = useState({ pendientes: [], url: "" });
  const [evaluacionesExtraPendientes, setEvaluacionesExtraPendientes] = useState([]);
  // Evaluaciones de proyecto RECIBIDAS y liberadas para la persona (solo top-to-bottom,
  // de alguien por encima en la jerarquía de empresa). Las bottom-to-top nunca llegan aquí.
  const [evalsRecibidas, setEvalsRecibidas] = useState(null);

  useEffect(() => {
    if (isAdmin) { setEvalsRecibidas([]); return; }
    let cancelado = false;
    // Cacheado en sessionStorage: pinta al instante lo último conocido y refresca
    // en segundo plano (recorrer los proyectos de Notion tarda).
    apiRequestCached(
      "/api/mis-evaluaciones-proyecto-recibidas",
      { token },
      (fresh) => { if (!cancelado) setEvalsRecibidas(fresh.evaluaciones || []); },
    )
      .then((d) => { if (!cancelado) setEvalsRecibidas(d.evaluaciones || []); })
      .catch(() => { if (!cancelado) setEvalsRecibidas([]); });
    return () => { cancelado = true; };
  }, [token, isAdmin]);

  useEffect(() => {
    const apply = (data) => { setEvaluados(data.evaluados || []); setEvaluado(data.evaluados?.[0]?.value || ""); };
    apiRequestCached("/api/evaluados", { token }, apply)
      .then(apply)
      .catch((err) => setStatus(err.message));
  }, [token]);

  useEffect(() => {
    const apply = (data) => setAdvisees(data.advisees || []);
    apiRequestCached("/api/mis-advisees", { token }, apply)
      .then(apply)
      .catch(() => {});
  }, [token]);

  useEffect(() => {
    if (isAdmin) return;
    const persona = user?.persona || "";
    if (!persona) return;
    const path = `/api/informe-final?evaluado=${encodeURIComponent(persona)}`;
    const apply = (data) => setInformeFinalEmpleado(data);
    apiRequestCached(path, { token }, apply)
      .then(apply)
      .catch(() => setInformeFinalEmpleado({ disponible: false, mensaje: t("admin.err_load_report") }));
  }, [token, isAdmin, user?.persona]);

  useEffect(() => {
    if (!isAdmin || adminModo !== "final" || !evaluado) return;
    setInformeFinalAdmin(null);
    apiRequest(`/api/informe-final?evaluado=${encodeURIComponent(evaluado)}`, { token })
      .then((data) => setInformeFinalAdmin(data))
      .catch(() => setInformeFinalAdmin({ disponible: false, mensaje: t("admin.err_load_report") }));
  }, [token, isAdmin, adminModo, evaluado]);

  useEffect(() => {
    const apply = (data) => setPerfil(data);
    apiRequestCached("/api/mi-perfil", { token }, apply)
      .then(apply)
      .catch(() => {});
  }, [token]);

  function abrirEdicionPais() {
    const actual = perfil.pais || "";
    setPaisSel(PAISES_PERMITIDOS.includes(actual) ? actual : "");
    setPaisMsg("");
    setEditandoPais(true);
  }

  async function guardarPais() {
    const valor = paisSel.trim();
    if (!valor) return;
    setPaisGuardando(true);
    setPaisMsg("");
    try {
      const data = await apiRequest("/api/set-pais", { token, method: "POST", body: { pais: valor } });
      const nuevo = data.pais || valor;
      setPerfil((p) => ({ ...p, pais: nuevo }));
      clearApiCache();
      setEditandoPais(false);
      setPaisMsg(t("dash.country_saved"));
    } catch {
      setPaisMsg(t("dash.country_error"));
    } finally {
      setPaisGuardando(false);
    }
  }

  useEffect(() => {
    const persona = user?.persona;
    if (!persona) return;
    const path = `/api/objetivos?nombre=${encodeURIComponent(persona)}`;
    const apply = (data) => setMisObjetivos(data.objetivos || []);
    apiRequestCached(path, { token }, apply)
      .then(apply)
      .catch(() => {});
  }, [token, user?.persona]);

  // Cerrar un objetivo propio: pasa a los antiguos, que ve el CA.
  async function eliminarMiObjetivo(page_id) {
    const persona = user?.persona;
    if (!persona || !window.confirm(t("goals.confirm_delete"))) return;
    setObjError("");
    setObjEliminando(page_id);
    try {
      await apiRequest("/api/objetivos", { token, method: "DELETE", body: { page_id, nombre: persona } });
      // Esta lista se sirve desde sessionStorage (SWR), así que hay que refrescar
      // también la caché: si no, al remontar el dashboard reaparecería el cerrado.
      const path = `/api/objetivos?nombre=${encodeURIComponent(persona)}`;
      const data = await apiRequest(path, { token });
      _setCache(path, data);
      setMisObjetivos(data.objetivos || []);
    } catch (err) {
      setObjError(err.message);
    } finally {
      setObjEliminando(null);
    }
  }

  useEffect(() => {
    if (isAdmin) return;
    // Cacheado (SWR): pinta al instante lo último conocido y revalida en segundo
    // plano. Recorrer los proyectos de Notion tarda, así que evitamos el waterfall
    // en cada remontaje del dashboard.
    const applyActivos = (d) => setProyectosActivos(d.proyectos || []);
    apiRequestCached("/api/evaluaciones-proyecto-activas", { token }, applyActivos)
      .then(applyActivos)
      .catch(() => {});
    const applyManager = (d) => setProyectosManager(d.proyectos || []);
    apiRequestCached("/api/proyectos-manager", { token }, applyManager)
      .then(applyManager)
      .catch(() => setProyectosManager([]));
  }, [token, isAdmin, proyectosVersion]);

  useEffect(() => {
    const apply = (d) => setEvaluacionesExtraPendientes(d.pendientes || []);
    apiRequestCached("/api/evaluaciones-extra-pendientes", { token }, apply)
      .then(apply)
      .catch(() => setEvaluacionesExtraPendientes([]));
  }, [token]);

  // Proyectos terminados: se cargan al abrir el desplegable (recorre todas las
  // subpáginas de proyecto en Notion, así que solo pegamos cuando el usuario lo pide).
  // Se recarga en CADA apertura para reflejar evals recién hechas; mientras llega la
  // respuesta se mantiene la lista anterior visible.
  const cargarProyectosTerminados = React.useCallback(() => {
    setTerminadosCargando(true);
    apiRequest("/api/mis-evaluaciones-proyecto-realizadas", { token })
      .then((d) => setProyectosTerminados(d.proyectos || []))
      .catch(() => setProyectosTerminados((prev) => prev || []))
      .finally(() => setTerminadosCargando(false));
  }, [token]);

  useEffect(() => {
    if (isAdmin) { setTareasSlack({ pendientes: [], url: "" }); return; }
    const apply = (d) => setTareasSlack({ pendientes: d.pendientes || [], url: d.slackUrl || "" });
    apiRequestCached("/api/tareas-slack", { token }, apply)
      .then(apply)
      .catch(() => {});
  }, [token, isAdmin]);

  useEffect(() => {
    if (isAdmin || !proyectosActivos.length) { setProyectosProgreso({}); setTareasProyecto([]); return; }
    let cancelado = false;
    // Una sola petición: el servidor devuelve equipo + evals completadas de cada proyecto
    // activo (antes eran 1 + 2N peticiones en cascada desde el navegador).
    apiRequest("/api/proyectos-progreso", { token })
      .then((data) => {
        if (cancelado) return;
        // Normaliza el nombre (minúsculas, sin acentos, sin espacios extra) para que
        // el emparejamiento no falle por variaciones entre lo guardado y el equipo.
        const norm = (s) => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
        const progreso = {};
        const tareas = [];
        (data.proyectos || []).forEach((p) => {
          const completadasKeys = (p.completadas || []).map((c) => `${c.tipo}:${norm(c.evaluado)}`);
          // La lista de evaluaciones a hacer viene calculada del servidor por
          // JERARQUÍA DE EMPRESA (cargo en Notion), no por rol en el proyecto.
          const lista = p.a_hacer || [];
          const pendientes = lista
            .filter((it) => !completadasKeys.includes(`${it.tipo}:${norm(it.evaluado)}`))
            .map((it) => ({ proyecto: p.nombre_proyecto, tipo: it.tipo, evaluado: it.evaluado, label: labelEvaluacionProyecto(it.tipo, it.evaluado), fecha_limite: p.fecha_limite || "" }));
          progreso[p.nombre_proyecto] = { done: lista.length - pendientes.length, total: lista.length };
          pendientes.forEach((it) => tareas.push(it));
        });
        setProyectosProgreso(progreso);
        setTareasProyecto(tareas);
      })
      .catch(() => {});
    return () => { cancelado = true; };
  }, [token, isAdmin, proyectosActivos, user?.persona, user?.username]);

  const role = isAdmin ? "Admin" : "";
  // Proyectos a mostrar: se ocultan los que ya tienes TODAS las evaluaciones completadas
  // (0 pendientes). Mientras el progreso aún carga (prog undefined) se muestran. Si añaden
  // miembros y vuelve a haber pendientes, reaparecen solos.
  const proyectosPendientes = proyectosActivos.filter((p) => {
    const prog = proyectosProgreso[p.nombre_proyecto];
    return !prog || (prog.total - prog.done) > 0;
  });
  const ownEvaluado = user?.persona || user?.username || "";
  const targetEvaluado = isAdmin ? evaluado : (evaluado || ownEvaluado);
  const selectedLabel = useMemo(() => evaluados.find((item) => item.value === evaluado)?.label || "", [evaluados, evaluado]);

  async function generate() {
    setLinks(null);
    setStatus(t("dash.gen_report"));
    try {
      const body = { evaluado: targetEvaluado };
      const data = await apiRequest("/api/generar", { token, method: "POST", body });
      setLinks(data);
      setStatus(t("dash.report_ready", { n: data.total }));
    } catch (err) {
      setStatus(err.message);
    }
  }

  async function downloadAnual(path) {
    try {
      const response = await fetch(apiUrl(path), { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) throw new Error(t("dash.err_download_file"));
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `informe_anual_${targetEvaluado.replace(/\s+/g, "_")}.docx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setStatus(err.message);
    }
  }

  async function openFile(path, filename) {
    if (!filename.endsWith(".docx")) {
      openAuthedFile(path, token);
      return;
    }
    setStatus(t("dash.downloading"));
    try {
      const response = await fetch(apiUrl(path), { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || t("admin.err_download"));
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
      setStatus(t("dash.file_ready"));
    } catch (err) {
      setStatus(err.message);
    }
  }

  const persona = user?.persona || user?.username || "";

  return (
    <main className="page dash-page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <div style={{ display: "flex", alignItems: "center", gap: "24px" }}>
          {onBackToRoleSelect && (
            <button className="link-button" onClick={onBackToRoleSelect}>{t("common.back")}</button>
          )}
          <div className="nav-user">
            <div className="nav-user-info">
              <span className="nav-user-name">{persona}</span>
              <button className="link-button logout-btn" onClick={onLogout}>{t("common.logout")}</button>
            </div>
            <div className="nav-avatar">
              {perfil.foto ? <img src={perfil.foto} alt="" /> : initials(persona)}
            </div>
          </div>
        </div>
      </nav>

      <div className="profile-wrap" style={{ flex: 1 }}>
        <div className="dash-layout">

          {/* LEFT — Mi perfil */}
          <aside className="dash-profile">
            <p className="eyebrow" style={{ color: "var(--fg)", textAlign: "center", fontWeight: 500, margin: 0 }}>{t("dash.my_profile")}</p>
          <div className="profile-photo-wrap">
            {perfil.foto
              ? <img src={perfil.foto} alt={persona} className="profile-photo" />
              : <div className="profile-photo-placeholder">{initials(persona)}</div>
            }
            <div className="profile-id">
              <h1 className="profile-name">{persona}</h1>
              {perfil.cargo && <p className="profile-cargo">{perfil.cargo}</p>}
            </div>
          </div>
            <hr style={{ ...DASH_DIVIDER, margin: 0 }} />
            <div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
                <p className="eyebrow" style={{ margin: 0, flexShrink: 0, fontSize: "0.7rem" }}>{t("dash.my_country")}</p>
                {!editandoPais && (
                  <p style={{ fontSize: 13, color: perfil.pais ? "#000" : "rgba(0,0,0,.45)", margin: 0 }}>
                    {perfil.pais || t("dash.country_none")}
                  </p>
                )}
                {!editandoPais && (
                  <button
                    type="button"
                    onClick={abrirEdicionPais}
                    className="dash-cambiar"
                    style={{ border: "none", background: "none", cursor: "pointer", padding: 0, minHeight: "auto", fontSize: 12, fontWeight: 400, marginLeft: "auto", flexShrink: 0 }}
                  >{t("dash.country_change")}</button>
                )}
              </div>
              {!editandoPais ? null : (
                <div style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 4 }}>
                  <select
                    value={paisSel}
                    onChange={(e) => setPaisSel(e.target.value)}
                    style={{ fontSize: 14 }}
                  >
                    <option value="">{t("dash.country_placeholder")}</option>
                    {PAISES_PERMITIDOS.map((p) => (
                      <option key={p} value={p}>{p}</option>
                    ))}
                  </select>
                  <div style={{ display: "flex", gap: 8 }}>
                    <button
                      type="button"
                      onClick={guardarPais}
                      disabled={paisGuardando || !paisSel}
                      style={{ fontSize: 13, padding: "5px 12px", minHeight: "auto" }}
                    >{paisGuardando ? t("common.loading") : t("common.save")}</button>
                    <button
                      type="button"
                      className="secondary"
                      onClick={() => { setEditandoPais(false); setPaisMsg(""); }}
                      disabled={paisGuardando}
                      style={{ fontSize: 13, padding: "5px 12px", minHeight: "auto" }}
                    >{t("common.cancel")}</button>
                  </div>
                </div>
              )}
              {paisMsg && <p className="fine" style={{ marginTop: 4 }}>{paisMsg}</p>}
            </div>
            <hr style={{ ...DASH_DIVIDER, margin: 0 }} />
            <DashCollapsible title={t("dash.my_goals")} open={objOpen} onToggle={() => setObjOpen((v) => !v)}>
              {objError && <p className="form-error" style={{ paddingLeft: 16 }}>{objError}</p>}
              {misObjetivos.length ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 8, paddingLeft: 16 }}>
                  {misObjetivos.map((obj) => (
                    <div key={obj.page_id} style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                      <p style={{ fontSize: 13, color: "#000", display: "flex", alignItems: "center", gap: 10 }}>
                        <span style={{ display: "inline-block", width: 4, height: 4, borderRadius: "50%", background: "var(--accent)", flexShrink: 0 }} />
                        <span>
                          {obj.titulo}
                          {obj.fecha && (
                            <span style={{ color: "rgba(0,0,0,.45)", fontWeight: 200 }}> · {formatearFecha(obj.fecha)}</span>
                          )}
                        </span>
                      </p>
                      {obj.kpis && (
                        <p style={{ fontSize: 13, fontWeight: 200, color: "rgba(0,0,0,.55)", display: "flex", alignItems: "flex-start", gap: 10, paddingLeft: 24 }}>
                          <span style={{ display: "inline-block", width: 4, height: 4, borderRadius: "50%", background: "var(--accent)", flexShrink: 0, marginTop: 6 }} />
                          <span><em>KPIs:</em> {obj.kpis}</span>
                        </p>
                      )}
                      {obj.descripcion && (
                        <p style={{ fontSize: 13, fontWeight: 200, color: "rgba(0,0,0,.55)", display: "flex", alignItems: "flex-start", gap: 10, paddingLeft: 24 }}>
                          <span style={{ display: "inline-block", width: 4, height: 4, borderRadius: "50%", background: "var(--accent)", flexShrink: 0, marginTop: 6 }} />
                          <span>{obj.descripcion}</span>
                        </p>
                      )}
                      <button
                        className="link-button objetivo-eliminar"
                        style={{ alignSelf: "flex-start", marginLeft: 24, marginTop: 2 }}
                        disabled={objEliminando === obj.page_id}
                        onClick={() => eliminarMiObjetivo(obj.page_id)}
                      >
                        <span aria-hidden="true">×</span>
                        {objEliminando === obj.page_id ? t("common.deleting") : t("common.delete")}
                      </button>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="fine">{t("dash.no_goals")}</p>
              )}
            </DashCollapsible>
            <hr style={{ ...DASH_DIVIDER, margin: 0 }} />
            <div className="dash-tareas">
              <DashCollapsible title={t("dash.pending_tasks")} open={tareasOpen} onToggle={() => setTareasOpen((v) => !v)}
                bodyMarginTop={2}
                badge={(() => { const n = tareasSlack.pendientes.length + tareasProyecto.length + evaluacionesExtraPendientes.length; return n > 0 ? n : null; })()}>
              {(tareasSlack.pendientes.length + tareasProyecto.length + evaluacionesExtraPendientes.length) === 0 ? (
                <p className="fine">{t("dash.no_pending_tasks")}</p>
              ) : (
                <div className="tareas-list">
                  {tareasSlack.pendientes.map((tp) => {
                    // Tolerante a ambos formatos: string (backend antiguo) u objeto {tipo, deadline}.
                    const tipoSlack = typeof tp === "string" ? tp : tp.tipo;
                    const deadlineSlack = typeof tp === "string" ? "" : tp.deadline;
                    return (
                    <div key={`slack-${tipoSlack}`} className="tarea-row"
                      onClick={() => { if (tareasSlack.url) window.location.href = tareasSlack.url; }}>
                      <span className="tarea-label">{t(`dash.slack_${tipoSlack}`)} {t("dash.slack_suffix")}</span>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                        {deadlineSlack && <span style={{ fontSize: 12, color: "rgba(0,0,0,.5)", whiteSpace: "nowrap" }}>{formatearFecha(deadlineSlack)}</span>}
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13, flexShrink: 0 }}><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /><polyline points="15 3 21 3 21 9" /><line x1="10" y1="14" x2="21" y2="3" /></svg>
                      </span>
                    </div>
                    );
                  })}
                  {tareasProyecto.map((it) => (
                    <div key={`proj-${it.proyecto}-${it.tipo}-${it.evaluado}`} className="tarea-row"
                      onClick={() => onNavigate({ type: "evaluaciones-proyecto", proyectos: proyectosActivos, initialProyecto: it.proyecto })}>
                      <span className="tarea-label">{it.label} · {it.proyecto}</span>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                        {it.fecha_limite && <span style={{ fontSize: 12, color: "rgba(0,0,0,.5)", whiteSpace: "nowrap" }}>{formatearFecha(it.fecha_limite)}</span>}
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13, flexShrink: 0 }}><polyline points="9 18 15 12 9 6" /></svg>
                      </span>
                    </div>
                  ))}
                  {evaluacionesExtraPendientes.map((ev) => (
                    <div key={`extra-${ev.page_id}`} className="tarea-row"
                      onClick={() => onNavigate({ type: "formulario-evaluacion-extra", solicitudPageId: ev.page_id, evaluado: ev.evaluado, contexto: ev.contexto })}>
                      <span className="tarea-label">{t("eep.requested_by", { nombre: ev.evaluado })}</span>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                        {ev.fecha_limite && <span style={{ fontSize: 12, color: "rgba(0,0,0,.5)", whiteSpace: "nowrap" }}>{formatearFecha(ev.fecha_limite)}</span>}
                        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13, flexShrink: 0 }}><polyline points="9 18 15 12 9 6" /></svg>
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </DashCollapsible>
            </div>
          </aside>

          {/* RIGHT — To-do + To-see */}
          <div className="dash-main">
            <section className="dash-section">
            <p className="eyebrow" style={{ color: "var(--fg)", textAlign: "left", fontWeight: 500, display: "flex", alignItems: "center", justifyContent: "flex-start", gap: 6 }}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14, flexShrink: 0 }}>
                <path d="M9 11l3 3L20 4" />
                <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
              </svg>
              To-do
            </p>
            <hr style={{ ...DASH_DIVIDER, margin: 0 }} />
            <nav style={{ display: "flex", flexDirection: "column" }}>
              {/* ── GENERAL ── */}
              <p className="eyebrow" style={{ margin: "0 0 2px", flexShrink: 0, fontSize: "0.7rem" }}>{t("dash.todo_general")}</p>
              {!isAdmin && proyectosPendientes.length > 0 && (
                <div>
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => setProjOpen((v) => !v)}
                    style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "6px 0", fontSize: 14, fontWeight: 400, cursor: "pointer", color: "#000", userSelect: "none" }}
                  >
                    <span><span className="dash-dot" />{t("dash.nav_do_proj_evals")}</span>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                      <span style={{ fontSize: 11, fontWeight: 500, color: "var(--accent)", whiteSpace: "nowrap" }}>
                        {t("dash.proj_evals_unfinished")}
                      </span>
                      <svg viewBox="0 0 24 24" fill="none" stroke="#000" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                        style={{ width: 11, height: 11, flexShrink: 0, transform: projOpen ? "rotate(180deg)" : "rotate(0deg)", transition: "transform .25s" }}>
                        <polyline points="18 15 12 9 6 15" />
                      </svg>
                    </span>
                  </div>
                  {projOpen && (
                    <div style={{ display: "flex", flexDirection: "column", gap: 1, paddingBottom: 8 }}>
                      {proyectosPendientes.map((p) => {
                        const prog = proyectosProgreso[p.nombre_proyecto];
                        return (
                          <div
                            key={p.nombre_proyecto}
                            onClick={() => onNavigate({ type: "evaluaciones-proyecto", proyectos: proyectosActivos, initialProyecto: p.nombre_proyecto })}
                            style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, fontSize: 13, color: "#000", cursor: "pointer", padding: "1px 0", paddingLeft: 16 }}
                          >
                            <span style={{ display: "inline-flex", alignItems: "center", minWidth: 0 }}>
                              <span style={{ display: "inline-block", width: 4, height: 4, borderRadius: "50%", background: "var(--accent)", marginRight: 10, flexShrink: 0 }} />
                              {p.nombre_proyecto}
                            </span>
                            {prog && (
                              <span style={{ display: "inline-flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
                                <span style={{ fontSize: 11, fontWeight: 400, color: "#000", opacity: 0.65, whiteSpace: "nowrap" }}>
                                  {t("dash.proj_evals_complete_label")}
                                </span>
                                <span
                                  style={{
                                    display: "inline-flex",
                                    alignItems: "center",
                                    justifyContent: "center",
                                    minWidth: 26,
                                    height: 20,
                                    padding: "0 5px",
                                    borderRadius: 4,
                                    background: "rgba(22,163,74,.12)",
                                    color: "#16A34A",
                                    opacity: 1,
                                    fontWeight: 500,
                                    fontSize: 11,
                                    whiteSpace: "nowrap",
                                  }}
                                >
                                  {prog.done}/{prog.total}
                                </span>
                              </span>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}
              <div>
                <div
                  role="button"
                  tabIndex={0}
                  onClick={() => setExtraEvalOpen((v) => !v)}
                  style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "6px 0", fontSize: 14, fontWeight: 400, cursor: "pointer", color: "#000", userSelect: "none" }}
                >
                  <span><span className="dash-dot" />{t("dash.nav_extra_evals")}</span>
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                    {evaluacionesExtraPendientes.length > 0 && (
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 6, flexShrink: 0 }}>
                        <span style={{ fontSize: 11, fontWeight: 400, color: "#000", opacity: 0.65, whiteSpace: "nowrap" }}>
                          {t("eep.to_complete")}
                        </span>
                        <span
                          title={t("dash.nav_pending_extra_evals")}
                          style={{
                            display: "inline-flex", alignItems: "center", justifyContent: "center",
                            minWidth: 20, height: 20, padding: "0 5px", borderRadius: 4,
                            background: "rgba(242,60,20,.12)", color: "var(--accent)", opacity: 1, fontWeight: 500,
                            fontSize: 11, whiteSpace: "nowrap",
                          }}
                        >
                          {evaluacionesExtraPendientes.length}
                        </span>
                      </span>
                    )}
                    <svg viewBox="0 0 24 24" fill="none" stroke="#000" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                      style={{ width: 11, height: 11, flexShrink: 0, transform: extraEvalOpen ? "rotate(180deg)" : "rotate(0deg)", transition: "transform .25s" }}>
                      <polyline points="18 15 12 9 6 15" />
                    </svg>
                  </span>
                </div>
                {extraEvalOpen && (
                  <div style={{ display: "flex", flexDirection: "column", gap: 1, paddingBottom: 8 }}>
                    <div
                      onClick={() => onNavigate({ type: "solicitar-evaluacion-extra" })}
                      style={{ display: "flex", alignItems: "center", gap: 6, fontSize: 13, color: "var(--accent)", cursor: "pointer", padding: "1px 0", paddingLeft: 16 }}
                    >
                      {t("dash.nav_request_extra_eval")}
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 12, height: 12, flexShrink: 0 }}><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /><polyline points="15 3 21 3 21 9" /><line x1="10" y1="14" x2="21" y2="3" /></svg>
                    </div>
                    {evaluacionesExtraPendientes.length > 0 && (
                      <p style={{ margin: "8px 0 2px", paddingLeft: 16, fontSize: 12, fontWeight: 500, color: "rgba(0,0,0,.55)" }}>
                        {t("dash.extra_evals_to_complete")}
                      </p>
                    )}
                    {evaluacionesExtraPendientes.map((p) => (
                      <div
                        key={p.page_id}
                        onClick={() => onNavigate({ type: "formulario-evaluacion-extra", solicitudPageId: p.page_id, evaluado: p.evaluado, contexto: p.contexto })}
                        style={{ display: "flex", alignItems: "flex-start", gap: 10, fontSize: 13, color: "#000", cursor: "pointer", padding: "1px 0", paddingLeft: 16 }}
                      >
                        <span style={{ display: "inline-block", width: 4, height: 4, borderRadius: "50%", background: "var(--accent)", flexShrink: 0, marginTop: 6 }} />
                        <span style={{ display: "flex", flexDirection: "column", gap: 2, minWidth: 0 }}>
                          <span>{t("eep.requested_by", { nombre: p.evaluado })}</span>
                          <span style={{ fontSize: 12, fontWeight: 200, color: "rgba(0,0,0,.55)" }}>{p.contexto}</span>
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
              {advisees.length > 0 && (
                <DashNavItem label={t("dash.nav_my_advisees")} onClick={() => onNavigate({ type: "advisees-list", advisees })} external />
              )}
              {/* ── RESPONSABLE DE PROYECTO ── */}
              {!isAdmin && (
                <>
                  <p className="eyebrow" style={{ margin: "14px 0 2px", flexShrink: 0, fontSize: "0.7rem" }}>{t("dash.todo_project_lead")}</p>
                  <DashNavItem label={t("dash.nav_activate_proj")} onClick={() => onNavigate({ type: "activar-evaluaciones-proyecto" })} external />
                  {proyectosManager?.length > 0 && (
                    <DashNavItem label={t("dash.nav_manage_projects")} onClick={() => onNavigate({ type: "mis-proyectos-activos" })} external />
                  )}
                </>
              )}
              {isAdmin && !onBackToRoleSelect && (
                <DashNavItem label={t("dash.nav_admin_panel")} onClick={() => setSeccionActiva((v) => v === "admin" ? null : "admin")} />
              )}
            </nav>
            </section>
            <section className="dash-section">
            <p className="eyebrow" style={{ color: "var(--fg)", textAlign: "left", fontWeight: 500, display: "flex", alignItems: "center", justifyContent: "flex-start", gap: 6 }}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14, flexShrink: 0 }}>
                <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
              To-see
            </p>
            <hr style={{ ...DASH_DIVIDER, margin: 0 }} />
            <div>
              <p className="eyebrow" style={{ margin: "0 0 6px", fontSize: "0.7rem" }}>{t("dash.my_reports")}</p>
              {informeFinalEmpleado === null ? (
                <p className="fine">{t("common.loading")}</p>
              ) : informeFinalEmpleado?.disponible ? (
                <div className="tareas-list">
                  {informeFinalEmpleado.htmlUrl && (
                    <div className="tarea-row" onClick={() => openFile(informeFinalEmpleado.htmlUrl, "informe_final.html")}>
                      <span className="tarea-label">{t("dash.open_web")}</span>
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14, flexShrink: 0 }}><circle cx="12" cy="12" r="9" /><line x1="3" y1="12" x2="21" y2="12" /><path d="M12 3a15 15 0 0 1 0 18 15 15 0 0 1 0-18z" /></svg>
                    </div>
                  )}
                  {informeFinalEmpleado.docxUrl && (
                    <div className="tarea-row" onClick={() => openFile(informeFinalEmpleado.docxUrl, "informe_final.docx")}>
                      <span className="tarea-label">{t("admin.download_word")}</span>
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14, flexShrink: 0 }}><path d="M12 3v12" /><polyline points="7 10 12 15 17 10" /><line x1="5" y1="21" x2="19" y2="21" /></svg>
                    </div>
                  )}
                </div>
              ) : (
                <p style={{ fontStyle: "italic", color: "var(--text-55)", fontSize: 13, margin: 0, paddingLeft: 16 }}>{t("dash.no_reports")}</p>
              )}
            </div>
            {/* ── EVALUACIONES DE PROYECTO RECIBIDAS (solo top-to-bottom liberadas) ── */}
            {!isAdmin && (
              <div>
                <p className="eyebrow" style={{ margin: "0 0 6px", fontSize: "0.7rem" }}>{t("dash.received_evals")}</p>
                {evalsRecibidas === null ? (
                  <p className="fine">{t("common.loading")}</p>
                ) : evalsRecibidas.length === 0 ? (
                  <p className="fine" style={{ fontStyle: "italic", paddingLeft: 14, margin: 0 }}>{t("dash.received_empty")}</p>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                    {evalsRecibidas.map((ev, i) => (
                      <div
                        key={ev.page_id || ev.url || i}
                        role="button"
                        tabIndex={0}
                        onClick={() => onNavigate({ type: "detalle-evaluacion-realizada", ev: { ...ev, evaluado: ev.evaluador }, proyecto: ev.proyecto })}
                        style={{ display: "flex", flexDirection: "column", gap: 1, fontSize: 12.5, color: "#000", cursor: "pointer" }}
                      >
                        <span style={{ fontWeight: 400 }}>
                          <span style={{ display: "inline-block", width: 4, height: 4, borderRadius: "50%", background: "var(--accent)", marginRight: 10, verticalAlign: "middle" }} />
                          {ev.proyecto}{ev.evaluador ? ` · ${ev.evaluador}` : ""}
                        </span>
                        {ev.fecha && <span style={{ fontSize: 11, color: "rgba(0,0,0,.5)", marginLeft: 14 }}>{ev.fecha}</span>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
            {/* ── PROYECTOS TERMINADOS ── */}
            {!isAdmin && (
              <>
                <DashCollapsible
                  title={t("dash.todo_finished_projects")}
                  open={terminadosOpen}
                  onToggle={() => { const next = !terminadosOpen; setTerminadosOpen(next); if (next) cargarProyectosTerminados(); }}
                  bodyMarginTop={2}
                >
                  <div style={{ display: "flex", flexDirection: "column", gap: 1 }}>
                    {terminadosCargando && !proyectosTerminados && (
                      <p className="fine">{t("dash.finished_loading")}</p>
                    )}
                    {!terminadosCargando && proyectosTerminados?.length === 0 && (
                      <p className="fine">{t("dash.finished_empty")}</p>
                    )}
                    {proyectosTerminados?.map((proy) => {
                      const abierto = proyTerminadoAbierto === proy.nombre_proyecto;
                      return (
                        <div key={proy.nombre_proyecto}>
                          <div
                            role="button"
                            tabIndex={0}
                            onClick={() => setProyTerminadoAbierto((v) => v === proy.nombre_proyecto ? null : proy.nombre_proyecto)}
                            style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, fontSize: 13, color: "#000", cursor: "pointer", padding: "1px 0", paddingLeft: 16, userSelect: "none" }}
                          >
                            <span style={{ display: "inline-flex", alignItems: "center", minWidth: 0 }}>
                              <span style={{ display: "inline-block", width: 4, height: 4, borderRadius: "50%", background: "var(--accent)", marginRight: 10, flexShrink: 0 }} />
                              {proy.nombre_proyecto}
                            </span>
                            <svg viewBox="0 0 24 24" fill="none" stroke="#000" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                              style={{ width: 10, height: 10, flexShrink: 0, transform: abierto ? "rotate(180deg)" : "rotate(0deg)", transition: "transform .25s" }}>
                              <polyline points="18 15 12 9 6 15" />
                            </svg>
                          </div>
                          {abierto && (
                            <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: "2px 0 6px 30px" }}>
                              {proy.evaluaciones?.length === 0 && (
                                <span style={{ fontSize: 12, color: "rgba(0,0,0,.5)" }}>{t("dash.finished_project_empty")}</span>
                              )}
                              {proy.evaluaciones?.map((ev, i) => (
                                <div
                                  key={ev.url || i}
                                  role="button"
                                  tabIndex={0}
                                  onClick={() => onNavigate({ type: "detalle-evaluacion-realizada", ev, proyecto: proy.nombre_proyecto })}
                                  style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 8, fontSize: 12.5, color: "#000", cursor: "pointer" }}
                                >
                                  <span style={{ display: "flex", alignItems: "flex-start", gap: 10, minWidth: 0 }}>
                                    <span style={{ display: "inline-block", width: 4, height: 4, borderRadius: "50%", background: "rgba(0,0,0,.35)", flexShrink: 0, marginTop: 6 }} />
                                    <span style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
                                      <span style={{ fontWeight: 400 }}>{ev.tipo}{ev.evaluado ? ` · ${ev.evaluado}` : ""}</span>
                                      {ev.fecha && <span style={{ fontSize: 11, color: "rgba(0,0,0,.5)" }}>{ev.fecha}</span>}
                                    </span>
                                  </span>
                                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13, flexShrink: 0, marginTop: 3, color: "rgba(0,0,0,.4)" }}><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /><polyline points="15 3 21 3 21 9" /><line x1="10" y1="14" x2="21" y2="3" /></svg>
                                </div>
                              ))}
                            </div>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </DashCollapsible>
              </>
            )}
            </section>
          </div>

        </div>
      </div>

      {status && (
        <p className="dash-status fine">{status}</p>
      )}

      {isAdmin && !onBackToRoleSelect && seccionActiva === "admin" && (
        <section className="panel" style={{ marginTop: "32px" }}>
          <p className="kicker">{t("dash.nav_admin_panel")}</p>
          <h2>{t("dash.manage_evals")}</h2>
          <label>{t("dash.evaluated_person")}</label>
          <select value={evaluado} onChange={(e) => setEvaluado(e.target.value)}>
            {evaluados.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
          <p className="fine">{t("dash.current_selection", { v: selectedLabel || t("dash.no_table") })}</p>
          <div className="actions" style={{ marginTop: "20px" }}>
            <button onClick={() => setAdminModo("borrador")} className={adminModo === "borrador" ? "" : "secondary"}>{t("dash.claude_draft")}</button>
            <button onClick={() => setAdminModo("final")} className={adminModo === "final" ? "" : "secondary"}>{t("dash.final_ca")}</button>
          </div>
          {adminModo === "borrador" ? (
            <>
              <div className="tools" style={{ marginTop: "24px" }}>
                <article className="tool">
                  <p className="kicker">{t("dash.annual_report")}</p>
                  <h2>{targetEvaluado ? t("dash.annual_report_of", { nombre: targetEvaluado }) : t("dash.annual_report")}</h2>
                  <p>{t("dash.annual_desc")}</p>
                  <button onClick={generate} disabled={!targetEvaluado}>{t("dash.gen_annual")}</button>
                </article>
              </div>
              {links && (
                <section className="result panel" style={{ marginTop: "24px" }}>
                  <h2>{t("dash.result")}</h2>
                  <div className="actions">
                    {links.htmlUrl && <button onClick={() => openFile(links.htmlUrl, "informe.html")}>{t("dash.open_web_short")}</button>}
                    {links.docxAnualUrl && <button className="secondary" onClick={() => downloadAnual(links.docxAnualUrl)}>{t("dash.download_annual")}</button>}
                  </div>
                </section>
              )}
            </>
          ) : (
            <div className="panel" style={{ marginTop: "24px" }}>
              <p className="kicker">{t("dash.final_ca")}</p>
              <h2>{targetEvaluado ? t("dash.final_report_of", { nombre: targetEvaluado }) : t("dash.final_report")}</h2>
              {!targetEvaluado ? (
                <p className="fine">{t("dash.select_person")}</p>
              ) : informeFinalAdmin === null ? (
                <p>{t("common.loading")}</p>
              ) : informeFinalAdmin?.disponible ? (
                <div className="actions">
                  {informeFinalAdmin.htmlUrl && <button onClick={() => openFile(informeFinalAdmin.htmlUrl, "informe_final.html")}>{t("dash.open_web_version")}</button>}
                  {informeFinalAdmin.docxUrl && <button className="secondary" onClick={() => openFile(informeFinalAdmin.docxUrl, "informe_final.docx")}>{t("admin.download_word")}</button>}
                </div>
              ) : (
                <p className="fine">{informeFinalAdmin?.mensaje || t("dash.no_final_report")}</p>
              )}
            </div>
          )}
        </section>
      )}
      <Footer />
    </main>
  );
}

function SubirInformePage({ token, advisee, onBack }) {
  const [file, setFile] = useState(null);
  const [status, setStatus] = useState("");
  const [links, setLinks] = useState(null);
  const [uploading, setUploading] = useState(false);
  const [informeActual, setInformeActual] = useState(null);

  useEffect(() => {
    apiRequest(`/api/informe-final?evaluado=${encodeURIComponent(advisee.nombre)}`, { token })
      .then((data) => { if (data.disponible) setInformeActual(data); })
      .catch(() => {});
  }, [token, advisee.nombre]);

  async function subir(e) {
    e.preventDefault();
    if (!file) return;
    setUploading(true);
    setStatus(t("subir.uploading"));
    setLinks(null);
    try {
      const formData = new FormData();
      formData.append("evaluado", advisee.nombre);
      formData.append("archivo", file);
      const response = await fetch(apiUrl("/api/subir-informe-final"), {
        method: "POST",
        headers: { Authorization: `Bearer ${token}` },
        body: formData,
      });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.error || t("subir.err_upload"));
      setStatus(t("subir.uploaded_ok"));
      setInformeActual(data);
      setLinks(null);
    } catch (err) {
      setStatus(err.message);
    } finally {
      setUploading(false);
    }
  }

  async function openFile(path, filename) {
    if (!filename.endsWith(".docx")) {
      openAuthedFile(path, token);
      return;
    }
    try {
      const response = await fetch(apiUrl(path), { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) throw new Error(t("admin.err_download"));
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = filename;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setStatus(err.message);
    }
  }

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <NavBack onBack={onBack} />
      </nav>
      <div className="profile-wrap" style={{ flex: 1 }}>
        <div className="dash-layout">

          {/* LEFT — perfil del advisee (mismo panel que la página de advisee) */}
          <aside className="dash-profile">
            <p className="eyebrow" style={{ color: "var(--fg)", textAlign: "center", fontWeight: 500, margin: 0 }}>{t("ad.eyebrow")}</p>
            <div className="profile-photo-wrap">
              {advisee.foto
                ? <img src={advisee.foto} alt={advisee.nombre} className="profile-photo" />
                : <div className="profile-photo-placeholder">{advisee.nombre.charAt(0)}</div>
              }
              <div className="profile-id">
                <h1 className="profile-name">{advisee.nombre}</h1>
                {advisee.cargo && <p className="profile-cargo">{advisee.cargo}</p>}
              </div>
            </div>
          </aside>

          {/* RIGHT — subir informe final + versión anterior */}
          <div className="dash-main">
            <section>
              <h2 style={{ marginBottom: 8 }}>{t("subir.upload_final")}</h2>
              <p className="fine" style={{ marginBottom: 24, maxWidth: 640 }}>{t("subir.upload_desc")}</p>

              <form onSubmit={subir}>
                <label>{t("subir.word_file")}</label>
                <input
                  type="file"
                  accept=".doc,.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                  onChange={(e) => setFile(e.target.files?.[0] || null)}
                  required
                />
                {status && <p className={[t("subir.uploading"), t("subir.uploaded_ok")].includes(status) ? "fine" : "error"} style={{ marginTop: 8 }}>{status}</p>}
                <div className="actions">
                  <button type="submit" disabled={uploading || !file}>
                    {uploading ? t("subir.uploading_btn") : t("subir.upload_btn")}
                  </button>
                </div>
              </form>

              {informeActual && (
                <div style={{ marginTop: 36, borderTop: "1px solid var(--border)", paddingTop: 24 }}>
                  <h2>{t("subir.current_version")}</h2>
                  <p className="fine">{t("subir.current_desc")}</p>
                  <div className="actions">
                    {informeActual.htmlUrl && <button onClick={() => openFile(informeActual.htmlUrl, "informe_final.html")}>{t("dash.open_web_version")}</button>}
                    {informeActual.docxUrl && <button className="secondary" onClick={() => openFile(informeActual.docxUrl, "informe_final.docx")}>{t("admin.download_word")}</button>}
                  </div>
                </div>
              )}
            </section>
          </div>
        </div>
      </div>
      <Footer />
    </main>
  );
}

function AdviseesList({ token, advisees, onBack, onNavigate }) {
  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <NavBack onBack={onBack} />
      </nav>
      <div className="advisees-page-wrap">
        <p className="kicker">Career Advisor</p>
        <h2>{t("dash.nav_my_advisees")}</h2>
        <div className="advisees-page-grid stagger">
          {advisees.map((a) => (
            <button
              key={a.nombre}
              className="advisee-page-card"
              onClick={() => onNavigate({ type: "advisee-detail", advisee: a, advisees })}
            >
              {a.foto
                ? <img src={a.foto} alt={a.nombre} className="advisee-page-foto" />
                : <div className="advisee-page-foto advisee-foto-placeholder">{a.nombre.charAt(0)}</div>
              }
              <span className="advisee-page-nombre">{a.nombre}</span>
            </button>
          ))}
        </div>
      </div>
      <Footer />
    </main>
  );
}

// Fila desplegable del To-do/To-see de la página de advisee (mismo estilo que la nav del dashboard).
function AdviseeNavGroup({ label, open, onToggle, children }) {
  return (
    <div>
      <div
        role="button"
        tabIndex={0}
        onClick={onToggle}
        style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "6px 0", fontSize: 14, fontWeight: 400, cursor: "pointer", color: "#000", userSelect: "none" }}
      >
        <span><span className="dash-dot" />{label}</span>
        <svg viewBox="0 0 24 24" fill="none" stroke="#000" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
          style={{ width: 11, height: 11, flexShrink: 0, transform: open ? "rotate(180deg)" : "rotate(0deg)", transition: "transform .25s" }}>
          <polyline points="18 15 12 9 6 15" />
        </svg>
      </div>
      {open && <div className="advisee-nav-children" style={{ paddingTop: 4, paddingBottom: 14, paddingLeft: 16 }}>{children}</div>}
    </div>
  );
}

function RegistroComentariosPage({ token, advisee, onBack }) {
  const [cargo, setCargo] = useState(advisee.cargo || "");
  const [notas, setNotas] = useState(null);
  const [loadingNotas, setLoadingNotas] = useState(true);
  const [nuevaNota, setNuevaNota] = useState("");
  const [guardandoNota, setGuardandoNota] = useState(false);
  const [notaError, setNotaError] = useState("");
  const [grabando, setGrabando] = useState(false);
  const [dictadoError, setDictadoError] = useState("");
  const recognitionRef = React.useRef(null);
  const baseNotaRef = React.useRef("");
  const dictadoSoportado =
    typeof window !== "undefined" && !!(window.SpeechRecognition || window.webkitSpeechRecognition);

  useEffect(() => {
    if (advisee.cargo) return;
    apiRequest(`/api/perfil-empleado?nombre=${encodeURIComponent(advisee.nombre)}`, { token })
      .then((perfil) => setCargo(perfil.cargo || ""))
      .catch(() => {});
  }, [token, advisee.nombre, advisee.cargo]);

  useEffect(() => {
    setLoadingNotas(true);
    apiRequest(`/api/opiniones-ca?advisee=${encodeURIComponent(advisee.nombre)}`, { token })
      .then((data) => setNotas(data.opiniones || []))
      .catch(() => setNotas([]))
      .finally(() => setLoadingNotas(false));
  }, [token, advisee.nombre]);

  useEffect(() => () => { try { recognitionRef.current?.stop(); } catch {} }, []);

  function toggleDictado() {
    if (grabando) {
      try { recognitionRef.current?.stop(); } catch {}
      return;
    }
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      setDictadoError(t("ad.dictation_unsupported"));
      return;
    }
    setDictadoError("");
    const rec = new SR();
    const LANGS = { es: "es-ES", en: "en-US", pt: "pt-PT" };
    rec.lang = LANGS[getLang()] || "es-ES";
    rec.continuous = true;
    rec.interimResults = true;
    baseNotaRef.current = nuevaNota ? nuevaNota.trimEnd() + " " : "";
    rec.onresult = (e) => {
      let texto = "";
      for (let i = 0; i < e.results.length; i++) texto += e.results[i][0].transcript;
      setNuevaNota(baseNotaRef.current + texto);
    };
    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        setDictadoError(t("ad.dictation_denied"));
      } else if (e.error !== "no-speech" && e.error !== "aborted") {
        setDictadoError(t("ad.dictation_error"));
      }
      setGrabando(false);
    };
    rec.onend = () => setGrabando(false);
    recognitionRef.current = rec;
    try {
      rec.start();
      setGrabando(true);
    } catch {
      setDictadoError(t("ad.dictation_error"));
    }
  }

  async function guardarNota(e) {
    e.preventDefault();
    const texto = nuevaNota.trim();
    if (!texto) return;
    setGuardandoNota(true);
    setNotaError("");
    try {
      const data = await apiRequest("/api/notas-ca", {
        token,
        method: "POST",
        body: { advisee: advisee.nombre, nota: texto },
      });
      if (data.ok) {
        const ahora = new Date().toISOString();
        setNotas((prev) => [{ fecha: ahora, opinion: texto, resumen_advisee: "" }, ...(prev || [])]);
        setNuevaNota("");
      } else {
        setNotaError(t("ad.err_save_note"));
      }
    } catch {
      setNotaError(t("ad.err_save_note2"));
    } finally {
      setGuardandoNota(false);
    }
  }

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <NavBack onBack={onBack} />
      </nav>
      <div className="profile-wrap" style={{ flex: 1 }}>
        <div className="dash-layout">

          {/* LEFT — mismo panel de perfil que la pagina anterior */}
          <aside className="dash-profile">
            <p className="eyebrow" style={{ color: "var(--fg)", textAlign: "center", fontWeight: 500, margin: 0 }}>{t("ad.eyebrow")}</p>
            <div className="profile-photo-wrap">
              {advisee.foto
                ? <img src={advisee.foto} alt={advisee.nombre} className="profile-photo" />
                : <div className="profile-photo-placeholder">{advisee.nombre.charAt(0)}</div>
              }
              <div className="profile-id">
                <h1 className="profile-name">{advisee.nombre}</h1>
                {cargo && <p className="profile-cargo">{cargo}</p>}
              </div>
            </div>
          </aside>

          {/* RIGHT — registro de comentarios */}
          <div className="dash-main">
            <section className="dash-section">
              <h1 style={{ marginBottom: 8 }}>{t("ad.meetings_log")}</h1>
              <p className="fine" style={{ marginTop: 0, marginBottom: 22, color: "#000", maxWidth: 620 }}>{t("regcom.desc")}</p>

              {dictadoSoportado && (
                <div style={{ display: "flex", justifyContent: "flex-end", marginBottom: 12 }}>
                  <button
                    type="button"
                    className={grabando ? "notas-ca-dictado grabando" : "notas-ca-dictado secondary"}
                    onClick={toggleDictado}
                  >
                    {grabando ? t("ad.dictation_stop") : t("ad.dictation_start")}
                  </button>
                </div>
              )}
              <form className="notas-ca-form" onSubmit={guardarNota}>
                <textarea
                  className="notas-ca-textarea"
                  placeholder={t("ad.note_placeholder")}
                  value={nuevaNota}
                  onChange={(e) => setNuevaNota(e.target.value)}
                  rows={4}
                />
                {grabando && <span className="notas-ca-dictado-hint fine">{t("ad.dictation_listening")}</span>}
                {dictadoError && <p className="form-error">{dictadoError}</p>}
                {notaError && <p className="form-error">{notaError}</p>}
                <button type="submit" disabled={guardandoNota || !nuevaNota.trim()}>
                  {guardandoNota ? t("common.saving") : t("ad.save_note")}
                </button>
              </form>
              <div className="notas-ca-historial">
                {loadingNotas ? (
                  <p className="fine">{t("ad.loading_history")}</p>
                ) : !notas || notas.length === 0 ? (
                  <p className="fine">{t("ad.no_notes")}</p>
                ) : (
                  notas.map((nota, i) => (
                    <article key={i} className="nota-ca-item">
                      <p className="nota-ca-fecha fine">{nota.fecha ? nota.fecha.slice(0, 10) : t("common.no_date")}</p>
                      {nota.resumen_advisee && (
                        <details className="nota-ca-resumen-wrap">
                          <summary className="fine">{t("ad.view_included_evals")}</summary>
                          <pre className="opinion-pre">{nota.resumen_advisee}</pre>
                        </details>
                      )}
                      <p className="nota-ca-texto">{nota.opinion || "—"}</p>
                    </article>
                  ))
                )}
              </div>
            </section>
          </div>
        </div>
      </div>
      <Footer />
    </main>
  );
}


function AdviseeDetail({ token, advisee, advisees, onBack, onNavigate }) {
  const [gestionOpen, setGestionOpen] = useState(false);
  const [comentariosOpen, setComentariosOpen] = useState(false);
  const [cargo, setCargo] = useState(advisee.cargo || "");
  const [accesoIndividual, setAccesoIndividual] = useState(false);
  const [togglingAccesoIndividual, setTogglingAccesoIndividual] = useState(false);
  const [notas, setNotas] = useState(null);
  const [loadingNotas, setLoadingNotas] = useState(true);
  const [nuevaNota, setNuevaNota] = useState("");
  const [guardandoNota, setGuardandoNota] = useState(false);
  const [notaError, setNotaError] = useState("");
  const [grabando, setGrabando] = useState(false);
  const [dictadoError, setDictadoError] = useState("");
  const recognitionRef = React.useRef(null);
  const baseNotaRef = React.useRef("");
  const dictadoSoportado =
    typeof window !== "undefined" && !!(window.SpeechRecognition || window.webkitSpeechRecognition);
  const [realizarOpen, setRealizarOpen] = useState(false);
  const [generandoFuente, setGenerandoFuente] = useState("");
  const [fuenteError, setFuenteError] = useState("");
  const [fuenteOk, setFuenteOk] = useState(false);
  const [tieneEvaluacionesExtra, setTieneEvaluacionesExtra] = useState(false);
  const [verInformeBusy, setVerInformeBusy] = useState(false);
  const [sinInformeFinal, setSinInformeFinal] = useState(false); // no hay versión final en Notion

  // Abre la versión final del informe guardada en Notion; si no hay, muestra el aviso con enlace.
  async function verVersionActualInforme() {
    setVerInformeBusy(true); setSinInformeFinal(false);
    try {
      const data = await apiRequest(`/api/informe-final?evaluado=${encodeURIComponent(advisee.nombre)}`, { token });
      if (data.disponible && data.htmlUrl) {
        openAuthedFile(data.htmlUrl, token);
      } else if (data.disponible && data.docxUrl) {
        openAuthedFile(data.docxUrl, token);
      } else {
        setSinInformeFinal(true);
      }
    } catch {
      setSinInformeFinal(true);
    } finally {
      setVerInformeBusy(false);
    }
  }

  // Descarga un PDF de una fuente (opiniones, evals proyecto, seguimiento, evals mensuales).
  async function descargarFuentePdf(endpoint, etiqueta) {
    setGenerandoFuente(endpoint);
    setFuenteError(""); setFuenteOk(false);
    try {
      const data = await apiRequest(endpoint, { token, method: "POST", body: { evaluado: advisee.nombre } });
      const path = data.pdfUrl;
      if (!path) throw new Error(t("ad.err_no_doc"));
      const response = await fetch(apiUrl(path), { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) {
        const d = await response.json().catch(() => ({}));
        throw new Error(d.error || t("admin.err_download"));
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `${etiqueta}_${advisee.nombre.replace(/\s+/g, "_")}.pdf`;
      link.click();
      URL.revokeObjectURL(url);
      setFuenteOk(true);
      setTimeout(() => setFuenteOk(false), 2600);
    } catch (err) {
      // El detalle técnico ("Failed to fetch", 500...) no le dice nada al CA: aquí
      // el único desenlace accionable es que esa fuente no tiene nada que descargar.
      console.error(`Descarga de fuente ${endpoint}:`, err);
      setFuenteError(t("ad.err_no_source_info"));
    } finally {
      setGenerandoFuente("");
    }
  }

  useEffect(() => {
    const apply = (data) => setAccesoIndividual(data.activo || false);
    apiRequestCached(`/api/acceso-advisee-individual?advisee=${encodeURIComponent(advisee.nombre)}`, { token }, apply)
      .then(apply)
      .catch(() => {});
  }, [token, advisee.nombre]);

  useEffect(() => {
    setLoadingNotas(true);
    apiRequest(`/api/opiniones-ca?advisee=${encodeURIComponent(advisee.nombre)}`, { token })
      .then((data) => setNotas(data.opiniones || []))
      .catch(() => setNotas([]))
      .finally(() => setLoadingNotas(false));
  }, [token, advisee.nombre]);

  useEffect(() => {
    apiRequest(`/api/evaluaciones-extra-recibidas?evaluado=${encodeURIComponent(advisee.nombre)}`, { token })
      .then((data) => setTieneEvaluacionesExtra((data.evaluaciones || []).length > 0))
      .catch(() => setTieneEvaluacionesExtra(false));
  }, [token, advisee.nombre]);

  useEffect(() => {
    if (advisee.cargo) return;
    apiRequest(`/api/perfil-empleado?nombre=${encodeURIComponent(advisee.nombre)}`, { token })
      .then((perfil) => setCargo(perfil.cargo || ""))
      .catch(() => {});
  }, [token, advisee.nombre, advisee.cargo]);

  async function toggleAccesoIndividual() {
    setTogglingAccesoIndividual(true);
    try {
      const data = await apiRequest("/api/acceso-advisee-individual", {
        token,
        method: "POST",
        body: { advisee: advisee.nombre, activo: !accesoIndividual },
      });
      setAccesoIndividual(data.activo);
    } catch {
    } finally {
      setTogglingAccesoIndividual(false);
    }
  }

  // Detiene el reconocimiento de voz si el componente se desmonta a media grabación.
  useEffect(() => () => { try { recognitionRef.current?.stop(); } catch {} }, []);

  // Dictado por voz con el reconocimiento nativo del navegador (Web Speech API):
  // gratuito, sin backend ni API keys. Transcribe en vivo y va rellenando el textarea
  // para que la persona lo revise y edite antes de guardar. El idioma del
  // reconocimiento sigue al idioma activo de la web (es/en/pt).
  function toggleDictado() {
    if (grabando) {
      try { recognitionRef.current?.stop(); } catch {}
      return;
    }
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    if (!SR) {
      setDictadoError(t("ad.dictation_unsupported"));
      return;
    }
    setDictadoError("");
    const rec = new SR();
    const LANGS = { es: "es-ES", en: "en-US", pt: "pt-PT" };
    rec.lang = LANGS[getLang()] || "es-ES";
    rec.continuous = true;
    rec.interimResults = true;
    // Punto de partida: lo que ya hubiera escrito, para dictar a continuación.
    baseNotaRef.current = nuevaNota ? nuevaNota.trimEnd() + " " : "";
    rec.onresult = (e) => {
      let texto = "";
      for (let i = 0; i < e.results.length; i++) texto += e.results[i][0].transcript;
      setNuevaNota(baseNotaRef.current + texto);
    };
    rec.onerror = (e) => {
      if (e.error === "not-allowed" || e.error === "service-not-allowed") {
        setDictadoError(t("ad.dictation_denied"));
      } else if (e.error !== "no-speech" && e.error !== "aborted") {
        setDictadoError(t("ad.dictation_error"));
      }
      setGrabando(false);
    };
    rec.onend = () => setGrabando(false);
    recognitionRef.current = rec;
    try {
      rec.start();
      setGrabando(true);
    } catch {
      setDictadoError(t("ad.dictation_error"));
    }
  }

  async function guardarNota(e) {
    e.preventDefault();
    const texto = nuevaNota.trim();
    if (!texto) return;
    setGuardandoNota(true);
    setNotaError("");
    try {
      const data = await apiRequest("/api/notas-ca", {
        token,
        method: "POST",
        body: { advisee: advisee.nombre, nota: texto },
      });
      if (data.ok) {
        const ahora = new Date().toISOString();
        setNotas((prev) => [{ fecha: ahora, opinion: texto, resumen_advisee: "" }, ...(prev || [])]);
        setNuevaNota("");
      } else {
        setNotaError(t("ad.err_save_note"));
      }
    } catch {
      setNotaError(t("ad.err_save_note2"));
    } finally {
      setGuardandoNota(false);
    }
  }

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <button className="link-button" onClick={onBack}>{t("ad.back_advisees")}</button>
      </nav>
      <div className="profile-wrap" style={{ flex: 1 }}>
        <div className="dash-layout">

          {/* LEFT — perfil del advisee */}
          <aside className="dash-profile">
            <p className="eyebrow" style={{ color: "var(--fg)", textAlign: "center", fontWeight: 500, margin: 0 }}>{t("ad.eyebrow")}</p>
            <div className="profile-photo-wrap">
              {advisee.foto
                ? <img src={advisee.foto} alt={advisee.nombre} className="profile-photo" />
                : <div className="profile-photo-placeholder">{advisee.nombre.charAt(0)}</div>
              }
              <div className="profile-id">
                <h1 className="profile-name">{advisee.nombre}</h1>
                {cargo && <p className="profile-cargo">{cargo}</p>}
              </div>
            </div>
          </aside>

          {/* RIGHT — To-do + To-see */}
          <div className="dash-main">

            {/* ── TO-DO ── */}
            <section className="dash-section">
              <p className="eyebrow" style={{ color: "var(--fg)", textAlign: "left", fontWeight: 500, display: "flex", alignItems: "center", justifyContent: "flex-start", gap: 6 }}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14, flexShrink: 0 }}>
                  <path d="M9 11l3 3L20 4" />
                  <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
                </svg>
                To-do
              </p>
              <hr style={{ ...DASH_DIVIDER, margin: 0 }} />
              <nav style={{ display: "flex", flexDirection: "column" }}>
                <DashNavItem
                  label={t("ad.edit_goals")}
                  onClick={() => onNavigate({ type: "objetivos", advisee, advisees, from: "advisee-detail", vista: "form" })}
                  external
                />

                <AdviseeNavGroup label={t("ad.manage_report")} open={gestionOpen} onToggle={() => setGestionOpen((v) => !v)}>
                  <div className="advisee-gestion" style={{ border: "none", padding: 0 }}>
                    <AdviseeNavGroup label={t("ad.make_final")} open={realizarOpen} onToggle={() => setRealizarOpen((v) => !v)}>
                      <DashNavItem
                        label={<>{t("ad.with_claude")}<span style={{ fontStyle: "italic", fontWeight: 200, marginLeft: 8, fontSize: 11, color: "var(--text-55)" }}>— {t("ad.recommended")}</span></>}
                        onClick={() => onNavigate({ type: "eval-anual", advisee, advisees, from: "advisee-detail" })}
                        external
                      />
                      {/* "Manualmente" lleva directo al informe editable en la web; los PDFs de
                          fuentes se descargan desde la propia página de rellenar (barra superior). */}
                      <DashNavItem
                        label={t("ad.manual")}
                        onClick={() => onNavigate({ type: "eval-anual", advisee, advisees, from: "advisee-detail", modo: "manual" })}
                        external
                      />
                    </AdviseeNavGroup>
                    <DashNavItem
                      label={verInformeBusy ? t("ad.generating") : t("ad.view_current_report")}
                      onClick={verVersionActualInforme}
                      disabled={verInformeBusy}
                      external
                    />
                    {sinInformeFinal && (
                      <p className="fine" style={{ margin: "2px 0 6px", paddingLeft: 14 }}>
                        {t("ad.no_final_pre", { nombre: advisee.nombre })}
                        <a href="#" onClick={(e) => { e.preventDefault(); onNavigate({ type: "eval-anual", advisee, advisees, from: "advisee-detail" }); }}>
                          {t("ad.no_final_link")}
                        </a>
                        {t("ad.no_final_post")}
                      </p>
                    )}
                    <div style={{ display: "flex", alignItems: "center", marginTop: 8 }}>
                      <span className="dash-dot" />
                      <button
                        className="secondary"
                        onClick={toggleAccesoIndividual}
                        disabled={togglingAccesoIndividual}
                        style={{ height: 30, minHeight: "auto", padding: "0 14px", fontSize: 12 }}
                      >
                        {togglingAccesoIndividual
                          ? t("common.saving")
                          : accesoIndividual
                          ? t("ad.access_active_revoke")
                          : t("ad.give_access")}
                      </button>
                    </div>
                  </div>
                </AdviseeNavGroup>

                <DashNavItem
                  label={t("ad.meetings_log")}
                  onClick={() => onNavigate({ type: "registro-comentarios", advisee, advisees, from: "advisee-detail" })}
                  external
                />

                <DashNavItem
                  label={t("adplan.nav_title")}
                  onClick={() => onNavigate({ type: "plan-accion", advisee, advisees, from: "advisee-detail" })}
                  external
                />
              </nav>
            </section>

            {/* ── TO-SEE ── */}
            <section className="dash-section">
              <p className="eyebrow" style={{ color: "var(--fg)", textAlign: "left", fontWeight: 500, display: "flex", alignItems: "center", justifyContent: "flex-start", gap: 6 }}>
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14, flexShrink: 0 }}>
                  <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z" />
                  <circle cx="12" cy="12" r="3" />
                </svg>
                To-see
              </p>
              <hr style={{ ...DASH_DIVIDER, margin: 0 }} />
              <nav style={{ display: "flex", flexDirection: "column" }}>
                <DashNavItem
                  label={generandoFuente === "/api/generar-pdf-completo" ? t("ad.generating") : t("ad.view_available_info")}
                  onClick={() => descargarFuentePdf("/api/generar-pdf-completo", "info_completa")}
                  disabled={!!generandoFuente}
                  download
                />
                {fuenteError && <p className="form-error" style={{ paddingLeft: 14 }}>{fuenteError}</p>}
                {fuenteOk && (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "#166534", marginTop: 4, paddingLeft: 14 }}>
                    <DrawCheck size={20} color="#166534" /> {t("ad.downloaded")}
                  </span>
                )}

                <DashNavItem
                  label={t("ad.goals_history")}
                  onClick={() => onNavigate({ type: "objetivos", advisee, advisees, from: "advisee-detail", vista: "historial" })}
                  external
                />
              </nav>
            </section>

          </div>
        </div>
      </div>
      <Footer />
    </main>
  );
}

// ---------------------------------------------------------------------------
// Plan de acción del advisee (página propia; mismo panel de perfil que la página de advisee)
// ---------------------------------------------------------------------------

function PlanAccionPage({ token, advisee, advisees, onBack, onNavigate }) {
  const [plan, setPlan] = useState(null);   // texto del plan guardado; null = cargando, "" = sin plan
  const [editando, setEditando] = useState(false);
  const [borrador, setBorrador] = useState("");
  const [busy, setBusy] = useState(false);
  const [generando, setGenerando] = useState(false);
  const [error, setError] = useState("");
  const [chatOpen, setChatOpen] = useState(false);
  const [chatMsgs, setChatMsgs] = useState([]);   // {rol, texto}
  const [chatInput, setChatInput] = useState("");
  const [chatBusy, setChatBusy] = useState(false);

  useEffect(() => {
    apiRequest(`/api/eval-anual/plan-guardado?evaluado=${encodeURIComponent(advisee.nombre)}`, { token })
      .then((r) => setPlan(r.plan || ""))
      .catch(() => setPlan(""));
  }, [token, advisee.nombre]);

  // Chat de dudas (Haiku) sobre el plan y las evaluaciones del advisee.
  async function preguntar(e) {
    e.preventDefault();
    const q = chatInput.trim();
    if (!q || chatBusy) return;
    const nuevos = [...chatMsgs, { rol: "user", texto: q }];
    setChatMsgs(nuevos);
    setChatInput("");
    setChatBusy(true);
    try {
      const r = await apiRequest("/api/eval-anual/plan-chat", {
        token, method: "POST", body: { evaluado: advisee.nombre, mensajes: nuevos },
      });
      setChatMsgs([...nuevos, { rol: "assistant", texto: r.respuesta || "—" }]);
    } catch (err) {
      setChatMsgs([...nuevos, { rol: "assistant", texto: err.message }]);
    } finally {
      setChatBusy(false);
    }
  }

  function editar() {
    setBorrador(plan || "");
    setError("");
    setEditando(true);
  }

  // Genera un plan nuevo con Claude (la parte de plan de acción del informe final,
  // sin recorrer todo el asistente) y entra en edición con el resultado.
  async function crearNuevo() {
    setGenerando(true); setError("");
    try {
      const r = await apiRequest(`/api/eval-anual/plan?evaluado=${encodeURIComponent(advisee.nombre)}&forzar=1`, { token });
      setBorrador(r.plan || "");
      setEditando(true);
    } catch (e) {
      setError(e.message);
    } finally {
      setGenerando(false);
    }
  }

  async function guardar() {
    setBusy(true); setError("");
    try {
      await apiRequest("/api/eval-anual/plan-guardar", { token, method: "POST", body: { evaluado: advisee.nombre, texto: borrador } });
      setPlan(borrador.trim());
      setEditando(false);
    } catch (e) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  }

  // Botón para abrir/cerrar el chat de dudas (disponible tanto viendo como editando el plan).
  const botonDudas = (
    <button className="secondary" type="button" onClick={() => setChatOpen((v) => !v)}>
      {chatOpen ? t("adplan.ask_close") : t("adplan.ask")}
    </button>
  );

  const panelDudas = chatOpen && (
    <div style={{ marginTop: 20, border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", overflow: "hidden", maxWidth: 720 }}>
      <div style={{ padding: "16px", display: "flex", flexDirection: "column", gap: 12, maxHeight: 360, overflowY: "auto" }}>
        {chatMsgs.length === 0 && <p className="fine" style={{ margin: 0 }}>{t("adplan.chat_intro")}</p>}
        {chatMsgs.map((m, i) => (
          <div key={i} style={{ alignSelf: m.rol === "user" ? "flex-end" : "flex-start", maxWidth: "88%" }}>
            <div className={m.rol === "user" ? "chat-bubble-user" : "chat-bubble-bot"} style={{ whiteSpace: "pre-wrap" }}>{m.texto}</div>
          </div>
        ))}
        {chatBusy && <p className="fine" style={{ margin: 0 }}>{t("adplan.chat_thinking")}</p>}
      </div>
      <form onSubmit={preguntar} style={{ display: "flex", gap: 8, borderTop: "1px solid var(--border)", padding: 10, background: "var(--bg)" }}>
        <input
          className="chat-input"
          value={chatInput}
          onChange={(e) => setChatInput(e.target.value)}
          placeholder={t("adplan.chat_placeholder")}
        />
        <button type="submit" disabled={chatBusy || !chatInput.trim()}>{t("adplan.chat_send")}</button>
      </form>
    </div>
  );

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <NavBack onBack={onBack} />
      </nav>
      <div className="profile-wrap" style={{ flex: 1 }}>
        <div className="dash-layout">

          {/* LEFT — perfil del advisee (mismo panel que la página de advisee) */}
          <aside className="dash-profile">
            <p className="eyebrow" style={{ color: "var(--fg)", textAlign: "center", fontWeight: 500, margin: 0 }}>{t("ad.eyebrow")}</p>
            <div className="profile-photo-wrap">
              {advisee.foto
                ? <img src={advisee.foto} alt={advisee.nombre} className="profile-photo" />
                : <div className="profile-photo-placeholder">{advisee.nombre.charAt(0)}</div>
              }
              <div className="profile-id">
                <h1 className="profile-name">{advisee.nombre}</h1>
                {advisee.cargo && <p className="profile-cargo">{advisee.cargo}</p>}
              </div>
            </div>
          </aside>

          {/* RIGHT — plan de acción */}
          <div className="dash-main">
            <section>
              <h2 style={{ marginBottom: 8 }}>{t("adplan.page_title")}</h2>
              <p className="fine" style={{ marginBottom: 24, maxWidth: 640 }}>{t("adplan.page_desc")}</p>

              {plan === null ? (
                <p className="fine">{t("common.loading")}</p>
              ) : generando ? (
                <div style={{ display: "flex", alignItems: "center", gap: 12, paddingTop: 8 }}>
                  <span className="spinner" style={{ width: 18, height: 18, border: "2px solid var(--border)", borderTopColor: "var(--accent)", borderRadius: "50%", display: "inline-block", animation: "spin 0.8s linear infinite" }} />
                  <p className="fine" style={{ margin: 0 }}>{t("adplan.generating")}</p>
                </div>
              ) : editando ? (
                <>
                  <textarea
                    className="notas-ca-textarea"
                    rows={12}
                    value={borrador}
                    onChange={(e) => setBorrador(e.target.value)}
                    placeholder={t("adplan.none_yet")}
                    autoFocus
                  />
                  {error && <p className="error" style={{ marginTop: 8 }}>{error}</p>}
                  <div className="actions">
                    <button onClick={guardar} disabled={busy}>
                      {busy ? t("common.saving") : t("eaw.plan_save")}
                    </button>
                    <button className="secondary" onClick={() => setEditando(false)} disabled={busy}>
                      {t("common.cancel")}
                    </button>
                    {botonDudas}
                  </div>
                  {panelDudas}
                </>
              ) : plan ? (
                <>
                  <p style={{ margin: "0 0 16px", fontSize: 20, fontWeight: 500, color: "var(--fg)" }}>
                    {t("adplan.exists")}
                  </p>
                  <div style={{ border: "1px solid var(--border)", borderRadius: "var(--radius-lg)", padding: "18px 20px", background: "var(--bg)", whiteSpace: "pre-wrap", fontSize: 15, lineHeight: 1.6, color: "#000" }}>
                    {plan}
                  </div>
                  <p className="fine" style={{ marginTop: 10, maxWidth: 640 }}>{t("adplan.source_note")}</p>
                  <div className="actions">
                    <button onClick={editar}>{t("adplan.edit")}</button>
                    <button className="secondary" onClick={crearNuevo}>{t("adplan.create_new")}</button>
                    {botonDudas}
                  </div>
                  {panelDudas}
                  {error && <p className="error" style={{ marginTop: 12 }}>{error}</p>}
                </>
              ) : (
                <>
                  <p style={{ fontSize: 15, color: "var(--text-60)", lineHeight: 1.6, maxWidth: 640 }}>
                    {t("adplan.none", { nombre: advisee.nombre })}
                  </p>
                  <div className="actions">
                    <button onClick={crearNuevo}>{t("adplan.create_new")}</button>
                  </div>
                  {error && <p className="error" style={{ marginTop: 12 }}>{error}</p>}
                </>
              )}
            </section>
          </div>
        </div>
      </div>
      <Footer />
    </main>
  );
}

// ---------------------------------------------------------------------------
// Mis proyectos en activo (responsable de proyecto)
// ---------------------------------------------------------------------------

function MisProyectosActivosPage({ token, user, onBack }) {
  const [proyectos, setProyectos] = useState([]);
  const [loading, setLoading] = useState(true);
  const [estadoMap, setEstadoMap] = useState({});
  const [todosEmpleados, setTodosEmpleados] = useState([]);
  const [añadirMap, setAñadirMap] = useState({});
  const [añadirValor, setAñadirValor] = useState({});
  const [accionMsg, setAccionMsg] = useState({});
  const [enviandoRec, setEnviandoRec] = useState({});
  const [recMsg, setRecMsg] = useState({});

  async function enviarRecordatorio(proyecto) {
    setEnviandoRec((prev) => ({ ...prev, [proyecto]: true }));
    setRecMsg((prev) => ({ ...prev, [proyecto]: "" }));
    try {
      const data = await apiRequest("/api/recordatorio-proyecto", {
        token,
        method: "POST",
        body: { proyecto },
      });
      if (data.ok) {
        const n = (data.enviados || []).length;
        setRecMsg((prev) => ({
          ...prev,
          [proyecto]: data.sin_pendientes ? t("mpa.rec_none") : t("mpa.rec_sent", { n }),
        }));
      } else {
        setRecMsg((prev) => ({ ...prev, [proyecto]: data.error || t("mpa.rec_err") }));
      }
    } catch (err) {
      setRecMsg((prev) => ({ ...prev, [proyecto]: err.message }));
    } finally {
      setEnviandoRec((prev) => ({ ...prev, [proyecto]: false }));
    }
  }

  function cargarEstado(nombre) {
    apiRequest(`/api/estado-proyecto?proyecto=${encodeURIComponent(nombre)}`, { token })
      .then((d) => setEstadoMap((prev) => ({ ...prev, [nombre]: d.estado || [] })))
      .catch(() => setEstadoMap((prev) => ({ ...prev, [nombre]: [] })));
  }

  function cargarProyectos() {
    apiRequest("/api/proyectos-manager", { token })
      .then((d) => {
        const lista = d.proyectos || [];
        setProyectos(lista);
        lista.forEach((p) => cargarEstado(p.nombre_proyecto));
      })
      .catch(() => {})
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    cargarProyectos();
    apiRequest("/api/todos-empleados", { token })
      .then((d) => setTodosEmpleados(d.empleados || []))
      .catch(() => {});
  }, [token]);

  async function modificarMiembro(accion, proyecto, empleado) {
    setAccionMsg((prev) => ({ ...prev, [proyecto]: "" }));
    try {
      const data = await apiRequest("/api/modificar-equipo-proyecto", {
        token,
        method: "POST",
        body: { accion, proyecto, empleado },
      });
      if (data.ok) {
        setAccionMsg((prev) => ({ ...prev, [proyecto]: accion === "añadir" ? t("mpa.member_added", { emp: empleado }) : t("mpa.member_removed", { emp: empleado }) }));
        setAñadirValor((prev) => ({ ...prev, [proyecto]: "" }));
        setAñadirMap((prev) => ({ ...prev, [proyecto]: false }));
        cargarProyectos();
        cargarEstado(proyecto);
      } else {
        setAccionMsg((prev) => ({ ...prev, [proyecto]: data.error || t("mpa.err_modify") }));
      }
    } catch (err) {
      setAccionMsg((prev) => ({ ...prev, [proyecto]: err.message }));
    }
  }

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <NavBack onBack={onBack} />
      </nav>

      <div style={{ flex: 1, width: "100%", paddingTop: "clamp(44px, 6vw, 68px)", paddingBottom: 48 }}>
        <p className="eyebrow">{t("mpa.kicker")}</p>
        <h1>{t("mpa.title")}</h1>
        <p className="fine" style={{ marginTop: 10, marginBottom: 28, color: "#000" }}>{t("mpa.summary")}<em>{t("dash.nav_do_proj_evals")}</em>{t("mpa.summary_suffix")}</p>

        {loading ? (
          <div>
            {Array.from({ length: 2 }).map((_, i) => (
              <div key={i} className="card" style={{ marginBottom: 20 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 16 }}>
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    <Skeleton width={170} height={14} />
                    <Skeleton width={90} height={11} />
                  </div>
                  <Skeleton width={100} height={6} radius={3} />
                </div>
                <Skeleton height={40} radius={8} style={{ marginBottom: 8 }} />
                <Skeleton height={40} radius={8} />
              </div>
            ))}
          </div>
        ) : proyectos.length === 0 ? (
          <p className="fine">{t("mpa.no_projects")}</p>
        ) : (
          proyectos.map((p, idx) => {
            const nombre = p.nombre_proyecto;
            const estado = estadoMap[nombre];
            const mostrarAnadir = añadirMap[nombre];
            const valorAnadir = añadirValor[nombre] || "";
            const msg = accionMsg[nombre] || "";
            const equipoActual = p.equipo || [];
            const disponibles = todosEmpleados.filter((e) => !equipoActual.includes(e));
            const total = estado ? estado.length : equipoActual.length;
            // Cuántas evaluaciones de compañeros ha COMPLETADO cada persona (como evaluador).
            const norm = (s) => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
            const completadasMap = {};
            (estado || []).forEach((tgt) => (tgt.evaluadores || []).forEach((ev) => {
              const k = norm(ev);
              completadasMap[k] = (completadasMap[k] || 0) + 1;
            }));
            const totalCompaneros = Math.max((estado ? estado.length : equipoActual.length) - 1, 0);
            const hechasDe = (m) => m.n_completadas ?? completadasMap[norm(m.nombre)] ?? 0;
            const personaHaCompletado = (m) =>
              (totalCompaneros === 0 || hechasDe(m) >= totalCompaneros) && m.autoevaluacion_hecha;
            const done = estado ? estado.filter(personaHaCompletado).length : 0;
            const pct = total ? Math.round((done / total) * 100) : 0;
            const msgEsError = msg.includes("Error") || msg.includes("error");
            return (
              <div key={nombre} className="card stagger-item" style={{ marginBottom: 20, animationDelay: `${Math.min(idx, 8) * 0.05}s` }}>
                {/* Header */}
                <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, flexWrap: "wrap", marginBottom: 16 }}>
                  <div>
                    <p style={{ fontSize: 14, fontWeight: 500, color: "#000", marginBottom: 2 }}>{nombre}</p>
                    <p style={{ fontSize: 12, fontWeight: 200, color: "#000" }}>{t("mpa.progress", { done, total })}</p>
                  </div>
                  <ProgressBar pct={pct} barWidth={72} height={5} />
                </div>

                {/* Members table */}
                {!estado ? (
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    <Skeleton height={34} radius={6} />
                    <Skeleton height={34} radius={6} />
                  </div>
                ) : estado.length === 0 ? (
                  <p className="fine">{t("mpa.no_data")}</p>
                ) : (
                  <div style={{ overflowX: "auto" }}>
                    <table className="gest-table" style={{ tableLayout: "fixed", width: "100%" }}>
                      <thead>
                        <tr>
                          {[[t("mpa.col_member"), "25%"], [t("mpa.col_completed"), "25%"], [t("mpa.col_status"), "25%"], ["", "25%"]].map(([h, w], hi) => (
                            <th key={hi} style={{ width: w }}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {estado.map((m) => {
                          const nHechas = hechasDe(m);
                          const totalEvals = totalCompaneros + 1; // compañeros + autoevaluación
                          const hechasTotal = nHechas + (m.autoevaluacion_hecha ? 1 : 0);
                          const personaCompleto = personaHaCompletado(m);
                          const faltanPeers = Math.max(totalCompaneros - nHechas, 0);
                          const estadoTitle = personaCompleto
                            ? t("mpa.done_all")
                            : [
                                !m.autoevaluacion_hecha ? t("mpa.pending_self") : null,
                                faltanPeers > 0 ? t("mpa.pending_peers", { n: faltanPeers }) : null,
                              ].filter(Boolean).join(" · ");
                          return (
                            <tr key={m.nombre}>
                              <td>{m.nombre}</td>
                              <td>
                                <span className={`badge ${personaCompleto ? "badge-dark" : "badge-light"}`} title={estadoTitle}>{hechasTotal}/{totalEvals}</span>
                              </td>
                              <td>
                                <span className={`badge ${personaCompleto ? "badge-success" : "badge-danger"}`} title={estadoTitle}>
                                  {personaCompleto ? t("mpa.complete") : t("mpa.pending")}
                                </span>
                              </td>
                              <td>
                                <button
                                  className="mpa-remove"
                                  onClick={() => modificarMiembro("eliminar", nombre, m.nombre)}
                                  title={t("mpa.remove_member", { nombre: m.nombre })}
                                >
                                  <span className="mpa-remove-x" aria-hidden="true">✕</span>
                                  {t("mpa.remove_short")}
                                </button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}

                {msg && <p className={msgEsError ? "error" : "fine"} style={{ marginTop: 10 }}>{msg}</p>}

                {/* Actions */}
                <div style={{ display: "flex", gap: 8, marginTop: 16, paddingTop: 14, borderTop: "1px solid var(--border)", alignItems: "center", flexWrap: "wrap" }}>
                  {mostrarAnadir ? (
                    <>
                      <input
                        type="text"
                        list={`emp-list-${idx}`}
                        value={valorAnadir}
                        onChange={(e) => setAñadirValor((prev) => ({ ...prev, [nombre]: e.target.value }))}
                        placeholder={t("mpa.select_person")}
                        style={{ flex: 1, minWidth: 180 }}
                      />
                      <datalist id={`emp-list-${idx}`}>
                        {disponibles.map((e) => <option key={e} value={e} />)}
                      </datalist>
                      <button disabled={!disponibles.includes(valorAnadir)} onClick={() => modificarMiembro("añadir", nombre, valorAnadir)}>{t("mpa.add")}</button>
                      <button className="secondary" onClick={() => setAñadirMap((prev) => ({ ...prev, [nombre]: false }))}>{t("common.cancel")}</button>
                    </>
                  ) : (
                    <>
                      <button type="button" onClick={() => setAñadirMap((prev) => ({ ...prev, [nombre]: true }))} className="mpa-pill">
                        {t("mpa.add_member")}
                      </button>
                      <button type="button" onClick={() => enviarRecordatorio(nombre)} disabled={enviandoRec[nombre]} className="mpa-pill">
                        {enviandoRec[nombre] ? t("mpa.rec_sending") : t("mpa.rec_button")}
                      </button>
                    </>
                  )}
                </div>
                {recMsg[nombre] && <p className="fine" style={{ marginTop: 8 }}>{recMsg[nombre]}</p>}
              </div>
            );
          })
        )}
      </div>
      <Footer />
    </main>
  );
}

// Activar evaluaciones de proyecto (responsable de proyecto)
// ---------------------------------------------------------------------------

// Formato obligatorio del nombre de proyecto: AÑO_EMPRESA_NOMBRE
// (año de 4 dígitos + al menos dos tokens en MAYÚSCULAS/dígitos, sin espacios ni tildes).
const FORMATO_PROYECTO = /^\d{4}(_[A-Z0-9]+){2,}$/;

// Formatea una fecha "YYYY-MM-DD" como "DD/MM/YYYY" para mostrar. Vacío si no hay fecha.
function formatearFecha(iso) {
  if (!iso) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})/.exec(iso);
  return m ? `${m[3]}/${m[2]}/${m[1]}` : iso;
}

function ActivarEvaluacionesProyectoPage({ token, user, onBack, onActivado }) {
  const [proyecto, setProyecto] = useState("");
  const [todosEmpleados, setTodosEmpleados] = useState([]);
  const [seleccionados, setSeleccionados] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadingEmpleados, setLoadingEmpleados] = useState(true);
  const [status, setStatus] = useState("");
  const [enviado, setEnviado] = useState(false);
  const [busqueda, setBusqueda] = useState("");
  const [campoFocus, setCampoFocus] = useState(false);

  const persona = user?.persona || user?.username || "";

  useEffect(() => {
    apiRequest("/api/todos-empleados", { token })
      .then((d) => setTodosEmpleados(d.empleados || []))
      .catch(() => setTodosEmpleados([]))
      .finally(() => setLoadingEmpleados(false));
  }, [token]);

  function toggleEmpleado(nombre) {
    setSeleccionados((prev) =>
      prev.includes(nombre) ? prev.filter((n) => n !== nombre) : [...prev, nombre]
    );
    setBusqueda("");
  }

  function quitarEmpleado(nombre) {
    setSeleccionados((prev) => prev.filter((n) => n !== nombre));
  }

  async function activar(e) {
    e.preventDefault();
    if (!proyecto.trim()) { setStatus(t("aep.err_type_project")); return; }
    if (!FORMATO_PROYECTO.test(proyecto.trim())) { setStatus(t("aep.err_format")); return; }
    // Sin miembros seleccionados se permite: el proyecto se activa solo para ti.
    setLoading(true);
    setStatus("");
    try {
      const data = await apiRequest("/api/activar-evaluaciones-proyecto", {
        token,
        method: "POST",
        body: { proyecto: proyecto.trim(), empleados: seleccionados },
      });
      if (data.ok) {
        setStatus(t("aep.activated", { n: data.activados?.length || seleccionados.length }));
        setEnviado(true);
        if (onActivado) onActivado();
      } else {
        setStatus(data.error || t("aep.err_activate"));
      }
    } catch (err) {
      setStatus(err.message);
    } finally {
      setLoading(false);
    }
  }

  const filtrados = todosEmpleados.filter((n) => n.toLowerCase().includes(busqueda.toLowerCase().trim()));
  const canSubmit = FORMATO_PROYECTO.test(proyecto.trim()) && !loading;
  const plural = seleccionados.length !== 1;
  // En esta pantalla el status solo se muestra en el formulario cuando es un error
  // o validacion (el exito se muestra en la vista "enviado"). Siempre error aqui.
  const statusEsError = true;

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <NavBack onBack={onBack} />
      </nav>

      <div style={{ flex: 1, width: "100%", paddingTop: "clamp(44px, 6vw, 68px)", paddingBottom: 48 }}>
        <p className="eyebrow">{t("mpa.kicker")}</p>
        <h1>{t("aep.title")}</h1>
        <p className="fine" style={{ marginTop: 10, color: "#000" }}>
          {t("aep.desc")}
        </p>
        <hr style={{ border: "none", borderTop: "1px solid var(--border)", margin: "24px 0" }} />

        {enviado ? (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 14, color: "#000" }}>
              <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 22, height: 22, borderRadius: "50%", background: "#000", color: "#fff", fontSize: 12, flexShrink: 0 }}>✓</span>
              {status}
            </div>
            <div className="actions">
              <button onClick={() => { setEnviado(false); setProyecto(""); setSeleccionados([]); setStatus(""); setBusqueda(""); }}>
                {t("aep.activate_another")}
              </button>
              <button className="secondary" onClick={onBack}>{t("aep.back_home")}</button>
            </div>
          </>
        ) : (
          <form onSubmit={activar}>
            <label htmlFor="proj-name">{t("aep.project_name")}</label>
            <p className="fine" style={{ marginTop: -2, marginBottom: 8, color: "#000", fontSize: 11 }}>
              {t("aep.format_hint")}
            </p>
            <input
              id="proj-name"
              type="text"
              value={proyecto}
              onChange={(e) => setProyecto(e.target.value)}
              placeholder="2026_EMPRESA_NOMBRE"
              required
            />
            {proyecto.trim() && !FORMATO_PROYECTO.test(proyecto.trim()) && (
              <p style={{ marginTop: 6, marginBottom: 0, color: "var(--accent)", fontSize: 12 }}>
                {t("aep.format_bad")}
              </p>
            )}

            <label style={{ marginTop: 24 }}>{t("aep.team_members")}</label>
            {loadingEmpleados ? (
              <p className="fine">{t("aep.loading_employees")}</p>
            ) : (
              <>
                <div style={{
                  display: "flex", flexWrap: "wrap", alignItems: "center", gap: 6,
                  border: `1px solid ${campoFocus ? "var(--accent)" : "var(--border)"}`,
                  borderRadius: "var(--radius-md)",
                  padding: "6px 10px", minHeight: 38, background: "var(--bg)",
                  transition: "border-color .15s",
                }}>
                  <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" style={{ color: "rgba(0,0,0,.35)", flexShrink: 0 }}>
                    <circle cx="11" cy="11" r="8" /><line x1="21" y1="21" x2="16.65" y2="16.65" />
                  </svg>
                  {seleccionados.map((nombre) => (
                    <span
                      key={nombre}
                      style={{
                        display: "inline-flex", alignItems: "center", gap: 6,
                        background: "var(--surface)", border: "1px solid var(--border)",
                        borderRadius: "var(--radius-pill)", padding: "3px 6px 3px 10px", fontSize: 12, whiteSpace: "nowrap",
                      }}
                    >
                      {nombre}
                      <button
                        type="button"
                        onClick={() => quitarEmpleado(nombre)}
                        aria-label={`${t("aep.remove_member")} ${nombre}`}
                        style={{
                          background: "none", border: "none", padding: 0, minHeight: 0,
                          height: 16, width: 16, lineHeight: 1, cursor: "pointer",
                          color: "rgba(0,0,0,.5)", fontSize: 12,
                          display: "flex", alignItems: "center", justifyContent: "center",
                        }}
                      >
                        ✕
                      </button>
                    </span>
                  ))}
                  <input
                    type="text"
                    value={busqueda}
                    onChange={(e) => setBusqueda(e.target.value)}
                    onFocus={() => setCampoFocus(true)}
                    onBlur={() => setCampoFocus(false)}
                    placeholder={seleccionados.length ? "" : t("aep.search_by_name")}
                    style={{ flex: 1, minWidth: 100, border: "none", outline: "none", background: "transparent", fontSize: 13, padding: "4px 2px" }}
                  />
                </div>
                <div style={{ marginTop: 8, maxHeight: 220, overflowY: "auto", border: "1px solid var(--border)", borderRadius: "var(--radius-md)", background: "var(--bg)" }}>
                  {filtrados.map((nombre) => {
                    const checked = seleccionados.includes(nombre);
                    return (
                      <div
                        key={nombre}
                        onClick={() => toggleEmpleado(nombre)}
                        style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 12px", borderBottom: "1px solid var(--border)", cursor: "pointer", userSelect: "none" }}
                      >
                        <span style={{ width: 14, height: 14, borderRadius: 4, border: `1px solid ${checked ? "var(--accent)" : "var(--border)"}`, background: "var(--bg)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                          {checked && (
                            <svg width="9" height="7" viewBox="0 0 9 7" fill="none"><path d="M1 3.5l2.5 2.5 4.5-5" stroke="var(--accent)" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
                          )}
                        </span>
                        <span style={{ fontSize: 13, fontWeight: 400, color: "#000" }}>{nombre}</span>
                      </div>
                    );
                  })}
                  {filtrados.length === 0 && (
                    <p className="fine" style={{ margin: 0, padding: "12px" }}>{t("admin.no_results", { q: busqueda })}</p>
                  )}
                </div>
              </>
            )}

            <p style={{ fontSize: 12, fontWeight: 200, color: "rgba(0,0,0,.5)", marginTop: 12 }}>
              <strong style={{ fontWeight: 500 }}>{seleccionados.length}</strong>{" "}
              {plural ? t("aep.members_selected_many") : t("aep.members_selected_one")}
            </p>
            {seleccionados.length === 0 && (
              <p style={{ fontSize: 12, fontWeight: 200, color: "rgba(0,0,0,.5)", marginTop: 4 }}>
                {t("aep.solo_hint")}
              </p>
            )}

            {status && <p className={statusEsError ? "error" : "fine"} style={{ marginTop: 8 }}>{status}</p>}

            <button
              type="submit"
              disabled={!canSubmit}
              style={{
                marginTop: 16, height: 36, padding: "0 20px", borderRadius: "var(--radius-md)",
                fontSize: 13, letterSpacing: "0.02em", fontWeight: 500,
                background: "var(--bg)",
                border: `1px solid ${canSubmit ? "var(--accent)" : "var(--border)"}`,
                color: canSubmit ? "var(--accent)" : "rgba(0,0,0,.35)",
                cursor: canSubmit ? "pointer" : "not-allowed",
              }}
            >
              {loading
                ? t("aep.activating")
                : canSubmit
                  ? (seleccionados.length === 0
                      ? t("aep.activate_solo")
                      : t(plural ? "aep.activate_n_many" : "aep.activate_n_one", { n: seleccionados.length }))
                  : t("dash.nav_activate_proj")}
            </button>
          </form>
        )}
      </div>
      <Footer />
    </main>
  );
}

// ---------------------------------------------------------------------------
// Página de selección de tipo de evaluación de proyecto
// ---------------------------------------------------------------------------

// La lista de evaluaciones a hacer (y su tipo de plantilla) la decide el SERVIDOR
// por jerarquía de empresa (GET /api/evaluaciones-proyecto-a-hacer y el campo
// `a_hacer` de /api/proyectos-progreso). Aquí solo se construye la etiqueta visible.
function labelEvaluacionProyecto(tipo, evaluado) {
  if (tipo === "autoevaluacion") return t("fep.label_auto");
  const base = tipo === "miembros_a_manager"
    ? t("fep.label_manager")
    : tipo === "mismos_miembros"
      ? t("fep.label_peer")
      : t("fep.label_member");
  return `${base} — ${evaluado}`;
}

function EvaluacionesProyectoPage({ token, user, proyectos, onBack, onNavigate, completedEvals = {}, initialProyecto }) {
  const [proyectoSeleccionado, setProyectoSeleccionado] = useState(initialProyecto || proyectos[0]?.nombre_proyecto || "");
  const [evaluacionesAHacer, setEvaluacionesAHacer] = useState([]);
  const [loadingEquipo, setLoadingEquipo] = useState(false);
  const [completadasNotion, setCompletadasNotion] = useState([]);
  const [progresoProyectos, setProgresoProyectos] = useState({});
  const managerDelProyecto = proyectos.find((p) => p.nombre_proyecto === proyectoSeleccionado)?.activado_por || "";
  const persona = user?.persona || user?.username || "";

  useEffect(() => {
    if (!proyectoSeleccionado) return;
    setLoadingEquipo(true);
    setCompletadasNotion([]);
    Promise.all([
      // El servidor decide qué evaluación corresponde a cada compañero según la
      // JERARQUÍA DE EMPRESA (cargo en Notion), no según el rol en el proyecto.
      apiRequest(`/api/evaluaciones-proyecto-a-hacer?proyecto=${encodeURIComponent(proyectoSeleccionado)}`, { token }),
      apiRequest(`/api/evaluaciones-proyecto-completadas?proyecto=${encodeURIComponent(proyectoSeleccionado)}`, { token }),
    ])
      .then(([aHacerData, completadasData]) => {
        setEvaluacionesAHacer(aHacerData.evaluaciones || []);
        setCompletadasNotion((completadasData.completadas || []).map((c) => `${c.tipo}:${c.evaluado}`));
      })
      .catch(() => {})
      .finally(() => setLoadingEquipo(false));
  }, [token, proyectoSeleccionado]);

  // Progreso (done/total) de TODOS los proyectos activos, para no listar en el selector
  // los que ya tienes 100% completados (misma lógica que el dashboard).
  useEffect(() => {
    let cancelado = false;
    apiRequest("/api/proyectos-progreso", { token })
      .then((data) => {
        if (cancelado) return;
        const norm = (s) => (s || "").normalize("NFD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
        const prog = {};
        (data.proyectos || []).forEach((p) => {
          const hechas = (p.completadas || []).map((c) => `${c.tipo}:${norm(c.evaluado)}`);
          const lista = p.a_hacer || [];
          const done = lista.filter((it) => hechas.includes(`${it.tipo}:${norm(it.evaluado)}`)).length;
          prog[p.nombre_proyecto] = { done, total: lista.length };
        });
        setProgresoProyectos(prog);
      })
      .catch(() => {});
    return () => { cancelado = true; };
  }, [token, persona]);

  const items = evaluacionesAHacer.map(({ tipo, evaluado, relacion }) => {
    const evalKey = `${tipo}:${evaluado}`;
    const completado =
      (completedEvals[proyectoSeleccionado] || []).includes(evalKey) ||
      completadasNotion.includes(evalKey);
    return { tipo, evaluado, relacion, evalKey, completado };
  });
  const totalEvals = items.length;
  const doneEvals = items.filter((i) => i.completado).length;
  const pct = totalEvals ? Math.round((doneEvals / totalEvals) * 100) : 0;
  const shownPct = useCountUp(pct);
  const shownDone = useCountUp(doneEvals);
  const grupoAuto = items.filter((i) => i.tipo === "autoevaluacion");
  const grupoManager = items.filter((i) => i.tipo === "miembros_a_manager");
  const grupoMiembros = items.filter((i) => i.tipo === "mismos_miembros" || i.tipo === "manager_a_miembros");

  // Proyectos que se muestran en el selector: se ocultan los que ya tienes 100%
  // completados (0 pendientes). Mientras el progreso aún carga (prog undefined) se muestran.
  const proyectosVisibles = proyectos.filter((p) => {
    const prog = progresoProyectos[p.nombre_proyecto];
    return !prog || (prog.total - prog.done) > 0;
  });
  // Si el proyecto seleccionado acaba de completarse (ya no se lista) y hay otros
  // pendientes, salta al primero pendiente para no quedar en uno oculto.
  useEffect(() => {
    if (proyectosVisibles.length > 0 && !proyectosVisibles.some((p) => p.nombre_proyecto === proyectoSeleccionado)) {
      setProyectoSeleccionado(proyectosVisibles[0].nombre_proyecto);
    }
  }, [progresoProyectos, proyectoSeleccionado]); // eslint-disable-line react-hooks/exhaustive-deps

  const abrirFormulario = (it) =>
    onNavigate({ type: "formulario-evaluacion-proyecto", proyecto: proyectoSeleccionado, tipo: it.tipo, evaluado: it.evaluado, relacion: it.relacion, manager: managerDelProyecto, proyectos });
  const abrirHistorial = (it) =>
    onNavigate({ type: "historial-evaluaciones", evaluado: it.evaluado, evaluador: persona, proyecto: proyectoSeleccionado, from: "evaluaciones-proyecto", proyectos });

  const renderRow = (it, showHistorial) => (
    <div key={it.evalKey} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
      <div
        onClick={it.completado ? undefined : () => abrirFormulario(it)}
        style={{ cursor: it.completado ? "default" : "pointer", flex: 1, minWidth: 0 }}
        title={it.completado ? "" : t("ep.fill_eval")}
      >
        <p style={{ fontSize: 14, fontWeight: 400, color: "#000", display: "flex", alignItems: "center", gap: 6 }}>
          {it.evaluado}
          {!it.completado && (
            <svg viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13, flexShrink: 0 }}>
              <path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" />
              <polyline points="15 3 21 3 21 9" />
              <line x1="10" y1="14" x2="21" y2="3" />
            </svg>
          )}
        </p>
        <p style={{ fontSize: 12, fontWeight: 200, color: it.completado ? "rgba(0,0,0,.4)" : "var(--accent)" }}>
          {it.completado ? t("ep.completed") : t("ep.pending")}
        </p>
      </div>
      {showHistorial && (
        <button className="btn-historial" onClick={() => abrirHistorial(it)} type="button">
          {t("ep.history", { nombre: it.evaluado, proyecto: proyectoSeleccionado })}
        </button>
      )}
    </div>
  );

  const EvalSection = ({ title, children }) => (
    <div style={{ marginBottom: 28 }}>
      <p className="eyebrow" style={{ marginBottom: 10, paddingBottom: 8, borderBottom: "1px solid var(--border)" }}>{title}</p>
      <div>{children}</div>
    </div>
  );

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <NavBack onBack={onBack} />
      </nav>

      <div style={{ flex: 1, width: "100%", paddingTop: "clamp(44px, 6vw, 68px)", paddingBottom: 48 }}>
        <p className="eyebrow">{t("ep.kicker")}</p>
        <h1 style={{ marginBottom: 24 }}>{proyectoSeleccionado || t("dash.nav_proj_evals")}</h1>

        {proyectosVisibles.length > 1 && (
          <div style={{ marginBottom: 28, maxWidth: 360 }}>
            <label htmlFor="proj-sel">{t("ep.project_label")}</label>
            <select id="proj-sel" value={proyectoSeleccionado} onChange={(e) => setProyectoSeleccionado(e.target.value)}>
              {proyectosVisibles.map((p) => (
                <option key={p.nombre_proyecto} value={p.nombre_proyecto}>{p.nombre_proyecto}</option>
              ))}
            </select>
          </div>
        )}

        {proyectoSeleccionado && (
          loadingEquipo ? (
            <p className="fine">{t("common.loading")}</p>
          ) : (
            <>
              <div style={{ marginBottom: 36 }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", marginBottom: 8 }}>
                  <span style={{ fontSize: 13, fontWeight: 200, color: "#000" }}>{t("ep.progress")}</span>
                  <span style={{ fontSize: 13, fontWeight: 400, color: "#000" }}>{t("ep.progress_stat", { done: shownDone, total: totalEvals, pct: shownPct })}</span>
                </div>
                <div style={{ height: 6, background: "var(--border)", borderRadius: 3, overflow: "hidden" }}>
                  <div style={{ height: "100%", width: `${shownPct}%`, background: "var(--accent)", opacity: 0.6, borderRadius: 3, transition: "width .1s linear" }} />
                </div>
              </div>

              {grupoAuto.length > 0 && (
                <EvalSection title={t("ep.section_auto")}>
                  {grupoAuto.map((it) => renderRow(it, false))}
                </EvalSection>
              )}
              {grupoManager.length > 0 && (
                <EvalSection title={t("ep.section_manager")}>
                  {grupoManager.map((it) => renderRow(it, true))}
                </EvalSection>
              )}
              {grupoMiembros.length > 0 && (
                <EvalSection title={t("ep.section_members")}>
                  {grupoMiembros.map((it) => renderRow(it, true))}
                </EvalSection>
              )}
            </>
          )
        )}
      </div>
      <Footer />
    </main>
  );
}

// ---------------------------------------------------------------------------
// Formulario de evaluación de proyecto
// ---------------------------------------------------------------------------

function FormularioEvaluacionProyecto({ token, user, proyecto, tipo, manager, evaluadoProp, relacion = "", onBack, onEnviado }) {
  const persona = user?.persona || user?.username || "";
  // La clave incluye al evaluado: sin él, los borradores de dos compañeros distintos
  // del mismo tipo se pisaban entre sí.
  const draftKey = `evaluabot_borrador:${persona}:${proyecto}:${tipo}:${evaluadoProp || ""}`;

  // Lee el borrador guardado una sola vez, en el primer render, para inicializar
  // el estado directamente (evita carreras con los efectos de autoguardado).
  const borradorInicial = useMemo(() => {
    try {
      const raw = localStorage.getItem(draftKey);
      if (raw) {
        const d = JSON.parse(raw);
        if (d && d.respuestas && Object.keys(d.respuestas).length > 0) return d;
      }
    } catch { /* ignorar borradores corruptos */ }
    return null;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [preguntas, setPreguntas] = useState(null);
  const [todosEmpleados, setTodosEmpleados] = useState([]);
  const [evaluado, setEvaluado] = useState(borradorInicial?.evaluado || "");
  const [respuestas, setRespuestas] = useState(borradorInicial?.respuestas || {});
  const [enviando, setEnviando] = useState(false);
  const [status, setStatus] = useState("");
  const [enviado, setEnviado] = useState(false);
  const [borradorMsg, setBorradorMsg] = useState("");
  const [borradorRestaurado, setBorradorRestaurado] = useState(Boolean(borradorInicial));
  const [confirmando, setConfirmando] = useState(false);
  // Si el usuario ya empezó a escribir, el borrador del servidor (que llega async)
  // no debe pisar lo que tiene en pantalla.
  const usuarioTocoRef = React.useRef(false);

  // Top-to-bottom: el evaluador está por ENCIMA del evaluado en la jerarquía de
  // empresa. Solo en este caso la evaluación se libera al evaluado y hay que
  // confirmar el envío.
  const esTopToBottom = relacion === "superior" && tipo !== "autoevaluacion";
  // Bottom-to-top y same level: la evaluación NO se libera al evaluado, la recibe
  // su CA. Se avisa igualmente antes de enviar por si quiere revisar respuestas.
  const esConfidencial =
    (relacion === "inferior" || relacion === "igual") && tipo !== "autoevaluacion";

  const LABELS_TIPOS = {
    autoevaluacion: t("fep.label_auto"),
    mismos_miembros: t("fep.label_peer"),
    miembros_a_manager: t("fep.label_manager"),
    manager_a_miembros: t("fep.label_member"),
  };
  const tipoLabel = evaluadoProp && tipo !== "autoevaluacion"
    ? `${LABELS_TIPOS[tipo] || tipo} — ${evaluadoProp}`
    : LABELS_TIPOS[tipo] || tipo;

  const necesitaSelector = !evaluadoProp && (tipo === "mismos_miembros" || tipo === "manager_a_miembros");
  const evaluadoFijo = evaluadoProp || (tipo === "autoevaluacion" ? persona : tipo === "miembros_a_manager" ? manager : "");

  useEffect(() => {
    apiRequest(`/api/preguntas-evaluacion-proyecto?tipo=${encodeURIComponent(tipo)}`, { token })
      .then((d) => setPreguntas(d.preguntas || []))
      .catch(() => setPreguntas([]));
  }, [token, tipo]);

  // Borrador del SERVIDOR (fuente de verdad, disponible desde cualquier dispositivo).
  // Se aplica solo si es más reciente que el local y el usuario aún no ha escrito.
  useEffect(() => {
    let cancelado = false;
    apiRequest(`/api/borrador-evaluacion-proyecto?proyecto=${encodeURIComponent(proyecto)}&tipo=${encodeURIComponent(tipo)}&evaluado=${encodeURIComponent(evaluadoProp || "")}`, { token })
      .then((d) => {
        if (cancelado || usuarioTocoRef.current) return;
        const serv = d.borrador;
        if (!serv || !serv.respuestas || Object.keys(serv.respuestas).length === 0) return;
        const tsServidor = serv.actualizado ? Date.parse(serv.actualizado) || 0 : 0;
        const tsLocal = borradorInicial?.ts || 0;
        if (tsServidor >= tsLocal) {
          setRespuestas(serv.respuestas);
          setBorradorRestaurado(true);
        }
      })
      .catch(() => { /* sin borrador remoto o sin conexión: seguimos con el local */ });
    return () => { cancelado = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, proyecto, tipo, evaluadoProp]);

  useEffect(() => {
    if (!necesitaSelector) return;
    apiRequest("/api/todos-empleados", { token })
      .then((d) => setTodosEmpleados(d.empleados || []))
      .catch(() => {});
  }, [token, necesitaSelector]);

  // Autoguarda el progreso en cada cambio (para poder completar después).
  // Nunca borra el borrador aquí: eso se hace solo al enviar o al descartar,
  // para no perder datos por una carrera con la inicialización del estado.
  useEffect(() => {
    if (enviado) return;
    if (Object.keys(respuestas).length === 0) return;
    try {
      localStorage.setItem(draftKey, JSON.stringify({ evaluado, respuestas, ts: Date.now() }));
    } catch { /* almacenamiento no disponible */ }
  }, [respuestas, evaluado, draftKey, enviado]);

  function setRespuesta(id, valor) {
    usuarioTocoRef.current = true;
    setRespuestas((prev) => ({ ...prev, [id]: valor }));
  }

  const evaluadoFinal = necesitaSelector ? evaluado : evaluadoFijo;

  async function guardarProgreso() {
    try {
      localStorage.setItem(draftKey, JSON.stringify({ evaluado, respuestas, ts: Date.now() }));
    } catch { /* almacenamiento no disponible */ }
    setStatus("");
    try {
      await apiRequest("/api/borrador-evaluacion-proyecto", {
        token,
        method: "POST",
        body: { proyecto, tipo, evaluado: evaluadoProp || evaluadoFinal || "", respuestas },
      });
      setBorradorMsg(t("fep.progress_saved"));
    } catch {
      setBorradorMsg(t("fep.err_save"));
    }
  }

  function descartarBorrador() {
    try { localStorage.removeItem(draftKey); } catch { /* noop */ }
    apiRequest("/api/borrador-evaluacion-proyecto/eliminar", {
      token,
      method: "POST",
      body: { proyecto, tipo, evaluado: evaluadoProp || "" },
    }).catch(() => {});
    setRespuestas({});
    setEvaluado("");
    setBorradorRestaurado(false);
    setBorradorMsg("");
    setStatus("");
  }

  async function enviar(e) {
    e.preventDefault();
    if (!evaluadoFinal) { setStatus(t("fep.err_select_person")); return; }
    if (preguntas && preguntas.some((p) => !String(respuestas[p.id] || "").trim())) {
      setStatus(t("fep.err_required"));
      return;
    }
    // Top-to-bottom: el evaluado podrá VER esta evaluación, así que el envío nunca
    // es directo — primero el aviso con "Guardar borrador" / "Enviar".
    if (esTopToBottom || esConfidencial) {
      setStatus("");
      setConfirmando(true);
      return;
    }
    await realizarEnvio();
  }

  async function guardarBorradorDesdeAviso() {
    setConfirmando(false);
    await guardarProgreso();
  }

  async function realizarEnvio() {
    setEnviando(true);
    setStatus("");
    setBorradorMsg("");
    try {
      const data = await apiRequest("/api/guardar-evaluacion-proyecto", {
        token,
        method: "POST",
        body: { proyecto, tipo, evaluado: evaluadoFinal, respuestas },
      });
      if (data.ok) {
        setConfirmando(false);
        setEnviado(true);
        setStatus(t("fep.saved_notion"));
        try { localStorage.removeItem(draftKey); } catch { /* noop */ }
        if (onEnviado) onEnviado();
      } else {
        setConfirmando(false);
        setStatus(data.error || t("fep.err_save"));
      }
    } catch (err) {
      setConfirmando(false);
      setStatus(err.message);
    } finally {
      setEnviando(false);
    }
  }

  if (preguntas === null) {
    return (
      <main className="page">
        <nav className="nav">
          <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
          <NavBack onBack={onBack} />
        </nav>
        <div style={{ padding: "40px", maxWidth: 820, margin: "0 auto", width: "100%" }}>
          <SkeletonForm rows={4} />
        </div>
      </main>
    );
  }

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <NavBack onBack={onBack} />
      </nav>
      <section className="hero">
        <div>
          <p className="kicker">{proyecto}</p>
          <h1 style={{ fontSize: "clamp(24px,4vw,52px)", lineHeight: 1.1 }}>{tipoLabel}</h1>
        </div>
      </section>

      {enviado ? (
        <section className="panel" style={{ marginTop: "32px" }}>
          <SavedOk text={t("fep.saved_ok")} />
          <div className="actions">
            <button className="secondary" onClick={onBack}>{t("auth.back_word")}</button>
          </div>
        </section>
      ) : (
        <form className="panel" style={{ marginTop: "32px" }} onSubmit={enviar}>
          {borradorRestaurado && (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, flexWrap: "wrap", background: "#F5F5F7", border: "1px solid #DBDBDE", borderRadius: "8px", padding: "12px 14px", marginBottom: "20px" }}>
              <span className="fine" style={{ margin: 0 }}>{t("fep.draft_restored")}</span>
              <button type="button" className="link-button" onClick={descartarBorrador}>{t("fep.discard_draft")}</button>
            </div>
          )}
          {necesitaSelector && (
            <>
              <label>{t("fep.person_to_eval")}</label>
              <select value={evaluado} onChange={(e) => setEvaluado(e.target.value)} required>
                <option value="">{t("fep.select_dash")}</option>
                {todosEmpleados.filter((n) => n !== persona).map((nombre) => (
                  <option key={nombre} value={nombre}>{nombre}</option>
                ))}
              </select>
            </>
          )}
          {!necesitaSelector && evaluadoFijo && (
            <p className="fine" style={{ marginBottom: "16px", color: "#000" }}>
              {tipo === "autoevaluacion" ? t("fep.evaluating_self", { nombre: evaluadoFijo }) : t("fep.evaluating", { nombre: evaluadoFijo })}
            </p>
          )}
          {tipo === "autoevaluacion" && (
            <p className="fine" style={{ marginBottom: "16px", color: "#000" }}>
              {t("fep.self_only_ca")}
            </p>
          )}
          {esConfidencial && evaluadoFinal && (
            <p className="fine" style={{ marginBottom: "16px", color: "#000" }}>
              {t("fep.confidential_note", { nombre: evaluadoFinal })}
            </p>
          )}

          {preguntas.length === 0 && (
            <p className="fine">{t("fep.no_questions")}</p>
          )}

          {(() => {
            let categoriaActual = null;
            return preguntas.map((p) => {
              const cambioCat = p.categoria && p.categoria !== categoriaActual;
              if (cambioCat) categoriaActual = p.categoria;
              const textoEsCategoria = (p.texto || "").trim().toLowerCase() === (p.categoria || "").trim().toLowerCase();
              const mostrarLabel = p.tipo !== "radio_3" && !textoEsCategoria && Boolean(p.texto);
              const opcionesBase = p.tipo === "radio_3"
                ? (p.opciones?.length ? p.opciones : ["Exceeds", "Achieves", "Expects more"])
                : [];
              const opciones = [...opcionesBase].reverse();
              return (
                <React.Fragment key={p.id}>
                  {cambioCat && (
                    <div style={{ marginTop: "32px", paddingBottom: "10px", borderBottom: "1px solid #DBDBDE" }}>
                      <span style={{ fontSize: "13px", fontWeight: 400, textTransform: "uppercase", letterSpacing: "0.1em", color: "rgba(0,0,0,0.55)" }}>
                        {p.categoria}
                      </span>
                    </div>
                  )}
                  <div style={{ marginTop: "18px" }}>
                    {mostrarLabel && (
                      <label style={{ fontWeight: 400, fontSize: "14px", marginBottom: "12px", display: "block", color: "#000000", textTransform: "none", letterSpacing: "normal" }}>
                        {p.texto} <span style={{ color: "#C1121F" }} aria-hidden="true">*</span>
                      </label>
                    )}
                    {p.tipo === "escala_1_5" && (
                      <div style={{ display: "flex", gap: "12px", flexWrap: "wrap", alignItems: "center" }}>
                        <span className="fine" style={{ fontSize: "12px", color: "#000", fontWeight: 400, opacity: 0.65 }}>{t("fep.scale_low")}</span>
                        <div style={{ display: "flex", border: "1px solid #DBDBDE", borderRadius: "8px", overflow: "hidden", width: "100%", maxWidth: "220px" }}>
                          {[1, 2, 3, 4, 5].map((val, idx) => {
                            const selected = respuestas[p.id] === String(val);
                            return (
                              <label
                                key={val}
                                className="eval-seg"
                                style={{
                                  flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
                                  padding: "4px 6px", cursor: "pointer",
                                  background: selected ? "#000000" : "#FFFFFF",
                                  color: selected ? "#FFFFFF" : "#000",
                                  borderLeft: idx > 0 ? "1px solid #DBDBDE" : "none",
                                  userSelect: "none", transition: "background 0.15s, color 0.15s",
                                }}
                              >
                                <input
                                  type="radio"
                                  name={p.id}
                                  value={String(val)}
                                  checked={selected}
                                  onChange={() => setRespuesta(p.id, String(val))}
                                  style={{ position: "absolute", opacity: 0, width: 0, height: 0, pointerEvents: "none" }}
                                />
                                <span className="eval-seg-text" style={{ display: "inline-block", fontSize: "14px", fontWeight: 400 }}>{val}</span>
                              </label>
                            );
                          })}
                        </div>
                        <span className="fine" style={{ fontSize: "12px", color: "#000", fontWeight: 400, opacity: 0.65 }}>{t("fep.scale_high")}</span>
                      </div>
                    )}
                    {p.tipo === "radio_3" && (
                      <div style={{ display: "flex", border: "1px solid #DBDBDE", borderRadius: "8px", overflow: "hidden", width: "100%", maxWidth: "480px" }}>
                        {opciones.map((op, idx) => {
                          const selected = respuestas[p.id] === op;
                          return (
                            <label
                              key={op}
                              className="eval-seg"
                              style={{
                                flex: 1,
                                display: "flex",
                                alignItems: "center",
                                justifyContent: "center",
                                padding: "14px 8px",
                                cursor: "pointer",
                                background: selected ? "#000000" : "#FFFFFF",
                                color: selected ? "#FFFFFF" : "#000",
                                borderLeft: idx > 0 ? "1px solid #DBDBDE" : "none",
                                userSelect: "none",
                                transition: "background 0.15s, color 0.15s",
                              }}
                            >
                              <input
                                type="radio"
                                name={p.id}
                                value={op}
                                checked={selected}
                                onChange={() => setRespuesta(p.id, op)}
                                style={{ position: "absolute", opacity: 0, width: 0, height: 0, pointerEvents: "none" }}
                              />
                              <span className="eval-seg-text" style={{ display: "inline-block", fontSize: "11px", fontWeight: 400, letterSpacing: "0.1em", textTransform: "uppercase" }}>
                                {op}
                              </span>
                            </label>
                          );
                        })}
                      </div>
                    )}
                    {p.tipo === "abierta" && (
                      <textarea
                        value={respuestas[p.id] || ""}
                        onChange={(e) => setRespuesta(p.id, e.target.value)}
                        rows={4}
                        style={{ width: "100%", border: "1px solid #DBDBDE", borderRadius: "6px", padding: "12px 14px", fontSize: "14px", lineHeight: "1.6", resize: "vertical", background: "transparent", color: "#000000", outline: "none", fontFamily: "inherit", boxSizing: "border-box" }}
                        placeholder={t("cep.ph_answer")}
                      />
                    )}
                  </div>
                </React.Fragment>
              );
            });
          })()}

          {status && <p className="error" style={{ marginTop: "16px" }}>{status}</p>}
          {borradorMsg && (
            <p className="fine" style={{ marginTop: "16px", color: "#166534" }}>{borradorMsg}</p>
          )}
          <div className="actions">
            <button type="submit" disabled={enviando || preguntas.length === 0}>
              {enviando ? t("common.saving") : t("fep.submit")}
            </button>
            <button
              type="button"
              className="secondary"
              onClick={guardarProgreso}
              disabled={enviando || Object.keys(respuestas).length === 0}
            >
              {esTopToBottom ? t("fep.save_draft") : t("fep.save_progress")}
            </button>
          </div>
        </form>
      )}

      {confirmando && (
        <div
          role="dialog"
          aria-modal="true"
          style={{ position: "fixed", inset: 0, background: "rgba(0,0,0,.45)", display: "flex", alignItems: "flex-end", justifyContent: "center", zIndex: 1000, padding: "16px 16px 12vh", overflowY: "auto" }}
        >
          <div style={{ background: "#FFFFFF", borderRadius: 12, padding: "28px 26px", maxWidth: 520, width: "100%", maxHeight: "80vh", overflowY: "auto", boxShadow: "0 12px 40px rgba(0,0,0,.25)" }}>
            <p style={{ fontSize: 15, lineHeight: 1.6, color: "#000", margin: 0 }}>
              {esConfidencial
                ? t("fep.confirm_send_text_ca", { nombre: evaluadoFinal })
                : t("fep.confirm_send_text", { nombre: evaluadoFinal })}
            </p>
            <div className="actions" style={{ marginTop: 22 }}>
              <button type="button" onClick={realizarEnvio} disabled={enviando}>
                {enviando ? t("common.saving") : t("fep.send")}
              </button>
              <button type="button" className="secondary" onClick={guardarBorradorDesdeAviso} disabled={enviando}>
                {t("fep.save_draft")}
              </button>
            </div>
          </div>
        </div>
      )}
      <Footer />
    </main>
  );
}

// ---------------------------------------------------------------------------
// Evaluaciones extra (fuera de proyecto)
// ---------------------------------------------------------------------------

function SolicitarEvaluacionExtraPage({ token, user, onBack }) {
  const [todosEmpleados, setTodosEmpleados] = useState([]);
  const [loadingEmpleados, setLoadingEmpleados] = useState(true);
  const [busqueda, setBusqueda] = useState("");
  const [evaluador, setEvaluador] = useState("");
  const [contexto, setContexto] = useState("");
  const [loading, setLoading] = useState(false);
  const [status, setStatus] = useState("");
  const [enviado, setEnviado] = useState(false);

  const persona = user?.persona || user?.username || "";

  useEffect(() => {
    apiRequest("/api/todos-empleados", { token })
      .then((d) => setTodosEmpleados((d.empleados || []).filter((n) => n !== persona)))
      .catch(() => setTodosEmpleados([]))
      .finally(() => setLoadingEmpleados(false));
  }, [token, persona]);

  async function enviar(e) {
    e.preventDefault();
    if (!evaluador) { setStatus(t("sex.err_select_employee")); return; }
    if (!contexto.trim()) { setStatus(t("sex.err_context")); return; }
    setLoading(true);
    setStatus("");
    try {
      const data = await apiRequest("/api/solicitar-evaluacion-extra", {
        token,
        method: "POST",
        body: { evaluador, contexto: contexto.trim() },
      });
      if (data.ok) {
        setStatus(t("sex.sent", { nombre: evaluador }));
        setEnviado(true);
      } else {
        setStatus(data.error || t("sex.err_send"));
      }
    } catch (err) {
      setStatus(err.message);
    } finally {
      setLoading(false);
    }
  }

  const filtrados = todosEmpleados.filter((n) => n.toLowerCase().includes(busqueda.toLowerCase().trim()));
  const canSubmit = Boolean(evaluador) && contexto.trim().length > 0 && !loading;

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <NavBack onBack={onBack} />
      </nav>

      <div style={{ flex: 1, width: "100%", paddingTop: "clamp(44px, 6vw, 68px)", paddingBottom: 48 }}>
        <p className="eyebrow">{t("sex.kicker")}</p>
        <h1>{t("sex.title")}</h1>
        <p className="fine" style={{ marginTop: 10, color: "#000" }}>
          {t("sex.desc")}
        </p>
        <hr style={{ border: "none", borderTop: "1px solid var(--border)", margin: "24px 0" }} />

        {enviado ? (
          <>
            <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 14, color: "#000" }}>
              <span style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", width: 22, height: 22, borderRadius: "50%", background: "#000", color: "#fff", fontSize: 12, flexShrink: 0 }}>✓</span>
              {status}
            </div>
            <div className="actions">
              <button onClick={() => { setEnviado(false); setEvaluador(""); setContexto(""); setStatus(""); setBusqueda(""); }}>
                {t("sex.request_another")}
              </button>
              <button className="secondary" onClick={onBack}>{t("sex.back_home")}</button>
            </div>
          </>
        ) : (
          <form onSubmit={enviar}>
            <label>{t("sex.who_label")}</label>
            {loadingEmpleados ? (
              <p className="fine">{t("aep.loading_employees")}</p>
            ) : (
              <>
                <div style={{ position: "relative" }}>
                  <span style={{ position: "absolute", left: 11, top: "50%", transform: "translateY(-50%)", color: "rgba(0,0,0,.35)", display: "flex", pointerEvents: "none" }}>
                    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"><circle cx="11" cy="11" r="8"/><line x1="21" y1="21" x2="16.65" y2="16.65"/></svg>
                  </span>
                  <input
                    type="text"
                    value={busqueda}
                    onChange={(e) => { setBusqueda(e.target.value); setEvaluador(""); }}
                    placeholder={t("aep.search_by_name")}
                    style={{ paddingLeft: 32 }}
                  />
                </div>
                <div style={{ marginTop: 8, maxHeight: 220, overflowY: "auto", border: "1px solid var(--border)", borderRadius: "var(--radius-md)", background: "#fff" }}>
                  {filtrados.map((nombre) => {
                    const selected = evaluador === nombre;
                    return (
                      <div
                        key={nombre}
                        onClick={() => { setEvaluador(nombre); setBusqueda(nombre); }}
                        style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 12px", borderBottom: "1px solid var(--border)", cursor: "pointer", userSelect: "none", background: selected ? "rgba(0,0,0,.04)" : "transparent" }}
                      >
                        <span style={{ width: 14, height: 14, borderRadius: "50%", border: `1px solid ${selected ? "#000" : "var(--border)"}`, background: selected ? "#000" : "#fff", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                          {selected && <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#fff" }} />}
                        </span>
                        <span style={{ fontSize: 13, fontWeight: 400, color: "#000" }}>{nombre}</span>
                      </div>
                    );
                  })}
                  {filtrados.length === 0 && (
                    <p className="fine" style={{ margin: 0, padding: "12px" }}>{t("admin.no_results", { q: busqueda })}</p>
                  )}
                </div>
              </>
            )}

            <label style={{ marginTop: 24 }}>{t("sex.context_label")}</label>
            <p className="fine" style={{ marginTop: -2, marginBottom: 8, color: "#000", fontSize: 11 }}>
              {t("sex.context_hint")}
            </p>
            <textarea
              value={contexto}
              onChange={(e) => setContexto(e.target.value)}
              rows={4}
              placeholder={t("sex.context_placeholder")}
              style={{ width: "100%", border: "1px solid #DBDBDE", borderRadius: "6px", padding: "12px 14px", fontSize: "14px", lineHeight: "1.6", resize: "vertical", background: "transparent", color: "#000000", outline: "none", fontFamily: "inherit", boxSizing: "border-box" }}
            />

            {status && <p className="error" style={{ marginTop: 8 }}>{status}</p>}

            <button
              type="submit"
              disabled={!canSubmit}
              style={{
                marginTop: 16, height: 36, padding: "0 20px", borderRadius: "var(--radius-pill)",
                border: "none", fontSize: 13, letterSpacing: "0.02em",
                background: canSubmit ? "var(--accent)" : "var(--border)",
                color: canSubmit ? "#fff" : "rgba(0,0,0,.35)",
                cursor: canSubmit ? "pointer" : "not-allowed",
              }}
            >
              {loading ? t("sex.sending") : t("sex.submit")}
            </button>
          </form>
        )}
      </div>
      <Footer />
    </main>
  );
}

function FormularioEvaluacionExtra({ token, evaluado, contexto, solicitudPageId, onBack }) {
  const [nota, setNota] = useState(null);
  const [justificacion, setJustificacion] = useState("");
  const [enviando, setEnviando] = useState(false);
  const [status, setStatus] = useState("");
  const [enviado, setEnviado] = useState(false);

  async function enviar(e) {
    e.preventDefault();
    if (!nota) { setStatus(t("fex.err_score")); return; }
    if (!justificacion.trim()) { setStatus(t("fex.err_justification")); return; }
    setEnviando(true);
    setStatus("");
    try {
      const data = await apiRequest("/api/guardar-evaluacion-extra", {
        token,
        method: "POST",
        body: { evaluado, contexto, nota, justificacion: justificacion.trim(), solicitudPageId },
      });
      if (data.ok) {
        setEnviado(true);
      } else {
        setStatus(data.error || t("fex.err_save"));
      }
    } catch (err) {
      setStatus(err.message);
    } finally {
      setEnviando(false);
    }
  }

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <NavBack onBack={onBack} />
      </nav>
      <section className="hero">
        <div>
          <p className="kicker">{t("fex.kicker")}</p>
          <h1 style={{ fontSize: "clamp(24px,4vw,52px)", lineHeight: 1.1 }}>{t("fex.title", { nombre: evaluado })}</h1>
        </div>
      </section>

      {enviado ? (
        <section className="panel" style={{ marginTop: "32px" }}>
          <SavedOk text={t("fex.saved_ok")} />
          <div className="actions">
            <button className="secondary" onClick={onBack}>{t("auth.back_word")}</button>
          </div>
        </section>
      ) : (
        <form className="panel" style={{ marginTop: "32px" }} onSubmit={enviar}>
          <p className="fine" style={{ marginBottom: 16 }}>{t("fex.context_label")}</p>
          <p style={{ fontSize: 14, marginBottom: 24 }}>{contexto}</p>

          <label style={{ fontWeight: 400, fontSize: "14px", marginBottom: "12px", display: "block", color: "#000000" }}>
            {t("fex.score_label")}
          </label>
          <div style={{ display: "flex", border: "1px solid #DBDBDE", borderRadius: "8px", overflow: "hidden", width: "100%", maxWidth: "320px" }}>
            {[1, 2, 3, 4, 5].map((val, idx) => (
              <label
                key={val}
                className="eval-seg"
                style={{
                  flex: 1, display: "flex", alignItems: "center", justifyContent: "center",
                  padding: "14px 8px", cursor: "pointer",
                  background: nota === val ? "#000000" : "#FFFFFF",
                  color: nota === val ? "#FFFFFF" : "rgba(0,0,0,0.55)",
                  borderLeft: idx > 0 ? "1px solid #DBDBDE" : "none",
                  userSelect: "none", transition: "background 0.15s, color 0.15s",
                }}
              >
                <input
                  type="radio"
                  name="nota"
                  value={val}
                  checked={nota === val}
                  onChange={() => setNota(val)}
                  style={{ position: "absolute", opacity: 0, width: 0, height: 0, pointerEvents: "none" }}
                />
                <span className="eval-seg-text" style={{ display: "inline-block", fontSize: "14px", fontWeight: 400 }}>{val}</span>
              </label>
            ))}
          </div>

          <label style={{ marginTop: 24, display: "block" }}>{t("fex.justification_label")}</label>
          <textarea
            value={justificacion}
            onChange={(e) => setJustificacion(e.target.value)}
            rows={5}
            placeholder={t("cep.ph_answer")}
            style={{ width: "100%", border: "1px solid #DBDBDE", borderRadius: "6px", padding: "12px 14px", fontSize: "14px", lineHeight: "1.6", resize: "vertical", background: "transparent", color: "#000000", outline: "none", fontFamily: "inherit", boxSizing: "border-box" }}
          />

          {status && <p className="error" style={{ marginTop: "16px" }}>{status}</p>}
          <div className="actions">
            <button type="submit" disabled={enviando}>
              {enviando ? t("common.saving") : t("fex.submit")}
            </button>
          </div>
        </form>
      )}
      <Footer />
    </main>
  );
}

function EvaluacionesSlackPage({ token, user, onBack, onNavigate, completadasApp = {}, onCompletada }) {
  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <NavBack onBack={onBack} />
      </nav>
      <div style={{ paddingTop: "clamp(44px, 6vw, 68px)" }}>
        <p className="kicker">{t("ess.page_kicker")}</p>
        <EvaluacionesSlackSection token={token} user={user} onNavigate={onNavigate} completadasApp={completadasApp} onCompletada={onCompletada} />
      </div>
      <Footer />
    </main>
  );
}

// Barra de carga: traduce las peticiones en curso a variables CSS que pinta `.nav::after`.
// El ancho es proporcional a las peticiones que ya han terminado (done/total): así
// avanza por pasos reales en vez de saltar al final enseguida.
function TopLoadingBar() {
  useEffect(() => {
    const root = document.documentElement;
    let trickle = null;
    let hideTimer = null;
    let progress = 0;          // % mostrado actualmente
    let total = 0, done = 0, count = 0;
    const set = (p) => { progress = p; root.style.setProperty("--load-progress", `${p}%`); };

    // Mientras una petición sigue en curso, avanza despacio hacia el siguiente
    // escalón proporcional (done+1)/total, sin pasar de él ni llegar al 100%.
    const tick = () => {
      const ceiling = total > 0 ? Math.min(((done + 1) / total) * 100, 95) : 90;
      if (progress < ceiling) set(progress + (ceiling - progress) * 0.06);
    };

    const unsubscribe = subscribeLoading((s) => {
      total = s.total; done = s.done; count = s.count;

      if (count === 0) {
        // tanda terminada → completa al 100% y desvanece
        if (trickle) { clearInterval(trickle); trickle = null; }
        set(100);
        hideTimer = setTimeout(() => {
          root.style.setProperty("--load-opacity", "0");
          set(0);
        }, 350);
        return;
      }

      clearTimeout(hideTimer);
      hideTimer = null;
      root.style.setProperty("--load-opacity", "1");
      // suelo proporcional: salta al % de peticiones ya completadas
      const floor = total > 0 ? (done / total) * 100 : 0;
      if (progress < floor) set(floor);
      if (!trickle) trickle = setInterval(tick, 250);
    });

    return () => {
      unsubscribe();
      if (trickle) clearInterval(trickle);
      clearTimeout(hideTimer);
      root.style.removeProperty("--load-progress");
      root.style.removeProperty("--load-opacity");
    };
  }, []);

  return null;
}

// Espera larga con la que el CA se queda mirando: leer todo Notion y que la IA analice
// el año entero tarda ~65s. Sin nada delante, la pantalla parecía colgada.
//
// La barra va contra el RELOJ, no contra el progreso real: el backend hace la petición
// entera de una vez y no informa de por dónde va, así que aquí solo se sabe el tiempo
// transcurrido. `segundosTipicos` sale de medirlo (~65s la primera área; las siguientes
// reutilizan el análisis y son casi instantáneas). Por eso se frena al 92% en vez de
// llegar al 100%: fingir que ha acabado cuando no sabemos si ha acabado es peor que no
// poner nada. Si se pasa del tiempo típico, se dice, en vez de dejar la barra clavada.
function BarraEspera({ segundosTipicos = 60, titulo, detalle }) {
  const [transcurrido, setTranscurrido] = useState(0);

  useEffect(() => {
    const inicio = Date.now();
    const id = setInterval(() => setTranscurrido((Date.now() - inicio) / 1000), 250);
    return () => clearInterval(id);
  }, []);

  // Se acerca al 92% de forma asintótica: avanza rápido al principio y se va frenando,
  // así nunca lo alcanza ni se queda parada del todo por mucho que tarde.
  const pct = Math.min(92, 92 * (1 - Math.exp(-transcurrido / (segundosTipicos / 2))));
  const tarde = transcurrido > segundosTipicos * 1.5;
  const mm = String(Math.floor(transcurrido / 60)).padStart(1, "0");
  const ss = String(Math.floor(transcurrido % 60)).padStart(2, "0");

  return (
    <section className="panel espera" aria-busy="true">
      <p className="espera-titulo">{titulo || t("common.loading")}</p>
      <div
        className="espera-barra"
        role="progressbar"
        aria-valuetext={t("eaw.wait_elapsed", { mm, ss })}
      >
        <div className="espera-barra-fill" style={{ width: `${pct}%` }} />
      </div>
      <p className="fine espera-pie">
        {tarde ? t("eaw.wait_slow") : detalle}
        {detalle || tarde ? " · " : ""}
        <span className="espera-reloj">{mm}:{ss}</span>
      </p>
    </section>
  );
}

// Notas de área permitidas en el informe final: A (achieves), E (exceeds), EM (expects more).
const NOTAS_AREA = ["A", "E", "EM"];
// Opciones fijas de retribución variable.
const OPC_VARIABLE = ["100%", "50%", "25%", "0%"];
const OPC_OBJ_CORP = [">96%", "95%", "94%", "92%-93%", "<92%"];

// Extrae el número de un valor de porcentaje ("60%" → "60"); "" si no hay número.
function pctNumero(v) {
  const m = String(v ?? "").match(/-?\d+([.,]\d+)?/);
  return m ? m[0].replace(",", ".") : "";
}

function esNumero(v) {
  const s = String(v ?? "").trim();
  return s !== "" && !isNaN(parseFloat(s));
}

// Detectores de formato inválido para el resaltado en rojo en vivo. Un campo VACÍO no se
// marca (eso lo avisa la validación al guardar); solo se marca lo que está mal escrito.
function notaEnteraMal(v) {
  const s = String(v ?? "").trim();
  if (s === "") return false;
  const n = Number(s);
  return !(Number.isInteger(n) && n >= 1 && n <= 5);
}
function numeroMal(v) {
  const s = String(v ?? "").trim();
  return s !== "" && isNaN(parseFloat(s));
}
function pctMal(v) {
  const s = String(v ?? "").trim();
  return s !== "" && !esNumero(pctNumero(s));
}

// Reglas para poder guardar la versión FINAL: todo relleno (salvo objetivos) y con el tipo
// correcto. Devuelve la lista de errores (vacía si el informe es válido).
function erroresBorradorFinal(b) {
  const e = [];
  if (!String(b.caSiguiente || "").trim()) e.push(t("eaw.val_ca_next"));
  if (!esNumero(b.salarioActual)) e.push(t("eaw.val_salary"));
  (b.dimensiones || []).forEach((d) => {
    if (!NOTAS_AREA.includes(String(d.nota || "").trim().toUpperCase())) e.push(t("eaw.val_area_score", { area: d.etiqueta }));
    if (!String(d.comentarios || "").trim()) e.push(t("eaw.val_area_comment", { area: d.etiqueta }));
  });
  const r = b.retribucion || {};
  const nP = Number(r.notaProyectos);
  const nC = Number(r.notaContribucion);
  if (!(Number.isInteger(nP) && nP >= 1 && nP <= 5)) e.push(t("eaw.val_score_projects"));
  if (!(Number.isInteger(nC) && nC >= 1 && nC <= 5)) e.push(t("eaw.val_score_cttf"));
  [["variable60", t("anualdoc.variable_60")], ["variable", t("anualdoc.variable")],
   ["objetivosCorporativos", t("anualdoc.corp_objectives")], ["totalVariable", t("eaw.total_variable_short")]]
    .forEach(([k, label]) => { if (!esNumero(pctNumero(r[k]))) e.push(t("eaw.val_pct", { field: label })); });
  const res = b.resultadoEval || {};
  if (!["SÍ", "SI", "NO"].includes(String(res.promocion || "").trim().toUpperCase())) e.push(t("eaw.val_promotion"));
  if (!String(res.cargoSiguiente || "").trim()) e.push(t("eaw.val_position"));
  if (!esNumero(res.nuevoSalarioFijo)) e.push(t("eaw.val_new_salary"));
  return e;
}

function EvaluacionAnualWizard({ token, advisee, onBack, modo }) {
  const nombre = (advisee && advisee.nombre) || advisee || "";
  const esManual = modo === "manual";
  const [est, setEst] = useState(null);
  const [step, setStep] = useState("loading"); // loading|identidad|loop|resumen|hecho|error
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [secIdx, setSecIdx] = useState(0);
  const [area, setArea] = useState(null);
  const [input, setInput] = useState("");
  const [evidOpen, setEvidOpen] = useState(true);
  const [borr, setBorr] = useState(null);       // borrador editable del informe final
  const [borrBusy, setBorrBusy] = useState(false);
  const [borrSaved, setBorrSaved] = useState(false);
  const [subiendo, setSubiendo] = useState(false);
  const [subida, setSubida] = useState(null);   // respuesta de la subida (urls)
  const [valErrors, setValErrors] = useState([]); // errores de validación al guardar versión final
  const [descInfo, setDescInfo] = useState(false);
  const [infoOk, setInfoOk] = useState(false);
  const [citaSel, setCitaSel] = useState(null);  // cid de la cita abierta en el chat
  const [resumenBusy, setResumenBusy] = useState(false); // sugerencia final del área
  const [resetting, setResetting] = useState(false);
  const [reloadNonce, setReloadNonce] = useState(0);
  const [plan, setPlan] = useState(null);        // plan de acción sugerido (texto)
  const [planInstr, setPlanInstr] = useState("");
  const [planBusy, setPlanBusy] = useState(false);
  const [planGuardado, setPlanGuardado] = useState(false);
  // Descarga de PDFs de fuentes (barra superior del flujo manual) + vista previa lateral.
  const [generandoFuente, setGenerandoFuente] = useState("");
  const [fuenteError, setFuenteError] = useState("");
  const [tieneEvaluacionesExtra, setTieneEvaluacionesExtra] = useState(false);
  const [fuentePreview, setFuentePreview] = useState(null); // {url, etiqueta} | null

  // Descarga el PDF de una fuente y, además, lo abre en el panel lateral para leerlo al rellenar.
  async function descargarFuentePdf(endpoint, etiqueta, titulo) {
    setGenerandoFuente(endpoint); setFuenteError("");
    try {
      const data = await apiRequest(endpoint, { token, method: "POST", body: { evaluado: nombre } });
      if (!data.pdfUrl) throw new Error("sin documento");
      const response = await fetch(apiUrl(data.pdfUrl), { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) throw new Error("descarga");
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `${etiqueta}_${nombre.replace(/\s+/g, "_")}.pdf`;
      a.click();
      // Se conserva el objectURL para la vista previa; se revoca al reemplazarlo o cerrarlo.
      setFuentePreview((prev) => { if (prev?.url) URL.revokeObjectURL(prev.url); return { url, etiqueta: titulo || etiqueta }; });
    } catch (err) {
      console.error(`Descarga de fuente ${endpoint}:`, err);
      setFuenteError(t("ad.err_no_source_info"));
    } finally {
      setGenerandoFuente("");
    }
  }

  function cerrarPreview() {
    setFuentePreview((prev) => { if (prev?.url) URL.revokeObjectURL(prev.url); return null; });
  }

  // Al salir del asistente se libera el objectURL de la vista previa.
  useEffect(() => () => { setFuentePreview((prev) => { if (prev?.url) URL.revokeObjectURL(prev.url); return null; }); }, []);

  // Solo el flujo manual necesita saber si hay evaluaciones extra (para mostrar su botón).
  useEffect(() => {
    if (!esManual) return;
    apiRequest(`/api/evaluaciones-extra-recibidas?evaluado=${encodeURIComponent(nombre)}`, { token })
      .then((data) => setTieneEvaluacionesExtra((data.evaluaciones || []).length > 0))
      .catch(() => setTieneEvaluacionesExtra(false));
  }, [token, nombre, esManual]);

  useEffect(() => {
    let alive = true;
    // Flujo manual: se salta la conversación por áreas y abre el Word editable en blanco.
    if (esManual) {
      apiRequest("/api/eval-anual/iniciar-manual", { token, method: "POST", body: { evaluado: nombre } })
        .then((r) => { if (!alive) return; setBorr(r.borrador); setStep("borrador"); })
        .catch((e) => { if (alive) { setError(e.message); setStep("error"); } });
      return () => { alive = false; };
    }
    apiRequest("/api/eval-anual/iniciar", { token, method: "POST", body: { evaluado: nombre } })
      .then((data) => {
        if (!alive) return;
        setEst(data);
        if (!data.identidadConfirmada) setStep("identidad");
        else if (data.estado === "completada") setStep("borrador");
        else if (data.seccionesConfirmadas >= data.totalSecciones) setStep("resumen");
        else { const i = data.secciones.findIndex((s) => !s.confirmada); setSecIdx(i < 0 ? 0 : i); setStep("loop"); }
      })
      .catch((e) => { if (alive) { setError(e.message); setStep("error"); } });
    return () => { alive = false; };
  }, [token, nombre, reloadNonce, esManual]);

  // Depende de la CLAVE del área actual (no de `est` entero): así, cuando enviar()
  // actualiza `est` para reflejar que un área quedó desconfirmada, este efecto no
  // se relanza y no pisa la conversación recién recibida con un refetch de más.
  const claveActual = est?.secciones?.[secIdx]?.clave;
  useEffect(() => {
    if (step !== "loop" || !claveActual) return;
    setArea(null); setInput(""); setEvidOpen(true); setError(""); setCitaSel(null);
    apiRequest(`/api/eval-anual/area?evaluado=${encodeURIComponent(nombre)}&clave=${encodeURIComponent(claveActual)}`, { token })
      .then(setArea)
      .catch((e) => setError(e.message));
  }, [step, claveActual, token, nombre]);

  async function confirmarIdentidad() {
    setBusy(true); setError("");
    try {
      await apiRequest("/api/eval-anual/confirmar-identidad", { token, method: "POST", body: { evaluado: nombre } });
      const i = est.secciones.findIndex((s) => !s.confirmada);
      setSecIdx(i < 0 ? 0 : i); setStep("loop");
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  async function enviar() {
    if (!input.trim()) { setError(t("eaw.err_write_points")); return; }
    setBusy(true); setError("");
    try {
      const r = await apiRequest("/api/eval-anual/responder-area", { token, method: "POST", body: { evaluado: nombre, clave: area.clave, texto: input } });
      // La conversación avanza → la sugerencia final anterior queda obsoleta (el backend la borra).
      setArea((a) => ({ ...a, conversacion: r.conversacion, propuesta: r.propuesta, resumen: [] }));
      // El backend desconfirma el área si ya estaba confirmada (reabrir + editar la
      // deja pendiente de volver a confirmar) -- reflejamos eso al momento en el stepper.
      setEst((e) => e && ({
        ...e,
        secciones: e.secciones.map((s) => (s.clave === area.clave ? { ...s, confirmada: false } : s)),
      }));
      setInput(""); setEvidOpen(false);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  // Sugerencia final del área, criterio a criterio. SOLO bajo demanda (botón):
  // no se genera al empezar a hablar ni al responder la IA.
  async function pedirResumenFinal() {
    setResumenBusy(true); setError("");
    try {
      const r = await apiRequest("/api/eval-anual/resumen-area", { token, method: "POST", body: { evaluado: nombre, clave: area.clave } });
      setArea((a) => ({ ...a, resumen: r.resumen || [] }));
    } catch (e) { setError(e.message); } finally { setResumenBusy(false); }
  }

  async function confirmarArea() {
    setBusy(true); setError("");
    try {
      const e2 = await apiRequest("/api/eval-anual/confirmar-area", { token, method: "POST", body: { evaluado: nombre, clave: area.clave } });
      setEst(e2);
      const next = e2.secciones.findIndex((s) => !s.confirmada);
      if (next === -1) setStep("resumen"); else setSecIdx(next);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  async function finalizar() {
    setBusy(true); setError("");
    try {
      await apiRequest("/api/eval-anual/finalizar", { token, method: "POST", body: { evaluado: nombre } });
      setBorr(null); setSubida(null);   // fuerza la recarga del borrador recién (re)generado
      setStep("borrador");
    } catch (e) { setError(e.message); } finally { setBusy(false); }
  }

  // Carga el borrador editable al entrar en el paso "borrador".
  useEffect(() => {
    if (step !== "borrador" || borr !== null) return;
    apiRequest(`/api/eval-anual/borrador?evaluado=${encodeURIComponent(nombre)}`, { token })
      .then((r) => setBorr(r.borrador))
      .catch((e) => setError(e.message));
  }, [step, borr, token, nombre]);

  async function guardarBorrador() {
    setBorrBusy(true); setError(""); setBorrSaved(false);
    try {
      const r = await apiRequest("/api/eval-anual/borrador-guardar", { token, method: "POST", body: { evaluado: nombre, borrador: borr } });
      if (r.borrador) setBorr(r.borrador);
      setBorrSaved(true);
      setTimeout(() => setBorrSaved(false), 2600);
    } catch (e) { setError(e.message); } finally { setBorrBusy(false); }
  }

  async function subirInformeFinal() {
    const errs = erroresBorradorFinal(borr);
    setValErrors(errs);
    if (errs.length) { window.scrollTo({ top: 0, behavior: "smooth" }); return; }
    if (!window.confirm(t("eaw.upload_confirm"))) return;
    setSubiendo(true); setError("");
    try {
      const r = await apiRequest("/api/eval-anual/subir-borrador", { token, method: "POST", body: { evaluado: nombre, borrador: borr } });
      setSubida(r);
    } catch (e) { setError(e.message); } finally { setSubiendo(false); }
  }

  async function descargarDocxFinal(path) {
    try {
      const response = await fetch(apiUrl(path), { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) throw new Error(t("admin.err_download"));
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `informe_final_${nombre.replace(/\s+/g, "_")}.docx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (e) { setError(e.message); }
  }

  // Plan de acción sugerido (paso final): se genera al entrar en el resumen.
  useEffect(() => {
    if (step !== "resumen" || plan !== null) return;
    apiRequest(`/api/eval-anual/plan?evaluado=${encodeURIComponent(nombre)}`, { token })
      .then((r) => setPlan(r.plan || ""))
      .catch((e) => setError(e.message));
  }, [step, plan, token, nombre]);

  async function guardarPlan() {
    setPlanBusy(true); setError(""); setPlanGuardado(false);
    try {
      await apiRequest("/api/eval-anual/plan-guardar", { token, method: "POST", body: { evaluado: nombre, texto: plan } });
      setPlanGuardado(true);
      setTimeout(() => setPlanGuardado(false), 2600);
    } catch (e) { setError(e.message); } finally { setPlanBusy(false); }
  }

  async function pedirCambiosPlan() {
    if (!planInstr.trim()) return;
    setPlanBusy(true); setError("");
    try {
      const r = await apiRequest("/api/eval-anual/plan-cambios", { token, method: "POST", body: { evaluado: nombre, instruccion: planInstr } });
      setPlan(r.plan || ""); setPlanInstr("");
    } catch (e) { setError(e.message); } finally { setPlanBusy(false); }
  }

  // Borra por completo la sesión (conversaciones, áreas confirmadas y borradores)
  // y vuelve a arrancar el asistente desde cero.
  async function eliminarYEmpezarDeCero() {
    if (!window.confirm(t("eaw.reset_confirm"))) return;
    setResetting(true); setError("");
    try {
      await apiRequest("/api/eval-anual/eliminar", { token, method: "POST", body: { evaluado: nombre } });
      setEst(null); setArea(null); setInput(""); setBorr(null); setSubida(null);
      setSecIdx(0); setCitaSel(null); setStep("loading");
      setReloadNonce((n) => n + 1);
    } catch (e) {
      setError(e.message);
    } finally {
      setResetting(false);
    }
  }

  // Descarga un PDF con TODA la información recibida por la persona (las 4 fuentes juntas).
  async function descargarInfoCompleta() {
    setDescInfo(true);
    try {
      const data = await apiRequest("/api/generar-pdf-completo", { token, method: "POST", body: { evaluado: nombre } });
      const path = data.pdfUrl;
      if (!path) throw new Error(t("ad.err_no_doc"));
      const response = await fetch(apiUrl(path), { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) {
        const d = await response.json().catch(() => ({}));
        throw new Error(d.error || t("admin.err_download"));
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `info_completa_${nombre.replace(/\s+/g, "_")}.pdf`;
      link.click();
      URL.revokeObjectURL(url);
      setInfoOk(true);
      setTimeout(() => setInfoOk(false), 2600);
    } catch (e) {
      setError(e.message);
    } finally {
      setDescInfo(false);
    }
  }

  const shell = (children) => (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <NavBack onBack={onBack} />
      </nav>
      <div style={{ flex: 1, paddingTop: "clamp(44px, 6vw, 68px)", paddingBottom: 48, maxWidth: 820, margin: "0 auto", width: "100%" }}>
        <p className="eyebrow">{t("eaw.eyebrow")}</p>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 16, flexWrap: "wrap" }}>
          <h1 style={{ marginBottom: 6 }}>{nombre}</h1>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            {est && step !== "loading" && step !== "identidad" && (
              <button className="secondary" onClick={eliminarYEmpezarDeCero} disabled={resetting || busy}
                style={{ borderColor: "#C1121F", color: "#C1121F" }}>
                {resetting ? t("eaw.resetting") : t("eaw.reset_all")}
              </button>
            )}
            <button className="secondary" onClick={descargarInfoCompleta} disabled={descInfo}>
              {descInfo ? t("eaw.generating") : t("eaw.full_info", { nombre })}
            </button>
            {infoOk && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "#166534", alignSelf: "center" }}>
                <DrawCheck size={20} color="#166534" /> {t("eaw.downloaded")}
              </span>
            )}
          </div>
        </div>
        {est && <p className="fine" style={{ marginBottom: 24 }}>{t("eaw.year_stat", { anio: est.anio, done: est.seccionesConfirmadas, total: est.totalSecciones })}</p>}
        {error && <p className="form-error">{error}</p>}
        {children}
      </div>
    </main>
  );

  if (step === "loading") return shell(<BarraEspera segundosTipicos={20} titulo={t("eaw.wait_starting")} detalle={t("eaw.wait_starting_detail")} />);
  if (step === "error") return shell(<p className="fine">{t("eaw.err_start")}</p>);

  if (step === "identidad") {
    return shell(
      <section className="panel">
        <h2 style={{ marginTop: 0 }}>{t("eaw.confirm_identity_q")}</h2>
        <p><strong>{nombre}</strong></p>
        <p className="fine">{t("eaw.year_projects", { list: est.proyectos.length ? est.proyectos.join(", ") : "—" })}</p>
        <div className="actions" style={{ marginTop: 16 }}>
          <button onClick={confirmarIdentidad} disabled={busy}>{t("eaw.yes_correct_start")}</button>
          <button className="secondary" onClick={onBack}>{t("eaw.no_back")}</button>
        </div>
      </section>
    );
  }

  if (step === "loop") {
    // La primera área es la cara: hay que leer todo Notion y que la IA analice el año
    // entero (~65s medidos). Las siguientes reutilizan ese análisis y son instantáneas,
    // así que solo se enseña la espera larga cuando toca.
    if (!area) {
      const primera = !est?.secciones?.some((s) => s.confirmada);
      return shell(
        <BarraEspera
          segundosTipicos={primera ? 65 : 8}
          titulo={t("eaw.wait_area", { nombre })}
          detalle={primera ? t("eaw.wait_area_detail") : null}
        />
      );
    }
    const tieneConv = area.conversacion && area.conversacion.length > 0;
    const evidMap = {};
    (area.evidencia || []).forEach((e) => { evidMap[e.cid] = e; });
    const renderCitas = (texto) => String(texto || "").split(/(\[[EOPSB]\d+\])/g).map((p, k) => {
      const mm = p.match(/^\[([EOPSB]\d+)\]$/);
      if (!mm) return p;
      return (
        <button key={k} type="button" onClick={() => setCitaSel(mm[1])}
          style={{ background: "none", border: "none", padding: "0 1px", minHeight: 0, height: "auto",
                   color: "#0563C1", fontWeight: 700, cursor: "pointer", fontSize: "0.8em", verticalAlign: "super" }}>
          {p}
        </button>
      );
    });
    const citaFicha = citaSel ? evidMap[citaSel] : null;
    const seccionActual = est.secciones[secIdx];
    return shell(
      <section className="panel">
        <p className="eyebrow">{t("eaw.area_n", { i: secIdx + 1, total: est.totalSecciones })}</p>
        <h2 style={{ marginTop: 0, display: "flex", alignItems: "center", gap: 10 }}>
          {area.etiqueta}
          {seccionActual?.confirmada && (
            <span style={{ fontSize: 13, fontWeight: 700, color: "#166534" }}>{t("eaw.area_confirmed_badge")}</span>
          )}
        </h2>

        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 16 }}>
          {est.secciones.map((s, i) => (
            <button
              key={s.clave}
              type="button"
              onClick={() => { setSecIdx(i); }}
              disabled={busy}
              title={s.etiqueta}
              style={{
                minHeight: 30, height: 30, padding: "0 10px", borderRadius: 15, fontSize: 12,
                border: i === secIdx ? "2px solid #101010" : "1px solid #d8d8d8",
                background: s.confirmada ? "#e8f5e9" : "#fff",
                color: "#101010", cursor: "pointer",
              }}
            >
              {s.confirmada ? "✓ " : ""}{i + 1}
            </button>
          ))}
        </div>

        {seccionActual?.confirmada && (
          <p className="fine" style={{ margin: "0 0 14px", color: "#92400e" }}>{t("eaw.reopened_notice")}</p>
        )}

        <details open style={{ marginBottom: 16, background: "#f7f7f4", borderRadius: 8, padding: "10px 14px" }}>
          <summary style={{ cursor: "pointer", fontWeight: 700, fontSize: 14 }}>
            {t("eaw.criteria_panel")}{area.cargo ? ` · ${area.cargo}` : ""}
          </summary>
          {(area.criterios || []).map((c, i) => (
            <div key={i} style={{ marginTop: 10 }}>
              <p className="fine" style={{ margin: 0, fontWeight: 700 }}>{c.nivel}</p>
              <ul className="fine" style={{ margin: "2px 0 0", paddingLeft: 18 }}>
                {c.criterios.map((cr, k) => <li key={k}>{cr}</li>)}
              </ul>
            </div>
          ))}
          {(!area.criterios || area.criterios.length === 0) && (
            <p className="fine" style={{ margin: "10px 0 0" }}>{t("eaw.no_criteria_position")}</p>
          )}
        </details>

        <details open={evidOpen} onToggle={(e) => setEvidOpen(e.target.open)} style={{ marginBottom: 16 }}>
          <summary className="fine" style={{ cursor: "pointer" }}>
            {t("eaw.info_considered", { n: area.evidencia.length })}
          </summary>
          {area.evidencia.length === 0 && <p className="fine" style={{ marginTop: 8 }}>{t("eaw.no_evidence")}</p>}
          {area.evidencia.map((e) => (
            <div key={e.cid} className="card" style={{ marginTop: 8 }}>
              <p style={{ margin: 0 }}><strong>[{e.cid}]</strong> {e.label}{e.evaluador ? ` · ${e.evaluador}` : ""}</p>
              <p className="fine" style={{ margin: "4px 0 0", whiteSpace: "pre-line" }}>{e.texto || "—"}</p>
            </div>
          ))}
          {/* Lo que Claude NO citó en este área. Antes no se mostraba, así que una evaluación
              que el modelo ignorase desaparecía sin que el CA supiera siquiera que existía. */}
          {(area.evidencia_no_citada || []).length > 0 && (
            <details style={{ marginTop: 12 }}>
              <summary className="fine" style={{ cursor: "pointer", color: "var(--text-55)" }}>
                {t("eaw.info_not_used", { n: area.evidencia_no_citada.length })}
              </summary>
              <p className="fine" style={{ margin: "8px 0 0" }}>{t("eaw.info_not_used_note")}</p>
              {area.evidencia_no_citada.map((e) => (
                <div key={e.cid} className="card" style={{ marginTop: 8, opacity: 0.75 }}>
                  <p style={{ margin: 0 }}><strong>[{e.cid}]</strong> {e.label}{e.evaluador ? ` · ${e.evaluador}` : ""}</p>
                  <p className="fine" style={{ margin: "4px 0 0", whiteSpace: "pre-line" }}>{e.texto || "—"}</p>
                </div>
              ))}
            </details>
          )}
        </details>

        {tieneConv && (
          <div style={{ marginBottom: 8 }}>
            {area.conversacion.map((m, i) => (
              <div key={i} style={{ margin: "10px 0", textAlign: m.rol === "ca" ? "right" : "left" }}>
                <span style={{
                  display: "inline-block", maxWidth: "85%", textAlign: "left", padding: "10px 14px",
                  borderRadius: 12, whiteSpace: "pre-line", fontSize: 14,
                  background: m.rol === "ca" ? "#101010" : "#f4f4f1", color: m.rol === "ca" ? "#fff" : "#101010",
                }}>{m.rol === "ia" ? renderCitas(m.texto) : m.texto}</span>
              </div>
            ))}
          </div>
        )}

        {citaSel && (
          <div className="card" style={{ marginBottom: 12, borderLeft: "3px solid #0563C1" }}>
            <p style={{ margin: 0, display: "flex", justifyContent: "space-between", gap: 8 }}>
              <strong>[{citaSel}]{citaFicha ? ` ${citaFicha.label}` : ""}</strong>
              <button type="button" className="link-button" onClick={() => setCitaSel(null)}>✕</button>
            </p>
            {citaFicha
              ? <>
                  {citaFicha.evaluador && <p className="fine" style={{ margin: "2px 0 0" }}>{citaFicha.evaluador}</p>}
                  <p className="fine" style={{ margin: "4px 0 0", whiteSpace: "pre-line" }}>{citaFicha.texto || "—"}</p>
                </>
              : <p className="fine" style={{ margin: "4px 0 0" }}>{t("eaw.ref_unavailable")}</p>}
          </div>
        )}

        {tieneConv && <p className="fine" style={{ margin: "0 0 12px" }}>{t("eaw.ref_hint")}</p>}

        {!tieneConv && <p style={{ marginBottom: 10 }}>{area.pregunta}</p>}

        <textarea rows={4} style={{ width: "100%" }} value={input} onChange={(e) => setInput(e.target.value)}
          placeholder={tieneConv ? t("eaw.ph_respond_ai") : t("eaw.ph_main_points")} />
        <div className="actions" style={{ marginTop: 10 }}>
          <button onClick={enviar} disabled={busy}>{busy ? t("eaw.sending") : tieneConv ? t("eaw.respond") : t("eaw.send_to_ai")}</button>
        </div>

        {/* Sugerencia final del área: al final de la conversación y solo si el CA la pide. */}
        {tieneConv && (
          <div style={{ marginTop: 22, borderTop: "1px solid #e2e2dd", paddingTop: 16 }}>
            {(area.resumen || []).length === 0 ? (
              <>
                <p className="fine" style={{ margin: "0 0 8px" }}>{t("eaw.final_summary_hint")}</p>
                <button className="secondary" onClick={pedirResumenFinal} disabled={busy || resumenBusy}>
                  {resumenBusy ? t("eaw.generating") : t("eaw.final_summary_btn")}
                </button>
              </>
            ) : (
              <>
                <h3 style={{ margin: "0 0 4px" }}>{t("eaw.final_summary_title")}</h3>
                <p className="fine" style={{ margin: "0 0 12px" }}>{t("eaw.final_summary_desc")}</p>
                {area.resumen.map((r, i) => (
                  <div key={i} className="card"
                    style={{ marginBottom: 8, ...(r.evaluable ? {} : { borderLeft: "3px solid #d9a300", background: "#fffdf5" }) }}>
                    <p style={{ margin: 0, fontWeight: 700, fontSize: 14 }}>{r.criterio}</p>
                    <p className="fine" style={{ margin: "4px 0 0", whiteSpace: "pre-line", ...(r.evaluable ? {} : { color: "#8a6d00" }) }}>
                      {r.evaluable ? renderCitas(r.valoracion) : r.valoracion}
                    </p>
                  </div>
                ))}
                <button className="secondary" onClick={pedirResumenFinal} disabled={busy || resumenBusy} style={{ marginTop: 4 }}>
                  {resumenBusy ? t("eaw.generating") : t("eaw.final_summary_refresh")}
                </button>
              </>
            )}
          </div>
        )}

        {/* Navegación entre áreas: debajo de la sugerencia final. */}
        <div className="actions" style={{ marginTop: 22 }}>
          <button className="secondary" onClick={() => setSecIdx((i) => i - 1)} disabled={busy || secIdx === 0}>
            {t("eaw.prev_area")}
          </button>
          {tieneConv && (
            <button className="secondary" onClick={confirmarArea} disabled={busy}>{t("eaw.confirm_area")}</button>
          )}
        </div>
      </section>
    );
  }

  if (step === "resumen") {
    return shell(
      <section className="panel">
        <h2 style={{ marginTop: 0 }}>{t("eaw.all_confirmed")}</h2>
        <p className="fine">{t("eaw.summary_desc")}</p>

        <h3 style={{ marginBottom: 6 }}>{t("eaw.plan_title")}</h3>
        <p className="fine" style={{ marginBottom: 8 }}>{t("eaw.plan_desc")}</p>
        {plan === null ? (
          <p className="fine">{t("eaw.plan_loading")}</p>
        ) : (
          <>
            <textarea rows={10} style={{ width: "100%" }} value={plan} onChange={(e) => setPlan(e.target.value)} />
            <div style={{ marginTop: 8, display: "flex", gap: 8, flexWrap: "wrap" }}>
              <input style={{ flex: 1, minWidth: 200 }} value={planInstr} onChange={(e) => setPlanInstr(e.target.value)}
                placeholder={t("eaw.plan_ask_ph")} />
              <button className="secondary" onClick={pedirCambiosPlan} disabled={planBusy || !planInstr.trim()}>
                {planBusy ? t("eaw.generating") : t("eaw.plan_ask")}
              </button>
              <button className="secondary" onClick={guardarPlan} disabled={planBusy}>
                {planBusy ? t("common.saving") : t("eaw.plan_save")}
              </button>
              {planGuardado && (
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "#166534" }}>
                  <DrawCheck size={20} color="#166534" /> {t("eaw.plan_saved")}
                </span>
              )}
            </div>
          </>
        )}

        <h3 style={{ marginBottom: 6 }}>{t("eaw.jump_to_area")}</h3>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 6, marginBottom: 16 }}>
          {est.secciones.map((s, i) => (
            <button
              key={s.clave}
              type="button"
              onClick={() => { setSecIdx(i); setStep("loop"); }}
              title={s.etiqueta}
              className="secondary"
              style={{ minHeight: 30, height: 30, padding: "0 10px", borderRadius: 15, fontSize: 12 }}
            >
              {s.confirmada ? "✓ " : ""}{s.etiqueta}
            </button>
          ))}
        </div>

        <div className="actions" style={{ marginTop: 20 }}>
          <button onClick={finalizar} disabled={busy}>{busy ? t("eaw.generating") : t("eaw.gen_draft")}</button>
        </div>
      </section>
    );
  }

  if (step === "borrador") {
    if (!borr) return shell(<p className="fine">{t("common.loading")}</p>);
    const yy = `'${String(borr.anio).slice(-2)}`;
    const yySig = `'${String(borr.anioSiguiente).slice(-2)}`;
    const docPage = {
      maxWidth: 840, margin: "0 auto", background: "#fff", color: "#101010",
      boxShadow: "0 6px 28px rgba(0,0,0,.14)", border: "1px solid #e6e6e2",
      borderRadius: 4, padding: "clamp(24px, 5vw, 56px)",
      fontFamily: "var(--font-sans)",
    };
    const brand = { textAlign: "center", fontWeight: 700, fontSize: 26, letterSpacing: ".01em", marginBottom: 4 };
    const td = { border: "1px solid #101010", padding: "8px 10px", verticalAlign: "top", fontSize: 14 };
    const tdLabel = { ...td, fontWeight: 700, background: "#f7f7f4", width: 140 };
    const tabla = { width: "100%", borderCollapse: "collapse", marginBottom: 20 };
    const secTitle = { fontSize: 13, textTransform: "uppercase", letterSpacing: ".08em", borderBottom: "2px solid #101010", paddingBottom: 6, margin: "26px 0 12px" };
    const inCell = { width: "100%", border: "1px solid transparent", background: "#f2f6ff", borderRadius: 3, padding: "2px 5px", margin: 0, minHeight: 0, height: "auto", fontSize: 14, outline: "none", fontFamily: "inherit" };
    const taCell = { ...inCell, resize: "vertical", lineHeight: 1.45 };
    const errStyle = { borderColor: "#C1121F", background: "#ffe9e9", color: "#7a0f16" };
    const marcar = (mal) => (mal ? errStyle : {});
    const setDim = (clave, patch) => setBorr((b) => ({ ...b, dimensiones: b.dimensiones.map((d) => (d.clave === clave ? { ...d, ...patch } : d)) }));
    const setRet = (k, v) => setBorr((b) => ({ ...b, retribucion: { ...b.retribucion, [k]: v } }));
    const setRes = (k, v) => setBorr((b) => ({ ...b, resultadoEval: { ...b.resultadoEval, [k]: v } }));
    const setObj = (i, patch) => setBorr((b) => ({ ...b, objetivos: b.objetivos.map((o, j) => (j === i ? { ...o, ...patch } : o)) }));
    // Input de porcentaje: número + sufijo "%", almacenado como "N%".
    const pctInput = (k) => (
      <div style={{ display: "flex", alignItems: "center", gap: 3 }}>
        <input type="number" min="0" max="100" style={{ ...inCell, textAlign: "right", ...marcar(pctMal(borr.retribucion[k])) }}
          value={pctNumero(borr.retribucion[k])}
          onChange={(e) => setRet(k, e.target.value === "" ? "" : `${e.target.value}%`)} />
        <span style={{ fontSize: 14 }}>%</span>
      </div>
    );
    // Desplegable de retribución con opciones fijas.
    const selectRet = (k, opciones) => (
      <select style={inCell} value={borr.retribucion[k] || ""} onChange={(e) => setRet(k, e.target.value)}>
        <option value="">—</option>
        {opciones.map((o) => <option key={o} value={o}>{o}</option>)}
      </select>
    );
    return shell(
      <section className="panel">
        <p className="fine" style={{ marginBottom: 18, textAlign: "center" }}>{esManual ? t("eaw.draft_step_desc_manual") : t("eaw.draft_step_desc")}</p>
        {esManual && (
          <div style={{ maxWidth: 840, margin: "0 auto 16px", padding: "12px 14px", border: "1px solid var(--border)", borderRadius: 8, background: "var(--surface, #fafafa)" }}>
            <p className="fine" style={{ margin: "0 0 8px", fontWeight: 600 }}>{t("eaw.sources_bar")}</p>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {[
                ["/api/generar-opiniones-ca", "opiniones", t("eaw.src_opinions")],
                ["/api/generar-pdf-evals-proyecto", "evals_proyecto", t("eaw.src_proj")],
                ["/api/generar-pdf-seguimiento", "seguimiento_personal", t("eaw.src_tracking")],
                ["/api/generar-pdf-evals-mensuales", "evals_mensuales", t("eaw.src_monthly")],
                ...(tieneEvaluacionesExtra ? [["/api/generar-pdf-evals-extra", "evals_extra", t("eaw.src_extra")]] : []),
                ["/api/generar-pdf-completo", "info_completa", t("eaw.src_all")],
              ].map(([ep, et, label]) => (
                <button key={ep} className="secondary" disabled={!!generandoFuente}
                  onClick={() => descargarFuentePdf(ep, et, label)}
                  style={{ height: 32, minHeight: "auto", padding: "0 12px", fontSize: 12 }}>
                  {generandoFuente === ep ? t("ad.generating") : label}
                </button>
              ))}
            </div>
            {fuenteError && <p className="form-error" style={{ margin: "8px 0 0" }}>{fuenteError}</p>}
          </div>
        )}
        <div style={docPage}>
        <div style={brand}>.Igeneris</div>
        <h2 style={{ margin: "0 0 24px", textAlign: "center", textDecoration: "underline", fontFamily: "inherit", fontSize: 20 }}>{t("anualdoc.title")}</h2>

        <table style={tabla}><tbody>
          <tr>
            <td style={tdLabel}>{t("anualdoc.employee")}</td><td style={td}>{borr.empleado}</td>
            <td style={tdLabel}>{t("anualdoc.date")}</td><td style={td}>{borr.fecha}</td>
          </tr>
          <tr>
            <td style={tdLabel}>{`CA ${yy}`}</td><td style={td}>{borr.caActual || "—"}</td>
            <td style={tdLabel}>{t("anualdoc.current_position")}</td><td style={td}>{borr.cargo || "—"}</td>
          </tr>
          <tr>
            <td style={tdLabel}>{`CA ${yySig}`}</td>
            <td style={td}><input style={inCell} value={borr.caSiguiente} onChange={(e) => setBorr((b) => ({ ...b, caSiguiente: e.target.value }))} /></td>
            <td style={tdLabel}>{t("anualdoc.current_salary")}</td>
            <td style={td}><input type="number" min="0" style={{ ...inCell, ...marcar(numeroMal(borr.salarioActual)) }} value={borr.salarioActual} onChange={(e) => setBorr((b) => ({ ...b, salarioActual: e.target.value }))} /></td>
          </tr>
        </tbody></table>

        <h3 style={secTitle}>{t("anualdoc.rating_year", { anio: `${borr.anio}/${borr.anioSiguiente}` })}</h3>
        <table style={tabla}>
          <thead><tr>
            <th style={{ ...tdLabel, width: 170 }}>{t("anualdoc.projects")}</th>
            <th style={{ ...tdLabel, width: 90, textAlign: "center" }}>{t("anualdoc.score")}</th>
            <th style={tdLabel}>{t("anualdoc.comments")}</th>
          </tr></thead>
          <tbody>
            {borr.dimensiones.map((d) => (
              <tr key={d.clave}>
                <td style={{ ...td, fontWeight: 500 }}>{d.etiqueta}</td>
                <td style={{ ...td, textAlign: "center" }}>
                  <select style={{ ...inCell, textAlign: "center" }} value={String(d.nota || "").toUpperCase()} onChange={(e) => setDim(d.clave, { nota: e.target.value })}>
                    <option value="">—</option>
                    {NOTAS_AREA.map((n) => <option key={n} value={n}>{n}</option>)}
                  </select>
                </td>
                <td style={td}>
                  <textarea rows={Math.max(3, (d.comentarios || "").split("\n").length)} style={taCell}
                    value={d.comentarios} onChange={(e) => setDim(d.clave, { comentarios: e.target.value })} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <p className="fine" style={{ margin: "-12px 0 20px", fontSize: 12 }}>{t("eaw.score_legend")}</p>

        <table style={tabla}><tbody>
          <tr>
            <td style={{ ...tdLabel, width: "34%" }}>{t("anualdoc.final_projects")}</td>
            <td style={{ ...td, width: "12%" }}><input type="number" min="1" max="5" step="1" style={{ ...inCell, textAlign: "center", ...marcar(notaEnteraMal(borr.retribucion.notaProyectos)) }} value={borr.retribucion.notaProyectos} onChange={(e) => setRet("notaProyectos", e.target.value)} /></td>
            <td style={{ ...td, width: "20%" }}>{t("anualdoc.variable_60")}</td>
            <td style={td}>{selectRet("variable60", OPC_VARIABLE)}</td>
          </tr>
          <tr>
            <td style={tdLabel}>{t("anualdoc.final_contrib")}</td>
            <td style={td}><input type="number" min="1" max="5" step="1" style={{ ...inCell, textAlign: "center", ...marcar(notaEnteraMal(borr.retribucion.notaContribucion)) }} value={borr.retribucion.notaContribucion} onChange={(e) => setRet("notaContribucion", e.target.value)} /></td>
            <td style={td}>{t("anualdoc.variable")}</td>
            <td style={td}>{selectRet("variable", OPC_VARIABLE)}</td>
          </tr>
          <tr>
            <td style={tdLabel}>{t("anualdoc.corp_objectives")}</td>
            <td style={td}>{selectRet("objetivosCorporativos", OPC_OBJ_CORP)}</td>
            <td style={{ ...td, fontWeight: 700 }}>{t("anualdoc.total_variable", { yy })}</td>
            <td style={td}>{pctInput("totalVariable")}</td>
          </tr>
        </tbody></table>

        <h3 style={secTitle}>{t("anualdoc.eval_result", { yy })}</h3>
        <table style={tabla}><tbody>
          <tr>
            <td style={{ ...tdLabel, width: "18%" }}>{t("anualdoc.promotion")}</td>
            <td style={{ ...td, width: "15%" }}>
              <select style={inCell} value={String(borr.resultadoEval.promocion || "").toUpperCase().replace("SI", "SÍ")} onChange={(e) => setRes("promocion", e.target.value)}>
                <option value="">—</option>
                <option value="SÍ">SÍ</option>
                <option value="NO">NO</option>
              </select>
            </td>
            <td style={{ ...tdLabel, width: "18%" }}>{t("anualdoc.position_next", { yy: yySig })}</td>
            <td style={{ ...td, width: "15%" }}><input style={inCell} value={borr.resultadoEval.cargoSiguiente} onChange={(e) => setRes("cargoSiguiente", e.target.value)} /></td>
            <td style={tdLabel}>{t("anualdoc.new_fixed_salary")}
              <input type="number" min="0" style={{ ...inCell, fontWeight: 400, marginTop: 4, ...marcar(numeroMal(borr.resultadoEval.nuevoSalarioFijo)) }} value={borr.resultadoEval.nuevoSalarioFijo} onChange={(e) => setRes("nuevoSalarioFijo", e.target.value)} />
            </td>
          </tr>
        </tbody></table>

        <h3 style={secTitle}>{t("anualdoc.improvement_objectives", { yy: yySig })}</h3>
        <table style={tabla}>
          <thead><tr>
            <th style={tdLabel}></th>
            <th style={{ ...tdLabel, width: 110, textAlign: "center" }}>{t("anualdoc.deadline")}</th>
          </tr></thead>
          <tbody>
            {borr.objetivos.map((o, i) => (
              <tr key={i}>
                <td style={td}>
                  <div style={{ display: "flex", gap: 6 }}>
                    <span style={{ fontSize: 14 }}>{i + 1}.</span>
                    <textarea rows={Math.max(1, (o.texto || "").split("\n").length)} style={taCell}
                      value={o.texto} onChange={(e) => setObj(i, { texto: e.target.value })} />
                  </div>
                </td>
                <td style={td}>
                  <input type="date" style={{ ...inCell, textAlign: "center" }} value={o.deadline} onChange={(e) => setObj(i, { deadline: e.target.value })} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        <button className="secondary" style={{ marginBottom: 4 }}
          onClick={() => setBorr((b) => ({ ...b, objetivos: [...b.objetivos, { texto: "", deadline: "" }] }))}>
          {t("eaw.add_objective")}
        </button>
        </div>

        {valErrors.length > 0 && (
          <div style={{ margin: "18px auto", maxWidth: 840, padding: "12px 16px", border: "1px solid #C1121F", borderRadius: 8, background: "#fff4f4" }}>
            <p style={{ margin: "0 0 6px", fontWeight: 700, color: "#C1121F" }}>{t("eaw.val_intro")}</p>
            <ul style={{ margin: 0, paddingLeft: 20, color: "#7a0f16", fontSize: 13 }}>
              {valErrors.map((msg, i) => <li key={i}>{msg}</li>)}
            </ul>
          </div>
        )}

        {subida && (
          <div style={{ margin: "18px 0" }}>
            <SavedOk text={t("eaw.uploaded_ok")} color="#000" />
            <div className="actions" style={{ justifyContent: "center" }}>
              {subida.htmlUrl && <button className="secondary" onClick={() => openAuthedFile(subida.htmlUrl, token)}>{t("dash.open_web_version")}</button>}
              {subida.docxUrl && <button className="secondary" onClick={() => descargarDocxFinal(subida.docxUrl)}>{t("admin.download_word")}</button>}
            </div>
          </div>
        )}

        <div className="actions">
          <button onClick={subirInformeFinal} disabled={subiendo || borrBusy}>
            {subiendo ? t("eaw.uploading") : t("eaw.upload_final")}
          </button>
          <button className="secondary" onClick={guardarBorrador} disabled={borrBusy || subiendo}>
            {borrBusy ? t("common.saving") : t("eaw.save_draft")}
          </button>
          {borrSaved && (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "#166534" }}>
              <DrawCheck size={20} color="#166534" /> {t("eaw.draft_saved")}
            </span>
          )}
        </div>
        {!esManual && (
          <div className="actions" style={{ marginTop: 10 }}>
            <button className="secondary" onClick={() => setStep("resumen")}>{t("eaw.back_to_areas")}</button>
          </div>
        )}

        {fuentePreview && (
          <aside style={{
            position: "fixed", top: 0, right: 0, height: "100vh", width: "clamp(320px, 32vw, 480px)",
            background: "#fff", boxShadow: "-6px 0 24px rgba(0,0,0,.18)", zIndex: 1000,
            display: "flex", flexDirection: "column",
          }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, padding: "10px 14px", borderBottom: "1px solid var(--border)" }}>
              <strong style={{ fontSize: 13, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{fuentePreview.etiqueta}</strong>
              <button className="secondary" onClick={cerrarPreview} style={{ height: 28, minHeight: "auto", padding: "0 12px", fontSize: 12, flexShrink: 0 }}>{t("eaw.preview_close")}</button>
            </div>
            <iframe title={fuentePreview.etiqueta} src={fuentePreview.url} style={{ flex: 1, border: "none", width: "100%" }} />
          </aside>
        )}
      </section>
    );
  }

  return shell(null);
}

function App() {
  const resetToken = getResetToken();
  const [token, setToken] = useState(localStorage.getItem("evaluabot_token") || sessionStorage.getItem("evaluabot_token") || "");
  const [user, setUser] = useState(null);
  const [page, setPage] = useState(null);
  const [adminMode, setAdminMode] = useState(null); // null | "personal" | "admin"
  const [completedEvals, setCompletedEvals] = useState({});
  const [slackEvalCompletadas, setSlackEvalCompletadas] = useState({});
  const [legalDoc, setLegalDoc] = useState(getLegalDoc());
  const [, forceLang] = useState(0);

  // Re-render de toda la app cuando el usuario cambia de idioma con el selector.
  useEffect(() => subscribeLang(() => forceLang((n) => n + 1)), []);

  useEffect(() => {
    const onHash = () => setLegalDoc(getLegalDoc());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  // Registra el estado inicial en el historial del navegador
  useEffect(() => {
    window.history.replaceState({ page: null, adminMode: null }, "");
  }, []);

  // Escucha el botón de atrás del navegador
  useEffect(() => {
    const onPopState = (e) => {
      if (e.state && "page" in e.state) {
        setPage(e.state.page);
        setAdminMode(e.state.adminMode ?? null);
      }
    };
    window.addEventListener("popstate", onPopState);
    return () => window.removeEventListener("popstate", onPopState);
  }, []);

  function navigate(newPage, newAdminModeOverride) {
    // Guarda el estado actual en el historial antes de cambiar de página
    window.history.pushState({ page, adminMode }, "");
    setPage(newPage);
    if (newAdminModeOverride !== undefined) setAdminMode(newAdminModeOverride);
  }

  function closeLegal() {
    if (window.location.hash) {
      window.history.replaceState(null, "", window.location.pathname + window.location.search);
    }
    setLegalDoc(null);
  }

  useEffect(() => {
    if (resetToken) return;
    if (!token) return;
    apiRequest("/api/me", { token })
      .then((data) => {
        if (data.user) { setLang(data.user.idioma); setUser(data.user); }
        else { clearApiCache(); localStorage.removeItem("evaluabot_token"); setToken(""); }
      })
      .catch(() => { clearApiCache(); localStorage.removeItem("evaluabot_token"); setToken(""); });
  }, [token, resetToken]);

  function handleLogout() {
    // Invalida el token también en el servidor (no solo en el navegador).
    if (token) apiRequest("/api/logout", { token, method: "POST" }).catch(() => {});
    localStorage.removeItem("evaluabot_token");
    sessionStorage.removeItem("evaluabot_token");
    clearApiCache();
    setToken("");
    setUser(null);
    setPage(null);
    setAdminMode(null);
    setCompletedEvals({});
  }

  function backTo(p) {
    if (p?.from === "advisee-detail") return () => navigate({ type: "advisee-detail", advisee: p.advisee, advisees: p.advisees });
    if (p?.from === "advisees-list") return () => navigate({ type: "advisees-list", advisees: p.advisees });
    return () => navigate(null);
  }

  if (legalDoc) {
    return <LegalPage doc={legalDoc} onBack={closeLegal} />;
  }

  if (resetToken || !token || !user) {
    return <AuthScreen onLogin={(newToken, newUser) => { clearApiCache(); setLang(newUser?.idioma); setToken(newToken); setUser(newUser); }} />;
  }

  const isAdmin = Boolean(user?.is_admin);

  if (isAdmin && adminMode === null) {
    return <AdminRoleSelect user={user} onChoose={(mode) => navigate(null, mode)} onLogout={handleLogout} />;
  }

  if (isAdmin && adminMode === "admin") {
    return <AdminPanel token={token} onBack={() => navigate(null, null)} />;
  }

  let content;
  if (page?.type === "advisees-list") {
    content = <AdviseesList token={token} advisees={page.advisees} onBack={() => navigate(null)} onNavigate={navigate} />;
  } else if (page?.type === "advisee-detail") {
    content = (
      <AdviseeDetail
        token={token}
        advisee={page.advisee}
        advisees={page.advisees}
        onBack={() => navigate({ type: "advisees-list", advisees: page.advisees })}
        onNavigate={navigate}
      />
    );
  } else if (page?.type === "registro-comentarios") {
    content = (
      <RegistroComentariosPage
        token={token}
        advisee={page.advisee}
        onBack={() => navigate({ type: "advisee-detail", advisee: page.advisee, advisees: page.advisees })}
      />
    );
  } else if (page?.type === "mis-objetivos") {
    content = <MisObjetivosPage token={token} persona={user?.persona || user?.username || ""} onBack={() => navigate(null)} />;
  } else if (page?.type === "objetivos") {
    content = (
      <ObjetivosPage
        token={token}
        advisee={page.advisee}
        caName={user?.persona || ""}
        onBack={backTo(page)}
        vista={page.vista || "form"}
        // Mismo page (conserva from/advisees, y con ello el "volver"), solo cambia la vista.
        onCambiarVista={(v) => navigate({ ...page, vista: v })}
      />
    );
  } else if (page?.type === "plan-accion") {
    content = <PlanAccionPage token={token} advisee={page.advisee} advisees={page.advisees} onBack={backTo(page)} onNavigate={navigate} />;
  } else if (page?.type === "subir-informe") {
    content = <SubirInformePage token={token} advisee={page.advisee} onBack={backTo(page)} />;
  } else if (page?.type === "eval-anual") {
    content = <EvaluacionAnualWizard token={token} advisee={page.advisee} modo={page.modo} onBack={backTo(page)} />;
  } else if (page?.type === "activar-evaluaciones-proyecto") {
    content = <ActivarEvaluacionesProyectoPage token={token} user={user} onBack={() => navigate(null)} />;
  } else if (page?.type === "mis-proyectos-activos") {
    content = <MisProyectosActivosPage token={token} user={user} onBack={() => navigate(null)} />;
  } else if (page?.type === "evaluaciones-proyecto") {
    content = (
      <EvaluacionesProyectoPage
        token={token}
        user={user}
        proyectos={page.proyectos || []}
        onBack={() => navigate(null)}
        onNavigate={navigate}
        completedEvals={completedEvals}
        initialProyecto={page.initialProyecto}
      />
    );
  } else if (page?.type === "solicitar-evaluacion-extra") {
    content = <SolicitarEvaluacionExtraPage token={token} user={user} onBack={() => navigate(null)} />;
  } else if (page?.type === "formulario-evaluacion-extra") {
    content = (
      <FormularioEvaluacionExtra
        token={token}
        evaluado={page.evaluado}
        contexto={page.contexto}
        solicitudPageId={page.solicitudPageId}
        onBack={() => navigate(null)}
      />
    );
  } else if (page?.type === "evaluaciones-slack") {
    content = (
      <EvaluacionesSlackPage
        token={token}
        user={user}
        onBack={() => navigate(null)}
        onNavigate={navigate}
        completadasApp={slackEvalCompletadas}
        onCompletada={(key) => setSlackEvalCompletadas(prev => ({ ...prev, [key]: true }))}
      />
    );
  } else if (page?.type === "historial-evaluaciones") {
    const backFromHistorial = page.from === "evaluaciones-proyecto"
      ? () => navigate({ type: "evaluaciones-proyecto", proyectos: page.proyectos || [], initialProyecto: page.proyecto })
      : () => navigate({ type: "evaluaciones-slack" });
    content = (
      <HistorialEvaluacionesPage
        token={token}
        evaluado={page.evaluado}
        evaluador={page.evaluador}
        proyecto={page.proyecto}
        onBack={backFromHistorial}
      />
    );
  } else if (page?.type === "detalle-evaluacion-realizada") {
    content = (
      <DetalleEvaluacionRealizadaPage
        ev={page.ev}
        proyecto={page.proyecto}
        onBack={() => navigate(null)}
      />
    );
  } else if (page?.type === "formulario-evaluacion-proyecto") {
    content = (
      <FormularioEvaluacionProyecto
        token={token}
        user={user}
        proyecto={page.proyecto}
        tipo={page.tipo}
        evaluadoProp={page.evaluado}
        relacion={page.relacion || ""}
        manager={page.manager}
        onBack={() => navigate({ type: "evaluaciones-proyecto", proyectos: page.proyectos || [], initialProyecto: page.proyecto })}
        onEnviado={() => setCompletedEvals((prev) => {
          const key = `${page.tipo}:${page.evaluado}`;
          const list = prev[page.proyecto] || [];
          if (list.includes(key)) return prev;
          return { ...prev, [page.proyecto]: [...list, key] };
        })}
      />
    );
  } else {
    content = (
      <Dashboard
        token={token}
        user={user}
        onLogout={handleLogout}
        onNavigate={navigate}
        onBackToRoleSelect={isAdmin && adminMode === "personal" ? () => navigate(null, null) : null}
      />
    );
  }

  return (
    <GoHomeContext.Provider value={() => navigate(null)}>
      {content}
    </GoHomeContext.Provider>
  );
}

createRoot(document.getElementById("root")).render(
  <>
    <TopLoadingBar />
    <LangToggle />
    <App />
  </>
);
