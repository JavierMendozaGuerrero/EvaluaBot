import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles/globals.css";
import "./styles/components.css";
import "./styles.css";
import privacidadMd from "./legal/privacidad.md?raw";
import terminosMd from "./legal/terminos.md?raw";
import { t, setLang, setLangManual, getLang, subscribeLang, nombreMes } from "./i18n";

const LEGAL_DOCS = {
  privacidad: { titulo: "Política de privacidad", texto: privacidadMd },
  terminos: { titulo: "Términos y condiciones", texto: terminosMd },
};

function getLegalDoc() {
  const hash = (window.location.hash || "").replace(/^#/, "").toLowerCase();
  return LEGAL_DOCS[hash] ? hash : null;
}

const API_BASE = import.meta.env.VITE_API_BASE_URL || `${window.location.protocol}//${window.location.hostname}:8000`;

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
      throw new Error(data.error || t("common.err_generic"));
    }
    return data;
  } finally {
    stopLoading();
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

function LegalPage({ doc, onBack }) {
  const data = LEGAL_DOCS[doc];
  useEffect(() => { window.scrollTo(0, 0); }, [doc]);
  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <button className="link-button" onClick={onBack}>{t("common.back")}</button>
      </nav>
      <div className="legal-wrap">
        {data ? <LegalContent texto={data.texto} /> : <p>{t("legal.unavailable")}</p>}
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
      window.open(apiUrl(`${path}&token=${encodeURIComponent(token)}`), "_blank", "noopener,noreferrer");
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
      if (!path) throw new Error("No se generó el documento.");
      const response = await fetch(apiUrl(path), { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) {
        const d = await response.json().catch(() => ({}));
        throw new Error(d.error || "No se pudo descargar el archivo.");
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
                <p className="kicker">Información disponible</p>
                <button className="secondary" disabled={!!generandoFuente}
                  onClick={() => descargarFuentePdf("/api/generar-pdf-evals-mensuales", "evals_mensuales")}>
                  {generandoFuente === "/api/generar-pdf-evals-mensuales" ? "Generando..." : "Evaluaciones mensuales"}
                </button>
                <button className="secondary" disabled={!!generandoFuente} style={{ marginTop: 8 }}
                  onClick={() => descargarFuentePdf("/api/generar-pdf-evals-proyecto", "evals_proyecto")}>
                  {generandoFuente === "/api/generar-pdf-evals-proyecto" ? "Generando..." : "Evaluaciones de proyecto"}
                </button>
                <button className="secondary" disabled={!!generandoFuente} style={{ marginTop: 8 }}
                  onClick={() => descargarFuentePdf("/api/generar-pdf-seguimiento", "seguimiento_personal")}>
                  {generandoFuente === "/api/generar-pdf-seguimiento" ? "Generando..." : "Seguimiento personal"}
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

function MisObjetivosPage({ token, persona, onBack }) {
  const [objetivos, setObjetivos] = useState([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    apiRequest(`/api/objetivos?nombre=${encodeURIComponent(persona)}`, { token })
      .then((data) => setObjetivos(data.objetivos || []))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [token, persona]);

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <button className="link-button" onClick={onBack}>{t("common.back")}</button>
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
            {objetivos.map((obj, i) => (
              <article key={i} className="objetivo-item">
                <p className="objetivo-titulo"><strong>{obj.titulo}</strong></p>
                {obj.kpis && <p className="objetivo-texto fine"><em>KPIs:</em> {obj.kpis}</p>}
              </article>
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

function ObjetivosPage({ token, advisee, caName, onBack }) {
  const [objetivos, setObjetivos] = useState([]);
  const [form, setForm] = useState({ titulo: "", kpis: "", descripcion: "", tipo: "" });
  const [pendientes, setPendientes] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deleting, setDeleting] = useState(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  function recargar() {
    return apiRequest(`/api/objetivos?nombre=${encodeURIComponent(advisee.nombre)}`, { token })
      .then((data) => setObjetivos(data.objetivos || []));
  }

  useEffect(() => {
    recargar().catch((err) => setError(err.message)).finally(() => setLoading(false));
  }, [token, advisee.nombre]);

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

  // Agrupa los objetivos por año y, dentro de cada año, por mes.
  // objetivos ya viene ordenado por fecha descendente desde el backend.
  const objetivosPorAnio = useMemo(() => {
    const anios = new Map(); // anio -> Map(mesIdx -> [obj])
    for (const obj of objetivos) {
      const fecha = obj.fecha || "";
      const anio = fecha.slice(0, 4) || t("common.no_date");
      const mesIdx = fecha.length >= 7 ? parseInt(fecha.slice(5, 7), 10) - 1 : -1;
      if (!anios.has(anio)) anios.set(anio, new Map());
      const meses = anios.get(anio);
      if (!meses.has(mesIdx)) meses.set(mesIdx, []);
      meses.get(mesIdx).push(obj);
    }
    return [...anios.entries()].map(([anio, meses]) => [anio, [...meses.entries()]]);
  }, [objetivos]);

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
      await recargar();
      setForm({ titulo: "", kpis: "", descripcion: "", tipo: "" });
      setPendientes([]);
      setSuccess(aGuardar.length === 1
        ? t("goals.saved_one")
        : t("goals.saved_many", { n: aGuardar.length }));
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function eliminar(page_id) {
    if (!window.confirm(t("goals.confirm_delete"))) return;
    setDeleting(page_id);
    try {
      await apiRequest("/api/objetivos", { token, method: "DELETE", body: { page_id } });
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
        <button className="link-button" onClick={onBack}>{t("common.back")}</button>
      </nav>
      <section className="hero dashboard-hero">
        <div>
          {advisee.foto
            ? <img src={advisee.foto} alt={advisee.nombre} className="objetivos-foto" />
            : <div className="objetivos-foto objetivos-foto-placeholder">{advisee.nombre.charAt(0)}</div>
          }
          <p className="kicker">{t("goals.kicker")}</p>
          <h1>{advisee.nombre}</h1>
        </div>
        <form className="panel" onSubmit={guardar}>
          <h2>{t("goals.new")}</h2>
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
      </section>

      <section className="objetivos-historial panel">
        <p className="kicker">{t("goals.history")}</p>
        <h2>{t("goals.of_person", { nombre: advisee.nombre })}</h2>
        {loading ? (
          <p>{t("common.loading")}</p>
        ) : objetivos.length ? (
          <div className="objetivos-anios">
            {objetivosPorAnio.map(([anio, meses], anioIdx) => (
              <details key={anio} className="objetivos-anio" open={anioIdx === 0}>
                <summary className="objetivos-anio-head"><span>{anio}</span></summary>
                {meses.map(([mesIdx, items], mesPos) => (
                  <details key={mesIdx} className="objetivos-mes" open={mesPos === 0}>
                    <summary className="objetivos-mes-head">{mesIdx >= 0 ? nombreMes(mesIdx) : t("common.no_date")}</summary>
                    <div className="objetivos-list">
                      {items.map((obj) => (
                        <article key={obj.page_id} className="objetivo-item">
                          {obj.tipo && <p className="opinion-fecha fine">{obj.tipo}</p>}
                          <p className="objetivo-titulo"><strong>{obj.titulo}</strong></p>
                          {obj.kpis && <p className="objetivo-texto fine"><em>KPIs:</em> {obj.kpis}</p>}
                          {obj.descripcion && <p className="objetivo-texto">{obj.descripcion}</p>}
                          <div style={{ marginTop: "8px" }}>
                            <button
                              className="link-button"
                              style={{ color: "var(--muted, #999)", fontSize: "12px" }}
                              disabled={deleting === obj.page_id}
                              onClick={() => eliminar(obj.page_id)}
                            >
                              {deleting === obj.page_id ? t("common.deleting") : t("common.delete")}
                            </button>
                          </div>
                        </article>
                      ))}
                    </div>
                  </details>
                ))}
              </details>
            ))}
          </div>
        ) : (
          <p>{t("goals.none_for", { nombre: advisee.nombre })}</p>
        )}
      </section>
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
        await apiRequest("/api/register", { method: "POST", body: form });
        setMode("login");
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
        const data = await apiRequest("/api/login", { method: "POST", body: form });
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
        <div className="chat-input-area"><div className="chat-btns">{[1,2,3,4].map(n => <button key={n} className="chat-btn" onClick={() => handleValoracion(String(n))}>{n}</button>)}</div></div>
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
        <div className="chat-input-area"><div className="chat-btns">{[1,2,3,4].map(n => <button key={n} className="chat-btn" onClick={() => handleModificarValor(String(n))}>{n}</button>)}</div></div>
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
  const [urgenciaVal, setUrgenciaVal] = React.useState("");
  const [urgenciaDesc, setUrgenciaDesc] = React.useState("");
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

  function handleUrgencia() {
    userSay("🚨 Urgencia");
    setUrgenciaVal("");
    setUrgenciaDesc("");
    botSay("🚨 Describe en una frase breve la urgencia:");
    setStep("urgencia_descripcion");
  }

  function handleUrgenciaSubmit() {
    const val = urgenciaVal.trim();
    if (!val) return;
    userSay(val);
    setUrgenciaDesc(val);
    setUrgenciaVal("");
    botSay(`📋 Tu urgencia:\n_${val}_\n\n¿La envío a tu CA?`);
    setStep("urgencia_confirmacion");
  }

  async function handleUrgenciaEnviar() {
    userSay("✅ Enviar al CA");
    setLoading(true);
    try {
      const d = await apiRequest("/api/urgencia-personal", { token, method: "POST", body: { descripcion: urgenciaDesc } });
      botSay(d.ok ? "✅ Tu urgencia ha sido enviada a tu CA." : "⚠️ No se pudo notificar a tu CA. Contacta directamente.");
    } catch (e) {
      botSay(`⚠️ No se pudo notificar: ${e.message || "Error desconocido"}`);
    } finally {
      setLoading(false);
    }
    setStep("esperando_comentario");
  }

  function handleUrgenciaModificar() {
    userSay("✏️ Modificar");
    setUrgenciaVal("");
    botSay("🚨 Describe de nuevo la urgencia:");
    setStep("urgencia_descripcion");
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
          <button className="chat-btn" style={{ color: "var(--danger, #e53e3e)" }} onClick={handleUrgencia}>🚨 Urgencia</button>
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
    if (step === "urgencia_descripcion") return (
      <div className="chat-input-area">
        <div className="chat-input-row">
          <textarea className="chat-input chat-textarea" placeholder="Describe la urgencia..." value={urgenciaVal} onChange={e => setUrgenciaVal(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleUrgenciaSubmit(); } }} rows={2} autoFocus />
          <button className="chat-send-btn" onClick={handleUrgenciaSubmit}>→</button>
        </div>
      </div>
    );
    if (step === "urgencia_confirmacion") return (
      <div className="chat-input-area"><div className="chat-btns">
        <button className="chat-btn primary" onClick={handleUrgenciaEnviar}>✅ Enviar al CA</button>
        <button className="chat-btn" onClick={handleUrgenciaModificar}>✏️ Modificar</button>
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

function ChatEvalCA({ token, user, adviseesProp, onComplete }) {
  const [msgs, setMsgs] = React.useState([{
    role: "bot",
    text: "📋 *CA: Revisión de advisees*\n\n_Esta revisión es totalmente privada, solo podrás verla tú._\n\n*Pulsa el botón* para comenzar.",
  }]);
  const [step, setStep] = React.useState("intro");
  const [adviseeActual, setAdviseeActual] = React.useState("");
  const [opinion, setOpinion] = React.useState("");
  const [inputVal, setInputVal] = React.useState("");
  const [loading, setLoading] = React.useState(false);
  const [advisees, setAdvisees] = React.useState([]);
  const [guardados, setGuardados] = React.useState([]);
  const bottomRef = React.useRef(null);

  React.useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: "smooth" }); }, [msgs]);

  React.useEffect(() => {
    apiRequest("/api/mis-advisees", { token })
      .then(d => setAdvisees((d.advisees || []).map(a => a.nombre).filter(Boolean)))
      .catch(() => setAdvisees((adviseesProp || []).map(a => a.nombre || a).filter(Boolean)));
  }, [token]);

  const botSay = (text) => setMsgs(m => [...m, { role: "bot", text }]);
  const userSay = (text) => setMsgs(m => [...m, { role: "user", text }]);

  const disponibles = advisees.filter(a => !guardados.includes(a));

  function handleComenzar() {
    userSay("Comenzar");
    if (advisees.length === 0) {
      botSay("Cargando advisees...");
    }
    setStep("esperando_advisee");
    botSay("¿De qué advisee te gustaría hacer seguimiento?");
  }

  async function handleAdvisee(nombre) {
    const val = (nombre || inputVal).trim();
    if (!val) return;
    setInputVal("");
    if (val.toLowerCase() === "no") {
      userSay("No");
      botSay("¡Perfecto, gracias por tu tiempo! 🎉");
      setStep("terminado");
      return;
    }
    const num = parseInt(val);
    const adviseeNombre = !isNaN(num) && num >= 1 && num <= disponibles.length
      ? disponibles[num - 1]
      : val;
    const found = advisees.find(a => a.toLowerCase() === adviseeNombre.toLowerCase());
    if (!found) {
      botSay(`*${adviseeNombre}* no aparece en tu lista de advisees. Selecciona uno de los botones o escribe *no* para terminar.`);
      return;
    }
    userSay(found);
    setAdviseeActual(found);
    setLoading(true);
    try {
      const d = await apiRequest(`/api/resumen-evaluaciones-advisee?advisee=${encodeURIComponent(found)}`, { token });
      botSay(d.resumen);
      if (d.sinNovedades) {
        botSay("¿De qué advisee te gustaría hacer seguimiento?");
      } else {
        botSay("¿Qué opinas de las evaluaciones? Escribe tu comentario sobre el progreso de tu advisee.");
        setStep("esperando_opinion");
      }
    } catch { botSay("⚠️ Error cargando evaluaciones. Inténtalo de nuevo."); }
    finally { setLoading(false); }
  }

  function handleOpinion() {
    const val = inputVal.trim();
    if (!val) return;
    userSay(val);
    setOpinion(val);
    setInputVal("");
    botSay(`*Resumen de tu valoración:*\n• Advisee: *${adviseeActual}*\n• Opinión: ${val}\n\n¿Lo guardo?`);
    setStep("confirmacion");
  }

  async function handleConfirmar() {
    userSay(t("cep.save_yes"));
    setLoading(true);
    try {
      await apiRequest("/api/notas-ca", { token, method: "POST", body: { advisee: adviseeActual, nota: opinion } });
      const nuevosGuardados = [...guardados, adviseeActual];
      setGuardados(nuevosGuardados);
      onComplete?.();
      const restantes = advisees.filter(a => !nuevosGuardados.includes(a));
      if (restantes.length > 0) {
        botSay("✅ Opinión guardada en Notion.\n\n¿De qué advisee te gustaría hacer seguimiento?");
        setStep("esperando_advisee");
      } else {
        botSay("✅ Opinión guardada en Notion.\n\n¡Has completado el seguimiento de todos tus advisees! 🎉");
        setStep("terminado");
      }
    } catch { botSay("⚠️ No se pudo guardar en Notion. Revisa permisos/logs."); }
    finally { setLoading(false); }
  }

  function handleModificar() {
    userSay("✏️ Modificar");
    setOpinion("");
    botSay("¿Qué comentario deseas registrar sobre las evaluaciones de tu advisee?");
    setStep("esperando_opinion");
  }

  function renderInput() {
    if (loading) return <div className="chat-input-area"><div className="chat-input-row"><span className="fine" style={{ color: "var(--muted)" }}>...</span></div></div>;
    if (step === "intro") return (
      <div className="chat-input-area"><div className="chat-btns"><button className="chat-btn primary" onClick={handleComenzar}>Comenzar</button></div></div>
    );
    if (step === "esperando_advisee") return (
      <div className="chat-input-area">
        <div className="chat-sugerencias" style={{ flexWrap: "wrap" }}>
          {disponibles.map(a => (
            <button key={a} className="chat-btn" onClick={() => handleAdvisee(a)}>{a}</button>
          ))}
          <button className="chat-btn" onClick={() => handleAdvisee("no")}>❌ Terminar</button>
        </div>
      </div>
    );
    if (step === "esperando_opinion") return (
      <div className="chat-input-area"><div className="chat-input-row">
        <textarea className="chat-input chat-textarea" placeholder="Escribe tu opinión..." value={inputVal} onChange={e => setInputVal(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleOpinion(); } }} rows={3} autoFocus />
        <button className="chat-send-btn" onClick={handleOpinion}>→</button>
      </div></div>
    );
    if (step === "confirmacion") return (
      <div className="chat-input-area"><div className="chat-btns">
        <button className="chat-btn primary" onClick={handleConfirmar}>{t("cep.save_yes")}</button>
        <button className="chat-btn" onClick={handleModificar}>{t("cep.btn_modificar")}</button>
      </div></div>
    );
    if (step === "terminado") return (
      <div className="chat-input-area"><span className="fine" style={{ color: "var(--muted)" }}>Revisión completada ✅</span></div>
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
        <button className="link-button" onClick={onBack}>{t("common.back")}</button>
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
                  <th>{t("hist.col_score")}</th>
                  <th>{t("hist.col_justif")}</th>
                  <th>{t("hist.col_relation")}</th>
                </tr>
              </thead>
              <tbody>
                {historial.map((ev, i) => (
                  <tr key={i}>
                    <td className="hist-fecha">{formatFecha(ev.fecha)}</td>
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

function EvaluacionesSlackSection({ token, user, advisees, onNavigate, onCompletada }) {
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

function DashNavItem({ label, onClick, disabled, external = false }) {
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
    </div>
  );
}

function DashCollapsible({ title, open, onToggle, children, badge = null }) {
  return (
    <div>
      <div onClick={onToggle} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", cursor: "pointer", userSelect: "none" }}>
        <span className="eyebrow" style={{ marginBottom: 0, fontSize: "0.7rem" }}><span className="dash-dot" />{title}</span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
          {badge != null && (
            <span style={{
              display: "inline-flex", alignItems: "center", justifyContent: "center",
              minWidth: 20, height: 20, padding: "0 5px", borderRadius: 4,
              background: "rgba(242,60,20,.12)", color: "var(--accent)", fontWeight: 500,
              fontSize: 11, whiteSpace: "nowrap",
            }}>{badge}</span>
          )}
          <svg viewBox="0 0 24 24" fill="none" stroke="rgba(0,0,0,.3)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
            style={{ width: 11, height: 11, flexShrink: 0, transform: open ? "rotate(180deg)" : "rotate(0deg)", transition: "transform .25s" }}>
            <polyline points="18 15 12 9 6 15" />
          </svg>
        </span>
      </div>
      {open && <div style={{ marginTop: 10 }}>{children}</div>}
    </div>
  );
}

const DASH_DIVIDER = { border: "none", borderTop: "1px solid var(--border)", margin: "16px 0" };
const PAISES_PERMITIDOS = ["España", "México", "Portugal"];

function Dashboard({ token, user, onLogout, onNavigate, onBackToRoleSelect = null }) {
  const [evaluados, setEvaluados] = useState([]);
  const [evaluado, setEvaluado] = useState("");
  const [status, setStatus] = useState("");
  const [links, setLinks] = useState(null);
  const [advisees, setAdvisees] = useState([]);
  const [opinionesModal, setOpinionesModal] = useState(null);
  const [loadingOpiniones, setLoadingOpiniones] = useState(false);
  const [evaluadosAnual, setEvaluadosAnual] = useState([]);
  const [evaluadoAnual, setEvaluadoAnual] = useState("");
  const [cargoAnual, setCargoAnual] = useState("");
  const [statusAnual, setStatusAnual] = useState("");
  const [linkAnual, setLinkAnual] = useState(null);
  const [accesoActivo, setAccesoActivo] = useState(false);
  const [togglingAcceso, setTogglingAcceso] = useState(false);
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
  const [informesOpen, setInformesOpen] = useState(false);
  const [objOpen, setObjOpen] = useState(false);
  const [tareasOpen, setTareasOpen] = useState(false);
  const [projOpen, setProjOpen] = useState(false);
  const [extraEvalOpen, setExtraEvalOpen] = useState(false);
  const [seccionActiva, setSeccionActiva] = useState(null);
  const [proyectosActivos, setProyectosActivos] = useState([]);
  const [proyectosManager, setProyectosManager] = useState(null);
  const [proyectosVersion, setProyectosVersion] = useState(0);
  const [proyectosProgreso, setProyectosProgreso] = useState({});
  const [tareasProyecto, setTareasProyecto] = useState([]);
  const [tareasSlack, setTareasSlack] = useState({ pendientes: [], url: "" });
  const [evaluacionesExtraPendientes, setEvaluacionesExtraPendientes] = useState([]);

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
    if (!isAdmin) return;
    const apply = (data) => { const lista = data.evaluados || []; setEvaluadosAnual(lista); if (lista.length) setEvaluadoAnual(lista[0].value); };
    apiRequestCached("/api/evaluados-anual", { token }, apply)
      .then(apply)
      .catch(() => {});
  }, [token, isAdmin]);

  useEffect(() => {
    const apply = (data) => setAccesoActivo(data.activo || false);
    apiRequestCached("/api/acceso-advisees", { token }, apply)
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

  useEffect(() => {
    if (isAdmin) return;
    apiRequest("/api/evaluaciones-proyecto-activas", { token })
      .then((d) => setProyectosActivos(d.proyectos || []))
      .catch(() => {});
    apiRequest("/api/proyectos-manager", { token })
      .then((d) => setProyectosManager(d.proyectos || []))
      .catch(() => setProyectosManager([]));
  }, [token, isAdmin, proyectosVersion]);

  useEffect(() => {
    apiRequest("/api/evaluaciones-extra-pendientes", { token })
      .then((d) => setEvaluacionesExtraPendientes(d.pendientes || []))
      .catch(() => setEvaluacionesExtraPendientes([]));
  }, [token]);

  useEffect(() => {
    if (isAdmin) { setTareasSlack({ pendientes: [], url: "" }); return; }
    apiRequest("/api/tareas-slack", { token })
      .then((d) => setTareasSlack({ pendientes: d.pendientes || [], url: d.slackUrl || "" }))
      .catch(() => {});
  }, [token, isAdmin]);

  useEffect(() => {
    if (isAdmin || !proyectosActivos.length) { setProyectosProgreso({}); setTareasProyecto([]); return; }
    const persona = user?.persona || user?.username || "";
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
          const equipo = p.equipo || [];
          const completadasKeys = (p.completadas || []).map((c) => `${c.tipo}:${norm(c.evaluado)}`);
          const lista = construirEvaluacionesProyectoAHacer(persona, p.activado_por || "", equipo);
          const pendientes = lista
            .filter((it) => !completadasKeys.includes(`${it.tipo}:${norm(it.evaluado)}`))
            .map((it) => ({ proyecto: p.nombre_proyecto, tipo: it.tipo, evaluado: it.evaluado, label: it.label }));
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
      if (cargoAnual) body.cargo = cargoAnual;
      const data = await apiRequest("/api/generar", { token, method: "POST", body });
      setLinks(data);
      setStatus(t("dash.report_ready", { n: data.total }));
    } catch (err) {
      setStatus(err.message);
    }
  }

  async function generateAnual() {
    setLinkAnual(null);
    setStatusAnual(t("dash.interpreting"));
    try {
      const data = await apiRequest("/api/generar-anual", { token, method: "POST", body: { evaluado: evaluadoAnual, cargo: cargoAnual } });
      setStatusAnual(t("dash.annual_generated"));
      setLinkAnual(data.docxUrl);
    } catch (err) {
      setStatusAnual(err.message);
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
      a.download = `informe_anual_${evaluadoAnual.replace(/\s+/g, "_")}.docx`;
      a.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setStatusAnual(err.message);
    }
  }

  async function loadOpiniones(adviseeNombre) {
    setLoadingOpiniones(true);
    try {
      const data = await apiRequest(`/api/opiniones-ca?advisee=${encodeURIComponent(adviseeNombre)}`, { token });
      setOpinionesModal({ nombre: adviseeNombre, opiniones: data.opiniones || [] });
    } catch (err) {
      setStatus(err.message);
    } finally {
      setLoadingOpiniones(false);
    }
  }

  async function openFile(path, filename) {
    if (!filename.endsWith(".docx")) {
      window.open(apiUrl(`${path}&token=${encodeURIComponent(token)}`), "_blank", "noopener,noreferrer");
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

  async function toggleAcceso() {
    setTogglingAcceso(true);
    try {
      const data = await apiRequest("/api/acceso-advisees", { token, method: "POST", body: { activo: !accesoActivo } });
      setAccesoActivo(data.activo);
    } catch (err) {
      setStatus(err.message);
    } finally {
      setTogglingAcceso(false);
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
        <div className="profile-grid">

          {/* LEFT — To-do */}
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <p className="eyebrow" style={{ color: "var(--fg)", textAlign: "center", fontWeight: 500, display: "flex", alignItems: "center", justifyContent: "center", gap: 6 }}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14, flexShrink: 0 }}>
                <path d="M9 11l3 3L20 4" />
                <path d="M21 12v7a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11" />
              </svg>
              To-do
            </p>
            <hr style={{ ...DASH_DIVIDER, margin: 0 }} />
            <nav style={{ display: "flex", flexDirection: "column" }}>
              {!isAdmin && (
                <DashNavItem label={t("dash.nav_activate_proj")} onClick={() => onNavigate({ type: "activar-evaluaciones-proyecto" })} external />
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
                    <svg viewBox="0 0 24 24" fill="none" stroke="rgba(0,0,0,.3)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                      style={{ width: 11, height: 11, flexShrink: 0, transform: extraEvalOpen ? "rotate(180deg)" : "rotate(0deg)", transition: "transform .25s" }}>
                      <polyline points="18 15 12 9 6 15" />
                    </svg>
                  </span>
                </div>
                {extraEvalOpen && (
                  <div style={{ display: "flex", flexDirection: "column", gap: 6, paddingBottom: 12 }}>
                    {evaluacionesExtraPendientes.map((p) => (
                      <div
                        key={p.page_id}
                        onClick={() => onNavigate({ type: "formulario-evaluacion-extra", solicitudPageId: p.page_id, evaluado: p.evaluado, contexto: p.contexto })}
                        style={{ display: "flex", flexDirection: "column", gap: 2, fontSize: 13, color: "#000", cursor: "pointer", padding: "5px 0", paddingLeft: 4 }}
                      >
                        <span>{t("eep.requested_by", { nombre: p.evaluado })}</span>
                        <span style={{ fontSize: 12, fontWeight: 200, color: "rgba(0,0,0,.55)" }}>{p.contexto}</span>
                      </div>
                    ))}
                    <div
                      onClick={() => onNavigate({ type: "solicitar-evaluacion-extra" })}
                      style={{ fontSize: 13, color: "var(--accent)", cursor: "pointer", padding: "5px 0", paddingLeft: 4 }}
                    >
                      {t("dash.nav_request_extra_eval")}
                    </div>
                  </div>
                )}
              </div>
              {!isAdmin && proyectosPendientes.length > 0 && (
                <div>
                  <div
                    role="button"
                    tabIndex={0}
                    onClick={() => setProjOpen((v) => !v)}
                    style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "6px 0", fontSize: 14, fontWeight: 400, cursor: "pointer", color: "#000", userSelect: "none" }}
                  >
                    <span><span className="dash-dot" />{t("dash.nav_proj_evals")}</span>
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 8, flexShrink: 0 }}>
                      <span style={{ fontSize: 11, fontWeight: 500, color: "var(--accent)", whiteSpace: "nowrap" }}>
                        {t("dash.proj_evals_unfinished")}
                      </span>
                      <svg viewBox="0 0 24 24" fill="none" stroke="rgba(0,0,0,.3)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"
                        style={{ width: 11, height: 11, flexShrink: 0, transform: projOpen ? "rotate(180deg)" : "rotate(0deg)", transition: "transform .25s" }}>
                        <polyline points="18 15 12 9 6 15" />
                      </svg>
                    </span>
                  </div>
                  {projOpen && (
                    <div style={{ display: "flex", flexDirection: "column", gap: 6, paddingBottom: 12 }}>
                      {proyectosPendientes.map((p) => {
                        const prog = proyectosProgreso[p.nombre_proyecto];
                        return (
                          <div
                            key={p.nombre_proyecto}
                            onClick={() => onNavigate({ type: "evaluaciones-proyecto", proyectos: proyectosActivos, initialProyecto: p.nombre_proyecto })}
                            style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 8, fontSize: 13, color: "#000", cursor: "pointer", padding: "5px 0", paddingLeft: 4 }}
                          >
                            <span>{p.nombre_proyecto}</span>
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
              {advisees.length > 0 && (
                <DashNavItem label={t("dash.nav_my_advisees")} onClick={() => onNavigate({ type: "advisees-list", advisees })} external />
              )}
              {!isAdmin && proyectosManager?.length > 0 && (
                <DashNavItem label={t("dash.nav_manage_projects")} onClick={() => onNavigate({ type: "mis-proyectos-activos" })} external />
              )}
              {isAdmin && !onBackToRoleSelect && (
                <DashNavItem label={t("dash.nav_admin_panel")} onClick={() => setSeccionActiva((v) => v === "admin" ? null : "admin")} />
              )}
            </nav>
          </div>

          {/* CENTER — profile photo + nombre */}
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

          {/* RIGHT — To-see */}
          <aside className="profile-info">
            <p className="eyebrow" style={{ color: "var(--fg)", textAlign: "center", fontWeight: 500, display: "flex", alignItems: "center", justifyContent: "center", gap: 6 }}>
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 14, height: 14, flexShrink: 0 }}>
                <path d="M1 12s4-7 11-7 11 7 11 7-4 7-11 7-11-7-11-7z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
              To-see
            </p>
            <hr style={{ ...DASH_DIVIDER, margin: 0 }} />

            

            <div>
              <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
                <p className="eyebrow" style={{ margin: 0, flexShrink: 0, fontSize: "0.7rem" }}><span className="dash-dot" />{t("dash.my_country")}</p>
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

            

            <DashCollapsible title={t("dash.my_goals")} open={objOpen} onToggle={() => setObjOpen((v) => !v)}>
              {misObjetivos.length ? (
                <div style={{ display: "flex", flexDirection: "column", gap: 8, paddingLeft: 12 }}>
                  {misObjetivos.map((obj, i) => (
                    <div key={i}>
                      <p style={{ fontSize: 13, color: "#000", display: "flex", alignItems: "center", gap: 6 }}>
                        <span style={{ color: "var(--accent)", flexShrink: 0, fontSize: "1.3em" }}>•</span>
                        {obj.titulo}
                      </p>
                      {obj.kpis && <p style={{ fontSize: 13, fontWeight: 200, color: "rgba(0,0,0,.55)", paddingLeft: 12 }}>{obj.kpis}</p>}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="fine">{t("dash.no_goals")}</p>
              )}
            </DashCollapsible>

            

            <DashCollapsible title={t("dash.my_reports")} open={informesOpen} onToggle={() => setInformesOpen((v) => !v)}>
              {informeFinalEmpleado === null ? (
                <p className="fine">{t("common.loading")}</p>
              ) : informeFinalEmpleado?.disponible ? (
                <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                  {informeFinalEmpleado.htmlUrl && (
                    <button className="secondary" onClick={() => openFile(informeFinalEmpleado.htmlUrl, "informe_final.html")}>{t("dash.open_web")}</button>
                  )}
                  {informeFinalEmpleado.docxUrl && (
                    <button className="secondary" onClick={() => openFile(informeFinalEmpleado.docxUrl, "informe_final.docx")}>{t("admin.download_word")}</button>
                  )}
                </div>
              ) : (
                <p className="fine">{t("dash.no_access")}</p>
              )}
            </DashCollapsible>

            <hr style={{ ...DASH_DIVIDER, margin: 0 }} />

            <div className="dash-tareas">
              <DashCollapsible title={t("dash.pending_tasks")} open={tareasOpen} onToggle={() => setTareasOpen((v) => !v)}
                badge={(() => { const n = tareasSlack.pendientes.length + tareasProyecto.length + evaluacionesExtraPendientes.length; return n > 0 ? n : null; })()}>
              {(tareasSlack.pendientes.length + tareasProyecto.length + evaluacionesExtraPendientes.length) === 0 ? (
                <p className="fine">{t("dash.no_pending_tasks")}</p>
              ) : (
                <div className="tareas-list">
                  {tareasSlack.pendientes.map((tp) => (
                    <div key={`slack-${tp}`} className="tarea-row"
                      onClick={() => { if (tareasSlack.url) window.location.href = tareasSlack.url; }}>
                      <span className="tarea-label">{t(`dash.slack_${tp}`)}</span>
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13, flexShrink: 0 }}><path d="M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6" /><polyline points="15 3 21 3 21 9" /><line x1="10" y1="14" x2="21" y2="3" /></svg>
                    </div>
                  ))}
                  {tareasProyecto.map((it) => (
                    <div key={`proj-${it.proyecto}-${it.tipo}-${it.evaluado}`} className="tarea-row"
                      onClick={() => onNavigate({ type: "evaluaciones-proyecto", proyectos: proyectosActivos, initialProyecto: it.proyecto })}>
                      <span className="tarea-label">{it.label} · {it.proyecto}</span>
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13, flexShrink: 0 }}><polyline points="9 18 15 12 9 6" /></svg>
                    </div>
                  ))}
                  {evaluacionesExtraPendientes.map((ev) => (
                    <div key={`extra-${ev.page_id}`} className="tarea-row"
                      onClick={() => onNavigate({ type: "formulario-evaluacion-extra", solicitudPageId: ev.page_id, evaluado: ev.evaluado, contexto: ev.contexto })}>
                      <span className="tarea-label">{t("eep.requested_by", { nombre: ev.evaluado })}</span>
                      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" style={{ width: 13, height: 13, flexShrink: 0 }}><polyline points="9 18 15 12 9 6" /></svg>
                    </div>
                  ))}
                </div>
              )}
            </DashCollapsible>
            </div>
          </aside>

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

      {opinionesModal && (
        <section className="opiniones-modal panel">
          <div className="opiniones-header">
            <div>
              <p className="kicker">Career Advisor</p>
              <h2>{t("dash.opinions_about", { nombre: opinionesModal.nombre })}</h2>
            </div>
            <button className="secondary" onClick={() => setOpinionesModal(null)}>{t("common.close")}</button>
          </div>
          {opinionesModal.opiniones.length ? (
            <div className="opiniones-list">
              {opinionesModal.opiniones.map((op, i) => (
                <article key={i} className="opinion-item">
                  <p className="opinion-fecha fine">{op.fecha ? op.fecha.slice(0, 10) : t("common.no_date")}</p>
                  {op.resumen_advisee && (
                    <div className="opinion-resumen">
                      <p className="fine"><strong>{t("dash.evals_seen")}</strong></p>
                      <pre className="opinion-pre">{op.resumen_advisee}</pre>
                    </div>
                  )}
                  <p className="fine"><strong>{t("dash.ca_opinion")}</strong></p>
                  <p className="opinion-texto">{op.opinion || "—"}</p>
                </article>
              ))}
            </div>
          ) : (
            <p>{t("dash.no_opinions", { nombre: opinionesModal.nombre })}</p>
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
      window.open(apiUrl(`${path}&token=${encodeURIComponent(token)}`), "_blank", "noopener,noreferrer");
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
        <button className="link-button" onClick={onBack}>{t("common.back")}</button>
      </nav>
      <section className="hero dashboard-hero">
        <div>
          {advisee.foto
            ? <img src={advisee.foto} alt={advisee.nombre} className="objetivos-foto" />
            : <div className="objetivos-foto objetivos-foto-placeholder">{advisee.nombre.charAt(0)}</div>
          }
          <p className="kicker">{t("dash.final_report")}</p>
          <h1>{advisee.nombre}</h1>
        </div>
        {informeActual && (
          <div className="panel" style={{ marginBottom: "24px" }}>
            <h2>{t("subir.current_version")}</h2>
            <p className="fine">{t("subir.current_desc")}</p>
            <div className="actions">
              {informeActual.htmlUrl && <button onClick={() => openFile(informeActual.htmlUrl, "informe_final.html")}>{t("dash.open_web_version")}</button>}
              {informeActual.docxUrl && <button className="secondary" onClick={() => openFile(informeActual.docxUrl, "informe_final.docx")}>{t("admin.download_word")}</button>}
            </div>
          </div>
        )}
        <form className="panel" onSubmit={subir}>
          <h2>{t("subir.upload_final")}</h2>
          <p>{t("subir.upload_desc")}</p>
          <label>{t("subir.word_file")}</label>
          <input
            type="file"
            accept=".doc,.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
            required
          />
          {status && <p className={[t("subir.uploading"), t("subir.uploaded_ok")].includes(status) ? "fine" : "error"}>{status}</p>}
          <div className="actions">
            <button type="submit" disabled={uploading || !file}>
              {uploading ? t("subir.uploading_btn") : t("subir.upload_btn")}
            </button>
          </div>
        </form>
      </section>
      {links && (
        <section className="result panel">
          <h2>{t("subir.uploaded")}</h2>
          <div className="actions">
            {links.htmlUrl && <button onClick={() => openFile(links.htmlUrl, "informe_final.html")}>{t("dash.open_web_version")}</button>}
            {links.docxUrl && <button className="secondary" onClick={() => openFile(links.docxUrl, "informe_final.docx")}>{t("admin.download_word")}</button>}
          </div>
        </section>
      )}
      <Footer />
    </main>
  );
}

function AdviseesList({ token, advisees, onBack, onNavigate }) {
  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <button className="link-button" onClick={onBack}>{t("common.back")}</button>
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

function AdviseeDetail({ token, advisee, advisees, onBack, onNavigate }) {
  const [gestionOpen, setGestionOpen] = useState(false);
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
  const [generandoBorrador, setGenerandoBorrador] = useState(false);
  const [borradorError, setBorradorError] = useState("");
  const [opinionesDocOpen, setOpinionesDocOpen] = useState(false);
  const [generandoOpiniones, setGenerandoOpiniones] = useState(false);
  const [opinionesDocError, setOpinionesDocError] = useState("");
  const [realizarOpen, setRealizarOpen] = useState(false);
  const [manualOpen, setManualOpen] = useState(false);
  const [generandoFuente, setGenerandoFuente] = useState("");
  const [fuenteError, setFuenteError] = useState("");
  const [fuenteOk, setFuenteOk] = useState(false);
  const [tieneEvaluacionesExtra, setTieneEvaluacionesExtra] = useState(false);
  const [planAdv, setPlanAdv] = useState(null);       // plan de acción guardado (texto); null = cargando
  const [planAdvSesion, setPlanAdvSesion] = useState(false);
  const [planAdvBusy, setPlanAdvBusy] = useState(false);
  const [planAdvOk, setPlanAdvOk] = useState(false);

  // Plan de acción del año que viene: SOLO se lee lo ya guardado (cero API).
  useEffect(() => {
    apiRequest(`/api/eval-anual/plan-guardado?evaluado=${encodeURIComponent(advisee.nombre)}`, { token })
      .then((r) => { setPlanAdv(r.plan || ""); setPlanAdvSesion(!!r.tieneSesion); })
      .catch(() => { setPlanAdv(""); setPlanAdvSesion(false); });
  }, [token, advisee.nombre]);

  async function guardarPlanAdvisee() {
    setPlanAdvBusy(true); setPlanAdvOk(false);
    try {
      await apiRequest("/api/eval-anual/plan-guardar", { token, method: "POST", body: { evaluado: advisee.nombre, texto: planAdv } });
      setPlanAdvOk(true);
      setTimeout(() => setPlanAdvOk(false), 2600);
    } catch (e) {
      setFuenteError(e.message);
    } finally {
      setPlanAdvBusy(false);
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
      setFuenteError(err.message);
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

  // Genera el borrador de informe con Claude y lo descarga directamente (sin pantalla intermedia).
  async function descargarBorrador() {
    setGenerandoBorrador(true);
    setBorradorError("");
    try {
      const data = await apiRequest("/api/generar", {
        token,
        method: "POST",
        body: { evaluado: advisee.nombre },
      });
      const path = data.docxAnualUrl;
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
      link.download = `informe_${advisee.nombre.replace(/\s+/g, "_")}.docx`;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setBorradorError(err.message);
    } finally {
      setGenerandoBorrador(false);
    }
  }

  // Genera el documento de opiniones del CA en el backend (skill eval-resumen-opiniones-ca)
  // y abre la versión web (HTML) o descarga el Word (.docx) según el formato pedido.
  async function generarOpiniones(formato) {
    setGenerandoOpiniones(true);
    setOpinionesDocError("");
    try {
      const data = await apiRequest("/api/generar-opiniones-ca", {
        token,
        method: "POST",
        body: { evaluado: advisee.nombre },
      });
      const path = formato === "web" ? data.htmlUrl : data.pdfUrl;
      if (!path) throw new Error(t("ad.err_no_doc"));
      if (formato === "web") {
        window.open(apiUrl(`${path}&token=${encodeURIComponent(token)}`), "_blank", "noopener,noreferrer");
      } else {
        const response = await fetch(apiUrl(path), { headers: { Authorization: `Bearer ${token}` } });
        if (!response.ok) {
          const d = await response.json().catch(() => ({}));
          throw new Error(d.error || t("admin.err_download"));
        }
        const blob = await response.blob();
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.href = url;
        link.download = `opiniones_${advisee.nombre.replace(/\s+/g, "_")}.pdf`;
        link.click();
        URL.revokeObjectURL(url);
      }
    } catch (err) {
      setOpinionesDocError(err.message);
    } finally {
      setGenerandoOpiniones(false);
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
      <div className="advisee-detail-wrap">
        <div className="advisee-detail-layout">
          <div className="advisee-detail-left">
            {advisee.foto
              ? <img src={advisee.foto} alt={advisee.nombre} className="advisee-detail-foto" />
              : <div className="advisee-detail-foto advisee-foto-placeholder">{advisee.nombre.charAt(0)}</div>
            }
            <h2 className="advisee-detail-nombre">{advisee.nombre}</h2>
          </div>
          <div className="advisee-detail-right">
            <button className="secondary" onClick={() => onNavigate({ type: "objetivos", advisee, advisees, from: "advisee-detail" })}>
              {t("ad.edit_goals")}
            </button>
            <button className="secondary" onClick={() => setGestionOpen((v) => !v)}>
              {gestionOpen ? t("ad.close_manage") : t("ad.manage_report")}
            </button>
            {gestionOpen && (
              <div className="advisee-gestion">
                <button className="secondary" onClick={() => setRealizarOpen((v) => !v)}>
                  {realizarOpen ? t("ad.close_make_final") : t("ad.make_final")}
                </button>
                {realizarOpen && (
                  <div className="opiniones-doc-opciones">
                    <button className="secondary" onClick={() => onNavigate({ type: "eval-anual", advisee, advisees, from: "advisee-detail" })}>
                      {t("ad.with_claude")}
                    </button>
                    <button className="secondary" onClick={() => setManualOpen((v) => !v)}>
                      {manualOpen ? t("ad.close_manual") : t("ad.manual")}
                    </button>
                    {manualOpen && (
                      <div className="opiniones-doc-opciones">
                        <button className="secondary" disabled={!!generandoFuente}
                          onClick={() => descargarFuentePdf("/api/generar-opiniones-ca", "opiniones")}>
                          {generandoFuente === "/api/generar-opiniones-ca" ? t("ad.generating") : t("ad.dl_opinions")}
                        </button>
                        <button className="secondary" disabled={!!generandoFuente}
                          onClick={() => descargarFuentePdf("/api/generar-pdf-evals-proyecto", "evals_proyecto")}>
                          {generandoFuente === "/api/generar-pdf-evals-proyecto" ? t("ad.generating") : t("ad.dl_proj_evals")}
                        </button>
                        <button className="secondary" disabled={!!generandoFuente}
                          onClick={() => descargarFuentePdf("/api/generar-pdf-seguimiento", "seguimiento_personal")}>
                          {generandoFuente === "/api/generar-pdf-seguimiento" ? t("ad.generating") : t("ad.dl_personal_tracking")}
                        </button>
                        <button className="secondary" disabled={!!generandoFuente}
                          onClick={() => descargarFuentePdf("/api/generar-pdf-evals-mensuales", "evals_mensuales")}>
                          {generandoFuente === "/api/generar-pdf-evals-mensuales" ? t("ad.generating") : t("ad.dl_monthly_evals")}
                        </button>
                        {tieneEvaluacionesExtra && (
                          <button className="secondary" disabled={!!generandoFuente}
                            onClick={() => descargarFuentePdf("/api/generar-pdf-evals-extra", "evals_extra")}>
                            {generandoFuente === "/api/generar-pdf-evals-extra" ? t("ad.generating") : t("ad.dl_extra_evals")}
                          </button>
                        )}
                        {fuenteError && <p className="form-error">{fuenteError}</p>}
                      </div>
                    )}
                  </div>
                )}
                <button className="secondary" onClick={() => onNavigate({ type: "subir-informe", advisee, from: "advisee-detail", advisees })}>
                  {t("ad.upload_final")}
                </button>
                <button
                  className={accesoIndividual ? "" : "secondary"}
                  onClick={toggleAccesoIndividual}
                  disabled={togglingAccesoIndividual}
                >
                  {togglingAccesoIndividual
                    ? t("common.saving")
                    : accesoIndividual
                    ? t("ad.access_active_revoke")
                    : t("ad.give_access")}
                </button>
              </div>
            )}
            <button className="secondary" disabled={!!generandoFuente}
              onClick={() => descargarFuentePdf("/api/generar-pdf-completo", "info_completa")}>
              {generandoFuente === "/api/generar-pdf-completo" ? t("ad.generating") : t("ad.view_available_info")}
            </button>
            {fuenteError && <p className="form-error">{fuenteError}</p>}
            {fuenteOk && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "#166534", marginTop: 4 }}>
                <DrawCheck size={20} color="#166534" /> {t("ad.downloaded")}
              </span>
            )}
          </div>
        </div>

        <section className="notas-ca-section">
          <div className="notas-ca-header">
            <h3 className="notas-ca-titulo">{t("adplan.title")}</h3>
          </div>
          {planAdv === null ? (
            <p className="fine">{t("common.loading")}</p>
          ) : planAdv ? (
            <>
              <textarea
                className="notas-ca-textarea"
                rows={8}
                value={planAdv}
                onChange={(e) => setPlanAdv(e.target.value)}
                placeholder={t("adplan.none_yet")}
              />
              <div className="notas-ca-acciones">
                <button className="secondary" onClick={guardarPlanAdvisee} disabled={planAdvBusy}>
                  {planAdvBusy ? t("common.saving") : t("eaw.plan_save")}
                </button>
                <button
                  className="secondary"
                  onClick={() => onNavigate({ type: "eval-anual", advisee, advisees, from: "advisee-detail" })}
                >
                  {t("adplan.open_assistant")}
                </button>
                {planAdvOk && (
                  <span style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "#166534", alignSelf: "center" }}>
                    <DrawCheck size={20} color="#166534" /> {t("eaw.plan_saved")}
                  </span>
                )}
              </div>
            </>
          ) : (
            <>
              <p className="fine">{t("adplan.none_yet")}</p>
              <div className="notas-ca-acciones">
                <button
                  className="secondary"
                  onClick={() => onNavigate({ type: "eval-anual", advisee, advisees, from: "advisee-detail" })}
                >
                  {t("adplan.open_assistant")}
                </button>
              </div>
            </>
          )}
        </section>

        <section className="notas-ca-section">
          <div className="notas-ca-header">
            <h3 className="notas-ca-titulo">{t("ad.meetings_log")}</h3>
            {dictadoSoportado && (
              <button
                type="button"
                className={grabando ? "notas-ca-dictado grabando" : "notas-ca-dictado secondary"}
                onClick={toggleDictado}
              >
                {grabando ? t("ad.dictation_stop") : t("ad.dictation_start")}
              </button>
            )}
          </div>
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
        <button className="link-button" onClick={onBack}>{t("common.back")}</button>
      </nav>

      <div style={{ flex: 1, width: "100%", paddingTop: "clamp(44px, 6vw, 68px)", paddingBottom: 48 }}>
        <p className="eyebrow">{t("mpa.kicker")}</p>
        <h1 style={{ marginBottom: 28 }}>{t("mpa.title")}</h1>

        {loading ? (
          <p className="fine">{t("common.loading")}</p>
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
            // Se prefiere el dato del backend (m.n_completadas); si no llega, se calcula aquí
            // invirtiendo la relación evaluado→evaluadores del propio estado.
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
              <React.Fragment key={nombre}>
              {idx > 0 && <hr style={{ border: "none", borderTop: "1px solid var(--border)", margin: "24px 0" }} />}
              <div className="card stagger-item" style={{ marginBottom: 16, animationDelay: `${Math.min(idx, 8) * 0.05}s` }}>
                {/* Header */}
                <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: 16, flexWrap: "wrap", marginBottom: 16 }}>
                  <div>
                    <p style={{ fontSize: 14, fontWeight: 500, color: "#000", marginBottom: 2 }}>{nombre}</p>
                    <p style={{ fontSize: 12, fontWeight: 200, color: "rgba(0,0,0,.45)" }}>{t("mpa.progress", { done, total })}</p>
                  </div>
                  <ProgressBar pct={pct} barWidth={72} height={5} />
                </div>

                {/* Members table */}
                {!estado ? (
                  <p className="fine">{t("mpa.loading_state")}</p>
                ) : estado.length === 0 ? (
                  <p className="fine">{t("mpa.no_data")}</p>
                ) : (
                  <div style={{ overflowX: "auto" }}>
                    <table className="gest-table">
                      <thead>
                        <tr>
                          {[t("mpa.col_member"), t("mpa.col_completed"), t("mpa.col_selfeval"), t("mpa.col_status"), ""].map((h, hi) => (
                            <th key={hi}>{h}</th>
                          ))}
                        </tr>
                      </thead>
                      <tbody>
                        {estado.map((m) => {
                          const nHechas = hechasDe(m);
                          const peersCompleto = totalCompaneros === 0 || nHechas >= totalCompaneros;
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
                                <span className={`badge ${peersCompleto ? "badge-dark" : "badge-light"}`}>{nHechas}/{totalCompaneros}</span>
                              </td>
                              <td>
                                {m.autoevaluacion_hecha
                                  ? <span style={{ color: "#000", fontSize: 14 }}>✓</span>
                                  : <span style={{ color: "var(--accent)", fontSize: 14 }}>✗</span>}
                              </td>
                              <td>
                                <span className={`badge ${personaCompleto ? "badge-dark" : "badge-light"}`} title={estadoTitle}>
                                  {personaCompleto ? t("mpa.complete") : t("mpa.pending")}
                                </span>
                              </td>
                              <td>
                                <button
                                  onClick={() => modificarMiembro("eliminar", nombre, m.nombre)}
                                  title={t("mpa.remove_member", { nombre: m.nombre })}
                                  style={{ background: "none", border: "none", minHeight: "auto", padding: "2px 4px", color: "rgba(0,0,0,.3)", fontSize: 16, cursor: "pointer" }}
                                >×</button>
                              </td>
                            </tr>
                          );
                        })}
                      </tbody>
                    </table>
                  </div>
                )}

                {msg && <p className={msgEsError ? "error" : "fine"} style={{ marginTop: 10 }}>{msg}</p>}

                {/* Add member */}
                {mostrarAnadir ? (
                  <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center", flexWrap: "wrap" }}>
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
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => setAñadirMap((prev) => ({ ...prev, [nombre]: true }))}
                    style={{ marginTop: 12, height: 32, minHeight: "auto", padding: "0 14px", background: "transparent", color: "#000", border: "1px solid var(--border)", borderRadius: "var(--radius-pill)", fontSize: 12, fontWeight: 400 }}
                  >
                    {t("mpa.add_member")}
                  </button>
                )}
                <button
                  type="button"
                  onClick={() => enviarRecordatorio(nombre)}
                  disabled={enviandoRec[nombre]}
                  style={{ marginTop: 12, marginLeft: 8, height: 32, minHeight: "auto", padding: "0 14px", background: "transparent", color: "var(--accent)", border: "1px solid var(--accent)", borderRadius: "var(--radius-pill)", fontSize: 12, fontWeight: 400 }}
                >
                  {enviandoRec[nombre] ? t("mpa.rec_sending") : t("mpa.rec_button")}
                </button>
                {recMsg[nombre] && <p className="fine" style={{ marginTop: 8 }}>{recMsg[nombre]}</p>}
              </div>
              </React.Fragment>
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

function ActivarEvaluacionesProyectoPage({ token, user, onBack, onActivado }) {
  const [proyecto, setProyecto] = useState("");
  const [todosEmpleados, setTodosEmpleados] = useState([]);
  const [seleccionados, setSeleccionados] = useState([]);
  const [loading, setLoading] = useState(false);
  const [loadingEmpleados, setLoadingEmpleados] = useState(true);
  const [status, setStatus] = useState("");
  const [enviado, setEnviado] = useState(false);
  const [busqueda, setBusqueda] = useState("");

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
  }

  async function activar(e) {
    e.preventDefault();
    if (!proyecto.trim()) { setStatus(t("aep.err_type_project")); return; }
    if (seleccionados.length === 0) { setStatus(t("aep.err_select_employee")); return; }
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
  const canSubmit = proyecto.trim().length > 0 && seleccionados.length > 0 && !loading;
  const plural = seleccionados.length !== 1;
  // En esta pantalla el status solo se muestra en el formulario cuando es un error
  // o validacion (el exito se muestra en la vista "enviado"). Siempre error aqui.
  const statusEsError = true;

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <button className="link-button" onClick={onBack}>{t("common.back")}</button>
      </nav>

      <div style={{ flex: 1, width: "100%", paddingTop: "clamp(44px, 6vw, 68px)", paddingBottom: 48 }}>
        <p className="eyebrow">{t("mpa.kicker")}</p>
        <h1>{t("aep.title")}</h1>
        <p className="fine" style={{ marginTop: 10, color: "rgba(0,0,0,.6)" }}>
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
            <p className="fine" style={{ marginTop: -2, marginBottom: 8, color: "rgba(0,0,0,.45)", fontSize: 11 }}>
              {t("aep.format_hint")}
            </p>
            <input
              id="proj-name"
              type="text"
              value={proyecto}
              onChange={(e) => setProyecto(e.target.value)}
              placeholder="2026_Empresa_NombreProyecto"
              required
            />

            <label style={{ marginTop: 24 }}>{t("aep.team_members")}</label>
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
                    onChange={(e) => setBusqueda(e.target.value)}
                    placeholder={t("aep.search_by_name")}
                    style={{ paddingLeft: 32 }}
                  />
                </div>
                <div style={{ marginTop: 8, maxHeight: 220, overflowY: "auto", border: "1px solid var(--border)", borderRadius: "var(--radius-md)", background: "#fff" }}>
                  {filtrados.map((nombre) => {
                    const checked = seleccionados.includes(nombre);
                    return (
                      <div
                        key={nombre}
                        onClick={() => toggleEmpleado(nombre)}
                        style={{ display: "flex", alignItems: "center", gap: 10, padding: "9px 12px", borderBottom: "1px solid var(--border)", cursor: "pointer", userSelect: "none" }}
                      >
                        <span style={{ width: 14, height: 14, borderRadius: 4, border: `1px solid ${checked ? "#000" : "var(--border)"}`, background: checked ? "#000" : "#fff", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
                          {checked && (
                            <svg width="9" height="7" viewBox="0 0 9 7" fill="none"><path d="M1 3.5l2.5 2.5 4.5-5" stroke="#fff" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round"/></svg>
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

            {status && <p className={statusEsError ? "error" : "fine"} style={{ marginTop: 8 }}>{status}</p>}

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
              {loading
                ? t("aep.activating")
                : canSubmit
                  ? t(plural ? "aep.activate_n_many" : "aep.activate_n_one", { n: seleccionados.length })
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

const TIPOS_EVAL_INFO = [
  { tipo: "autoevaluacion", label: "Autoevaluación", desc: "Evalúa tu propio desempeño en el proyecto." },
  { tipo: "mismos_miembros", label: "Evaluación a tus miembros del equipo del mismo nivel", desc: "Evalúa a un compañero de equipo del mismo nivel." },
  { tipo: "miembros_a_manager", label: "Evaluación de miembros del equipo a managers", desc: "Evalúa al responsable del proyecto (NPS)." },
  { tipo: "manager_a_miembros", label: "Evaluación de managers a miembros del equipo", desc: "Evalúa el desempeño de un miembro de tu equipo." },
];

function construirEvaluacionesProyectoAHacer(persona, managerDelProyecto, equipo) {
  if (!equipo.length) return [];
  const personaNorm = persona.toLowerCase().trim();
  const managerNorm = managerDelProyecto.toLowerCase().trim();
  const esManager = personaNorm === managerNorm;
  const lista = [{ tipo: "autoevaluacion", evaluado: persona, label: "Autoevaluación" }];
  if (esManager) {
    equipo.filter((m) => m.toLowerCase().trim() !== managerNorm)
      .forEach((m) => lista.push({ tipo: "manager_a_miembros", evaluado: m, label: `Evaluación a miembro — ${m}` }));
  } else {
    lista.push({ tipo: "miembros_a_manager", evaluado: managerDelProyecto, label: `Evaluación al responsable — ${managerDelProyecto}` });
    equipo.filter((m) => m.toLowerCase().trim() !== personaNorm && m.toLowerCase().trim() !== managerNorm)
      .forEach((m) => lista.push({ tipo: "mismos_miembros", evaluado: m, label: `Evaluación a compañero — ${m}` }));
  }
  return lista;
}

function EvaluacionesProyectoPage({ token, user, proyectos, onBack, onNavigate, completedEvals = {}, initialProyecto }) {
  const [proyectoSeleccionado, setProyectoSeleccionado] = useState(initialProyecto || proyectos[0]?.nombre_proyecto || "");
  const [equipo, setEquipo] = useState([]);
  const [loadingEquipo, setLoadingEquipo] = useState(false);
  const [completadasNotion, setCompletadasNotion] = useState([]);
  const managerDelProyecto = proyectos.find((p) => p.nombre_proyecto === proyectoSeleccionado)?.activado_por || "";
  const persona = user?.persona || user?.username || "";

  useEffect(() => {
    if (!proyectoSeleccionado) return;
    setLoadingEquipo(true);
    setCompletadasNotion([]);
    Promise.all([
      apiRequest(`/api/equipo-proyecto?proyecto=${encodeURIComponent(proyectoSeleccionado)}`, { token }),
      apiRequest(`/api/evaluaciones-proyecto-completadas?proyecto=${encodeURIComponent(proyectoSeleccionado)}`, { token }),
    ])
      .then(([equipoData, completadasData]) => {
        setEquipo(equipoData.empleados || []);
        setCompletadasNotion((completadasData.completadas || []).map((c) => `${c.tipo}:${c.evaluado}`));
      })
      .catch(() => {})
      .finally(() => setLoadingEquipo(false));
  }, [token, proyectoSeleccionado]);

  const evaluacionesAHacer = useMemo(
    () => construirEvaluacionesProyectoAHacer(persona, managerDelProyecto, equipo),
    [equipo, persona, managerDelProyecto]
  );

  const items = evaluacionesAHacer.map(({ tipo, evaluado, label }) => {
    const evalKey = `${tipo}:${evaluado}`;
    const completado =
      (completedEvals[proyectoSeleccionado] || []).includes(evalKey) ||
      completadasNotion.includes(evalKey);
    return { tipo, evaluado, label, evalKey, completado };
  });
  const totalEvals = items.length;
  const doneEvals = items.filter((i) => i.completado).length;
  const pct = totalEvals ? Math.round((doneEvals / totalEvals) * 100) : 0;
  const shownPct = useCountUp(pct);
  const shownDone = useCountUp(doneEvals);
  const grupoAuto = items.filter((i) => i.tipo === "autoevaluacion");
  const grupoManager = items.filter((i) => i.tipo === "miembros_a_manager");
  const grupoMiembros = items.filter((i) => i.tipo === "mismos_miembros" || i.tipo === "manager_a_miembros");

  const abrirFormulario = (it) =>
    onNavigate({ type: "formulario-evaluacion-proyecto", proyecto: proyectoSeleccionado, tipo: it.tipo, evaluado: it.evaluado, manager: managerDelProyecto, proyectos });
  const abrirHistorial = (it) =>
    onNavigate({ type: "historial-evaluaciones", evaluado: it.evaluado, evaluador: persona, proyecto: proyectoSeleccionado, from: "evaluaciones-proyecto", proyectos });

  const renderRow = (it, showHistorial) => (
    <div key={it.evalKey} style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, padding: "10px 0", borderBottom: "1px solid var(--border)" }}>
      <div
        onClick={it.completado ? undefined : () => abrirFormulario(it)}
        style={{ cursor: it.completado ? "default" : "pointer", flex: 1, minWidth: 0 }}
        title={it.completado ? "" : t("ep.fill_eval")}
      >
        <p style={{ fontSize: 14, fontWeight: 400, color: "#000" }}>{it.evaluado}</p>
        <p style={{ fontSize: 12, fontWeight: 200, color: it.completado ? "rgba(0,0,0,.4)" : "var(--accent)" }}>
          {it.completado ? t("ep.completed") : t("ep.pending")}
        </p>
      </div>
      {showHistorial && (
        <button className="btn-historial" onClick={() => abrirHistorial(it)} type="button">{t("ep.history")}</button>
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
        <button className="link-button" onClick={onBack}>{t("common.back")}</button>
      </nav>

      <div style={{ flex: 1, width: "100%", paddingTop: "clamp(44px, 6vw, 68px)", paddingBottom: 48 }}>
        <p className="eyebrow">{t("ep.kicker")}</p>
        <h1 style={{ marginBottom: 24 }}>{proyectoSeleccionado || t("dash.nav_proj_evals")}</h1>

        {proyectos.length > 1 && (
          <div style={{ marginBottom: 28, maxWidth: 360 }}>
            <label htmlFor="proj-sel">{t("ep.project_label")}</label>
            <select id="proj-sel" value={proyectoSeleccionado} onChange={(e) => setProyectoSeleccionado(e.target.value)}>
              {proyectos.map((p) => (
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
                  <span style={{ fontSize: 13, fontWeight: 200, color: "rgba(0,0,0,.55)" }}>{t("ep.progress")}</span>
                  <span style={{ fontSize: 13, fontWeight: 400, color: "#000" }}>{t("ep.progress_stat", { done: shownDone, total: totalEvals, pct: shownPct })}</span>
                </div>
                <div style={{ height: 6, background: "var(--border)", borderRadius: 3, overflow: "hidden" }}>
                  <div style={{ height: "100%", width: `${shownPct}%`, background: "#000", borderRadius: 3, transition: "width .1s linear" }} />
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

function FormularioEvaluacionProyecto({ token, user, proyecto, tipo, manager, evaluadoProp, onBack, onEnviado }) {
  const persona = user?.persona || user?.username || "";
  const draftKey = `evaluabot_borrador:${persona}:${proyecto}:${tipo}`;

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
    setRespuestas((prev) => ({ ...prev, [id]: valor }));
  }

  function guardarProgreso() {
    try {
      localStorage.setItem(draftKey, JSON.stringify({ evaluado, respuestas, ts: Date.now() }));
      setStatus("");
      setBorradorMsg(t("fep.progress_saved"));
    } catch {
      setBorradorMsg(t("fep.err_save"));
    }
  }

  function descartarBorrador() {
    try { localStorage.removeItem(draftKey); } catch { /* noop */ }
    setRespuestas({});
    setEvaluado("");
    setBorradorRestaurado(false);
    setBorradorMsg("");
    setStatus("");
  }

  const evaluadoFinal = necesitaSelector ? evaluado : evaluadoFijo;

  async function enviar(e) {
    e.preventDefault();
    if (!evaluadoFinal) { setStatus(t("fep.err_select_person")); return; }
    if (preguntas && preguntas.some((p) => !String(respuestas[p.id] || "").trim())) {
      setStatus(t("fep.err_required"));
      return;
    }
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
        setEnviado(true);
        setStatus(t("fep.saved_notion"));
        try { localStorage.removeItem(draftKey); } catch { /* noop */ }
        if (onEnviado) onEnviado();
      } else {
        setStatus(data.error || t("fep.err_save"));
      }
    } catch (err) {
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
          <button className="link-button" onClick={onBack}>{t("common.back")}</button>
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
        <button className="link-button" onClick={onBack}>{t("common.back")}</button>
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
            <button onClick={() => { setEnviado(false); setRespuestas({}); setEvaluado(""); setStatus(""); }}>
              {t("fep.new_eval")}
            </button>
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
            <p className="fine" style={{ marginBottom: "16px" }}>
              {tipo === "autoevaluacion" ? t("fep.evaluating_self", { nombre: evaluadoFijo }) : t("fep.evaluating", { nombre: evaluadoFijo })}
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
                      <span style={{ fontSize: "11px", fontWeight: 400, textTransform: "uppercase", letterSpacing: "0.1em", color: "rgba(0,0,0,0.55)" }}>
                        {p.categoria}
                      </span>
                    </div>
                  )}
                  <div style={{ marginTop: "18px" }}>
                    {mostrarLabel && (
                      <label style={{ fontWeight: 400, fontSize: "14px", marginBottom: "12px", display: "block", color: "#000000" }}>
                        {p.texto} <span style={{ color: "#C1121F" }} aria-hidden="true">*</span>
                      </label>
                    )}
                    {p.tipo === "escala_1_5" && (
                      <div style={{ display: "flex", gap: "12px", flexWrap: "wrap", alignItems: "center" }}>
                        <span className="fine" style={{ fontSize: "12px" }}>{t("fep.scale_low")}</span>
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
                                  color: selected ? "#FFFFFF" : "rgba(0,0,0,0.55)",
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
                        <span className="fine" style={{ fontSize: "12px" }}>{t("fep.scale_high")}</span>
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
                                color: selected ? "#FFFFFF" : "rgba(0,0,0,0.55)",
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
              {t("fep.save_progress")}
            </button>
          </div>
        </form>
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
        <button className="link-button" onClick={onBack}>{t("common.back")}</button>
      </nav>

      <div style={{ flex: 1, width: "100%", paddingTop: "clamp(44px, 6vw, 68px)", paddingBottom: 48 }}>
        <p className="eyebrow">{t("sex.kicker")}</p>
        <h1>{t("sex.title")}</h1>
        <p className="fine" style={{ marginTop: 10, color: "rgba(0,0,0,.6)" }}>
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
                    onChange={(e) => setBusqueda(e.target.value)}
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
                        onClick={() => setEvaluador(nombre)}
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
            <p className="fine" style={{ marginTop: -2, marginBottom: 8, color: "rgba(0,0,0,.45)", fontSize: 11 }}>
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
        <button className="link-button" onClick={onBack}>{t("common.back")}</button>
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
            {[1, 2, 3, 4].map((val, idx) => (
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

function EvaluacionesSlackPage({ token, user, advisees, onBack, onNavigate, completadasApp = {}, onCompletada }) {
  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <button className="link-button" onClick={onBack}>{t("common.back")}</button>
      </nav>
      <div style={{ paddingTop: "clamp(44px, 6vw, 68px)" }}>
        <p className="kicker">{t("ess.page_kicker")}</p>
        <EvaluacionesSlackSection token={token} user={user} advisees={advisees || []} onNavigate={onNavigate} completadasApp={completadasApp} onCompletada={onCompletada} />
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

function EvaluacionAnualWizard({ token, advisee, onBack }) {
  const nombre = (advisee && advisee.nombre) || advisee || "";
  const [est, setEst] = useState(null);
  const [step, setStep] = useState("loading"); // loading|identidad|loop|resumen|hecho|error
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [secIdx, setSecIdx] = useState(0);
  const [area, setArea] = useState(null);
  const [input, setInput] = useState("");
  const [evidOpen, setEvidOpen] = useState(true);
  const [finUrls, setFinUrls] = useState(null);
  const [descInfo, setDescInfo] = useState(false);
  const [infoOk, setInfoOk] = useState(false);
  const [citaSel, setCitaSel] = useState(null);  // cid de la cita abierta en el chat
  const [resetting, setResetting] = useState(false);
  const [reloadNonce, setReloadNonce] = useState(0);
  const [plan, setPlan] = useState(null);        // plan de acción sugerido (texto)
  const [planInstr, setPlanInstr] = useState("");
  const [planBusy, setPlanBusy] = useState(false);
  const [planGuardado, setPlanGuardado] = useState(false);

  useEffect(() => {
    let alive = true;
    apiRequest("/api/eval-anual/iniciar", { token, method: "POST", body: { evaluado: nombre } })
      .then((data) => {
        if (!alive) return;
        setEst(data);
        if (!data.identidadConfirmada) setStep("identidad");
        else if (data.seccionesConfirmadas >= data.totalSecciones) setStep("resumen");
        else { const i = data.secciones.findIndex((s) => !s.confirmada); setSecIdx(i < 0 ? 0 : i); setStep("loop"); }
      })
      .catch((e) => { if (alive) { setError(e.message); setStep("error"); } });
    return () => { alive = false; };
  }, [token, nombre, reloadNonce]);

  useEffect(() => {
    if (step !== "loop" || !est) return;
    const sec = est.secciones[secIdx];
    if (!sec) return;
    setArea(null); setInput(""); setEvidOpen(true); setError(""); setCitaSel(null);
    apiRequest(`/api/eval-anual/area?evaluado=${encodeURIComponent(nombre)}&clave=${encodeURIComponent(sec.clave)}`, { token })
      .then(setArea)
      .catch((e) => setError(e.message));
  }, [step, secIdx, est, token, nombre]);

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
      setArea((a) => ({ ...a, conversacion: r.conversacion, propuesta: r.propuesta, diagnostico: r.diagnostico ?? a.diagnostico }));
      setInput(""); setEvidOpen(false);
    } catch (e) { setError(e.message); } finally { setBusy(false); }
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
      const r = await apiRequest("/api/eval-anual/finalizar", { token, method: "POST", body: { evaluado: nombre } });
      setFinUrls({ html: r.htmlUrl, docx: r.docxUrl });
      setStep("hecho");
    } catch (e) { setError(e.message); } finally { setBusy(false); }
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

  function abrirHtml(path) {
    window.open(apiUrl(`${path}&token=${encodeURIComponent(token)}`), "_blank", "noopener,noreferrer");
  }

  // Borra por completo la sesión (conversaciones, áreas confirmadas y borradores)
  // y vuelve a arrancar el asistente desde cero.
  async function eliminarYEmpezarDeCero() {
    if (!window.confirm(t("eaw.reset_confirm"))) return;
    setResetting(true); setError("");
    try {
      await apiRequest("/api/eval-anual/eliminar", { token, method: "POST", body: { evaluado: nombre } });
      setEst(null); setArea(null); setInput(""); setFinUrls(null);
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
        <button className="link-button" onClick={onBack}>{t("common.back")}</button>
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
              {descInfo ? t("eaw.generating") : t("eaw.full_info")}
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

  if (step === "loading") return shell(<p className="fine">{t("common.loading")}</p>);
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
    if (!area) return shell(<p className="fine">{t("eaw.loading_area")}</p>);
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
    return shell(
      <section className="panel">
        <p className="eyebrow">{t("eaw.area_n", { i: secIdx + 1, total: est.totalSecciones })}</p>
        <h2 style={{ marginTop: 0 }}>{area.etiqueta}</h2>

        <details open style={{ marginBottom: 16, background: "#f7f7f4", borderRadius: 8, padding: "10px 14px" }}>
          <summary style={{ cursor: "pointer", fontWeight: 700, fontSize: 14 }}>
            {t("eaw.criteria_panel")}{area.cargo ? ` · ${area.cargo}` : ""}
          </summary>
          {/* El diagnóstico (nivel + gaps) solo aparece tras enviar tu opinión inicial. */}
          {area.diagnostico && (
            <p style={{ margin: "10px 0 0", whiteSpace: "pre-line", fontSize: 14 }}>{renderCitas(area.diagnostico)}</p>
          )}
          {(area.criterios || []).map((c, i) => (
            <div key={i} style={{ marginTop: 10 }}>
              <p className="fine" style={{ margin: 0, fontWeight: 700 }}>{c.nivel}</p>
              <ul className="fine" style={{ margin: "2px 0 0", paddingLeft: 18 }}>
                {c.criterios.map((cr, k) => <li key={k}>{cr}</li>)}
              </ul>
            </div>
          ))}
          {(!area.criterios || area.criterios.length === 0) && !area.diagnostico && (
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

        <div className="actions" style={{ marginTop: 20 }}>
          <button onClick={finalizar} disabled={busy}>{busy ? t("eaw.generating") : t("eaw.gen_draft")}</button>
          <button className="secondary" onClick={() => { setSecIdx(0); setStep("loop"); }}>{t("eaw.review_areas")}</button>
        </div>
      </section>
    );
  }

  if (step === "hecho") {
    return shell(
      <section className="panel">
        <SavedOk text={t("eaw.draft_done")} color="#000" />
        <p className="fine" style={{ textAlign: "center" }}>{t("eaw.draft_desc")}</p>
        <div className="actions" style={{ marginTop: 16, justifyContent: "center" }}>
          {finUrls?.html && <button onClick={() => abrirHtml(finUrls.html)}>{t("eaw.view_draft")}</button>}
          <button className="secondary" onClick={onBack}>{t("auth.back_word")}</button>
        </div>
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

  if (page?.type === "advisees-list") {
    return <AdviseesList token={token} advisees={page.advisees} onBack={() => navigate(null)} onNavigate={navigate} />;
  }
  if (page?.type === "advisee-detail") {
    return (
      <AdviseeDetail
        token={token}
        advisee={page.advisee}
        advisees={page.advisees}
        onBack={() => navigate({ type: "advisees-list", advisees: page.advisees })}
        onNavigate={navigate}
      />
    );
  }
  if (page?.type === "mis-objetivos") {
    return <MisObjetivosPage token={token} persona={user?.persona || user?.username || ""} onBack={() => navigate(null)} />;
  }
  if (page?.type === "objetivos") {
    return <ObjetivosPage token={token} advisee={page.advisee} caName={user?.persona || ""} onBack={backTo(page)} />;
  }
  if (page?.type === "subir-informe") {
    return <SubirInformePage token={token} advisee={page.advisee} onBack={backTo(page)} />;
  }
  if (page?.type === "eval-anual") {
    return <EvaluacionAnualWizard token={token} advisee={page.advisee} onBack={backTo(page)} />;
  }
  if (page?.type === "activar-evaluaciones-proyecto") {
    return <ActivarEvaluacionesProyectoPage token={token} user={user} onBack={() => navigate(null)} onActivado={() => setProyectosVersion((v) => v + 1)} />;
  }
  if (page?.type === "mis-proyectos-activos") {
    return <MisProyectosActivosPage token={token} user={user} onBack={() => navigate(null)} />;
  }
  if (page?.type === "evaluaciones-proyecto") {
    return (
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
  }
  if (page?.type === "solicitar-evaluacion-extra") {
    return <SolicitarEvaluacionExtraPage token={token} user={user} onBack={() => navigate(null)} />;
  }
  if (page?.type === "formulario-evaluacion-extra") {
    return (
      <FormularioEvaluacionExtra
        token={token}
        evaluado={page.evaluado}
        contexto={page.contexto}
        solicitudPageId={page.solicitudPageId}
        onBack={() => navigate(null)}
      />
    );
  }
  if (page?.type === "evaluaciones-slack") {
    return (
      <EvaluacionesSlackPage
        token={token}
        user={user}
        advisees={[]}
        onBack={() => navigate(null)}
        onNavigate={navigate}
        completadasApp={slackEvalCompletadas}
        onCompletada={(key) => setSlackEvalCompletadas(prev => ({ ...prev, [key]: true }))}
      />
    );
  }
  if (page?.type === "historial-evaluaciones") {
    const backFromHistorial = page.from === "evaluaciones-proyecto"
      ? () => navigate({ type: "evaluaciones-proyecto", proyectos: page.proyectos || [], initialProyecto: page.proyecto })
      : () => navigate({ type: "evaluaciones-slack" });
    return (
      <HistorialEvaluacionesPage
        token={token}
        evaluado={page.evaluado}
        evaluador={page.evaluador}
        proyecto={page.proyecto}
        onBack={backFromHistorial}
      />
    );
  }
  if (page?.type === "formulario-evaluacion-proyecto") {
    return (
      <FormularioEvaluacionProyecto
        token={token}
        user={user}
        proyecto={page.proyecto}
        tipo={page.tipo}
        evaluadoProp={page.evaluado}
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
  }
  return (
    <Dashboard
      token={token}
      user={user}
      onLogout={handleLogout}
      onNavigate={navigate}
      onBackToRoleSelect={isAdmin && adminMode === "personal" ? () => navigate(null, null) : null}
    />
  );
}

createRoot(document.getElementById("root")).render(
  <>
    <TopLoadingBar />
    <LangToggle />
    <App />
  </>
);
