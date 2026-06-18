import React, { useEffect, useMemo, useState } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";

const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";

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

function isStrongPassword(password) {
  return password.length >= 8 && /[A-Z]/.test(password) && /[^A-Za-z0-9]/.test(password);
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

function AuthScreen({ onLogin }) {
  const resetToken = getResetToken();
  const [mode, setMode] = useState(resetToken ? "reset" : "login");
  const [form, setForm] = useState({ username: "", email: "", password: "", confirmPassword: "", newPassword: "", confirmNewPassword: "", adminCode: "" });
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
        localStorage.setItem("evaluabot_token", data.token);
        onLogin(data.token, data.user);
      }
    } catch (err) {
      setError(err.message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="page auth-page">
      <nav className="nav">
        <a className="brand" href="/">igeneris</a>
        <button className="link-button" onClick={() => { setError(""); setMessage(""); setMode(mode === "login" ? "register" : "login"); }}>
          {mode === "login" ? "Registro" : "Login"}
        </button>
      </nav>
      <section className="hero">
        <div>
          <p className="kicker">Evaluaciones internas</p>
          <h1>{mode === "forgot" ? "Recupera tu acceso." : mode === "reset" ? "Crea una contrasena nueva." : mode === "login" ? "Accede a tus informes." : "Registra tu usuario."}</h1>
          <p className="lead">Consulta informes, trayectorias y feedback con permisos por persona.</p>
        </div>
        <form className="panel auth-form" onSubmit={submit}>
          <h2>{mode === "forgot" ? "Enviar email" : mode === "reset" ? "Cambiar contrasena" : mode === "login" ? "Entrar" : "Crear cuenta"}</h2>
          {error && <p className="error">{error}</p>}
          {message && <p className="fine">{message}</p>}
          {mode === "forgot" ? (
            <>
              <label>Email</label>
              <input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} required />
            </>
          ) : mode === "reset" ? (
            <>
              <label>Nueva contrasena</label>
              <PasswordInput value={form.newPassword} onChange={(e) => setForm({ ...form, newPassword: e.target.value })} minLength={8} />
              <label>Repite la contrasena</label>
              <PasswordInput value={form.confirmNewPassword} onChange={(e) => setForm({ ...form, confirmNewPassword: e.target.value })} minLength={8} />
            </>
          ) : (
            <>
              <label>{mode === "login" ? "Usuario o email" : "Usuario"}</label>
              <input value={form.username} onChange={(e) => setForm({ ...form, username: e.target.value })} required />
              <label>Contrasena</label>
              <PasswordInput value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} minLength={mode === "register" ? 8 : undefined} />
              {mode === "register" && (
                <>
                  <label>Repite la contrasena</label>
                  <PasswordInput value={form.confirmPassword} onChange={(e) => setForm({ ...form, confirmPassword: e.target.value })} minLength={8} />
                </>
              )}
            </>
          )}
          {(mode === "register" || mode === "reset") && (
            <p className={(passwordInvalid || passwordsMismatch) ? "error fine" : "fine"}>
              Minimo 8 caracteres, una mayuscula y un caracter especial. Las contrasenas deben coincidir.
            </p>
          )}
          {mode === "register" && (
            <>
              <label>Clave admin</label>
              <PasswordInput placeholder="Solo Ana" value={form.adminCode} onChange={(e) => setForm({ ...form, adminCode: e.target.value })} required={false} />
            </>
          )}
          <div className="actions">
            <button type="submit" disabled={!canSubmit}>
              {loading ? "Procesando..." : mode === "forgot" ? "Enviar enlace" : mode === "reset" ? "Guardar contrasena" : mode === "login" ? "Entrar" : "Crear cuenta"}
            </button>
            {mode === "login" && <button type="button" className="secondary" onClick={() => { setError(""); setMessage(""); setMode("forgot"); }}>Olvide mi contrasena</button>}
            {(mode === "forgot" || mode === "reset") && <button type="button" className="secondary" onClick={() => { window.history.replaceState({}, "", window.location.pathname); setError(""); setMessage(""); setMode("login"); }}>Volver</button>}
          </div>
        </form>
      </section>
    </main>
  );
}

function Dashboard({ token, user, onLogout }) {
  const [evaluados, setEvaluados] = useState([]);
  const [evaluado, setEvaluado] = useState("");
  const [status, setStatus] = useState("");
  const [links, setLinks] = useState(null);
  const [revision, setRevision] = useState(null);

  useEffect(() => {
    apiRequest("/api/evaluados", { token })
      .then((data) => {
        setEvaluados(data.evaluados || []);
        setEvaluado(data.evaluados?.[0]?.value || "");
      })
      .catch((err) => setStatus(err.message));
  }, [token]);

  useEffect(() => {
    if (!user?.is_admin) return;
    loadRevision();
  }, [token, user?.is_admin]);

  const role = user?.is_admin ? "Admin" : `Solo ${user?.persona || user?.username}`;
  const selectedLabel = useMemo(() => evaluados.find((item) => item.value === evaluado)?.label || "", [evaluados, evaluado]);

  async function generate(kind) {
    setLinks(null);
    setStatus(kind === "generar" ? "Claude esta generando el informe..." : "Preparando trayectoria visual...");
    try {
      const data = await apiRequest(`/api/${kind}`, { token, method: "POST", body: { evaluado } });
      setStatus(kind === "generar" ? `Informe listo con ${data.total} evaluaciones.` : `Trayectoria lista con ${data.total} evaluaciones.`);
      setLinks(data);
    } catch (err) {
      setStatus(err.message);
    }
  }

  async function loadRevision() {
    try {
      const data = await apiRequest("/api/revision-pendiente", { token });
      setRevision(data);
    } catch (err) {
      setStatus(err.message);
    }
  }

  async function sendPending(pendingId) {
    setStatus("Enviando evaluacion a Slack...");
    try {
      await apiRequest("/api/revision-pendiente/enviar", {
        token,
        method: "POST",
        body: { pendingId },
      });
      setStatus("Evaluacion enviada a Slack.");
      await loadRevision();
    } catch (err) {
      setStatus(err.message);
    }
  }

  async function openFile(path, filename) {
    setStatus("Abriendo archivo protegido...");
    try {
      const response = await fetch(apiUrl(path), {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || "No se pudo abrir el archivo.");
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      if (filename.endsWith(".docx")) {
        const link = document.createElement("a");
        link.href = url;
        link.download = filename;
        link.click();
        URL.revokeObjectURL(url);
      } else {
        window.open(url, "_blank", "noopener,noreferrer");
      }
      setStatus("Archivo listo.");
    } catch (err) {
      setStatus(err.message);
    }
  }

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/">igeneris</a>
        <div className="nav-links">
          <span>{user?.username}</span>
          <span>{role}</span>
          <button className="link-button" onClick={onLogout}>Cerrar sesion</button>
        </div>
      </nav>

      <section className="hero dashboard-hero">
        <div>
          <p className="kicker">People analytics</p>
          <h1>Centro de evaluaciones.</h1>
        </div>
        <div className="panel">
          <p className="lead">Genera informes y trayectorias visuales a partir del feedback guardado en Notion.</p>
          <label>Persona evaluada</label>
          <select value={evaluado} onChange={(e) => setEvaluado(e.target.value)}>
            {evaluados.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
          </select>
          <p className="fine">Seleccion actual: {selectedLabel || "sin tabla disponible"}</p>
        </div>
      </section>

      <section className="tools">
        <article className="tool">
          <p className="kicker">Informe</p>
          <h2>Documento ejecutivo</h2>
          <p>Analisis con Claude y descarga en Word cuando hay evaluaciones nuevas.</p>
          <button onClick={() => generate("generar")} disabled={!evaluado}>Generar informe</button>
        </article>
        <article className="tool wrapped">
          <p className="kicker">Trayectoria</p>
          <h2>Vista tipo wrapped</h2>
          <p>Navega por fechas, proyecto, satisfaccion y comentarios clave.</p>
          <button className="secondary" onClick={() => generate("trayectoria")} disabled={!evaluado}>Generar trayectoria</button>
        </article>
      </section>

      {status && <section className="status panel"><p>{status}</p></section>}
      {links && (
        <section className="result panel">
          <h2>Resultado</h2>
          <div className="actions">
            {links.htmlUrl && <button onClick={() => openFile(links.htmlUrl, "informe.html")}>Abrir web</button>}
            {links.docxUrl && <button className="secondary" onClick={() => openFile(links.docxUrl, "informe.docx")}>Descargar Word</button>}
          </div>
        </section>
      )}

      {user?.is_admin && revision && (
        <section className="review panel">
          <p className="kicker">Revision previa</p>
          <h2>Evaluaciones de Slack</h2>
          {revision.pendientes?.length ? (
            <div className="pending-list">
              {revision.pendientes.map((item) => (
                <article className="pending" key={item.id}>
                  <p><strong>{item.creada}</strong></p>
                  <p>{item.origen}</p>
                  <button onClick={() => sendPending(item.id)}>Enviar evaluacion</button>
                </article>
              ))}
            </div>
          ) : (
            <p>No hay evaluaciones pendientes de revision.</p>
          )}
          <button className="secondary" onClick={loadRevision}>Actualizar</button>
        </section>
      )}
    </main>
  );
}

function App() {
  const resetToken = getResetToken();
  const [token, setToken] = useState(localStorage.getItem("evaluabot_token") || "");
  const [user, setUser] = useState(null);

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

  if (resetToken || !token || !user) {
    return <AuthScreen onLogin={(newToken, newUser) => { setToken(newToken); setUser(newUser); }} />;
  }
  return <Dashboard token={token} user={user} onLogout={() => { localStorage.removeItem("evaluabot_token"); setToken(""); setUser(null); }} />;
}

createRoot(document.getElementById("root")).render(<App />);
