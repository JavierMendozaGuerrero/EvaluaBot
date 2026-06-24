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
                  {informeFinal.pdfUrl && (
                    <button className="secondary" onClick={() => openFile(informeFinal.pdfUrl, "informe_final.pdf")}>
                      Descargar PDF
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
                <p className="opinion-fecha fine">{obj.fecha ? obj.fecha.slice(0, 10) : "Sin fecha"}{obj.ca ? ` — ${obj.ca}` : ""}</p>
                <p className="objetivo-texto">{obj.objetivos}</p>
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
  const [texto, setTexto] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");

  useEffect(() => {
    apiRequest(`/api/objetivos?nombre=${encodeURIComponent(advisee.nombre)}`, { token })
      .then((data) => setObjetivos(data.objetivos || []))
      .catch((err) => setError(err.message))
      .finally(() => setLoading(false));
  }, [token, advisee.nombre]);

  async function guardar(e) {
    e.preventDefault();
    if (!texto.trim()) return;
    setError("");
    setSuccess("");
    setSaving(true);
    try {
      await apiRequest("/api/objetivos", { token, method: "POST", body: { nombre: advisee.nombre, objetivos: texto.trim() } });
      const data = await apiRequest(`/api/objetivos?nombre=${encodeURIComponent(advisee.nombre)}`, { token });
      setObjetivos(data.objetivos || []);
      setTexto("");
      setSuccess("Objetivos guardados correctamente.");
    } catch (err) {
      setError(err.message);
    } finally {
      setSaving(false);
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
          <h2>Nuevos objetivos</h2>
          {error && <p className="error">{error}</p>}
          {success && <p className="fine">{success}</p>}
          <label>Redacta los objetivos</label>
          <textarea
            className="objetivos-textarea"
            value={texto}
            onChange={(e) => setTexto(e.target.value)}
            rows={8}
            placeholder="Escribe aqui los objetivos para este periodo..."
          />
          <div className="actions">
            <button type="submit" disabled={saving || !texto.trim()}>
              {saving ? "Guardando..." : "Guardar objetivos"}
            </button>
          </div>
        </form>
      </section>

      <section className="objetivos-historial panel">
        <p className="kicker">Historial</p>
        <h2>Objetivos anteriores</h2>
        {loading ? (
          <p>Cargando...</p>
        ) : objetivos.length ? (
          <div className="objetivos-list">
            {objetivos.map((obj, i) => (
              <article key={i} className="objetivo-item">
                <p className="opinion-fecha fine">{obj.fecha ? obj.fecha.slice(0, 10) : "Sin fecha"}</p>
                <p className="objetivo-texto">{obj.objetivos}</p>
              </article>
            ))}
          </div>
        ) : (
          <p>No hay objetivos anteriores para {advisee.nombre}.</p>
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
                    {informeFinalEmpleado.pdfUrl && (
                      <button className="secondary" onClick={() => openFile(informeFinalEmpleado.pdfUrl, "informe_final.pdf")}>
                        Descargar PDF
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
              <p className="profile-obj-text">{misObjetivos[0].objetivos}</p>
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
                  {informeFinalAdmin.pdfUrl && <button className="secondary" onClick={() => openFile(informeFinalAdmin.pdfUrl, "informe_final.pdf")}>Descargar PDF</button>}
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
              {informeActual.pdfUrl && <button className="secondary" onClick={() => openFile(informeActual.pdfUrl, "informe_final.pdf")}>Descargar PDF</button>}
            </div>
          </div>
        )}
        <form className="panel" onSubmit={subir}>
          <h2>Subir versión final</h2>
          <p>Sube el PDF con tu versión final. Se guarda en Notion y el advisee podrá descargarlo. Se mantienen las 2 versiones más recientes.</p>
          <label>Archivo PDF (.pdf)</label>
          <input
            type="file"
            accept=".pdf,application/pdf"
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
            {links.pdfUrl && <button className="secondary" onClick={() => openFile(links.pdfUrl, "informe_final.pdf")}>Descargar PDF</button>}
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

function App() {
  const resetToken = getResetToken();
  const [token, setToken] = useState(localStorage.getItem("evaluabot_token") || sessionStorage.getItem("evaluabot_token") || "");
  const [user, setUser] = useState(null);
  const [page, setPage] = useState(null);
  const [adminMode, setAdminMode] = useState(null); // null | "personal" | "admin"

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
