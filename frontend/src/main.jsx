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
        <a className="brand" href="/">igeneris</a>
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
        <a className="brand" href="/">igeneris</a>
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
        <a className="brand" href="/">igeneris</a>
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
    </main>
  );
}

function AuthScreen({ onLogin }) {
  const resetToken = getResetToken();
  const [mode, setMode] = useState(resetToken ? "reset" : "login");
  const [form, setForm] = useState({ username: "", email: "", password: "", confirmPassword: "", newPassword: "", confirmNewPassword: "" });
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

function Dashboard({ token, user, onLogout, onNavigate }) {
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

  useEffect(() => {
    apiRequest("/api/evaluados", { token })
      .then((data) => {
        setEvaluados(data.evaluados || []);
        setEvaluado(data.evaluados?.[0]?.value || "");
      })
      .catch((err) => setStatus(err.message));
  }, [token]);

  useEffect(() => {
    apiRequest("/api/mis-advisees", { token })
      .then((data) => setAdvisees(data.advisees || []))
      .catch(() => {});
  }, [token]);

  useEffect(() => {
    if (!isAdmin) return;
    apiRequest("/api/evaluados-anual", { token })
      .then((data) => {
        const lista = data.evaluados || [];
        setEvaluadosAnual(lista);
        if (lista.length) setEvaluadoAnual(lista[0].value);
      })
      .catch(() => {});
  }, [token, isAdmin]);

  useEffect(() => {
    apiRequest("/api/acceso-advisees", { token })
      .then((data) => setAccesoActivo(data.activo || false))
      .catch(() => {});
  }, [token]);

  useEffect(() => {
    if (isAdmin) return;
    const persona = user?.persona || "";
    if (!persona) return;
    apiRequest(`/api/informe-final?evaluado=${encodeURIComponent(persona)}`, { token })
      .then((data) => setInformeFinalEmpleado(data))
      .catch(() => setInformeFinalEmpleado({ disponible: false, mensaje: "No se pudo cargar el informe." }));
  }, [token, isAdmin, user?.persona]);

  useEffect(() => {
    if (!isAdmin || adminModo !== "final" || !evaluado) return;
    setInformeFinalAdmin(null);
    apiRequest(`/api/informe-final?evaluado=${encodeURIComponent(evaluado)}`, { token })
      .then((data) => setInformeFinalAdmin(data))
      .catch(() => setInformeFinalAdmin({ disponible: false, mensaje: "No se pudo cargar el informe." }));
  }, [token, isAdmin, adminModo, evaluado]);

  const role = isAdmin ? "Admin" : "";
  const ownEvaluado = user?.persona || user?.username || "";
  const targetEvaluado = isAdmin ? evaluado : (evaluado || ownEvaluado);
  const selectedLabel = useMemo(() => evaluados.find((item) => item.value === evaluado)?.label || "", [evaluados, evaluado]);

  async function generate(kind) {
    setLinks(null);
    setStatus(kind === "generar" ? "Claude esta generando el informe..." : "Preparando trayectoria visual...");
    try {
      const body = { evaluado: targetEvaluado };
      if (kind === "generar" && cargoAnual) body.cargo = cargoAnual;
      const data = await apiRequest(`/api/${kind}`, { token, method: "POST", body });
      setStatus(kind === "generar" ? `Informe listo con ${data.total} evaluaciones.` : `Trayectoria lista con ${data.total} evaluaciones.`);
      setLinks(data);
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

  return (
    <main className="page">
      <nav className="nav">
        <a className="brand" href="/">igeneris</a>
        <div className="nav-links">
          <span>{user?.username}</span>
          {role && <span>{role}</span>}
          <button className="link-button" onClick={() => onNavigate({ type: "mis-objetivos" })}>Mis objetivos</button>
          <button className="link-button" onClick={onLogout}>Cerrar sesion</button>
        </div>
      </nav>

      <section className="hero dashboard-hero">
        <div>
          <p className="kicker">Desarrollo de talento</p>
          <h1>Centro de evaluaciones.</h1>
        </div>
        {isAdmin && (
          <div className="panel">
            <p className="lead">Genera informes y trayectorias visuales a partir del feedback guardado en Notion.</p>
            <label>Persona evaluada</label>
            <select value={evaluado} onChange={(e) => setEvaluado(e.target.value)}>
              {evaluados.map((item) => <option key={item.value} value={item.value}>{item.label}</option>)}
            </select>
            <p className="fine">Seleccion actual: {selectedLabel || "sin tabla disponible"}</p>
          </div>
        )}
      </section>

      {isAdmin ? (
        <>
          <div className="actions" style={{ marginTop: "24px" }}>
            <button onClick={() => setAdminModo("borrador")} className={adminModo === "borrador" ? "" : "secondary"}>Borrador de Claude</button>
            <button onClick={() => setAdminModo("final")} className={adminModo === "final" ? "" : "secondary"}>Versión final CA</button>
          </div>
          {adminModo === "borrador" ? (
            <section className="tools">
              <article className="tool">
                <p className="kicker">Informe anual</p>
                <h2>Informe anual{targetEvaluado ? ` de ${targetEvaluado}` : ""}</h2>
                <p>Realiza una base para la realizacion del informe anual de evaluaciones.</p>
                <button onClick={() => generate("generar")} disabled={!targetEvaluado}>Generar informe anual</button>
              </article>
              <article className="tool wrapped">
                <p className="kicker">Trayectoria</p>
                <h2>Vista tipo wrapped</h2>
                <p>Navega por fechas, proyecto, satisfaccion y comentarios clave.</p>
                <button className="secondary" onClick={() => generate("trayectoria")} disabled={!targetEvaluado}>Generar trayectoria</button>
              </article>
            </section>
          ) : (
            <section className="tools panel" style={{ marginTop: "24px" }}>
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
            </section>
          )}
        </>
      ) : (
        <section className="tools" style={{ marginTop: "24px" }}>
          <article className="tool">
            <p className="kicker">Mi informe final</p>
            <h2>Informe final</h2>
            <p>El informe anual elaborado por tu CA con el feedback recibido durante el año.</p>
            {informeFinalEmpleado === null ? (
              <p className="fine">Cargando...</p>
            ) : informeFinalEmpleado?.disponible ? (
              <div className="actions">
                {informeFinalEmpleado.htmlUrl && <button onClick={() => openFile(informeFinalEmpleado.htmlUrl, "informe_final.html")}>Ver web</button>}
                {informeFinalEmpleado.docxUrl && <button className="secondary" onClick={() => openFile(informeFinalEmpleado.docxUrl, "informe_final.docx")}>Descargar Word</button>}
              </div>
            ) : (
              <p className="fine">No tienes acceso.</p>
            )}
          </article>
          <article className="tool wrapped">
            <p className="kicker">Resumen del año</p>
            <h2>Tu trayectoria</h2>
            <p>Navega por fechas, proyecto, satisfacción y comentarios clave de tus evaluaciones.</p>
            {informeFinalEmpleado === null ? (
              <p className="fine">Cargando...</p>
            ) : informeFinalEmpleado?.accesoActivo ? (
              <button className="secondary" onClick={() => generate("trayectoria")} disabled={!ownEvaluado}>
                {status && status.includes("trayectoria") ? "Generando..." : "Ver trayectoria"}
              </button>
            ) : (
              <p className="fine">No tienes acceso.</p>
            )}
          </article>
        </section>
      )}

      {status && <section className="status panel"><p>{status}</p></section>}
      {links && adminModo === "borrador" && (
        <section className="result panel">
          <h2>Resultado</h2>
          <div className="actions">
            {links.htmlUrl && <button onClick={() => openFile(links.htmlUrl, "informe.html")}>Abrir web</button>}
            {links.docxAnualUrl && <button className="secondary" onClick={() => downloadAnual(links.docxAnualUrl)}>Descargar informe anual</button>}
          </div>
        </section>
      )}

      {advisees.length > 0 && (
        <section className="advisees-section panel">
          <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", flexWrap: "wrap", gap: "12px", marginBottom: "18px" }}>
            <div>
              <p className="kicker">Career Advisor</p>
              <h2 style={{ margin: 0 }}>Mis advisees</h2>
            </div>
            {!isAdmin && (
              <button
                className={accesoActivo ? "" : "secondary"}
                onClick={toggleAcceso}
                disabled={togglingAcceso}
                style={{ alignSelf: "center" }}
              >
                {togglingAcceso ? "..." : accesoActivo ? "Acceso activo — revocar" : "Dar acceso a mis advisees"}
              </button>
            )}
          </div>
          <div className="advisees-list">
            {advisees.map((a) => (
              <div key={a.nombre} className="advisee-card">
                {a.foto
                  ? <img src={a.foto} alt={a.nombre} className="advisee-foto" />
                  : <div className="advisee-foto advisee-foto-placeholder">{a.nombre.charAt(0)}</div>
                }
                <span className="advisee-nombre">{a.nombre}</span>
                <button className="secondary advisee-btn" onClick={() => loadOpiniones(a.nombre)} disabled={loadingOpiniones}>
                  Opiniones
                </button>
                <button className="secondary advisee-btn" onClick={() => onNavigate({ type: "objetivos", advisee: a })}>
                  Meter objetivos
                </button>
                <button className="secondary advisee-btn" onClick={() => onNavigate({ type: "informes-advisee", advisee: a })}>
                  Ver informes
                </button>
                <button className="secondary advisee-btn" onClick={() => onNavigate({ type: "subir-informe", advisee: a })}>
                  Subir informe
                </button>
              </div>
            ))}
          </div>
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
        <a className="brand" href="/">igeneris</a>
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
          <p>Sube el Word con tu versión final. Se convierte a HTML automáticamente y se guarda en Notion. Se mantienen las 2 versiones más recientes.</p>
          <label>Archivo Word (.docx)</label>
          <input
            type="file"
            accept=".docx,application/vnd.openxmlformats-officedocument.wordprocessingml.document"
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
    </main>
  );
}

function App() {
  const resetToken = getResetToken();
  const [token, setToken] = useState(localStorage.getItem("evaluabot_token") || "");
  const [user, setUser] = useState(null);
  const [page, setPage] = useState(null);

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
  if (page?.type === "informes-advisee") {
    return (
      <InformesAdvisee
        token={token}
        advisee={page.advisee}
        onBack={() => setPage(null)}
      />
    );
  }
  if (page?.type === "mis-objetivos") {
    return (
      <MisObjetivosPage
        token={token}
        persona={user?.persona || user?.username || ""}
        onBack={() => setPage(null)}
      />
    );
  }
  if (page?.type === "objetivos") {
    return (
      <ObjetivosPage
        token={token}
        advisee={page.advisee}
        caName={user?.persona || ""}
        onBack={() => setPage(null)}
      />
    );
  }
  if (page?.type === "subir-informe") {
    return (
      <SubirInformePage
        token={token}
        advisee={page.advisee}
        onBack={() => setPage(null)}
      />
    );
  }
  return (
    <Dashboard
      token={token}
      user={user}
      onLogout={() => { localStorage.removeItem("evaluabot_token"); setToken(""); setUser(null); setPage(null); }}
      onNavigate={setPage}
    />
  );
}

createRoot(document.getElementById("root")).render(<App />);
