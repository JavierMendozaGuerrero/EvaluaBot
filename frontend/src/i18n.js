// ---------------------------------------------------------------------------
// i18n de la web. El idioma sale de /api/me (columna Idioma de Notion).
// Por defecto "es". El contenido escrito en Notion NO se traduce aqui.
// Catalogo STRINGS: clave -> { es, en }. Placeholders con {nombre}.
// Para anadir un idioma nuevo: sumar su codigo aqui y su traduccion en cada clave.
// ---------------------------------------------------------------------------

const LANG_KEY = "evaluabot_lang";
export const IDIOMAS = ["es", "en", "pt"];
function _norm(l) { return IDIOMAS.includes(l) ? l : "es"; }

let _lang = "es";
const _langListeners = new Set();

// Overlay PT cargado bajo demanda: pt.js (461 líneas) solo se descarga cuando el
// usuario usa portugués, no en el bundle inicial. Mientras carga, t() cae a ES.
let _ptCargado = false;
function _ensurePtLoaded() {
  if (_ptCargado) return;
  _ptCargado = true;
  import("./pt").then(({ PT }) => {
    for (const k in PT) {
      if (STRINGS[k] && PT[k]) STRINGS[k].pt = PT[k];
    }
    _notifyLang();  // re-render para pintar las cadenas PT ya fusionadas
  }).catch(() => { _ptCargado = false; });
}

// Al cargar: si hay elección manual guardada, tiene prioridad sobre el idioma de Notion.
try {
  const guardado = localStorage.getItem(LANG_KEY);
  if (IDIOMAS.includes(guardado)) _lang = guardado;
} catch {}
if (_lang === "pt") _ensurePtLoaded();

function _notifyLang() {
  for (const fn of _langListeners) { try { fn(_lang); } catch {} }
}

// Suscripción para forzar re-render al cambiar de idioma. Devuelve función para desuscribir.
export function subscribeLang(fn) { _langListeners.add(fn); return () => _langListeners.delete(fn); }

// Fija el idioma SIN persistir (usado por /api/me con el idioma de Notion).
export function setLang(l) {
  const nl = _norm(l);
  if (nl === "pt") _ensurePtLoaded();
  if (nl === _lang) return;
  _lang = nl;
  _notifyLang();
}

// Elección manual del selector: fija, persiste y notifica.
export function setLangManual(l) {
  const nl = _norm(l);
  if (nl === "pt") _ensurePtLoaded();
  try { localStorage.setItem(LANG_KEY, nl); } catch {}
  if (nl !== _lang) { _lang = nl; }
  _notifyLang();
}

export function getLang() { return _lang; }

const MESES = {
  es: ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto", "Septiembre", "Octubre", "Noviembre", "Diciembre"],
  en: ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
  pt: ["Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"],
};
export function nombreMes(idx) {
  const arr = MESES[_lang] || MESES.es;
  return arr[idx] || "";
}

export function t(clave, vars) {
  const entrada = STRINGS[clave];
  if (!entrada) return clave;
  let s = entrada[_lang] || entrada.es || clave;
  if (vars) for (const k in vars) s = s.split(`{${k}}`).join(vars[k]);
  return s;
}

// ¿Está esta clave en el catálogo? t() devuelve la propia clave cuando no lo está, y hay
// sitios (los `code` de error del backend) donde eso se le pintaría al usuario.
export function tieneClave(clave) { return Object.prototype.hasOwnProperty.call(STRINGS, clave); }

export const STRINGS = {
  // --- Comunes / reutilizados ---
  "common.back": { es: "← Volver", en: "← Back" },
  "common.home": { es: "Inicio", en: "Home" },
  "common.logout": { es: "Cerrar sesión", en: "Log out" },
  "common.cancel": { es: "Cancelar", en: "Cancel" },
  "common.save": { es: "Guardar", en: "Save" },
  "common.loading": { es: "Cargando…", en: "Loading…" },
  "pw.show": { es: "Mostrar contraseña", en: "Show password" },
  "pw.hide": { es: "Ocultar contraseña", en: "Hide password" },
  "common.no_date": { es: "Sin fecha", en: "No date" },
  "common.err_generic": { es: "No se pudo completar la acción.", en: "The action could not be completed." },

  // --- Errores del backend, por el `code` que devuelve la API (ver backend/ia.py).
  // Los busca mensajeDeError() en main.jsx. Si un código no está aquí, se pinta el
  // texto en español que manda el backend: sigue siendo comprensible, no es un fallo.
  "err.ia_sin_saldo": { es: "La API de Claude asociada a esta herramienta se ha quedado sin saldo. Contacta con el organizador de la cuenta de Claude (tech@igeneris.com) o con el responsable de la herramienta.", en: "The Claude API used by this tool has run out of credit. Please contact the Claude account owner (tech@igeneris.com) or the tool's owner." },
  "err.ia_config": { es: "La API de Claude asociada a esta herramienta no está bien configurada y ha rechazado la petición. Contacta con el responsable de la herramienta (tech@igeneris.com).", en: "The Claude API used by this tool is misconfigured and rejected the request. Please contact the tool's owner (tech@igeneris.com)." },
  "err.ia_no_configurada": { es: "La IA no está disponible: a esta herramienta le falta la clave de la API de Claude. Contacta con el responsable de la herramienta (tech@igeneris.com).", en: "The AI is unavailable: this tool is missing the Claude API key. Please contact the tool's owner (tech@igeneris.com)." },
  "err.ia_saturada": { es: "La IA está saturada en este momento. Espera un par de minutos y vuelve a intentarlo; si sigue fallando, avisa al responsable de la herramienta (tech@igeneris.com).", en: "The AI is overloaded right now. Wait a couple of minutes and try again; if it keeps failing, let the tool's owner know (tech@igeneris.com)." },
  "err.ia_conexion": { es: "No se ha podido conectar con la IA. Comprueba tu conexión y vuelve a intentarlo; si sigue fallando, avisa al responsable de la herramienta (tech@igeneris.com).", en: "Could not connect to the AI. Check your connection and try again; if it keeps failing, let the tool's owner know (tech@igeneris.com)." },
  "err.ia_entrada_larga": { es: "Hay demasiada información para que la IA la procese de una vez. Acorta el texto y vuelve a intentarlo; si no puedes, avisa al responsable de la herramienta (tech@igeneris.com).", en: "There is too much information for the AI to process at once. Shorten the text and try again; if you can't, let the tool's owner know (tech@igeneris.com)." },
  "err.ia_error": { es: "La IA no ha podido responder ahora mismo. Vuelve a intentarlo; si sigue fallando, avisa al responsable de la herramienta (tech@igeneris.com).", en: "The AI could not answer right now. Try again; if it keeps failing, let the tool's owner know (tech@igeneris.com)." },
  "err.error_inesperado": { es: "Ha ocurrido un error inesperado y la acción no se ha completado. Vuelve a intentarlo; si sigue fallando, avisa al responsable de la herramienta (tech@igeneris.com).", en: "Something unexpected went wrong and the action was not completed. Try again; if it keeps failing, let the tool's owner know (tech@igeneris.com)." },

  // --- Panel admin ---
  "admin.reports": { es: "Informes", en: "Reports" },
  "admin.view_final_report": { es: "Ver informe final", en: "View final report" },
  "admin.download_word": { es: "Descargar Word", en: "Download Word" },
  "admin.no_final_report": { es: "Sin informe final disponible.", en: "No final report available." },
  "admin.search_employee": { es: "Buscar empleado", en: "Search employee" },
  "admin.search_placeholder": { es: "Escribe un nombre...", en: "Type a name..." },
  "admin.no_results": { es: "No hay resultados para “{q}”.", en: "No results for “{q}”." },
  "admin.err_load_report": { es: "No se pudo cargar el informe.", en: "Could not load the report." },
  "admin.err_download": { es: "No se pudo descargar el archivo.", en: "Could not download the file." },
  // Descarga de PDFs de fuentes en el panel de admin. No reutilizan las claves admin.eval_type_*
  // (esas son "Mensual"/"Proyecto", etiquetas de tipo, no rótulos de botón).
  "admin.available_info": { es: "Información disponible", en: "Available information" },
  "admin.dl_monthly_evals": { es: "Evaluaciones mensuales", en: "Monthly evaluations" },
  "admin.dl_proj_evals": { es: "Evaluaciones de proyecto", en: "Project evaluations" },
  "admin.dl_personal_tracking": { es: "Seguimiento personal", en: "Personal tracking" },
  "admin.confidential_feedback_title": { es: "Feedback confidencial (solo Head of People)", en: "Confidential feedback (Head of People only)" },
  "admin.confidential_feedback_note": { es: "Evaluaciones de personas de su equipo hacia esta persona. Totalmente privado y anónimo: no se comparte con su CA ni con nadie más de la empresa.", en: "Feedback from this person's team members about them. Totally private and anonymous: never shared with their CA or anyone else in the company." },
  "admin.confidential_feedback_empty": { es: "No hay feedback confidencial registrado.", en: "No confidential feedback recorded." },
  "admin.confidential_feedback_all_btn": { es: "Ver todas las evaluaciones confidenciales (bottom to top)", en: "View all confidential evaluations (bottom to top)" },
  "admin.confidential_feedback_all_note": { es: "Evaluaciones de equipos hacia sus responsables. Totalmente privado y anónimo: no se comparte con ningún CA ni con nadie más de la empresa.", en: "Feedback from teams about their leads. Totally private and anonymous: never shared with any CA or anyone else in the company." },
  "admin.confidential_feedback_search_ph": { es: "Filtrar por persona o proyecto...", en: "Filter by person or project..." },
  "admin.eval_count_tooltip": { es: "Evaluaciones realizadas / asignadas este ciclo", en: "Evaluations completed / assigned this cycle" },
  "admin.eval_compliance_title": { es: "Cumplimiento de evaluaciones", en: "Evaluation compliance" },
  "admin.eval_compliance_note": { es: "Por ciclo y tipo: evaluaciones realizadas / asignadas a esta persona.", en: "By cycle and type: evaluations completed / assigned to this person." },
  "admin.eval_compliance_empty": { es: "Aún no hay evaluaciones asignadas registradas.", en: "No assigned evaluations recorded yet." },
  "admin.eval_cycle": { es: "Ciclo", en: "Cycle" },
  "admin.eval_type_mensual": { es: "Mensual", en: "Monthly" },
  "admin.eval_type_personal": { es: "Seguimiento personal", en: "Personal follow-up" },
  "admin.eval_type_ca": { es: "CA reviews", en: "CA reviews" },
  "admin.eval_type_proyecto": { es: "Proyecto", en: "Project" },
  "admin.eval_type_extra": { es: "Extra", en: "Extra" },

  // --- Objetivos ---
  "obj.personal_dev": { es: "Desarrollo personal", en: "Personal development" },
  "obj.my_goals_title": { es: "Mis objetivos.", en: "My goals." },
  "obj.kpis_label": { es: "KPIs:", en: "KPIs:" },
  "obj.none_yet": { es: "Todavía no tienes objetivos registrados.", en: "You don’t have any goals recorded yet." },

  // --- Objetivos: alta/edicion (CA) ---
  "common.saving": { es: "Guardando...", en: "Saving..." },
  "common.delete": { es: "Eliminar", en: "Delete" },
  "common.deleting": { es: "Eliminando...", en: "Deleting..." },
  "goals.confirm_delete": { es: "¿Eliminar este objetivo? Dejará de estar activo y pasará a objetivos antiguos.", en: "Delete this goal? It will no longer be active and will move to past goals." },
  "goals.kicker": { es: "Objetivos", en: "Goals" },
  "goals.new": { es: "Nuevo objetivo", en: "New goal" },
  "goals.remove_aria": { es: "Quitar objetivo", en: "Remove goal" },
  "goals.title_label": { es: "Título *", en: "Title *" },
  "goals.title_ph": { es: "Ej: Mejorar habilidades de presentación", en: "e.g. Improve presentation skills" },
  "goals.type_label": { es: "Tipo", en: "Type" },
  "goals.type_ph": { es: "Ej: CTTF, proyecto", en: "e.g. CTTF, project" },
  "goals.kpis_field_label": { es: "KPIs para su cumplimiento", en: "KPIs to achieve it" },
  "goals.kpis_ph": { es: "Ej: Presentar en 2 reuniones de cliente al trimestre", en: "e.g. Present at 2 client meetings per quarter" },
  "goals.desc_label": { es: "Descripción", en: "Description" },
  "goals.desc_ph": { es: "Detalla cómo trabajar este objetivo...", en: "Describe how to work on this goal..." },
  "goals.add_another": { es: "+ Añadir otro", en: "+ Add another" },
  "goals.save_many": { es: "Guardar {n} objetivos", en: "Save {n} goals" },
  "goals.save_one": { es: "Guardar objetivos", en: "Save goals" },
  "goals.saved_title": { es: "Objetivos guardados", en: "Goals saved" },
  "goals.of_person": { es: "Objetivos de {nombre}", en: "{nombre}’s goals" },
  "goals.current": { es: "Objetivos actuales", en: "Current goals" },
  "goals.old": { es: "Objetivos antiguos", en: "Past goals" },
  "goals.none_current": { es: "{nombre} no tiene objetivos activos.", en: "{nombre} has no active goals." },
  "goals.none_old": { es: "Todavía no hay objetivos antiguos.", en: "No past goals yet." },
  "goals.closed_by": { es: "Eliminado por {quien} · {fecha}", en: "Deleted by {quien} · {fecha}" },
  "goals.go_form": { es: "→ ¿Quieres introducirle nuevos objetivos a {nombre}?", en: "→ Want to set new goals for {nombre}?" },
  "goals.go_history": { es: "→ ¿Quieres ver los objetivos que ya tiene {nombre}?", en: "→ Want to see {nombre}’s existing goals?" },

  // --- Footer ---
  "footer.privacy": { es: "Privacidad", en: "Privacy" },
  "footer.terms": { es: "Términos", en: "Terms" },

  // --- Documentos legales (titulos; el cuerpo son .md aparte) ---
  "legal.unavailable": { es: "Documento no disponible.", en: "Document not available." },

  // --- Seleccion de rol (admin) ---
  "role.welcome": { es: "Bienvenida", en: "Welcome" },
  "role.how_enter": { es: "¿Cómo quieres entrar hoy?", en: "How do you want to sign in today?" },
  "role.admin_title": { es: "Administrador", en: "Administrator" },
  "role.admin_desc": { es: "Consulta evaluaciones e informes de cualquier empleado", en: "View evaluations and reports for any employee" },
  "role.personal_title": { es: "Perfil personal", en: "Personal profile" },
  "role.personal_desc": { es: "Accede como cualquier otro empleado de la empresa", en: "Access as any other employee in the company" },

  // --- Login / registro / recuperar contrasena (AuthScreen) ---
  "auth.eyebrow": { es: "Evaluaciones internas", en: "Internal evaluations" },
  "auth.title_verify": { es: "Verificación requerida", en: "Verification required" },
  "auth.account_verified": { es: "Cuenta verificada. Ya puedes iniciar sesión.", en: "Account verified. You can now sign in." },
  "auth.title_forgot": { es: "Recuperar contraseña", en: "Reset password" },
  "auth.title_reset": { es: "Nueva contraseña", en: "New password" },
  "auth.title_login": { es: "Iniciar sesión", en: "Sign in" },
  "auth.title_register": { es: "Crear cuenta", en: "Create account" },
  "auth.desc_forgot": { es: "Introduce tu email corporativo y te enviaremos un enlace para restablecer tu contraseña.", en: "Enter your work email and we’ll send you a link to reset your password." },
  "auth.desc_reset": { es: "Elige una nueva contraseña para tu cuenta.", en: "Choose a new password for your account." },
  "auth.back_to_login": { es: "← Volver al inicio de sesión", en: "← Back to sign in" },
  "auth.back_word": { es: "Volver", en: "Back" },
  "auth.err_weak_pw": { es: "La contraseña debe tener mínimo 8 caracteres, una mayúscula y un carácter especial.", en: "The password must be at least 8 characters and include one uppercase letter and one special character." },
  "auth.err_pw_mismatch": { es: "Las contraseñas no coinciden.", en: "The passwords do not match." },
  "auth.forgot_sent": { es: "Si el email existe, te hemos enviado un enlace para cambiar la contraseña.", en: "If the email exists, we’ve sent you a link to change your password." },
  "auth.pw_updated": { es: "Contraseña actualizada. Ya puedes entrar.", en: "Password updated. You can sign in now." },
  "auth.verify_intro_1": { es: "Por seguridad, hemos enviado un código de 6 dígitos a ", en: "For security, we’ve sent a 6-digit code to " },
  "auth.verify_intro_2": { es: ". Introdúcelo a continuación. Caduca en 10 minutos.", en: ". Enter it below. It expires in 10 minutes." },
  "auth.verify_code_label": { es: "Código de verificación", en: "Verification code" },
  "auth.repeat_pw": { es: "Repite la contraseña", en: "Repeat password" },
  "auth.user_or_email": { es: "Usuario o email", en: "Username or email" },
  "auth.user": { es: "Usuario", en: "Username" },
  "auth.password": { es: "Contraseña", en: "Password" },
  "auth.remember": { es: "Recuérdame", en: "Remember me" },
  "auth.pw_hint": { es: "Mínimo 8 caracteres, una mayúscula y un carácter especial. Las contraseñas deben coincidir.", en: "At least 8 characters, one uppercase letter and one special character. The passwords must match." },
  "auth.processing": { es: "Procesando...", en: "Processing..." },
  "auth.verify_btn": { es: "Verificar", en: "Verify" },
  "auth.send_link": { es: "Enviar enlace", en: "Send link" },
  "auth.save_pw": { es: "Guardar contraseña", en: "Save password" },
  "auth.forgot_link": { es: "Olvidé mi contraseña", en: "I forgot my password" },
  "auth.legal_1": { es: "Al acceder aceptas nuestra ", en: "By signing in you accept our " },
  "auth.legal_privacy": { es: "política de privacidad", en: "privacy policy" },
  "auth.legal_2": { es: " y los ", en: " and the " },
  "auth.legal_terms": { es: "términos y condiciones", en: "terms and conditions" },
  "auth.legal_3": { es: " de uso de la plataforma.", en: " for using the platform." },

  // --- Chat evaluacion de proyecto (ChatEvalProyecto) ---
  "cep.grace_intro": { es: "💬 Tienes evaluaciones recientes que puedes modificar durante 2 días desde que las guardaste.\n\nPulsa *✏️ Modificar respuestas* para cambiar algo.", en: "💬 You have recent evaluations you can edit for 2 days after saving them.\n\nTap *✏️ Edit answers* to change something." },
  "cep.pending_intro": { es: "📍 *Tienes una evaluación mensual pendiente.*\n\n_Esta evaluación es totalmente privada, solo podrá verla el CA de la persona evaluada._\n_Si en algún momento quieres cancelar, pulsa Cancelar._\n\n*Pulsa el botón* para comenzar la evaluación.", en: "📍 *You have a monthly evaluation pending.*\n\n_This evaluation is fully private; only the evaluated person’s CA can see it._\n_If at any point you want to cancel, tap Cancel._\n\n*Tap the button* to start the evaluation." },
  "cep.resumen_head": { es: "*Resumen de tus respuestas:*", en: "*Summary of your answers:*" },
  "cep.resumen_evaluado": { es: "- *Persona evaluada*: {v}", en: "- *Person evaluated*: {v}" },
  "cep.resumen_proyecto": { es: "- *Proyecto*: {v}", en: "- *Project*: {v}" },
  "cep.resumen_satisf": { es: "\n¿Estás satisfecho con tus respuestas?\nPulsa *✅ Sí, guardar* o *✏️ Modificar*.", en: "\nAre you happy with your answers?\nTap *✅ Yes, save* or *✏️ Edit*." },
  "cep.btn_comenzar": { es: "Comenzar", en: "Start" },
  "cep.ask_area": { es: "¿A qué área perteneces?\n*1.* Negocio\n*2.* MiddleOffice\n*3.* Palantir", en: "Which area do you belong to?\n*1.* Business\n*2.* MiddleOffice\n*3.* Palantir" },
  "cep.area_negocio": { es: "Negocio", en: "Business" },
  "cep.ask_who_list": { es: "¿A quién quieres evaluar?\n{lista}", en: "Who do you want to evaluate?\n{lista}" },
  "cep.ask_who": { es: "¿A quién quieres evaluar? Dime el nombre de la persona.", en: "Who do you want to evaluate? Tell me the person’s name." },
  "cep.ask_who_short": { es: "¿A quién quieres evaluar? Dime el nombre.", en: "Who do you want to evaluate? Tell me the name." },
  "cep.ask_project": { es: "Escribe el nombre de uno de los proyectos en los que estás trabajando. Más adelante podrás evaluar el resto", en: "Type the name of one of the projects you’re working on. You’ll be able to evaluate the rest later." },
  "cep.project_ok": { es: "Perfecto 😊, vamos con el proyecto *{val}*. Dime el nombre de uno de los miembros de tu equipo, podrás evaluar al resto después.", en: "Great 😊, let’s go with the project *{val}*. Tell me the name of one of your team members; you can evaluate the rest afterwards." },
  "cep.already_evaluated": { es: "Ya has evaluado a *{emp}* en *{proy}* en esta sesión. Dime el nombre de otro miembro.", en: "You’ve already evaluated *{emp}* in *{proy}* this session. Tell me another member’s name." },
  "cep.no_questions": { es: "⚠️ No hay preguntas configuradas.", en: "⚠️ No questions configured." },
  "cep.not_found_suggest": { es: "*{nombre}* no aparece en la lista de empleados.\n¿Querías decir alguno de estos?\n{sug}", en: "*{nombre}* is not in the employee list.\nDid you mean one of these?\n{sug}" },
  "cep.not_found": { es: "*{nombre}* no aparece en la lista de empleados. Escribe nombre y apellido como aparece en la lista.", en: "*{nombre}* is not in the employee list. Type the first and last name as they appear in the list." },
  "cep.err_temp_data": { es: "⚠️ Error temporal consultando datos. Vuelve a intentarlo.", en: "⚠️ Temporary error fetching data. Please try again." },
  "cep.updated": { es: "✅ *Evaluación actualizada* ❤️\n\n¿Quieres modificar la evaluación de alguien más?", en: "✅ *Evaluation updated* ❤️\n\nDo you want to edit someone else’s evaluation?" },
  "cep.saved": { es: "✅ *Evaluación guardada en Notion*.\n\n¿Hay más miembros en el equipo que quieras evaluar?", en: "✅ *Evaluation saved to Notion*.\n\nAre there more team members you’d like to evaluate?" },
  "cep.err_save": { es: "⚠️ No se pudo guardar en Notion. {msg}", en: "⚠️ Could not save to Notion. {msg}" },
  "cep.btn_modificar": { es: "✏️ Modificar", en: "✏️ Edit" },
  "cep.mod_item_persona": { es: "1. Persona evaluada", en: "1. Person evaluated" },
  "cep.mod_item_proyecto": { es: "2. Proyecto", en: "2. Project" },
  "cep.ask_which_mod": { es: "¿Qué respuesta quieres modificar?\n{items}\n\nResponde con el número.", en: "Which answer do you want to edit?\n{items}\n\nReply with the number." },
  "cep.reply_number": { es: "Por favor, responde con un número 🔢", en: "Please reply with a number 🔢" },
  "cep.reply_number_range": { es: "Por favor, responde con un número del 1 al {max} 🔢", en: "Please reply with a number from 1 to {max} 🔢" },
  "cep.enter_person": { es: "Indica el nombre de la persona a evaluar.", en: "Enter the name of the person to evaluate." },
  "cep.enter_new_project": { es: "Escribe el nuevo nombre del proyecto.", en: "Type the new project name." },
  "cep.enter_new_answer": { es: "Escribe la nueva respuesta.", en: "Type the new answer." },
  "cep.not_found_suggest2": { es: "*{v}* no aparece en la lista.\n¿Querías decir alguno de estos?\n{sug}", en: "*{v}* is not in the list.\nDid you mean one of these?\n{sug}" },
  "cep.not_found2": { es: "*{v}* no aparece en la lista. Escribe nombre y apellido.", en: "*{v}* is not in the list. Type the first and last name." },
  "cep.err_temp": { es: "⚠️ Error temporal. Vuelve a intentarlo.", en: "⚠️ Temporary error. Please try again." },
  "cep.reply_1_5": { es: "Por favor, responde con un número del 1 al 5 🔢", en: "Please reply with a number from 1 to 5 🔢" },
  "cep.ask_other_member_proj": { es: "Perfecto. ¿Qué otro miembro del proyecto *{proy}* quieres evaluar?", en: "Great. Which other member of the project *{proy}* do you want to evaluate?" },
  "cep.ask_other_member": { es: "Perfecto. ¿Qué otro miembro quieres evaluar?", en: "Great. Which other member do you want to evaluate?" },
  "cep.thanks_close": { es: "Perfecto, muchas gracias por tu tiempo ❤️. Ya puedes cerrar esta sección 👋", en: "Great, thank you very much for your time ❤️. You can now close this section 👋" },
  "cep.ask_other_project": { es: "¿Estás trabajando en algún otro proyecto?", en: "Are you working on any other project?" },
  "cep.thanks_grace": { es: "Perfecto, muchas gracias por tu tiempo ❤️\n\n💬 Si quieres modificar tus respuestas, tienes un plazo de 2 días.", en: "Great, thank you very much for your time ❤️\n\n💬 If you want to edit your answers, you have a 2-day window." },
  "cep.yes": { es: "✅ Sí", en: "✅ Yes" },
  "cep.no": { es: "❌ No", en: "❌ No" },
  "cep.save_yes": { es: "✅ Sí, guardar", en: "✅ Yes, save" },
  "cep.completed": { es: "Evaluación completada ✅", en: "Evaluation completed ✅" },
  "cep.ask_whose_mod": { es: "¿La evaluación de quién quieres modificar?", en: "Whose evaluation do you want to edit?" },
  "cep.btn_mod_answers": { es: "✏️ Modificar respuestas", en: "✏️ Edit answers" },
  "cep.bye": { es: "¡Hasta pronto! 👋", en: "See you soon! 👋" },
  "cep.ph_project": { es: "Nombre del proyecto...", en: "Project name..." },
  "cep.ph_person": { es: "Nombre del compañero...", en: "Colleague’s name..." },
  "cep.ph_answer": { es: "Escribe tu respuesta...", en: "Type your answer..." },
  "cep.ph_field_number": { es: "Número del campo...", en: "Field number..." },
  "cep.ph_or_name": { es: "O escribe el nombre...", en: "Or type the name..." },
  "cep.ph_new_answer": { es: "Nueva respuesta...", en: "New answer..." },

  // --- Seccion de evaluaciones en la web (EvaluacionesSlackSection) ---
  "ess.intro": { es: "Contestar aquí es exactamente igual que contestar en Slack. Tus respuestas se guardan en el mismo sitio y en el mismo formato.", en: "Answering here is exactly the same as answering in Slack. Your answers are saved in the same place and format." },
  "ess.tab_monthly": { es: "Evaluación mensual", en: "Monthly evaluation" },
  "ess.tab_personal": { es: "Evaluación personal", en: "Personal evaluation" },
  "ess.tip_done": { es: "Ya has completado esta evaluación en el ciclo actual", en: "You’ve already completed this evaluation in the current cycle" },
  "ess.tip_editable": { es: "Puedes modificar tus respuestas (2 días de margen)", en: "You can edit your answers (2-day window)" },
  "ess.tip_soon": { es: "Próximamente", en: "Coming soon" },
  "ess.editable": { es: "Modificable", en: "Editable" },
  "ess.soon_short": { es: "Próx.", en: "Soon" },
  "ess.select_type": { es: "Selecciona un tipo de evaluación.", en: "Select an evaluation type." },
  "ess.page_kicker": { es: "Evaluaciones en Slack", en: "Slack evaluations" },

  // --- Dashboard ---
  "dash.gen_report": { es: "Claude está generando el informe...", en: "Claude is generating the report..." },
  "dash.report_ready": { es: "Informe listo con {n} evaluaciones.", en: "Report ready with {n} evaluations." },
  "dash.err_download_file": { es: "Error al descargar el archivo.", en: "Error downloading the file." },
  "dash.downloading": { es: "Descargando archivo...", en: "Downloading file..." },
  "dash.file_ready": { es: "Archivo listo.", en: "File ready." },
  "dash.todo_general": { es: "General", en: "General" },
  "dash.todo_project_lead": { es: "Responsable de proyecto", en: "Project lead" },
  "dash.todo_finished_projects": { es: "Ver evaluaciones de proyectos realizadas", en: "View completed project evaluations" },
  "dash.finished_loading": { es: "Cargando…", en: "Loading…" },
  "dash.finished_empty": { es: "No has realizado evaluaciones de proyecto en los últimos 2 años.", en: "You haven’t completed any project evaluations in the last 2 years." },
  "dash.finished_project_empty": { es: "Sin evaluaciones.", en: "No evaluations." },
  "det.question": { es: "Pregunta", en: "Question" },
  "det.answer": { es: "Respuesta", en: "Answer" },
  "dash.nav_activate_proj": { es: "Activar evaluaciones de proyecto", en: "Activate project evaluations" },
  "dash.nav_proj_evals": { es: "Evaluaciones por proyectos", en: "Evaluations by project" },
  "dash.nav_do_proj_evals": { es: "Realizar evaluaciones de proyecto", en: "Do project evaluations" },
  "dash.proj_evals_complete_label": { es: "Completado", en: "Completed" },
  "dash.proj_evals_unfinished": { es: "No terminado", en: "Not finished" },
  "dash.nav_my_advisees": { es: "Mis advisees", en: "My advisees" },
  "dash.nav_manage_projects": { es: "Gestionar mis proyectos en activo", en: "Manage my active projects" },
  "dash.nav_admin_panel": { es: "Panel admin", en: "Admin panel" },
  "dash.nav_extra_evals": { es: "Evaluaciones extra (fuera de proyecto)", en: "Extra evaluations (outside a project)" },
  "dash.nav_request_extra_eval": { es: "Pedir evaluación extra", en: "Request an extra evaluation" },
  "dash.nav_pending_extra_evals": { es: "Evaluaciones extra pendientes", en: "Pending extra evaluations" },
  "dash.extra_evals_to_complete": { es: "Evaluaciones extra a completar:", en: "Extra evaluations to complete:" },
  "dash.pending_tasks": { es: "Tareas pendientes", en: "Pending tasks" },
  "dash.slack_mensual": { es: "Evaluación mensual", en: "Monthly evaluation" },
  "dash.slack_personal": { es: "Seguimiento personal", en: "Personal tracking" },
  "dash.slack_ca": { es: "Evaluación de tus advisees", en: "Advisee evaluation" },
  "dash.slack_suffix": { es: "(Evaluación en Slack)", en: "(Slack evaluation)" },
  "dash.no_pending_tasks": { es: "Sin tareas pendientes ✓", en: "No pending tasks ✓" },

  "dash.my_profile": { es: "Mi perfil", en: "My profile" },
  "dash.my_country": { es: "Mi país", en: "My country" },
  "dash.country_change": { es: "Cambiar", en: "Change" },
  "dash.country_placeholder": { es: "Selecciona tu país", en: "Select your country" },
  "dash.country_none": { es: "Sin definir", en: "Not set" },
  "dash.country_saved": { es: "País actualizado.", en: "Country updated." },
  "dash.country_error": { es: "No se pudo guardar el país.", en: "Couldn’t save the country." },
  "dash.my_goals": { es: "Mis objetivos", en: "My goals" },
  "dash.no_goals": { es: "Sin objetivos definidos.", en: "No goals defined." },
  "dash.my_reports": { es: "Mis informes", en: "My reports" },
  "dash.received_evals": { es: "Evaluaciones recibidas", en: "Evaluations received" },
  "dash.received_empty": { es: "Todavía no tienes evaluaciones disponibles.", en: "No evaluations available yet." },
  "dash.no_reports": { es: "No hay informes disponibles", en: "No reports available" },
  "dash.open_web": { es: "Abrir en web", en: "Open in browser" },
  "dash.manage_evals": { es: "Gestión de evaluaciones", en: "Evaluation management" },
  "dash.evaluated_person": { es: "Persona evaluada", en: "Person evaluated" },
  "dash.current_selection": { es: "Selección actual: {v}", en: "Current selection: {v}" },
  "dash.no_table": { es: "sin tabla disponible", en: "no table available" },
  "dash.claude_draft": { es: "Borrador de Claude", en: "Claude draft" },
  "dash.final_ca": { es: "Versión final CA", en: "CA final version" },
  "dash.annual_report": { es: "Informe anual", en: "Annual report" },
  "dash.annual_report_of": { es: "Informe anual de {nombre}", en: "Annual report for {nombre}" },
  "dash.annual_desc": { es: "Genera una base para el informe anual de evaluaciones.", en: "Generate a base for the annual evaluation report." },
  "dash.gen_annual": { es: "Generar informe anual", en: "Generate annual report" },
  "dash.result": { es: "Resultado", en: "Result" },
  "dash.open_web_short": { es: "Abrir web", en: "Open web" },
  "dash.download_annual": { es: "Descargar informe anual", en: "Download annual report" },
  "dash.final_report": { es: "Informe final", en: "Final report" },
  "dash.final_report_of": { es: "Informe final de {nombre}", en: "Final report for {nombre}" },
  "dash.select_person": { es: "Selecciona una persona evaluada.", en: "Select a person to evaluate." },
  "dash.open_web_version": { es: "Abrir versión web", en: "Open web version" },
  "dash.no_final_report": { es: "No hay informe final disponible.", en: "No final report available." },

  // --- Historial de evaluaciones ---
  "hist.err_load": { es: "No se pudieron cargar las evaluaciones.", en: "The evaluations could not be loaded." },
  "hist.title": { es: "Historial de evaluaciones", en: "Evaluation history" },
  "hist.project_label": { es: "Proyecto:", en: "Project:" },
  "hist.empty": { es: "No hay evaluaciones registradas tuyas para este proyecto aún.", en: "You have no evaluations recorded for this project yet." },
  "hist.col_date": { es: "Fecha", en: "Date" },
  "hist.col_project": { es: "Proyecto", en: "Project" },
  "hist.col_score": { es: "Valoración", en: "Score" },
  "hist.col_justif": { es: "Justificación", en: "Justification" },
  "hist.col_relation": { es: "Relación", en: "Relationship" },
  "hist.rel_superior": { es: "Superior", en: "Superior" },
  "hist.rel_equal": { es: "Igual", en: "Same level" },
  "hist.rel_lower": { es: "Inferior", en: "Subordinate" },

  // --- Subir informe final (SubirInformePage) ---
  "subir.uploading": { es: "Subiendo informe...", en: "Uploading report..." },
  "subir.err_upload": { es: "No se pudo subir el informe.", en: "The report could not be uploaded." },
  "subir.uploaded_ok": { es: "Informe subido correctamente.", en: "Report uploaded successfully." },
  "subir.current_version": { es: "Versión actual", en: "Current version" },
  "subir.current_desc": { es: "Ya hay un informe final subido. Puedes descargarlo o subir uno nuevo para reemplazarlo.", en: "There’s already a final report uploaded. You can download it or upload a new one to replace it." },
  "subir.upload_final": { es: "Subir versión final", en: "Upload final version" },
  "subir.upload_desc": { es: "Sube el Word con tu versión final. Se guarda en Notion y el advisee podrá descargarlo. Se mantienen las 2 versiones más recientes.", en: "Upload the Word file with your final version. It’s saved to Notion and the advisee can download it. The 2 most recent versions are kept." },
  "subir.word_file": { es: "Archivo Word (.docx)", en: "Word file (.docx)" },
  "subir.uploading_btn": { es: "Subiendo...", en: "Uploading..." },
  "subir.upload_btn": { es: "Subir informe", en: "Upload report" },

  // --- Detalle de advisee (AdviseeDetail) ---
  "ad.err_no_doc": { es: "No se generó el documento.", en: "The document was not generated." },
  "ad.err_save_note": { es: "No se pudo guardar la nota.", en: "The note could not be saved." },
  "ad.err_save_note2": { es: "Error al guardar la nota.", en: "Error saving the note." },
  "ad.back_advisees": { es: "← Mis advisees", en: "← My advisees" },
  "ad.eyebrow": { es: "Advisee", en: "Advisee" },
  "ad.goals_history": { es: "Ver objetivos de mi advisee", en: "View my advisee’s goals" },
  "ad.edit_goals": { es: "Introducir objetivos", en: "Enter goals" },
  "ad.manage_report": { es: "Gestionar Informe final", en: "Manage final report" },
  "ad.make_final": { es: "Realizar Informe final", en: "Make final report" },
  "ad.with_claude": { es: "Con ayuda de Claude", en: "With Claude’s help" },
  "ad.recommended": { es: "Opción recomendada", en: "Recommended option" },
  "ad.manual": { es: "Manualmente", en: "Manually" },
  "ad.generating": { es: "Generando...", en: "Generating..." },
  "ad.dl_opinions": { es: "Descargar PDF de opiniones", en: "Download opinions PDF" },
  "ad.dl_proj_evals": { es: "Descargar PDF de evaluaciones de proyecto", en: "Download project evaluations PDF" },
  "ad.dl_personal_tracking": { es: "Descargar PDF de seguimiento personal", en: "Download personal tracking PDF" },
  "ad.dl_monthly_evals": { es: "Descargar PDF de evaluaciones mensuales", en: "Download monthly evaluations PDF" },
  "ad.dl_extra_evals": { es: "Descargar Evaluaciones extra (fuera de proyecto)", en: "Download extra evaluations (outside a project)" },
  "ad.err_no_source_info": { es: "No hay info disponible de esta sección", en: "No information available for this section" },
  "ad.dl_all_in_one": { es: "Todo lo anterior en un solo PDF", en: "All of the above in a single PDF" },
  "ad.upload_final": { es: "Subir informe final", en: "Upload final report" },
  "ad.access_active_revoke": { es: "Acceso a informe activo — revocar", en: "Report access active — revoke" },
  "ad.give_access": { es: "Dar acceso a su informe", en: "Give access to their report" },
  "ad.view_available_info": { es: "Descargar PDF con información disponible", en: "Download PDF with available information" },
  "ad.meetings_log": { es: "Registro de comentarios", en: "Comments log" },
  "regcom.desc": { es: "Aquí puedes registrar y consultar tus comentarios y notas de seguimiento sobre este advisee. Se guardan de forma privada: solo tú, como Career Advisor, puedes verlos.", en: "Here you can log and review your comments and follow-up notes about this advisee. They are kept private — only you, as their Career Advisor, can see them." },
  "ad.note_placeholder": { es: "Escribe aquí cualquier anotación o comentario sobre tu advisee", en: "Write any note or comment about your advisee here" },
  "ad.save_note": { es: "Guardar nota", en: "Save note" },
  "ad.loading_history": { es: "Cargando historial...", en: "Loading history..." },
  "ad.no_notes": { es: "No hay notas registradas todavía.", en: "No notes recorded yet." },
  "ad.view_included_evals": { es: "Ver evaluaciones incluidas", en: "View included evaluations" },
  "ad.dictation_start": { es: "🎤 Dictar por voz", en: "🎤 Dictate by voice" },
  "ad.dictation_stop": { es: "■ Detener grabación", en: "■ Stop recording" },
  "ad.dictation_listening": { es: "Escuchando… habla ahora. El texto aparecerá arriba para revisarlo.", en: "Listening… speak now. The text will appear above for review." },
  "ad.dictation_denied": { es: "No se pudo acceder al micrófono. Revisa los permisos del navegador.", en: "Couldn't access the microphone. Check your browser permissions." },
  "ad.dictation_error": { es: "Error al dictar. Inténtalo de nuevo.", en: "Dictation error. Please try again." },
  "ad.dictation_unsupported": { es: "Tu navegador no permite el dictado por voz. Prueba con Chrome o Edge.", en: "Your browser doesn't support voice dictation. Try Chrome or Edge." },

  // --- Mis proyectos en activo (MisProyectosActivosPage) ---
  "mpa.member_added": { es: "{emp} añadido.", en: "{emp} added." },
  "mpa.member_removed": { es: "{emp} eliminado.", en: "{emp} removed." },
  "mpa.err_modify": { es: "Error al modificar.", en: "Error modifying." },
  "mpa.kicker": { es: "Gestión de proyecto", en: "Project management" },
  "mpa.title": { es: "Mis proyectos en activo", en: "My active projects" },
  "mpa.summary": { es: "Como responsable de proyecto, aquí puedes seguir el progreso de las evaluaciones aún sin terminar. Para completarlas ve a la sección ", en: "As project lead, here you can track the progress of unfinished evaluations. To complete them, go to the " },
  "mpa.remove_short": { es: "Eliminar miembro", en: "Remove member" },
  "mpa.summary_suffix": { es: ".", en: " section." },
  "mpa.no_projects": { es: "No tienes proyectos con evaluaciones activas.", en: "You have no projects with active evaluations." },
  "mpa.progress": { es: "{done} de {total} evaluaciones completadas", en: "{done} of {total} evaluations completed" },
  "mpa.no_data": { es: "Sin datos de evaluación todavía.", en: "No evaluation data yet." },
  "mpa.col_member": { es: "Miembro", en: "Member" },
  "mpa.col_completed": { es: "Evaluaciones completadas", en: "Completed evaluations" },
  "mpa.col_status": { es: "Estado", en: "Status" },
  "mpa.complete": { es: "Completo", en: "Complete" },
  "mpa.pending": { es: "Pendiente", en: "Pending" },
  "mpa.done_all": { es: "Ha completado todas sus evaluaciones", en: "Has completed all their evaluations" },
  "mpa.pending_self": { es: "Falta su autoevaluación", en: "Missing their self-evaluation" },
  "mpa.pending_peers": { es: "Le faltan {n} evaluación(es) de compañeros", en: "Missing {n} peer evaluation(s)" },
  "mpa.remove_member": { es: "Eliminar {nombre}", en: "Remove {nombre}" },
  "mpa.select_person": { es: "Selecciona una persona...", en: "Select a person..." },
  "mpa.add": { es: "Añadir", en: "Add" },
  "mpa.add_member": { es: "+ Añadir miembro", en: "+ Add member" },
  "mpa.rec_button": { es: "Enviar recordatorio al equipo", en: "Send reminder to team" },
  "mpa.rec_sending": { es: "Enviando...", en: "Sending..." },
  "mpa.rec_sent": { es: "Recordatorio enviado a {n} persona(s) con evaluaciones pendientes.", en: "Reminder sent to {n} person(s) with pending evaluations." },
  "mpa.rec_none": { es: "Nadie tiene evaluaciones pendientes en este proyecto. 🎉", en: "Nobody has pending evaluations in this project. 🎉" },
  "mpa.rec_err": { es: "No se pudo enviar el recordatorio.", en: "Could not send the reminder." },

  // --- Activar evaluaciones de proyecto (ActivarEvaluacionesProyectoPage) ---
  "aep.err_type_project": { es: "Escribe el nombre del proyecto.", en: "Type the project name." },
  "aep.err_format": { es: "El nombre debe seguir el formato AÑO_EMPRESA_NOMBRE en mayúsculas, sin espacios ni tildes (p.ej. 2026_ACME_INNOVACION).", en: "The name must follow the format YEAR_COMPANY_NAME in uppercase, no spaces or accents (e.g. 2026_ACME_INNOVATION)." },
  "aep.format_bad": { es: "Formato erróneo", en: "Invalid format" },
  "aep.activated": { es: "Evaluaciones activadas para {n} persona(s). Se les ha enviado una notificación por Slack.", en: "Evaluations activated for {n} person(s). They’ve been notified on Slack." },
  "aep.err_activate": { es: "No se pudo activar.", en: "Could not activate." },
  "aep.title": { es: "Activar evaluaciones", en: "Activate evaluations" },
  "aep.desc": { es: "Como responsable de proyecto, introduce el nombre del proyecto y selecciona los miembros de tu equipo. Se les notificará por Slack y podrán acceder a los formularios de evaluación.", en: "As project lead, enter the project name and select your team members. They’ll be notified on Slack and will be able to access the evaluation forms." },
  "aep.activate_another": { es: "Activar otro proyecto", en: "Activate another project" },
  "aep.back_home": { es: "Volver al inicio", en: "Back to home" },
  "aep.project_name": { es: "Nombre del proyecto", en: "Project name" },
  "aep.format_hint": { es: "Formato: AÑO_EMPRESA_NOMBRE en mayúsculas, sin espacios ni tildes (p.ej. 2026_ACME_INNOVACION)", en: "Format: YEAR_COMPANY_NAME in uppercase, no spaces or accents (e.g. 2026_ACME_INNOVATION)" },
  "aep.team_members": { es: "Miembros del equipo", en: "Team members" },
  "aep.loading_employees": { es: "Cargando empleados...", en: "Loading employees..." },
  "aep.search_by_name": { es: "Buscar por nombre...", en: "Search by name..." },
  "aep.remove_member": { es: "Quitar", en: "Remove" },
  "aep.members_selected_one": { es: "miembro seleccionado", en: "member selected" },
  "aep.members_selected_many": { es: "miembros seleccionados", en: "members selected" },
  "aep.activating": { es: "Activando...", en: "Activating..." },
  "aep.activate_n_one": { es: "Activar evaluaciones ({n} seleccionado)", en: "Activate evaluations ({n} selected)" },
  "aep.activate_n_many": { es: "Activar evaluaciones ({n} seleccionados)", en: "Activate evaluations ({n} selected)" },
  "aep.activate_solo": { es: "Activar evaluaciones (solo tú)", en: "Activate evaluations (only you)" },
  "aep.solo_hint": { es: "Si no seleccionas a nadie, el proyecto se activará solo para ti.", en: "If you don’t select anyone, the project will be activated only for you." },

  // --- Evaluaciones de proyecto: listado (EvaluacionesProyectoPage) ---
  "ep.fill_eval": { es: "Rellenar evaluación", en: "Fill in evaluation" },
  "ep.completed": { es: "Completada", en: "Completed" },
  "ep.pending": { es: "Pendiente", en: "Pending" },
  "ep.history": {
    es: "Historial de evaluaciones hechas a {nombre} en {proyecto}",
    en: "History of evaluations of {nombre} in {proyecto}",
  },
  "ep.kicker": { es: "Evaluación de proyecto", en: "Project evaluation" },
  "ep.project_label": { es: "Proyecto", en: "Project" },
  "ep.progress": { es: "Progreso de evaluaciones", en: "Evaluation progress" },
  "ep.progress_stat": { es: "{done} de {total} completadas · {pct}%", en: "{done} of {total} completed · {pct}%" },
  "ep.section_auto": { es: "Autoevaluación", en: "Self-evaluation" },
  "ep.section_manager": { es: "Evaluaciones a miembros del equipo", en: "Evaluations of team members" },
  "ep.section_members": { es: "Evaluaciones al resto del equipo", en: "Evaluations of the rest of the team" },

  // --- Formulario de evaluacion de proyecto (FormularioEvaluacionProyecto) ---
  "fep.label_auto": { es: "Autoevaluación", en: "Self-evaluation" },
  "fep.label_peer": { es: "Evaluación a compañero", en: "Peer evaluation" },
  "fep.label_manager": { es: "Evaluación al responsable", en: "Evaluation of the lead" },
  "fep.label_member": { es: "Evaluación a miembro", en: "Member evaluation" },
  "fep.err_select_person": { es: "Selecciona la persona a evaluar.", en: "Select the person to evaluate." },
  "fep.err_required": { es: "Por favor responde todas las preguntas obligatorias.", en: "Please answer all required questions." },
  "fep.saved_notion": { es: "Evaluación guardada correctamente en Notion.", en: "Evaluation saved successfully to Notion." },
  "fep.err_save": { es: "No se pudo guardar.", en: "Could not save." },
  "fep.saved_ok": { es: "Evaluación guardada correctamente.", en: "Evaluation saved successfully." },
  "fep.person_to_eval": { es: "Persona a evaluar", en: "Person to evaluate" },
  "fep.select_dash": { es: "— Selecciona —", en: "— Select —" },
  "fep.evaluating_self": { es: "Evaluándote a ti mismo: {nombre}", en: "Evaluating yourself: {nombre}" },
  "fep.evaluating": { es: "Evaluando a: {nombre}", en: "Evaluating: {nombre}" },
  "fep.no_questions": { es: "No hay preguntas configuradas para este tipo de evaluación.", en: "No questions configured for this type of evaluation." },
  "fep.scale_low": { es: "1 — Carece de cumplimiento", en: "1 — Does not meet" },
  "fep.scale_high": { es: "5 — Cumple totalmente", en: "5 — Fully meets" },
  "fep.submit": { es: "Enviar evaluación", en: "Submit evaluation" },
  "fep.save_progress": { es: "Guardar progreso", en: "Save progress" },
  "fep.progress_saved": { es: "Progreso guardado. Puedes volver más tarde para terminar.", en: "Progress saved. You can come back later to finish." },
  "fep.draft_restored": { es: "Hemos restaurado tu progreso guardado.", en: "We restored your saved progress." },
  "fep.discard_draft": { es: "Descartar progreso", en: "Discard progress" },
  "fep.save_draft": { es: "Guardar borrador", en: "Save draft" },
  "fep.send": { es: "Enviar", en: "Send" },
  "fep.confirm_send_text": {
    es: "Vas a mandar la evaluación a {nombre} y este la va a tener disponible para verla. Recomendamos que te reúnas personalmente con él/ella antes de liberársela. Si todavía no estás listo para mandarla, puedes guardar el borrador y volver más tarde.",
    en: "You are about to send the evaluation to {nombre}, who will be able to see it. We recommend meeting with them in person before releasing it. If you are not ready to send it yet, you can save the draft and come back later.",
  },
  "fep.confirm_send_text_ca": {
    es: "Esta evaluación no la recibirá directamente {nombre}. Por confidencialidad de tus respuestas, la recibirá su CA. ¿Quieres modificar alguna de tus respuestas antes de enviarla?",
    en: "{nombre} will not receive this evaluation directly. To keep your answers confidential, it will go to their CA. Would you like to change any of your answers before sending it?",
  },
  "fep.confidential_note": {
    es: "Esta evaluación no la recibirá directamente {nombre}. Por confidencialidad de tus respuestas, la recibirá su CA.",
    en: "{nombre} will not receive this evaluation directly. To keep your answers confidential, it will go to their CA.",
  },
  "fep.self_only_ca": {
    es: "Esta información solo le llegará a tu CA.",
    en: "This information will only reach your CA.",
  },

  // --- Solicitar evaluación extra, fuera de proyecto (SolicitarEvaluacionExtraPage) ---
  "sex.kicker": { es: "Evaluación extra", en: "Extra evaluation" },
  "sex.title": { es: "Pedir evaluación extra", en: "Request an extra evaluation" },
  "sex.desc": { es: "Pide a un compañero que te evalúe sobre algo en lo que habéis trabajado juntos, fuera de un proyecto. Es opcional para él: recibirá una notificación por Slack y decidirá si responde.", en: "Ask a colleague to evaluate you on something you worked on together, outside of a project. It's optional for them: they'll get a Slack notification and decide whether to answer." },
  "sex.who_label": { es: "¿A quién se lo pides?", en: "Who are you asking?" },
  "sex.context_label": { es: "¿Sobre qué debe evaluarte?", en: "What should they evaluate you on?" },
  "sex.context_hint": { es: "Este texto le llegará a la persona como parte de una notificación en Slack y se le mostrará junto con la evaluación.", en: "This text will reach the person as part of a Slack notification and will be shown to them alongside the evaluation." },
  "sex.context_placeholder": { es: "P. ej.: la sesión de trabajo que preparamos juntos para el cliente X", en: "E.g.: the work session we prepared together for client X" },
  "sex.err_select_employee": { es: "Selecciona a quién se lo pides.", en: "Select who you're asking." },
  "sex.err_context": { es: "Escribe sobre qué debe evaluarte.", en: "Write what they should evaluate you on." },
  "sex.err_send": { es: "No se pudo enviar la solicitud.", en: "Could not send the request." },
  "sex.sent": { es: "Solicitud enviada a {nombre}. Se le ha notificado por Slack.", en: "Request sent to {nombre}. They've been notified on Slack." },
  "sex.sending": { es: "Enviando...", en: "Sending..." },
  "sex.submit": { es: "Enviar solicitud", en: "Send request" },
  "sex.request_another": { es: "Pedir otra evaluación", en: "Request another evaluation" },
  "sex.back_home": { es: "Volver al inicio", en: "Back to home" },

  // --- Evaluaciones extra pendientes (listado inline en el Dashboard) ---
  "eep.requested_by": { es: "Evaluación a completar pedida por: {nombre}", en: "Evaluation to complete, requested by: {nombre}" },
  "eep.to_complete": { es: "Completar", en: "To complete" },

  // --- Formulario de evaluación extra (FormularioEvaluacionExtra) ---
  "fex.kicker": { es: "Evaluación extra", en: "Extra evaluation" },
  "fex.title": { es: "Evaluar a {nombre}", en: "Evaluate {nombre}" },
  "fex.context_label": { es: "Te ha pedido que le evalúes sobre:", en: "They've asked you to evaluate them on:" },
  "fex.score_label": { es: "Nota (1 a 5)", en: "Score (1 to 5)" },
  "fex.justification_label": { es: "Justifica tu valoración", en: "Justify your score" },
  "fex.err_score": { es: "Selecciona una nota del 1 al 5.", en: "Select a score from 1 to 5." },
  "fex.err_justification": { es: "Escribe una justificación.", en: "Write a justification." },
  "fex.err_save": { es: "No se pudo guardar la evaluación.", en: "Could not save the evaluation." },
  "fex.saved_ok": { es: "Evaluación guardada correctamente.", en: "Evaluation saved successfully." },
  "fex.submit": { es: "Enviar evaluación", en: "Submit evaluation" },

  // --- Wizard de evaluacion anual asistida (EvaluacionAnualWizard) ---
  "eaw.err_write_points": { es: "Escribe tus puntos antes de enviar.", en: "Write your points before sending." },
  "eaw.eyebrow": { es: "Evaluación anual asistida", en: "Assisted annual evaluation" },
  "eaw.generating": { es: "Generando…", en: "Generating…" },
  "eaw.full_info": { es: "Info recopilada de {nombre}", en: "Info collected on {nombre}" },
  "eaw.reset_all": { es: "Eliminar y empezar de 0", en: "Delete and start over" },
  "eaw.resetting": { es: "Eliminando…", en: "Deleting…" },
  "eaw.reset_confirm": { es: "¿Seguro que quieres eliminar todo el progreso de este informe (conversaciones y áreas confirmadas) y empezar de cero? Esta acción no se puede deshacer.", en: "Are you sure you want to delete all progress on this report (conversations and confirmed areas) and start over? This action cannot be undone." },
  "eaw.year_stat": { es: "Año {anio} · {done}/{total} áreas confirmadas", en: "Year {anio} · {done}/{total} areas confirmed" },
  "eaw.err_start": { es: "No se pudo iniciar la evaluación.", en: "The evaluation could not be started." },
  "eaw.confirm_identity_q": { es: "¿Es esta la persona que vas a evaluar?", en: "Is this the person you’re going to evaluate?" },
  "eaw.year_projects": { es: "Proyectos del año: {list}", en: "Projects this year: {list}" },
  "eaw.yes_correct_start": { es: "Sí, es correcto · empezar", en: "Yes, correct · start" },
  "eaw.no_back": { es: "No, volver", en: "No, go back" },
  "eaw.wait_starting": { es: "Preparando la evaluación…", en: "Getting the evaluation ready…" },
  "eaw.wait_starting_detail": { es: "Estamos reuniendo todo lo que se ha dicho durante el año.", en: "We're gathering everything said over the year." },
  "eaw.wait_area": { es: "Preparando la evaluación de {nombre}…", en: "Getting {nombre}'s evaluation ready…" },
  // El minuto no es un adorno: es lo que tarda de verdad (medido). Decirlo por delante
  // evita que el CA piense que se ha quedado colgado y recargue, que solo lo alarga.
  "eaw.wait_area_detail": { es: "La IA está leyendo todas sus evaluaciones del año. Suele tardar alrededor de un minuto y solo pasa la primera vez.", en: "The AI is reading all of their evaluations for the year. This usually takes about a minute, and only happens the first time." },
  "eaw.wait_slow": { es: "Está tardando más de lo normal, pero sigue en marcha. No cierres la página.", en: "It's taking longer than usual, but it's still running. Don't close the page." },
  "eaw.wait_elapsed": { es: "Llevas esperando {mm}:{ss}", en: "Waiting for {mm}:{ss}" },
  "eaw.area_n": { es: "Área {i}/{total}", en: "Area {i}/{total}" },
  "eaw.info_considered": { es: "Información que la IA consideró de esta área ({n})", en: "Information the AI considered for this area ({n})" },
  "eaw.no_evidence": { es: "Sin evidencia específica para esta área.", en: "No specific evidence for this area." },
  "eaw.info_not_used": { es: "Otras fuentes que la IA no usó aquí ({n})", en: "Other sources the AI did not use here ({n})" },
  "eaw.info_not_used_note": { es: "La IA no las citó en esta área. Están aquí para que las veas y decidas tú si son relevantes.", en: "The AI did not cite these for this area. They are listed so you can see them and decide for yourself whether they are relevant." },
  "eaw.ref_unavailable": { es: "Referencia no disponible en esta área.", en: "Reference not available in this area." },
  "eaw.ref_hint": { es: "Pulsa una cita [E#] en la respuesta de la IA para ver la fuente al momento.", en: "Click a citation [E#] in the AI reply to see the source instantly." },
  "eaw.criteria_panel": { es: "Criterios y nivel", en: "Criteria & level" },
  "eaw.no_criteria_position": { es: "No existen criterios para este puesto.", en: "There are no criteria for this position." },
  "eaw.ph_respond_ai": { es: "Responde a la IA…", en: "Reply to the AI…" },
  "eaw.ph_main_points": { es: "Tus puntos principales y tu opinión…", en: "Your main points and your opinion…" },
  "eaw.sending": { es: "Enviando…", en: "Sending…" },
  "eaw.respond": { es: "Responder", en: "Reply" },
  "eaw.send_to_ai": { es: "Enviar a la IA", en: "Send to the AI" },
  "eaw.confirm_area": { es: "Confirmar área y continuar →", en: "Confirm area and continue →" },
  "eaw.all_confirmed": { es: "Todas las áreas confirmadas", en: "All areas confirmed" },
  "eaw.summary_desc": { es: "Al finalizar, la IA rellena el borrador con lo acordado en cada área (los huecos de notas/retribución quedan en blanco). Podrás editarlo aquí mismo y subirlo como informe final sin salir de la web.", en: "When you finish, the AI fills in the draft with what was agreed for each area (the notes/compensation gaps stay blank). You'll be able to edit it right here and upload it as the final report without leaving the web." },
  "eaw.gen_draft": { es: "Generar borrador", en: "Generate draft" },
  "eaw.plan_title": { es: "Plan de acción sugerido (año que viene)", en: "Suggested action plan (next year)" },
  "eaw.plan_desc": { es: "Objetivos sugeridos por la IA a partir de la evaluación y los gaps. Es una sugerencia: edítalo o pide cambios.", en: "Objectives suggested by the AI from the evaluation and gaps. It's a suggestion: edit it or ask for changes." },
  "eaw.plan_loading": { es: "Generando el plan…", en: "Generating the plan…" },
  "eaw.plan_save": { es: "Guardar plan", en: "Save plan" },
  "eaw.plan_ask": { es: "Pedir cambios a la IA", en: "Ask the AI for changes" },
  "eaw.plan_ask_ph": { es: "Ej: hazlo más ambicioso, quita el objetivo 3…", en: "e.g. make it more ambitious, remove objective 3…" },
  "eaw.plan_saved": { es: "Plan guardado", en: "Plan saved" },
  "eaw.prev_area": { es: "← Área anterior", en: "← Previous area" },
  "eaw.area_confirmed_badge": { es: "✓ Confirmada", en: "✓ Confirmed" },
  "eaw.reopened_notice": { es: "Has reabierto esta área: al enviar un nuevo punto quedará pendiente de volver a confirmar.", en: "You've reopened this area: sending a new point will leave it pending re-confirmation." },
  "eaw.jump_to_area": { es: "Ir a un área concreta", en: "Jump to a specific area" },
  "eaw.downloaded": { es: "Descargado", en: "Downloaded" },
  "ad.downloaded": { es: "Descargado", en: "Downloaded" },
  "adplan.none_yet": { es: "Aún no hay plan guardado. Abre el asistente para crearlo con ayuda de Claude.", en: "No saved plan yet. Open the assistant to create one with Claude's help." },
  "adplan.page_title": { es: "Plan de acción", en: "Action plan" },
  "adplan.nav_title": { es: "Generar plan de acción", en: "Generate action plan" },
  "adplan.page_desc": { es: "El plan de acción recoge los objetivos y áreas de mejora acordados para el año siguiente, a partir de la evaluación final del advisee.", en: "The action plan gathers the goals and areas for improvement agreed for the coming year, based on the advisee's final evaluation." },
  "adplan.none": { es: "{nombre} no tiene ningún plan de acción diseñado. Para crear uno, realiza el informe final con ayuda de Claude o pulsa el botón de crear plan de acción nuevo de abajo.", en: "{nombre} doesn't have any action plan designed yet. To create one, complete the final report with Claude's help or click the button to create a new action plan below." },
  "adplan.create_new": { es: "Crear plan de acción nuevo", en: "Create new action plan" },
  "adplan.edit": { es: "Editar plan", en: "Edit plan" },
  "adplan.generating": { es: "Generando plan de acción con la ayuda de Claude…", en: "Generating action plan with Claude's help…" },
  "adplan.ask": { es: "Tengo dudas", en: "I have questions" },
  "adplan.ask_close": { es: "Cerrar chat", en: "Close chat" },
  "adplan.chat_intro": { es: "Pregunta lo que quieras sobre este plan de acción o sobre las evaluaciones del advisee.", en: "Ask anything about this action plan or the advisee's evaluations." },
  "adplan.chat_placeholder": { es: "Escribe tu pregunta…", en: "Type your question…" },
  "adplan.chat_send": { es: "Preguntar", en: "Ask" },
  "adplan.chat_thinking": { es: "Pensando…", en: "Thinking…" },
  "adplan.exists": { es: "Ya existe un plan de acción guardado", en: "There's already a saved action plan" },
  "adplan.source_note": { es: "Generado a partir de las evaluaciones recibidas del advisee y de los criterios de su puesto (y del siguiente nivel). Revísalo y ajústalo antes de usarlo.", en: "Generated from the advisee's received evaluations and the criteria of their role (and the next level). Review and adjust it before use." },
  // --- Sugerencia final del área (asistente anual, criterio a criterio) ---
  "eaw.final_summary_hint": { es: "Cuando des el área por hablada, pide la sugerencia final: una valoración por cada criterio del panel «Criterios y nivel».", en: "When you're done discussing this area, ask for the final suggestion: one assessment per criterion from the “Criteria & level” panel." },
  "eaw.final_summary_btn": { es: "¿Ya quieres la sugerencia final de este área según la info y la conversación?", en: "Ready for the final suggestion for this area based on the info and the conversation?" },
  "eaw.final_summary_title": { es: "Sugerencia final del área, criterio a criterio", en: "Final suggestion for this area, criterion by criterion" },
  "eaw.final_summary_desc": { es: "Una valoración por cada criterio de tu nivel. Los criterios que no se han podido evaluar por falta de información aparecen marcados.", en: "One assessment per criterion at your level. Criteria that couldn't be assessed for lack of information are flagged." },
  "eaw.final_summary_refresh": { es: "Actualizar sugerencia final", en: "Refresh final suggestion" },
  // --- Borrador editable del informe final (asistente anual) ---
  "eaw.draft_step_desc": { es: "Este borrador replica la plantilla oficial del informe final. Los comentarios vienen prellenados con lo acordado en cada área; los campos del CA (notas, retribución, promoción, salarios, deadlines) están vacíos para que los rellenes tú. Edítalo aquí y súbelo directamente: no hace falta descargar nada.", en: "This draft mirrors the official final report template. Comments are pre-filled with what was agreed for each area; the CA fields (scores, compensation, promotion, salaries, deadlines) are empty for you to fill in. Edit it here and upload it directly: no download needed." },
  "eaw.save_draft": { es: "Guardar borrador", en: "Save draft" },
  "eaw.draft_saved": { es: "Borrador guardado", en: "Draft saved" },
  "eaw.upload_final": { es: "Subir informe final", en: "Upload final report" },
  "eaw.upload_confirm": { es: "¿Subir este borrador como informe final? Se registrará como la versión oficial del informe de esta persona.", en: "Upload this draft as the final report? It will be registered as this person's official report version." },
  "eaw.uploading": { es: "Subiendo…", en: "Uploading…" },
  "eaw.uploaded_ok": { es: "Informe final subido ✓", en: "Final report uploaded ✓" },
  "eaw.back_to_areas": { es: "← Volver a las áreas", en: "← Back to the areas" },
  "eaw.add_objective": { es: "+ Añadir objetivo", en: "+ Add objective" },
  // Etiquetas de la plantilla oficial del informe anual (idénticas al documento)
  "anualdoc.title": { es: "EVALUACIÓN ANUAL", en: "ANNUAL EVALUATION" },
  "anualdoc.employee": { es: "Empleado", en: "Employee" },
  "anualdoc.date": { es: "Fecha", en: "Date" },
  "anualdoc.current_position": { es: "Posición actual", en: "Current position" },
  "anualdoc.current_salary": { es: "Salario actual", en: "Current salary" },
  "anualdoc.rating_year": { es: "CALIFICACIÓN {anio}", en: "RATING {anio}" },
  "anualdoc.projects": { es: "PROYECTOS", en: "PROJECTS" },
  "anualdoc.score": { es: "NOTA", en: "SCORE" },
  "anualdoc.comments": { es: "COMENTARIOS", en: "COMMENTS" },
  "anualdoc.final_projects": { es: "Nota final Proyectos", en: "Final Projects score" },
  "anualdoc.variable_60": { es: "Variable (60%)", en: "Variable (60%)" },
  "anualdoc.final_contrib": { es: "Nota final Contrib. To the firm (10%)", en: "Final Contrib. to the firm score (10%)" },
  "anualdoc.variable": { es: "Variable", en: "Variable" },
  "anualdoc.corp_objectives": { es: "Consecución Objetivos corp.", en: "Corp. objectives achievement" },
  "anualdoc.total_variable": { es: "Total Variable {yy} =", en: "Total Variable {yy} =" },
  "anualdoc.eval_result": { es: "RESULTADO EVAL {yy}", en: "EVAL RESULT {yy}" },
  "anualdoc.promotion": { es: "PROMOCIÓN", en: "PROMOTION" },
  "anualdoc.position_next": { es: "POSICIÓN {yy}", en: "POSITION {yy}" },
  "anualdoc.new_fixed_salary": { es: "Nuevo salario fijo =", en: "New fixed salary =" },
  "anualdoc.improvement_objectives": { es: "OPORTUNIDADES DE MEJORA / OBJETIVOS {yy}", en: "IMPROVEMENT OPPORTUNITIES / OBJECTIVES {yy}" },
  "anualdoc.deadline": { es: "Deadline", en: "Deadline" },
};

// El overlay PT (frontend/src/pt.js) ya no se importa aquí de forma estática: se
// carga bajo demanda vía _ensurePtLoaded() cuando el idioma activo es portugués.