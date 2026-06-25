import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const API_BASE = import.meta.env.VITE_API_BASE_URL || `${window.location.protocol}//${window.location.hostname}:8000`;

function apiUrl(path) {
  return `${API_BASE}${path}`;
}

async function apiRequest(path, { token, method = "GET", body } = {}) {
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
    throw new Error(data.error || "No se pudo completar la accion.");
  }
  return data;
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
        aria-label={visible ? "Ocultar contrasena" : "Mostrar contrasena"}
        title={visible ? "Ocultar contrasena" : "Mostrar contrasena"}
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
        <a href="#">Privacidad</a>
        <a href="#">Términos</a>
      </nav>
    </footer>
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
            <button className="link-button logout-btn" onClick={onLogout}>Cerrar sesión</button>
          </div>
        </div>
      </nav>
      <div className="role-select-body">
        <p className="kicker">Bienvenida</p>
        <h2>¿Cómo quieres entrar hoy?</h2>
        <div className="role-select-grid">
          <button className="role-card" onClick={() => onChoose("admin")}>
            <span className="role-card-title">Administrador</span>
            <span className="role-card-desc">Consulta evaluaciones e informes de cualquier empleado</span>
          </button>
          <button className="role-card secondary" onClick={() => onChoose("personal")}>
            <span className="role-card-title">Perfil personal</span>
            <span className="role-card-desc">Accede como cualquier otro empleado de la empresa</span>
          </button>
        </div>
      </div>
      <Footer />
    </main>
  );
}

function AdminPanel({ token, onBack }) {
  const [evaluados, setEvaluados] = useState([]);
  const [search, setSearch] = useState("");
  const [selected, setSelected] = useState(null);
  const [informeFinal, setInformeFinal] = useState(null);
  const [statusMsg, setStatusMsg] = useState("");

  useEffect(() => {
    apiRequest("/api/evaluados", { token })
      .then((data) => setEvaluados(data.evaluados || []))
      .catch(() => {});
  }, [token]);

  useEffect(() => {
    if (!selected) return;
    setInformeFinal(null);
    apiRequest(`/api/informe-final?evaluado=${encodeURIComponent(selected.nombre)}`, { token })
      .then((data) => setInformeFinal(data))
      .catch(() => setInformeFinal({ disponible: false, mensaje: "No se pudo cargar el informe." }));
  }, [token, selected?.nombre]);

  async function selectEmpleado(item) {
    setStatusMsg("");
    try {
      const perfil = await apiRequest(`/api/perfil-empleado?nombre=${encodeURIComponent(item.value)}`, { token });
      setSelected({ nombre: item.value, foto: perfil.foto || null, cargo: perfil.cargo || "" });
    } catch {
      setSelected({ nombre: item.value, foto: null, cargo: "" });
    }
  }

  async function generarWrapped() {
    if (!selected) return;
    setStatusMsg("Preparando trayectoria visual...");
    try {
      const data = await apiRequest("/api/trayectoria", { token, method: "POST", body: { evaluado: selected.nombre } });
      setStatusMsg("");
      window.open(apiUrl(`${data.htmlUrl}&token=${encodeURIComponent(token)}`), "_blank", "noopener,noreferrer");
    } catch (err) {
      setStatusMsg(err.message);
    }
  }

  async function openFile(path, filename) {
    if (!filename.endsWith(".docx")) {
      window.open(apiUrl(`${path}&token=${encodeURIComponent(token)}`), "_blank", "noopener,noreferrer");
      return;
    }
    try {
      const response = await fetch(apiUrl(path), { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) throw new Error("No se pudo descargar el archivo.");
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

  const filtrados = evaluados.filter((e) =>
    e.label.toLowerCase().includes(search.toLowerCase())
  );

  if (selected) {
    return (
      <main className="page">
        <nav className="nav">
          <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
          <button className="link-button" onClick={() => { setSelected(null); setInformeFinal(null); setStatusMsg(""); }}>← Volver</button>
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
              <p className="kicker">Informes</p>
              {informeFinal === null ? (
                <p className="fine">Cargando...</p>
              ) : informeFinal?.disponible ? (
                <>
                  {informeFinal.htmlUrl && (
                    <button onClick={() => openFile(informeFinal.htmlUrl, "informe_final.html")}>
                      Ver informe final
                    </button>
                  )}
                  {informeFinal.docxUrl && (
                    <button className="secondary" onClick={() => openFile(informeFinal.docxUrl, "informe_final.docx")}>
                      Descargar Word
                    </button>
                  )}
                </>
              ) : (
                <p className="fine">{informeFinal?.mensaje || "Sin informe final disponible."}</p>
              )}
              <button
                className="secondary"
                onClick={generarWrapped}
                disabled={statusMsg === "Preparando trayectoria visual..."}
              >
                {statusMsg === "Preparando trayectoria visual..." ? "Generando..." : "Su wrapped"}
              </button>
              {statusMsg && statusMsg !== "Preparando trayectoria visual..." && (
                <p className="fine error">{statusMsg}</p>
              )}
            </div>
          </div>
        </div>
        <Footer />
      </main>
    );
  }

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <button className="link-button" onClick={onBack}>← Volver</button>
      </nav>
      <div className="admin-search-wrap">
        <p className="kicker">Administrador</p>
        <h2>Buscar empleado</h2>
        <div className="admin-search-field">
          <input
            type="text"
            placeholder="Escribe un nombre..."
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
        <div className="advisees-page-grid">
          {filtrados.map((e) => (
            <button
              key={e.value}
              className="advisee-page-card"
              onClick={() => selectEmpleado(e)}
            >
              <div className="advisee-page-foto advisee-foto-placeholder">{e.label.charAt(0)}</div>
              <span className="advisee-page-nombre">{e.label}</span>
            </button>
          ))}
          {filtrados.length === 0 && search && (
            <p className="fine" style={{ textAlign: "center", width: "100%" }}>No hay resultados para &ldquo;{search}&rdquo;.</p>
          )}
        </div>
      </div>
      <Footer />
    </main>
  );
}

function InformesAdvisee({ token, advisee, onBack }) {
  const [status, setStatus] = useState("");
  const [links, setLinks] = useState(null);

  async function generate(kind) {
    setLinks(null);
    setStatus(kind === "generar" ? "Claude esta generando el informe..." : "Preparando trayectoria visual...");
    try {
      const data = await apiRequest(`/api/${kind}`, { token, method: "POST", body: { evaluado: advisee.nombre } });
      setStatus(kind === "generar" ? `Informe listo con ${data.total} evaluaciones.` : `Trayectoria lista con ${data.total} evaluaciones.`);
      setLinks(data);
    } catch (err) {
      setStatus(err.message);
    }
  }

  async function openFile(path, filename) {
    if (!filename.endsWith(".docx")) {
      window.open(apiUrl(`${path}&token=${encodeURIComponent(token)}`), "_blank", "noopener,noreferrer");
      return;
    }
    try {
      const response = await fetch(apiUrl(path), { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || "No se pudo descargar el archivo.");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
    } catch (err) {
      setStatus(err.message);
    }
  }

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <button className="link-button" onClick={onBack}>← Volver</button>
      </nav>
      <section className="hero dashboard-hero">
        <div>
          {advisee.foto
            ? <img src={advisee.foto} alt={advisee.nombre} className="objetivos-foto" />
            : <div className="objetivos-foto objetivos-foto-placeholder">{advisee.nombre.charAt(0)}</div>
          }
          <p className="kicker">Informes</p>
          <h1>{advisee.nombre}</h1>
        </div>
        <div className="panel">
          <p className="lead">Genera el informe ejecutivo o la trayectoria visual a partir del feedback recogido.</p>
          <div className="actions">
            <button onClick={() => generate("generar")}>Generar informe</button>
            <button className="secondary" onClick={() => generate("trayectoria")}>Generar trayectoria</button>
          </div>
        </div>
      </section>

      {status && <section className="status panel"><p>{status}</p></section>}
      {links && (
        <section className="result panel">
          <h2>Resultado</h2>
          <div className="actions">
            {links.htmlUrl && <button onClick={() => openFile(links.htmlUrl, "informe.html")}>Abrir web</button>}
            {links.docxAnualUrl && <button className="secondary" onClick={() => openFile(links.docxAnualUrl, `informe_${advisee.nombre.replace(/\s+/g, "_")}.docx`)}>Descargar Word</button>}
          </div>
        </section>
      )}
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
        <button className="link-button" onClick={onBack}>← Volver</button>
      </nav>
      <section className="hero dashboard-hero">
        <div>
          <p className="kicker">Desarrollo personal</p>
          <h1>Mis objetivos.</h1>
        </div>
      </section>
      <section className="objetivos-historial panel">
        {error && <p className="error">{error}</p>}
        {loading ? (
          <p>Cargando...</p>
        ) : objetivos.length ? (
          <div className="objetivos-list">
            {objetivos.map((obj, i) => (
              <article key={i} className="objetivo-item">
                <p className="opinion-fecha fine">
                  {obj.fecha ? obj.fecha.slice(0, 10) : "Sin fecha"}
                  {obj.ca ? ` — ${obj.ca}` : ""}
                  {obj.tipo ? ` · ${obj.tipo}` : ""}
                </p>
                <p className="objetivo-titulo"><strong>{obj.titulo}</strong></p>
                {obj.kpis && <p className="objetivo-texto fine"><em>KPIs:</em> {obj.kpis}</p>}
                {obj.descripcion && <p className="objetivo-texto">{obj.descripcion}</p>}
              </article>
            ))}
          </div>
        ) : (
          <p>Todavia no tienes objetivos registrados.</p>
        )}
      </section>
      <Footer />
    </main>
  );
}

function ObjetivosPage({ token, advisee, caName, onBack }) {
  const [objetivos, setObjetivos] = useState([]);
  const [form, setForm] = useState({ titulo: "", kpis: "", descripcion: "", tipo: "" });
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

  async function guardar(e) {
    e.preventDefault();
    if (!form.titulo.trim()) return;
    setError("");
    setSuccess("");
    setSaving(true);
    try {
      await apiRequest("/api/objetivos", {
        token,
        method: "POST",
        body: { nombre: advisee.nombre, titulo: form.titulo.trim(), kpis: form.kpis.trim(), descripcion: form.descripcion.trim(), tipo: form.tipo.trim() },
      });
      await recargar();
      setForm({ titulo: "", kpis: "", descripcion: "", tipo: "" });
      setSuccess("Objetivo guardado correctamente.");
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function eliminar(page_id) {
    if (!window.confirm("¿Eliminar este objetivo?")) return;
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
        <button className="link-button" onClick={onBack}>← Volver</button>
      </nav>
      <section className="hero dashboard-hero">
        <div>
          {advisee.foto
            ? <img src={advisee.foto} alt={advisee.nombre} className="objetivos-foto" />
            : <div className="objetivos-foto objetivos-foto-placeholder">{advisee.nombre.charAt(0)}</div>
          }
          <p className="kicker">Objetivos</p>
          <h1>{advisee.nombre}</h1>
        </div>
        <form className="panel" onSubmit={guardar}>
          <h2>Nuevo objetivo</h2>
          {error && <p className="error">{error}</p>}
          {success && <p className="fine">{success}</p>}
          <label>Título *</label>
          <input
            type="text"
            value={form.titulo}
            onChange={(e) => setForm((f) => ({ ...f, titulo: e.target.value }))}
            placeholder="Ej: Mejorar habilidades de presentación"
            required
          />
          <label style={{ marginTop: "12px" }}>Tipo</label>
          <input
            type="text"
            value={form.tipo}
            onChange={(e) => setForm((f) => ({ ...f, tipo: e.target.value }))}
            placeholder="Ej: Desarrollo personal, Técnico, Liderazgo..."
          />
          <label style={{ marginTop: "12px" }}>KPIs para su cumplimiento</label>
          <input
            type="text"
            value={form.kpis}
            onChange={(e) => setForm((f) => ({ ...f, kpis: e.target.value }))}
            placeholder="Ej: Presentar en 2 reuniones de cliente al trimestre"
          />
          <label style={{ marginTop: "12px" }}>Descripción</label>
          <textarea
            className="objetivos-textarea"
            value={form.descripcion}
            onChange={(e) => setForm((f) => ({ ...f, descripcion: e.target.value }))}
            rows={5}
            placeholder="Detalla cómo trabajar este objetivo..."
          />
          <div className="actions">
            <button type="submit" disabled={saving || !form.titulo.trim()}>
              {saving ? "Guardando..." : "Guardar objetivo"}
            </button>
          </div>
        </form>
      </section>

      <section className="objetivos-historial panel">
        <p className="kicker">Historial</p>
        <h2>Objetivos de {advisee.nombre}</h2>
        {loading ? (
          <p>Cargando...</p>
        ) : objetivos.length ? (
          <div className="objetivos-list">
            {objetivos.map((obj) => (
              <article key={obj.page_id} className="objetivo-item">
                <p className="opinion-fecha fine">
                  {obj.fecha ? obj.fecha.slice(0, 10) : "Sin fecha"}
                  {obj.tipo ? ` · ${obj.tipo}` : ""}
                </p>
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
                    {deleting === obj.page_id ? "Eliminando..." : "Eliminar"}
                  </button>
                </div>
              </article>
            ))}
          </div>
        ) : (
          <p>No hay objetivos para {advisee.nombre}.</p>
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
      setError("La contrasena debe tener minimo 8 caracteres, una mayuscula y un caracter especial.");
      return;
    }
    if ((mode === "reset" && form.newPassword !== form.confirmNewPassword) || (mode === "register" && form.password !== form.confirmPassword)) {
      setError("Las contrasenas no coinciden.");
      return;
    }
    setLoading(true);
    try {
      if (mode === "register") {
        await apiRequest("/api/register", { method: "POST", body: form });
        setMode("login");
      } else if (mode === "forgot") {
        await apiRequest("/api/password-reset/request", { method: "POST", body: { email: form.email } });
        setMessage("Si el email existe, te hemos enviado un enlace para cambiar la contrasena.");
      } else if (mode === "reset") {
        await apiRequest("/api/password-reset/confirm", { method: "POST", body: { token: resetToken, password: form.newPassword, confirmPassword: form.confirmNewPassword } });
        localStorage.removeItem("evaluabot_token");
        window.history.replaceState({}, "", window.location.pathname);
        setMode("login");
        setMessage("Contrasena actualizada. Ya puedes entrar.");
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

  return (
    <main className="page auth-page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
      </nav>
      <div className="auth-body">
        <p className="kicker">Evaluaciones internas</p>
        <h2 className="auth-heading">
          {mode === "verify-code" ? "Verificación requerida" : mode === "forgot" ? "Recupera tu acceso" : mode === "reset" ? "Nueva contraseña" : mode === "login" ? "Iniciar sesión" : "Crear cuenta"}
        </h2>
        {error && <p className="error">{error}</p>}
        {message && <p className="fine">{message}</p>}
        <form onSubmit={submit}>
          {mode === "verify-code" ? (
            <>
              <p className="fine">Por seguridad, hemos enviado un código de 6 dígitos a <strong>{maskedEmail}</strong>. Introdúcelo a continuación. Caduca en 10 minutos.</p>
              <label>Código de verificación</label>
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
              <label>Nueva contraseña</label>
              <PasswordInput value={form.newPassword} onChange={(e) => setForm({ ...form, newPassword: e.target.value })} minLength={8} />
              <label>Repite la contraseña</label>
              <PasswordInput value={form.confirmNewPassword} onChange={(e) => setForm({ ...form, confirmNewPassword: e.target.value })} minLength={8} />
            </>
          ) : (
            <>
              <label>{mode === "login" ? "Usuario o email" : "Usuario"}</label>
              <input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required />
              <label>Contraseña</label>
              <PasswordInput value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} minLength={mode === "register" ? 8 : undefined} />
              {mode === "register" && (
                <>
                  <label>Repite la contraseña</label>
                  <PasswordInput value={form.confirmPassword} onChange={(e) => setForm({ ...form, confirmPassword: e.target.value })} minLength={8} />
                </>
              )}
            </>
          )}
          {mode === "login" && (
            <label className="check-label">
              <input type="checkbox" className="check-input" checked={rememberMe} onChange={(e) => setRememberMe(e.target.checked)} />
              Recuérdame
            </label>
          )}
          {(mode === "register" || mode === "reset") && (
            <p className={(passwordInvalid || passwordsMismatch) ? "error fine" : "fine"}>
              Mínimo 8 caracteres, una mayúscula y un carácter especial. Las contraseñas deben coincidir.
            </p>
          )}
          <div className="actions">
            <button type="submit" disabled={!canSubmit}>
              {loading ? "Procesando..." : mode === "verify-code" ? "Verificar" : mode === "forgot" ? "Enviar enlace" : mode === "reset" ? "Guardar contraseña" : mode === "login" ? "Iniciar sesión" : "Crear cuenta"}
            </button>
            {mode === "login" && (
              <button type="button" className="secondary" onClick={() => { setError(""); setMessage(""); setMode("forgot"); }}>
                Olvidé mi contraseña
              </button>
            )}
            {(mode === "forgot" || mode === "reset" || mode === "verify-code") && (
              <button type="button" className="secondary" onClick={() => { window.history.replaceState({}, "", window.location.pathname); setError(""); setMessage(""); setForm((f) => ({ ...f, verifyCode: "" })); setMode("login"); }}>
                Volver
              </button>
            )}
          </div>
        </form>
        {mode === "login" && (
          <p className="auth-legal">
            Al acceder aceptas nuestra <a href="#">política de privacidad</a> y los <a href="#">términos y condiciones</a> de uso de la plataforma.
          </p>
        )}
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
  const [msgs, setMsgs] = React.useState([{
    role: "bot",
    text: "📍 *Tienes una evaluación mensual pendiente.*\n\n_Esta evaluación es totalmente privada, solo podrá verla el CA de la persona evaluada._\n_Si en algún momento quieres cancelar, pulsa Cancelar._\n\n*Pulsa el botón* para comenzar la evaluación.",
  }]);
  const [step, setStep] = React.useState("intro");
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
    const lines = ["*Resumen de tus respuestas:*"];
    lines.push(`- *Persona evaluada*: ${resp.evaluado || ""}`);
    if (resp.proyecto) lines.push(`- *Proyecto*: ${resp.proyecto}`);
    for (const q of preg) {
      const label = q.texto.split("\n")[0].replace(/\*/g, "").slice(0, 55);
      lines.push(`- *${label}*: ${resp[q.clave] || ""}`);
    }
    lines.push("\n¿Estás satisfecho con tus respuestas?\nPulsa *✅ Sí, guardar* o *✏️ Modificar*.");
    return lines.join("\n");
  }

  function handleComenzar() {
    userSay("Comenzar");
    botSay("¿A qué área perteneces?\n*1.* Negocio\n*2.* MiddleOffice\n*3.* Palantir");
    setStep("pedir_area");
  }

  async function handleArea(areaVal) {
    const LABELS = { negocio: "Negocio", middleoffice: "MiddleOffice", palantir: "Palantir" };
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
        botSay(lista.length ? `¿A quién quieres evaluar?\n${lista.map(e => `- ${e}`).join("\n")}` : "¿A quién quieres evaluar? Dime el nombre de la persona.");
        setSugerencias(lista);
      } catch { botSay("¿A quién quieres evaluar? Dime el nombre de la persona."); }
      finally { setLoading(false); }
      setStep("pedir_persona");
    } else {
      botSay("Escribe el nombre de uno de los proyectos en los que estás trabajando. Más adelante podrás evaluar el resto");
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
    botSay(`Perfecto 😊, vamos con el proyecto *${val}*. Dime el nombre de uno de los miembros de tu equipo, podrás evaluar al resto después.`);
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
          botSay(`Ya has evaluado a *${d.empleado}* en *${respuestas.proyecto || "?"}* en esta sesión. Dime el nombre de otro miembro.`);
          return;
        }
        setEvaluadoNombre(d.empleado);
        setRelacion(d.relacion || "igual");
        const finalPregs = d.preguntas?.length ? d.preguntas : preguntas;
        setPreguntas(finalPregs);
        setPreguntaIdx(0);
        setRespuestas(r => ({ ...r, evaluado: d.empleado }));
        if (finalPregs.length) { botSay(finalPregs[0].texto); setStep("preguntas"); }
        else botSay("⚠️ No hay preguntas configuradas.");
      } else if (d.sugerencias?.length) {
        setSugerencias(d.sugerencias);
        botSay(`*${nombre}* no aparece en la lista de empleados.\n¿Querías decir alguno de estos?\n${d.sugerencias.map((s, i) => `${i + 1}. ${s}`).join("\n")}`);
      } else {
        botSay(`*${nombre}* no aparece en la lista de empleados. Escribe nombre y apellido como aparece en la lista.`);
      }
    } catch { botSay("⚠️ Error temporal consultando datos. Vuelve a intentarlo."); }
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
    userSay("✅ Sí, guardar");
    setLoading(true);
    try {
      const respsClave = Object.fromEntries(Object.entries(respuestas).filter(([k, v]) => k !== "evaluado" && k !== "proyecto" && v));
      await apiRequest("/api/guardar-evaluacion-slack", {
        token, method: "POST",
        body: { evaluado: respuestas.evaluado, proyecto: respuestas.proyecto || "", area: area || "negocio", respuestas: respsClave },
      });
      const clave = `${(respuestas.proyecto || "").toLowerCase()}|${(respuestas.evaluado || "").toLowerCase()}`;
      setEvaluadosEnSesion(prev => [...prev, clave]);
      botSay("✅ *Evaluación guardada en Notion*.\n\n¿Hay más miembros en el equipo que quieras evaluar?");
      setStep("mas_personas");
      onComplete?.();
    } catch { botSay("⚠️ No se pudo guardar en Notion. Revisa permisos/logs."); }
    finally { setLoading(false); }
  }

  function handleModificar() {
    userSay("✏️ Modificar");
    const items = ["1. Persona evaluada"];
    if (respuestas.proyecto) items.push("2. Proyecto");
    const base = respuestas.proyecto ? 3 : 2;
    preguntas.forEach((q, i) => items.push(`${base + i}. ${q.texto.split("\n")[0].replace(/\*/g, "").slice(0, 55)}`));
    botSay(`¿Qué respuesta quieres modificar?\n${items.join("\n")}\n\nResponde con el número.`);
    setStep("modificar_menu");
  }

  function handleModificarMenu() {
    const num = parseInt(inputVal.trim());
    if (isNaN(num)) { botSay("Por favor, responde con un número 🔢"); return; }
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
    if (!campo) { botSay(`Por favor, responde con un número del 1 al ${2 + (respuestas.proyecto ? 1 : 0) + preguntas.length - (respuestas.proyecto ? 0 : 1)} 🔢`); return; }
    setModificandoCampo(campo);
    if (campo === "evaluado") botSay("Indica el nombre de la persona a evaluar.");
    else if (campo === "proyecto") botSay("Escribe el nuevo nombre del proyecto.");
    else botSay(preguntas.find(q => q.clave === campo)?.texto || "Escribe la nueva respuesta.");
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
          botSay(`*${v}* no aparece en la lista.\n¿Querías decir alguno de estos?\n${d.sugerencias.map((s, i) => `${i + 1}. ${s}`).join("\n")}`);
        } else {
          botSay(`*${v}* no aparece en la lista. Escribe nombre y apellido.`);
        }
      } catch { botSay("⚠️ Error temporal. Vuelve a intentarlo."); }
      finally { setLoading(false); }
    } else {
      const esVal = campo === "q1" || campo === "mo_contribucion";
      if (esVal && !["1","2","3","4","5"].includes(v)) { botSay("Por favor, responde con un número del 1 al 5 🔢"); return; }
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
    userSay(si ? "✅ Sí" : "❌ No");
    if (si) {
      setEvaluadoNombre("");
      setRespuestas(r => ({ proyecto: r.proyecto }));
      setPreguntaIdx(0);
      setSugerencias([]);
      if (area === "middleoffice") {
        botSay(moEvaluables.length ? `¿A quién quieres evaluar?\n${moEvaluables.map(e => `- ${e}`).join("\n")}` : "¿A quién quieres evaluar? Dime el nombre.");
        setSugerencias(moEvaluables);
      } else {
        botSay(`Perfecto. ¿Qué otro miembro${proyecto ? ` del proyecto *${proyecto}*` : ""} quieres evaluar?`);
      }
      setStep("pedir_persona");
    } else if (area === "middleoffice") {
      botSay("Perfecto, muchas gracias por tu tiempo ❤️. Ya puedes cerrar esta sección 👋");
      setStep("terminado");
    } else {
      botSay("¿Estás trabajando en algún otro proyecto?");
      setStep("mas_proyectos");
    }
  }

  function handleMasProyectos(si) {
    userSay(si ? "✅ Sí" : "❌ No");
    if (si) {
      setProyecto(""); setEvaluadoNombre(""); setRespuestas({}); setPreguntaIdx(0); setSugerencias([]);
      botSay("Escribe el nombre de uno de los proyectos en los que estás trabajando. Más adelante podrás evaluar el resto");
      setStep("pedir_proyecto");
    } else {
      botSay("Perfecto, muchas gracias por tu tiempo ❤️. Ya puedes cerrar esta sección 👋");
      setStep("terminado");
    }
  }

  const pregActual = preguntas[preguntaIdx];
  const esValoracion = pregActual?.clave === "q1" || pregActual?.clave === "mo_contribucion";
  const esModValoracion = modificandoCampo === "q1" || modificandoCampo === "mo_contribucion";

  function renderInput() {
    if (loading) return <div className="chat-input-area"><div className="chat-input-row"><span className="fine" style={{ color: "var(--muted)" }}>...</span></div></div>;
    if (step === "intro") return (
      <div className="chat-input-area"><div className="chat-btns"><button className="chat-btn primary" onClick={handleComenzar}>Comenzar</button></div></div>
    );
    if (step === "pedir_area") return (
      <div className="chat-input-area"><div className="chat-btns">
        <button className="chat-btn" onClick={() => handleArea("negocio")}>Negocio</button>
        <button className="chat-btn" onClick={() => handleArea("middleoffice")}>MiddleOffice</button>
        <button className="chat-btn" onClick={() => handleArea("palantir")}>Palantir</button>
      </div></div>
    );
    if (step === "pedir_proyecto") return (
      <div className="chat-input-area"><div className="chat-input-row">
        <input className="chat-input" placeholder="Nombre del proyecto..." value={inputVal} onChange={e => setInputVal(e.target.value)} onKeyDown={e => e.key === "Enter" && handleProyecto()} autoFocus />
        <button className="chat-send-btn" onClick={handleProyecto}>→</button>
      </div></div>
    );
    if (step === "pedir_persona") return (
      <div className="chat-input-area">
        {sugerencias.length > 0 && <div className="chat-sugerencias">{sugerencias.map(s => <button key={s} className="chat-btn" onClick={() => { setSugerencias([]); handlePersonaSubmit(s); }}>{s}</button>)}</div>}
        <div className="chat-input-row">
          <input className="chat-input" placeholder="Nombre del compañero..." value={inputVal} onChange={e => { setInputVal(e.target.value); buscarSugerencias(e.target.value); }} onKeyDown={e => e.key === "Enter" && handlePersonaSubmit(inputVal)} autoFocus />
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
          <textarea className="chat-input chat-textarea" placeholder="Escribe tu respuesta..." value={inputVal} onChange={e => setInputVal(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); handleRespuestaPregunta(); } }} rows={2} autoFocus />
          <button className="chat-send-btn" onClick={handleRespuestaPregunta}>→</button>
        </div></div>
      );
    }
    if (step === "confirmacion") return (
      <div className="chat-input-area"><div className="chat-btns">
        <button className="chat-btn primary" onClick={handleConfirmar}>✅ Sí, guardar</button>
        <button className="chat-btn" onClick={handleModificar}>✏️ Modificar</button>
      </div></div>
    );
    if (step === "modificar_menu") return (
      <div className="chat-input-area"><div className="chat-input-row">
        <input className="chat-input" placeholder="Número del campo..." value={inputVal} onChange={e => setInputVal(e.target.value)} onKeyDown={e => e.key === "Enter" && handleModificarMenu()} autoFocus />
        <button className="chat-send-btn" onClick={handleModificarMenu}>→</button>
      </div></div>
    );
    if (step === "modificar_valor") {
      if (sugerencias.length > 0) return (
        <div className="chat-input-area">
          <div className="chat-sugerencias">{sugerencias.map(s => <button key={s} className="chat-btn" onClick={() => { setSugerencias([]); handleModificarValor(s); }}>{s}</button>)}</div>
          <div className="chat-input-row">
            <input className="chat-input" placeholder="O escribe el nombre..." value={inputVal} onChange={e => setInputVal(e.target.value)} onKeyDown={e => e.key === "Enter" && handleModificarValor(inputVal)} autoFocus />
            <button className="chat-send-btn" onClick={() => handleModificarValor(inputVal)}>→</button>
          </div>
        </div>
      );
      if (esModValoracion) return (
        <div className="chat-input-area"><div className="chat-btns">{[1,2,3,4,5].map(n => <button key={n} className="chat-btn" onClick={() => handleModificarValor(String(n))}>{n}</button>)}</div></div>
      );
      return (
        <div className="chat-input-area"><div className="chat-input-row">
          <input className="chat-input" placeholder="Nueva respuesta..." value={inputVal} onChange={e => setInputVal(e.target.value)} onKeyDown={e => e.key === "Enter" && handleModificarValor(inputVal)} autoFocus />
          <button className="chat-send-btn" onClick={() => handleModificarValor(inputVal)}>→</button>
        </div></div>
      );
    }
    if (step === "mas_personas") return (
      <div className="chat-input-area"><div className="chat-btns">
        <button className="chat-btn primary" onClick={() => handleMasPersonas(true)}>✅ Sí</button>
        <button className="chat-btn" onClick={() => handleMasPersonas(false)}>❌ No</button>
      </div></div>
    );
    if (step === "mas_proyectos") return (
      <div className="chat-input-area"><div className="chat-btns">
        <button className="chat-btn primary" onClick={() => handleMasProyectos(true)}>✅ Sí</button>
        <button className="chat-btn" onClick={() => handleMasProyectos(false)}>❌ No</button>
      </div></div>
    );
    if (step === "terminado") return (
      <div className="chat-input-area"><span className="fine" style={{ color: "var(--muted)" }}>Evaluación completada ✅</span></div>
    );
    return null;
  }

  const mostrarHistBar = evaluadoNombre && ["preguntas","confirmacion","modificar_menu","modificar_valor","mas_personas"].includes(step);

  return (
    <div className="eval-chat-area">
      {mostrarHistBar && (
        <div className="chat-hist-bar">
          <span className="chat-hist-info">
            {evaluadoNombre}{proyecto ? ` · ${proyecto}` : ""}
          </span>
          {onNavigate && (
            <button className="chat-hist-btn" onClick={() => onNavigate({ type: "historial-evaluaciones", evaluado: evaluadoNombre, evaluador: persona, proyecto })}>
              📊 Ver evaluaciones anteriores
            </button>
          )}
        </div>
      )}
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
      .catch(() => setError("No se pudieron cargar las evaluaciones."));
  }, [token, evaluado, evaluador, proyecto]);

  function formatFecha(iso) {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleDateString("es-ES", { day: "2-digit", month: "short", year: "numeric" });
    } catch { return iso.slice(0, 10); }
  }

  const RELACION_LABELS = { superior: "Superior", igual: "Igual", inferior: "Inferior" };

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <button className="link-button" onClick={onBack}>← Volver</button>
      </nav>
      <div className="historial-page">
        <p className="kicker">Historial de evaluaciones</p>
        <h1 className="historial-title">{evaluado}</h1>
        <p className="fine historial-subtitle">Proyecto: <strong>{proyecto || "—"}</strong></p>
        {error && <p className="historial-empty">{error}</p>}
        {historial === null && !error && <p className="fine" style={{ opacity: 0.5 }}>Cargando...</p>}
        {historial?.length === 0 && (
          <p className="historial-empty">No hay evaluaciones registradas tuyas para este proyecto aún.</p>
        )}
        {historial?.length > 0 && (
          <div className="historial-tabla-wrap">
            <table className="historial-tabla">
              <thead>
                <tr>
                  <th>Fecha</th>
                  <th>Valoración</th>
                  <th>Justificación</th>
                  <th>Relación</th>
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

function EvaluacionesSlackSection({ token, user, advisees, onNavigate }) {
  const [estadoCiclo, setEstadoCiclo] = React.useState(null);
  const [tipoActivo, setTipoActivo] = React.useState(null);
  const [completadas, setCompletadas] = React.useState({});

  React.useEffect(() => {
    apiRequest("/api/estado-ciclo-slack", { token })
      .then(d => { setEstadoCiclo(d); setCompletadas(d.completadas || {}); })
      .catch(() => setEstadoCiclo({ cicloActivo: true, completadas: {}, esCA: false }));
  }, [token]);

  const esCA = estadoCiclo?.esCA || advisees.length > 0;
  const tipos = [
    { key: "proyecto", label: "Evaluación mensual", disponible: true },
    { key: "personal", label: "Evaluación personal", disponible: false },
    ...(esCA ? [{ key: "ca", label: "Opiniones CA", disponible: false }] : []),
  ];

  return (
    <div>
      <p className="fine" style={{ marginBottom: "24px" }}>
        Contestar aquí es exactamente igual que contestar en Slack. Tus respuestas se guardan en el mismo sitio y en el mismo formato.
      </p>
      <div className="eval-slack-layout">
        <nav className="eval-tipos">
          {tipos.map(tipo => (
            <button
              key={tipo.key}
              className={`eval-tipo-btn${tipoActivo === tipo.key ? " active" : ""}${completadas[tipo.key] ? " completada" : ""}`}
              onClick={() => { if (tipo.disponible && !completadas[tipo.key]) setTipoActivo(tipo.key); }}
              disabled={!tipo.disponible || completadas[tipo.key]}
              title={completadas[tipo.key] ? "Ya has completado esta evaluación en el ciclo actual" : !tipo.disponible ? "Próximamente" : ""}
            >
              <span>{tipo.label}</span>
              {completadas[tipo.key]
                ? <span className="eval-tick">✅</span>
                : !tipo.disponible
                  ? <span className="eval-tick" style={{ fontSize: "11px", opacity: 0.4 }}>Próx.</span>
                  : null
              }
            </button>
          ))}
        </nav>
        <div style={{ minHeight: "500px", display: "flex", flexDirection: "column" }}>
          {tipoActivo === "proyecto"
            ? <ChatEvalProyecto key="proyecto" token={token} user={user} onComplete={() => setCompletadas(c => ({ ...c, proyecto: true }))} onNavigate={onNavigate} />
            : <div className="eval-chat-area"><div className="eval-placeholder"><p className="fine">{tipoActivo ? "Esta evaluación estará disponible próximamente." : "Selecciona un tipo de evaluación."}</p></div></div>
          }
        </div>
      </div>
    </div>
  );
}

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
  const isAdmin = Boolean(user?.is_admin);
  const [perfil, setPerfil] = useState({ foto: "", cargo: "" });
  const [misObjetivos, setMisObjetivos] = useState([]);
  const [informesOpen, setInformesOpen] = useState(false);
  const [seccionActiva, setSeccionActiva] = useState(null);
  const [proyectosActivos, setProyectosActivos] = useState([]);
  const [proyectosManager, setProyectosManager] = useState(null);
  const [proyectosVersion, setProyectosVersion] = useState(0);

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
      .catch(() => setInformeFinalEmpleado({ disponible: false, mensaje: "No se pudo cargar el informe." }));
  }, [token, isAdmin, user?.persona]);

  useEffect(() => {
    if (!isAdmin || adminModo !== "final" || !evaluado) return;
    setInformeFinalAdmin(null);
    apiRequest(`/api/informe-final?evaluado=${encodeURIComponent(evaluado)}`, { token })
      .then((data) => setInformeFinalAdmin(data))
      .catch(() => setInformeFinalAdmin({ disponible: false, mensaje: "No se pudo cargar el informe." }));
  }, [token, isAdmin, adminModo, evaluado]);

  useEffect(() => {
    const apply = (data) => setPerfil(data);
    apiRequestCached("/api/mi-perfil", { token }, apply)
      .then(apply)
      .catch(() => {});
  }, [token]);

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

  const role = isAdmin ? "Admin" : "";
  const ownEvaluado = user?.persona || user?.username || "";
  const targetEvaluado = isAdmin ? evaluado : (evaluado || ownEvaluado);
  const selectedLabel = useMemo(() => evaluados.find((item) => item.value === evaluado)?.label || "", [evaluados, evaluado]);

  async function generate(kind) {
    setLinks(null);
    setStatus(kind === "generar" ? "Claude está generando el informe..." : "Preparando trayectoria visual...");
    try {
      const body = { evaluado: targetEvaluado };
      if (kind === "generar" && cargoAnual) body.cargo = cargoAnual;
      const data = await apiRequest(`/api/${kind}`, { token, method: "POST", body });
      setLinks(data);
      if (kind === "trayectoria" && data.htmlUrl) {
        setStatus("");
        window.open(apiUrl(`${data.htmlUrl}&token=${encodeURIComponent(token)}`), "_blank", "noopener,noreferrer");
      } else {
        setStatus(kind === "generar" ? `Informe listo con ${data.total} evaluaciones.` : "");
      }
    } catch (err) {
      setStatus(err.message);
    }
  }

  async function generateAnual() {
    setLinkAnual(null);
    setStatusAnual("Claude está interpretando el texto del evaluador...");
    try {
      const data = await apiRequest("/api/generar-anual", { token, method: "POST", body: { evaluado: evaluadoAnual, cargo: cargoAnual } });
      setStatusAnual("Informe anual generado.");
      setLinkAnual(data.docxUrl);
    } catch (err) {
      setStatusAnual(err.message);
    }
  }

  async function downloadAnual(path) {
    try {
      const response = await fetch(apiUrl(path), { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) throw new Error("Error al descargar el archivo.");
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
    setStatus("Descargando archivo...");
    try {
      const response = await fetch(apiUrl(path), { headers: { Authorization: `Bearer ${token}` } });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || "No se pudo descargar el archivo.");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      URL.revokeObjectURL(url);
      setStatus("Archivo listo.");
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
            <button className="link-button" onClick={onBackToRoleSelect}>← Volver</button>
          )}
          <div className="nav-user">
            <div className="nav-user-info">
              <span className="nav-user-name">{persona}</span>
              <button className="link-button logout-btn" onClick={onLogout}>Cerrar sesión</button>
            </div>
            <div className="nav-avatar">
              {perfil.foto ? <img src={perfil.foto} alt="" /> : initials(persona)}
            </div>
          </div>
        </div>
      </nav>

      <div className="profile-wrap">
        <h2 className="profile-name">{persona}</h2>
        <div className="profile-grid">

          <nav className="profile-menu">
            {!isAdmin && (
              <button
                className="menu-item"
                onClick={() => onNavigate({ type: "activar-evaluaciones-proyecto" })}
              >
                Responsables de proyecto
              </button>
            )}
            {!isAdmin && proyectosManager?.length > 0 && (
              <button
                className="menu-item"
                onClick={() => onNavigate({ type: "mis-proyectos-activos" })}
              >
                Mis proyectos en activo
              </button>
            )}
            {!isAdmin && proyectosActivos.length > 0 && (
              <button
                className="menu-item"
                onClick={() => onNavigate({ type: "evaluaciones-proyecto", proyectos: proyectosActivos })}
              >
                Evaluaciones por proyectos
              </button>
            )}
            <button className={`menu-item${informesOpen ? " active" : ""}`} onClick={() => setInformesOpen((v) => !v)}>
              Mis informes
            </button>
            {informesOpen && (
              <div className="submenu">
                {informeFinalEmpleado === null ? (
                  <p className="fine">Cargando...</p>
                ) : informeFinalEmpleado?.disponible ? (
                  <>
                    {informeFinalEmpleado.htmlUrl && (
                      <button className="secondary" onClick={() => openFile(informeFinalEmpleado.htmlUrl, "informe_final.html")}>
                        Abrir en web
                      </button>
                    )}
                    {informeFinalEmpleado.docxUrl && (
                      <button className="secondary" onClick={() => openFile(informeFinalEmpleado.docxUrl, "informe_final.docx")}>
                        Descargar Word
                      </button>
                    )}
                  </>
                ) : (
                  <p className="fine">No tienes acceso.</p>
                )}
              </div>
            )}
            <button
              className="menu-item"
              onClick={() => generate("trayectoria")}
              disabled={!ownEvaluado || status === "Preparando trayectoria visual..."}
            >
              {status === "Preparando trayectoria visual..." ? "Generando..." : "Mi wrapped"}
            </button>
            <button
              className="menu-item"
              onClick={() => onNavigate({ type: "evaluaciones-slack" })}
            >
              Evaluaciones en Slack
            </button>
            {advisees.length > 0 && (
              <button
                className="menu-item"
                onClick={() => onNavigate({ type: "advisees-list", advisees })}
              >
                Mis advisees
              </button>
            )}
            {isAdmin && !onBackToRoleSelect && (
              <button
                className={`menu-item${seccionActiva === "admin" ? " active" : ""}`}
                onClick={() => setSeccionActiva((v) => v === "admin" ? null : "admin")}
              >
                Panel admin
              </button>
            )}
          </nav>

          <div className="profile-photo-wrap">
            {perfil.foto
              ? <img src={perfil.foto} alt={persona} className="profile-photo" />
              : <div className="profile-photo-placeholder">{initials(persona)}</div>
            }
          </div>

          <aside className="profile-info">
            <p className="profile-info-label">Mi puesto</p>
            <p className="profile-info-value">{perfil.cargo || "—"}</p>
            <p className="profile-info-label" style={{ marginTop: "28px" }}>Mis objetivos</p>
            {misObjetivos.length ? (
              <ul className="profile-obj-list" style={{ margin: 0, paddingLeft: "16px" }}>
                {misObjetivos.map((obj, i) => (
                  <li key={i} className="profile-obj-text">{obj.titulo}</li>
                ))}
              </ul>
            ) : (
              <p className="fine">Sin objetivos definidos.</p>
            )}
          </aside>

        </div>
      </div>

      {status && status !== "Preparando trayectoria visual..." && (
        <p className="dash-status fine">{status}</p>
      )}

      {isAdmin && !onBackToRoleSelect && seccionActiva === "admin" && (
        <section className="panel" style={{ marginTop: "32px" }}>
          <p className="kicker">Panel admin</p>
          <h2>Gestión de evaluaciones</h2>
          <label>Persona evaluada</label>
          <select value={evaluado} onChange={(e) => setEvaluado(e.target.value)}>
            {evaluados.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
          <p className="fine">Selección actual: {selectedLabel || "sin tabla disponible"}</p>
          <div className="actions" style={{ marginTop: "20px" }}>
            <button onClick={() => setAdminModo("borrador")} className={adminModo === "borrador" ? "" : "secondary"}>Borrador de Claude</button>
            <button onClick={() => setAdminModo("final")} className={adminModo === "final" ? "" : "secondary"}>Versión final CA</button>
          </div>
          {adminModo === "borrador" ? (
            <>
              <div className="tools" style={{ marginTop: "24px" }}>
                <article className="tool">
                  <p className="kicker">Informe anual</p>
                  <h2>Informe anual{targetEvaluado ? ` de ${targetEvaluado}` : ""}</h2>
                  <p>Genera una base para el informe anual de evaluaciones.</p>
                  <button onClick={() => generate("generar")} disabled={!targetEvaluado}>Generar informe anual</button>
                </article>
                <article className="tool wrapped">
                  <p className="kicker">Trayectoria</p>
                  <h2>Vista tipo wrapped</h2>
                  <p>Navega por fechas, proyecto, satisfacción y comentarios clave.</p>
                  <button className="secondary" onClick={() => generate("trayectoria")} disabled={!targetEvaluado}>Generar trayectoria</button>
                </article>
              </div>
              {links && (
                <section className="result panel" style={{ marginTop: "24px" }}>
                  <h2>Resultado</h2>
                  <div className="actions">
                    {links.htmlUrl && <button onClick={() => openFile(links.htmlUrl, "informe.html")}>Abrir web</button>}
                    {links.docxAnualUrl && <button className="secondary" onClick={() => downloadAnual(links.docxAnualUrl)}>Descargar informe anual</button>}
                  </div>
                </section>
              )}
            </>
          ) : (
            <div className="panel" style={{ marginTop: "24px" }}>
              <p className="kicker">Versión final CA</p>
              <h2>Informe final{targetEvaluado ? ` de ${targetEvaluado}` : ""}</h2>
              {!targetEvaluado ? (
                <p className="fine">Selecciona una persona evaluada.</p>
              ) : informeFinalAdmin === null ? (
                <p>Cargando...</p>
              ) : informeFinalAdmin?.disponible ? (
                <div className="actions">
                  {informeFinalAdmin.htmlUrl && <button onClick={() => openFile(informeFinalAdmin.htmlUrl, "informe_final.html")}>Abrir versión web</button>}
                  {informeFinalAdmin.docxUrl && <button className="secondary" onClick={() => openFile(informeFinalAdmin.docxUrl, "informe_final.docx")}>Descargar Word</button>}
                </div>
              ) : (
                <p className="fine">{informeFinalAdmin?.mensaje || "No hay informe final disponible."}</p>
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
              <h2>Opiniones sobre {opinionesModal.nombre}</h2>
            </div>
            <button className="secondary" onClick={() => setOpinionesModal(null)}>Cerrar</button>
          </div>
          {opinionesModal.opiniones.length ? (
            <div className="opiniones-list">
              {opinionesModal.opiniones.map((op, i) => (
                <article key={i} className="opinion-item">
                  <p className="opinion-fecha fine">{op.fecha ? op.fecha.slice(0, 10) : "Sin fecha"}</p>
                  {op.resumen_advisee && (
                    <div className="opinion-resumen">
                      <p className="fine"><strong>Evaluaciones vistas:</strong></p>
                      <pre className="opinion-pre">{op.resumen_advisee}</pre>
                    </div>
                  )}
                  <p className="fine"><strong>Opinión del CA:</strong></p>
                  <p className="opinion-texto">{op.opinion || "—"}</p>
                </article>
              ))}
            </div>
          ) : (
            <p>No hay opiniones guardadas sobre {opinionesModal.nombre}.</p>
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
    setStatus("Subiendo informe...");
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
      if (!response.ok) throw new Error(data.error || "No se pudo subir el informe.");
      setStatus("Informe subido correctamente.");
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
      if (!response.ok) throw new Error("No se pudo descargar el archivo.");
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
        <button className="link-button" onClick={onBack}>← Volver</button>
      </nav>
      <section className="hero dashboard-hero">
        <div>
          {advisee.foto
            ? <img src={advisee.foto} alt={advisee.nombre} className="objetivos-foto" />
            : <div className="objetivos-foto objetivos-foto-placeholder">{advisee.nombre.charAt(0)}</div>
          }
          <p className="kicker">Informe final</p>
          <h1>{advisee.nombre}</h1>
        </div>
        {informeActual && (
          <div className="panel" style={{ marginBottom: "24px" }}>
            <h2>Versión actual</h2>
            <p className="fine">Ya hay un informe final subido. Puedes descargarlo o subir uno nuevo para reemplazarlo.</p>
            <div className="actions">
              {informeActual.htmlUrl && <button onClick={() => openFile(informeActual.htmlUrl, "informe_final.html")}>Abrir versión web</button>}
              {informeActual.docxUrl && <button className="secondary" onClick={() => openFile(informeActual.docxUrl, "informe_final.docx")}>Descargar Word</button>}
            </div>
          </div>
        )}
        <form className="panel" onSubmit={subir}>
          <h2>Subir versión final</h2>
          <p>Sube el Word con tu versión final. Se guarda en Notion y el advisee podrá descargarlo. Se mantienen las 2 versiones más recientes.</p>
          <label>Archivo Word (.docx)</label>
          <input
            type="file"
            accept=".doc,.docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
            onChange={(e) => setFile(e.target.files?.[0] || null)}
            required
          />
          {status && <p className={status.includes("Error") || status.includes("pudo") ? "error" : "fine"}>{status}</p>}
          <div className="actions">
            <button type="submit" disabled={uploading || !file}>
              {uploading ? "Subiendo..." : "Subir informe"}
            </button>
          </div>
        </form>
      </section>
      {links && (
        <section className="result panel">
          <h2>Informe subido</h2>
          <div className="actions">
            {links.htmlUrl && <button onClick={() => openFile(links.htmlUrl, "informe_final.html")}>Abrir versión web</button>}
            {links.docxUrl && <button className="secondary" onClick={() => openFile(links.docxUrl, "informe_final.docx")}>Descargar Word</button>}
          </div>
        </section>
      )}
      <Footer />
    </main>
  );
}

function AdviseesList({ token, advisees, onBack, onNavigate }) {
  const [accesoActivo, setAccesoActivo] = useState(false);
  const [toggling, setToggling] = useState(false);

  useEffect(() => {
    const apply = (data) => setAccesoActivo(data.activo || false);
    apiRequestCached("/api/acceso-advisees", { token }, apply)
      .then(apply)
      .catch(() => {});
  }, [token]);

  async function toggleAcceso() {
    setToggling(true);
    try {
      const data = await apiRequest("/api/acceso-advisees", { token, method: "POST", body: { activo: !accesoActivo } });
      setAccesoActivo(data.activo);
    } catch {
    } finally {
      setToggling(false);
    }
  }

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <button className="link-button" onClick={onBack}>← Volver</button>
      </nav>
      <div className="advisees-page-wrap">
        <p className="kicker">Career Advisor</p>
        <h2>Mis advisees</h2>
        <div className="advisees-page-grid">
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
        <div style={{ marginTop: "40px" }}>
          <button
            className={accesoActivo ? "" : "secondary"}
            onClick={toggleAcceso}
            disabled={toggling}
          >
            {toggling ? "Guardando..." : accesoActivo ? "Acceso activo — revocar" : "Dar acceso a mis advisees"}
          </button>
        </div>
      </div>
      <Footer />
    </main>
  );
}

function AdviseeDetail({ token, advisee, advisees, onBack, onNavigate }) {
  const [gestionOpen, setGestionOpen] = useState(false);
  const [opiniones, setOpiniones] = useState(null);
  const [loadingOpiniones, setLoadingOpiniones] = useState(false);
  const [accesoIndividual, setAccesoIndividual] = useState(false);
  const [togglingAccesoIndividual, setTogglingAccesoIndividual] = useState(false);

  useEffect(() => {
    const apply = (data) => setAccesoIndividual(data.activo || false);
    apiRequestCached(`/api/acceso-advisee-individual?advisee=${encodeURIComponent(advisee.nombre)}`, { token }, apply)
      .then(apply)
      .catch(() => {});
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

  async function cargarOpiniones() {
    if (opiniones !== null) { setOpiniones(null); return; }
    setLoadingOpiniones(true);
    try {
      const data = await apiRequest(`/api/opiniones-ca?advisee=${encodeURIComponent(advisee.nombre)}`, { token });
      setOpiniones(data.opiniones || []);
    } catch {
      setOpiniones([]);
    } finally {
      setLoadingOpiniones(false);
    }
  }

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <button className="link-button" onClick={onBack}>← Mis advisees</button>
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
            <button onClick={() => onNavigate({ type: "objetivos", advisee, advisees, from: "advisee-detail" })}>
              Editar objetivos
            </button>
            <button className="secondary" onClick={() => setGestionOpen((v) => !v)}>
              {gestionOpen ? "Cerrar gestión" : "Gestionar informe"}
            </button>
            {gestionOpen && (
              <div className="advisee-gestion">
                <button className="secondary" onClick={cargarOpiniones} disabled={loadingOpiniones}>
                  {loadingOpiniones ? "Cargando..." : opiniones !== null ? "Ocultar opiniones" : "Ver tus opiniones sobre este advisee"}
                </button>
                <button className="secondary" onClick={() => onNavigate({ type: "informes-advisee", advisee, from: "advisee-detail", advisees })}>
                  Ver borrador de informe generado por Claude
                </button>
                <button className="secondary" onClick={() => onNavigate({ type: "subir-informe", advisee, from: "advisee-detail", advisees })}>
                  Subir informe final
                </button>
                <button
                  className={accesoIndividual ? "" : "secondary"}
                  onClick={toggleAccesoIndividual}
                  disabled={togglingAccesoIndividual}
                >
                  {togglingAccesoIndividual
                    ? "Guardando..."
                    : accesoIndividual
                    ? "Acceso a informe activo — revocar"
                    : "Dar acceso a su informe"}
                </button>
              </div>
            )}
          </div>
        </div>
        {opiniones !== null && (
          <section className="opiniones-modal panel" style={{ marginTop: "32px" }}>
            <h2 style={{ marginBottom: "18px" }}>Opiniones sobre {advisee.nombre}</h2>
            {opiniones.length ? (
              <div className="opiniones-list">
                {opiniones.map((op, i) => (
                  <article key={i} className="opinion-item">
                    <p className="opinion-fecha fine">{op.fecha ? op.fecha.slice(0, 10) : "Sin fecha"}</p>
                    {op.resumen_advisee && (
                      <div className="opinion-resumen">
                        <p className="fine"><strong>Evaluaciones vistas:</strong></p>
                        <pre className="opinion-pre">{op.resumen_advisee}</pre>
                      </div>
                    )}
                    <p className="fine"><strong>Opinión del CA:</strong></p>
                    <p className="opinion-texto">{op.opinion || "—"}</p>
                  </article>
                ))}
              </div>
            ) : (
              <p>No hay opiniones guardadas sobre {advisee.nombre}.</p>
            )}
          </section>
        )}
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
  const [expanded, setExpanded] = useState(null);
  const [estadoMap, setEstadoMap] = useState({});
  const [loadingEstado, setLoadingEstado] = useState({});
  const [todosEmpleados, setTodosEmpleados] = useState([]);
  const [añadirMap, setAñadirMap] = useState({});
  const [añadirValor, setAñadirValor] = useState({});
  const [accionMsg, setAccionMsg] = useState({});

  const persona = user?.persona || user?.username || "";

  function cargarProyectos() {
    apiRequest("/api/proyectos-manager", { token })
      .then((d) => setProyectos(d.proyectos || []))
      .catch(() => {})
      .finally(() => setLoading(false));
  }

  useEffect(() => {
    cargarProyectos();
    apiRequest("/api/todos-empleados", { token })
      .then((d) => setTodosEmpleados(d.empleados || []))
      .catch(() => {});
  }, [token]);

  function cargarEstado(nombre) {
    setLoadingEstado((prev) => ({ ...prev, [nombre]: true }));
    apiRequest(`/api/estado-proyecto?proyecto=${encodeURIComponent(nombre)}`, { token })
      .then((d) => setEstadoMap((prev) => ({ ...prev, [nombre]: d.estado || [] })))
      .catch(() => setEstadoMap((prev) => ({ ...prev, [nombre]: [] })))
      .finally(() => setLoadingEstado((prev) => ({ ...prev, [nombre]: false })));
  }

  function toggleProyecto(nombre) {
    if (expanded === nombre) { setExpanded(null); return; }
    setExpanded(nombre);
    cargarEstado(nombre);
  }

  async function modificarMiembro(accion, proyecto, empleado) {
    setAccionMsg((prev) => ({ ...prev, [proyecto]: "" }));
    try {
      const data = await apiRequest("/api/modificar-equipo-proyecto", {
        token,
        method: "POST",
        body: { accion, proyecto, empleado },
      });
      if (data.ok) {
        setAccionMsg((prev) => ({ ...prev, [proyecto]: accion === "añadir" ? `${empleado} añadido.` : `${empleado} eliminado.` }));
        setAñadirValor((prev) => ({ ...prev, [proyecto]: "" }));
        setAñadirMap((prev) => ({ ...prev, [proyecto]: false }));
        cargarProyectos();
        cargarEstado(proyecto);
      } else {
        setAccionMsg((prev) => ({ ...prev, [proyecto]: data.error || "Error al modificar." }));
      }
    } catch (err) {
      setAccionMsg((prev) => ({ ...prev, [proyecto]: err.message }));
    }
  }

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <button className="link-button" onClick={onBack}>← Volver</button>
      </nav>
      <section className="hero">
        <div>
          <p className="kicker">Gestión de proyecto</p>
          <h1 style={{ fontSize: "clamp(32px,6vw,72px)" }}>Mis proyectos en activo</h1>
        </div>
      </section>
      <section className="panel" style={{ marginTop: "32px" }}>
        {loading ? (
          <p>Cargando...</p>
        ) : proyectos.length === 0 ? (
          <p className="fine">No tienes proyectos con evaluaciones activas.</p>
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: "16px" }}>
            {proyectos.map((p) => {
              const isOpen = expanded === p.nombre_proyecto;
              const estado = estadoMap[p.nombre_proyecto] || [];
              const cargando = loadingEstado[p.nombre_proyecto];
              const mostrarAnadir = añadirMap[p.nombre_proyecto];
              const valorAnadir = añadirValor[p.nombre_proyecto] || "";
              const msg = accionMsg[p.nombre_proyecto] || "";
              const equipoActual = p.equipo || [];
              const disponibles = todosEmpleados.filter((e) => !equipoActual.includes(e));
              return (
                <div key={p.nombre_proyecto} style={{ border: "1px solid var(--border,#e5e7eb)", borderRadius: "8px", overflow: "hidden" }}>
                  <button
                    className="link-button"
                    style={{ width: "100%", textAlign: "left", padding: "16px 20px", display: "flex", justifyContent: "space-between", alignItems: "center", fontWeight: 600, background: "none", border: "none", cursor: "pointer" }}
                    onClick={() => toggleProyecto(p.nombre_proyecto)}
                  >
                    <span>{p.nombre_proyecto}</span>
                    <span className="fine" style={{ fontWeight: 400 }}>{p.equipo.length} miembros &nbsp;{isOpen ? "▲" : "▼"}</span>
                  </button>
                  {isOpen && (
                    <div style={{ padding: "0 20px 20px" }}>
                      <div style={{ marginBottom: "12px", display: "flex", flexWrap: "wrap", gap: "8px", alignItems: "center" }}>
                        <span style={{ fontSize: "13px", color: "#6b7280" }}>Equipo:</span>
                        {equipoActual.map((miembro) => (
                          <span key={miembro} style={{ display: "inline-flex", alignItems: "center", gap: "4px", background: "#f3f4f6", borderRadius: "99px", padding: "2px 10px", fontSize: "13px" }}>
                            {miembro}
                            <button
                              onClick={() => modificarMiembro("eliminar", p.nombre_proyecto, miembro)}
                              title={`Eliminar ${miembro}`}
                              style={{ background: "none", border: "none", cursor: "pointer", color: "#ef4444", fontWeight: 700, padding: "0 2px", lineHeight: 1 }}
                            >×</button>
                          </span>
                        ))}
                        <button
                          className="link-button"
                          style={{ fontSize: "13px", padding: "2px 10px", border: "1px dashed var(--border,#e5e7eb)", borderRadius: "99px" }}
                          onClick={() => setAñadirMap((prev) => ({ ...prev, [p.nombre_proyecto]: !mostrarAnadir }))}
                        >
                          {mostrarAnadir ? "Cancelar" : "+ Añadir"}
                        </button>
                      </div>
                      {mostrarAnadir && (
                        <div style={{ display: "flex", gap: "8px", marginBottom: "12px", alignItems: "center" }}>
                          <select
                            value={valorAnadir}
                            onChange={(e) => setAñadirValor((prev) => ({ ...prev, [p.nombre_proyecto]: e.target.value }))}
                            style={{ flex: 1, padding: "6px 10px", borderRadius: "6px", border: "1px solid var(--border,#e5e7eb)", fontSize: "14px" }}
                          >
                            <option value="">Selecciona una persona...</option>
                            {disponibles.map((e) => <option key={e} value={e}>{e}</option>)}
                          </select>
                          <button
                            className="cta-button"
                            style={{ padding: "6px 16px", fontSize: "14px" }}
                            disabled={!valorAnadir}
                            onClick={() => modificarMiembro("añadir", p.nombre_proyecto, valorAnadir)}
                          >Añadir</button>
                        </div>
                      )}
                      {msg && <p style={{ fontSize: "13px", color: msg.includes("Error") || msg.includes("error") ? "#ef4444" : "#16a34a", marginBottom: "8px" }}>{msg}</p>}
                      {cargando ? (
                        <p className="fine">Cargando estado de evaluaciones...</p>
                      ) : estado.length === 0 ? (
                        <p className="fine">Sin datos de evaluación todavía.</p>
                      ) : (
                        <div style={{ overflowX: "auto" }}>
                          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: "14px" }}>
                            <thead>
                              <tr style={{ borderBottom: "2px solid var(--border,#e5e7eb)" }}>
                                <th style={{ textAlign: "left", padding: "8px 4px" }}>Miembro</th>
                                <th style={{ textAlign: "center", padding: "8px 12px" }}>Recibidas</th>
                                <th style={{ textAlign: "center", padding: "8px 12px" }}>Autoevaluación</th>
                                <th style={{ textAlign: "left", padding: "8px 4px" }}>Evaluado por</th>
                                <th style={{ textAlign: "left", padding: "8px 4px" }}>Pendiente de</th>
                              </tr>
                            </thead>
                            <tbody>
                              {estado.map((m) => {
                                const total = m.n_evaluaciones + m.pendientes.length;
                                const completo = m.pendientes.length === 0;
                                const ninguna = m.n_evaluaciones === 0;
                                const badge = completo
                                  ? { bg: "#dcfce7", color: "#166534" }
                                  : ninguna ? { bg: "#fee2e2", color: "#991b1b" }
                                  : { bg: "#fef9c3", color: "#713f12" };
                                return (
                                  <tr key={m.nombre} style={{ borderBottom: "1px solid var(--border,#f0f0f0)" }}>
                                    <td style={{ padding: "10px 4px", fontWeight: 500 }}>{m.nombre}</td>
                                    <td style={{ padding: "10px 12px", textAlign: "center" }}>
                                      <span style={{ background: badge.bg, color: badge.color, padding: "2px 10px", borderRadius: "99px", fontWeight: 600, fontSize: "13px" }}>
                                        {m.n_evaluaciones}/{total}
                                      </span>
                                    </td>
                                    <td style={{ padding: "10px 12px", textAlign: "center" }}>
                                      {m.autoevaluacion_hecha
                                        ? <span style={{ color: "#16a34a", fontWeight: 600 }}>✓</span>
                                        : <span style={{ color: "#ef4444", fontWeight: 600 }}>✗</span>}
                                    </td>
                                    <td style={{ padding: "10px 4px", color: "#4b5563" }}>
                                      {m.evaluadores.length ? m.evaluadores.join(", ") : <span className="fine">—</span>}
                                    </td>
                                    <td style={{ padding: "10px 4px" }}>
                                      {completo
                                        ? <span style={{ color: "#16a34a" }}>✓ Completo</span>
                                        : <span style={{ color: "#ef4444" }}>{m.pendientes.join(", ")}</span>}
                                    </td>
                                  </tr>
                                );
                              })}
                            </tbody>
                          </table>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        )}
      </section>
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
    if (!proyecto.trim()) { setStatus("Escribe el nombre del proyecto."); return; }
    if (seleccionados.length === 0) { setStatus("Selecciona al menos un empleado."); return; }
    setLoading(true);
    setStatus("");
    try {
      const data = await apiRequest("/api/activar-evaluaciones-proyecto", {
        token,
        method: "POST",
        body: { proyecto: proyecto.trim(), empleados: seleccionados },
      });
      if (data.ok) {
        setStatus(`Evaluaciones activadas para ${data.activados?.length || seleccionados.length} persona(s). Se les ha enviado una notificación por Slack.`);
        setEnviado(true);
        if (onActivado) onActivado();
      } else {
        setStatus(data.error || "No se pudo activar.");
      }
    } catch (err) {
      setStatus(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <button className="link-button" onClick={onBack}>← Volver</button>
      </nav>
      <section className="hero">
        <div>
          <p className="kicker">Gestión de proyecto</p>
          <h1 style={{ fontSize: "clamp(32px,6vw,72px)" }}>Activar evaluaciones</h1>
        </div>
      </section>
      <section className="panel" style={{ marginTop: "32px" }}>
        {enviado ? (
          <>
            <p className="fine" style={{ color: "#166534" }}>{status}</p>
            <div className="actions">
              <button onClick={() => { setEnviado(false); setProyecto(""); setSeleccionados([]); setStatus(""); }}>
                Activar otro proyecto
              </button>
              <button className="secondary" onClick={onBack}>Volver al inicio</button>
            </div>
          </>
        ) : (
          <form onSubmit={activar}>
            <p className="fine">Como responsable de proyecto, introduce el nombre del proyecto y selecciona los miembros de tu equipo. Se les notificará por Slack y podrán acceder a los formularios de evaluación.</p>
            <label>Nombre del proyecto</label>
            <p className="fine">Formato: AÑO_EMPRESA_NOMBRE (sin espacios ni tildes, p.ej. <em>2024_Acme_Innovacion</em>)</p>
            <input
              type="text"
              value={proyecto}
              onChange={(e) => setProyecto(e.target.value)}
              placeholder="2024_Empresa_NombreProyecto"
              required
            />
            <label style={{ marginTop: "24px" }}>Miembros del equipo</label>
            {loadingEmpleados ? (
              <p className="fine">Cargando empleados...</p>
            ) : (
              <>
                <input
                  type="text"
                  value={busqueda}
                  onChange={(e) => setBusqueda(e.target.value)}
                  placeholder="Buscar por nombre..."
                  style={{ marginTop: "8px", marginBottom: "8px" }}
                />
                <div style={{ display: "flex", flexDirection: "column", gap: "8px", maxHeight: "340px", overflowY: "auto", padding: "4px 0" }}>
                  {todosEmpleados
                    .filter((nombre) => nombre.toLowerCase().includes(busqueda.toLowerCase().trim()))
                    .map((nombre) => (
                      <label key={nombre} className="check-label" style={{ cursor: "pointer", userSelect: "none" }}>
                        <input
                          type="checkbox"
                          className="check-input"
                          checked={seleccionados.includes(nombre)}
                          onChange={() => toggleEmpleado(nombre)}
                        />
                        {nombre}
                      </label>
                    ))}
                  {todosEmpleados.filter((n) => n.toLowerCase().includes(busqueda.toLowerCase().trim())).length === 0 && (
                    <p className="fine" style={{ margin: 0 }}>No hay resultados para "{busqueda}".</p>
                  )}
                </div>
              </>
            )}
            {status && <p className={status.includes("Error") || status.includes("pudo") || status.includes("existe") ? "error" : "fine"} style={{ marginTop: "12px" }}>{status}</p>}
            <div className="actions">
              <button type="submit" disabled={loading}>
                {loading ? "Activando..." : `Activar evaluaciones (${seleccionados.length} seleccionados)`}
              </button>
            </div>
          </form>
        )}
      </section>
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

  const evaluacionesAHacer = useMemo(() => {
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
  }, [equipo, persona, managerDelProyecto]);

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <button className="link-button" onClick={onBack}>← Volver</button>
      </nav>
      <section className="hero">
        <div>
          <p className="kicker">Evaluaciones</p>
          <h1 style={{ fontSize: "clamp(32px,6vw,72px)" }}>Evaluaciones por proyectos</h1>
        </div>
      </section>

      {proyectos.length > 1 && (
        <section className="panel" style={{ marginTop: "32px" }}>
          <label>Proyecto</label>
          <select value={proyectoSeleccionado} onChange={(e) => setProyectoSeleccionado(e.target.value)}>
            {proyectos.map((p) => (
              <option key={p.nombre_proyecto} value={p.nombre_proyecto}>{p.nombre_proyecto}</option>
            ))}
          </select>
        </section>
      )}

      {proyectoSeleccionado && (
        <section className="panel" style={{ marginTop: "32px" }}>
          <p className="kicker">{proyectoSeleccionado}</p>
          <h2>Tus evaluaciones</h2>
          {loadingEquipo ? (
            <p className="fine">Cargando...</p>
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: "12px", marginTop: "16px" }}>
              {evaluacionesAHacer.map(({ tipo, evaluado, label }) => {
                const evalKey = `${tipo}:${evaluado}`;
                const completado =
                  (completedEvals[proyectoSeleccionado] || []).includes(evalKey) ||
                  completadasNotion.includes(evalKey);
                return (
                  <button
                    key={evalKey}
                    className="secondary"
                    style={{ textAlign: "left", padding: "14px 18px", opacity: completado ? 0.55 : 1 }}
                    disabled={completado}
                    onClick={() =>
                      onNavigate({
                        type: "formulario-evaluacion-proyecto",
                        proyecto: proyectoSeleccionado,
                        tipo,
                        evaluado,
                        manager: managerDelProyecto,
                        proyectos,
                      })
                    }
                  >
                    {completado && <span style={{ marginRight: "8px", color: "#166534" }}>✓</span>}
                    {label}
                  </button>
                );
              })}
            </div>
          )}
        </section>
      )}
      <Footer />
    </main>
  );
}

// ---------------------------------------------------------------------------
// Formulario de evaluación de proyecto
// ---------------------------------------------------------------------------

function FormularioEvaluacionProyecto({ token, user, proyecto, tipo, manager, evaluadoProp, onBack, onEnviado }) {
  const [preguntas, setPreguntas] = useState(null);
  const [todosEmpleados, setTodosEmpleados] = useState([]);
  const [evaluado, setEvaluado] = useState("");
  const [respuestas, setRespuestas] = useState({});
  const [enviando, setEnviando] = useState(false);
  const [status, setStatus] = useState("");
  const [enviado, setEnviado] = useState(false);

  const persona = user?.persona || user?.username || "";

  const LABELS_TIPOS = {
    autoevaluacion: "Autoevaluación",
    mismos_miembros: "Evaluación a compañero",
    miembros_a_manager: "Evaluación al responsable",
    manager_a_miembros: "Evaluación a miembro",
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

  function setRespuesta(id, valor) {
    setRespuestas((prev) => ({ ...prev, [id]: valor }));
  }

  const evaluadoFinal = necesitaSelector ? evaluado : evaluadoFijo;

  async function enviar(e) {
    e.preventDefault();
    if (!evaluadoFinal) { setStatus("Selecciona la persona a evaluar."); return; }
    if (preguntas && preguntas.some((p) => p.tipo !== "abierta" && !respuestas[p.id])) {
      setStatus("Por favor responde todas las preguntas obligatorias.");
      return;
    }
    setEnviando(true);
    setStatus("");
    try {
      const data = await apiRequest("/api/guardar-evaluacion-proyecto", {
        token,
        method: "POST",
        body: { proyecto, tipo, evaluado: evaluadoFinal, respuestas },
      });
      if (data.ok) {
        setEnviado(true);
        setStatus("Evaluación guardada correctamente en Notion.");
        if (onEnviado) onEnviado();
      } else {
        setStatus(data.error || "No se pudo guardar.");
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
          <button className="link-button" onClick={onBack}>← Volver</button>
        </nav>
        <p className="fine" style={{ padding: "40px" }}>Cargando preguntas...</p>
      </main>
    );
  }

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <button className="link-button" onClick={onBack}>← Volver</button>
      </nav>
      <section className="hero">
        <div>
          <p className="kicker">{proyecto}</p>
          <h1 style={{ fontSize: "clamp(24px,4vw,52px)", lineHeight: 1.1 }}>{tipoLabel}</h1>
        </div>
      </section>

      {enviado ? (
        <section className="panel" style={{ marginTop: "32px" }}>
          <p className="fine" style={{ color: "#166534" }}>Evaluación guardada correctamente.</p>
          <div className="actions">
            <button onClick={() => { setEnviado(false); setRespuestas({}); setEvaluado(""); setStatus(""); }}>
              Nueva evaluación
            </button>
            <button className="secondary" onClick={onBack}>Volver</button>
          </div>
        </section>
      ) : (
        <form className="panel" style={{ marginTop: "32px" }} onSubmit={enviar}>
          {necesitaSelector && (
            <>
              <label>Persona a evaluar</label>
              <select value={evaluado} onChange={(e) => setEvaluado(e.target.value)} required>
                <option value="">— Selecciona —</option>
                {todosEmpleados.filter((n) => n !== persona).map((nombre) => (
                  <option key={nombre} value={nombre}>{nombre}</option>
                ))}
              </select>
            </>
          )}
          {!necesitaSelector && evaluadoFijo && (
            <p className="fine" style={{ marginBottom: "16px" }}>
              {tipo === "autoevaluacion" ? `Evaluándote a ti mismo: ${evaluadoFijo}` : `Evaluando a: ${evaluadoFijo}`}
            </p>
          )}

          {preguntas.length === 0 && (
            <p className="fine">No hay preguntas configuradas para este tipo de evaluación.</p>
          )}

          {(() => {
            let categoriaActual = null;
            return preguntas.map((p) => {
              const cambioCat = p.categoria && p.categoria !== categoriaActual;
              if (cambioCat) categoriaActual = p.categoria;
              return (
                <React.Fragment key={p.id}>
                  {cambioCat && (
                    <p style={{ fontWeight: 800, fontSize: "13px", marginTop: "28px", marginBottom: "4px", textTransform: "uppercase", letterSpacing: "0.05em" }}>
                      {p.categoria}
                    </p>
                  )}
                  <div style={{ marginTop: "18px" }}>
                    <label style={{ fontWeight: 600, fontSize: "14px", marginBottom: "10px", display: "block" }}>
                      {p.texto}
                    </label>
                    {p.tipo === "escala_1_5" && (
                      <div style={{ display: "flex", gap: "12px", flexWrap: "wrap", alignItems: "center" }}>
                        <span className="fine" style={{ fontSize: "12px" }}>1 — Carece de cumplimiento</span>
                        {[1, 2, 3, 4, 5].map((val) => (
                          <label key={val} style={{ display: "flex", alignItems: "center", gap: "4px", cursor: "pointer", fontSize: "14px", fontWeight: respuestas[p.id] === String(val) ? 800 : 400 }}>
                            <input
                              type="radio"
                              name={p.id}
                              value={String(val)}
                              checked={respuestas[p.id] === String(val)}
                              onChange={() => setRespuesta(p.id, String(val))}
                              style={{ width: "auto" }}
                            />
                            {val}
                          </label>
                        ))}
                        <span className="fine" style={{ fontSize: "12px" }}>5 — Cumple totalmente</span>
                      </div>
                    )}
                    {p.tipo === "radio_3" && (
                      <div style={{ display: "flex", gap: "16px", flexWrap: "wrap" }}>
                        {(p.opciones.length ? p.opciones : ["Exceeds", "Achieves", "Expects more"]).map((op) => (
                          <label key={op} style={{ display: "flex", alignItems: "center", gap: "6px", cursor: "pointer", fontSize: "14px", fontWeight: respuestas[p.id] === op ? 800 : 400 }}>
                            <input
                              type="radio"
                              name={p.id}
                              value={op}
                              checked={respuestas[p.id] === op}
                              onChange={() => setRespuesta(p.id, op)}
                              style={{ width: "auto" }}
                            />
                            {op}
                          </label>
                        ))}
                      </div>
                    )}
                    {p.tipo === "abierta" && (
                      <textarea
                        value={respuestas[p.id] || ""}
                        onChange={(e) => setRespuesta(p.id, e.target.value)}
                        rows={4}
                        style={{ width: "100%", border: "1px solid #d8d8d8", padding: "10px", fontSize: "14px", resize: "vertical", background: "transparent", color: "#101010", outline: "none", fontFamily: "inherit" }}
                        placeholder="Escribe tu respuesta..."
                      />
                    )}
                  </div>
                </React.Fragment>
              );
            });
          })()}

          {status && <p className={status.includes("Error") || status.includes("pudo") ? "error" : "fine"} style={{ marginTop: "16px" }}>{status}</p>}
          <div className="actions">
            <button type="submit" disabled={enviando || preguntas.length === 0}>
              {enviando ? "Guardando..." : "Enviar evaluación"}
            </button>
          </div>
        </form>
      )}
      <Footer />
    </main>
  );
}

function EvaluacionesSlackPage({ token, user, advisees, onBack, onNavigate }) {
  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/"><img src="/src/logo.png" alt="igeneris" className="brand-logo" /></a>
        <button className="link-button" onClick={onBack}>← Volver</button>
      </nav>
      <div style={{ paddingTop: "40px" }}>
        <p className="kicker">Evaluaciones en Slack</p>
        <EvaluacionesSlackSection token={token} user={user} advisees={advisees || []} onNavigate={onNavigate} />
      </div>
      <Footer />
    </main>
  );
}

function App() {
  const resetToken = getResetToken();
  const [token, setToken] = useState(localStorage.getItem("evaluabot_token") || sessionStorage.getItem("evaluabot_token") || "");
  const [user, setUser] = useState(null);
  const [page, setPage] = useState(null);
  const [adminMode, setAdminMode] = useState(null); // null | "personal" | "admin"
  const [completedEvals, setCompletedEvals] = useState({});

  function navigate(newPage, newAdminModeOverride) {
    setPage(newPage);
    if (newAdminModeOverride !== undefined) setAdminMode(newAdminModeOverride);
  }

  useEffect(() => {
    if (resetToken) return;
    if (!token) return;
    apiRequest("/api/me", { token })
      .then((data) => {
        if (data.user) setUser(data.user);
        else localStorage.removeItem("evaluabot_token");
      })
      .catch(() => localStorage.removeItem("evaluabot_token"));
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

  if (resetToken || !token || !user) {
    return <AuthScreen onLogin={(newToken, newUser) => { setToken(newToken); setUser(newUser); }} />;
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
  if (page?.type === "informes-advisee") {
    return <InformesAdvisee token={token} advisee={page.advisee} onBack={backTo(page)} />;
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
  if (page?.type === "evaluaciones-slack") {
    return (
      <EvaluacionesSlackPage
        token={token}
        user={user}
        advisees={[]}
        onBack={() => navigate(null)}
        onNavigate={navigate}
      />
    );
  }
  if (page?.type === "historial-evaluaciones") {
    return (
      <HistorialEvaluacionesPage
        token={token}
        evaluado={page.evaluado}
        evaluador={page.evaluador}
        proyecto={page.proyecto}
        onBack={() => navigate({ type: "evaluaciones-slack" })}
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

createRoot(document.getElementById("root")).render(<App />);
