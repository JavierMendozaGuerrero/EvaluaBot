"""Traducciones de la interfaz fija del bot e informes (i18n).

El contenido escrito a mano en Notion (preguntas, objetivos, comentarios) NO se
traduce aqui: se muestra tal y como se guardo. Aqui solo viven los textos fijos
que genera el propio codigo.

Uso:
    from .i18n import t
    t("bot.saludo", idioma, nombre="Ana")

Idiomas soportados: 'es' (por defecto) y 'en'.
"""
import logging

IDIOMA_POR_DEFECTO = "es"
IDIOMAS_SOPORTADOS = ("es", "en", "pt")
_ETIQUETA_IDIOMA = {"es": "ES", "en": "EN", "pt": "PT"}
_BANDERA_IDIOMA = {"es": "🇪🇸", "en": "🇬🇧", "pt": "🇵🇹"}


# Catalogo de textos: clave -> {"es": ..., "en": ...}
# Los textos admiten placeholders de str.format, p.ej. "Hola {nombre}".
# Se rellena por superficie (bot, informes, ...) a medida que se traduce.
TEXTOS: dict[str, dict[str, str]] = {
    # --- Informe de evaluaciones (reports.py) ---
    "report.titulo": {"es": "Informe de evaluaciones", "en": "Evaluations report"},
    "report.generado": {"es": "Generado el {fecha}", "en": "Generated on {fecha}"},
    "report.cerrar": {"es": "Cerrar", "en": "Close"},
    "report.evaluado": {"es": "Evaluado", "en": "Employee"},
    "report.evaluaciones": {"es": "Evaluaciones", "en": "Evaluations"},
    "report.fuente": {"es": "Fuente", "en": "Source"},
    # --- Aviso compartido: el bot no es inteligente (se muestra al iniciar cada evaluacion) ---
    "bot.no_inteligente": {
        "es": "🤖 Este bot no es inteligente: solo te hace preguntas y guarda tus respuestas tal cual. No entiende ni interpreta lo que escribes, así que no esperes respuestas conversacionales.",
        "en": "🤖 This bot is not intelligent: it just asks you questions and stores your answers as they are. It doesn't understand or interpret what you write, so don't expect conversational replies.",
    },
    # --- Pregunta compartida del DM inicial: ver ejemplo con botones Sí/No ---
    "bot.example_q": {
        "es": ":point_right: *Para empezar, entra en el hilo y selecciona si quieres ver un ejemplo antes de empezar*",
        "en": ":point_right: *To begin, open the thread and choose whether you want to see an example before starting*",
        "pt": ":point_right: *Para começar, entra no tópico e seleciona se queres ver um exemplo antes de começar*",
    },
    # --- Bot Slack: evaluacion mensual de proyecto (slack_bot.py) ---
    "bm.back_btn": {"es": "⬅️ Atrás", "en": "⬅️ Back"},
    "bm.back_done": {"es": "⬅️ Volviste atrás", "en": "⬅️ Went back"},
    "bm.pendientes_link": {"es": "📋 También la tienes en tu <{url}|lista de pendientes>", "en": "📋 You can also find it in your <{url}|pending list>"},
    "bm.pendientes_titulo": {"es": "Evaluación mensual", "en": "Monthly evaluation"},
    "bm.pending_fallback": {"es": "📍 Tienes una evaluación mensual pendiente", "en": "📍 You have a monthly evaluation pending"},
    "bm.pending_header": {
        "es": "📍 *Tienes una evaluación mensual pendiente.*",
        "en": "📍 *You have a monthly evaluation pending.*",
        "pt": "📍 *Tens uma avaliação mensal pendente.*",
    },
    "bm.pending_body": {
        "es": ("_Recordatorio: esta evaluación es opcional. Recomendamos realizarla, pero no es obligatoria._\n"
               "_No es necesario evaluar a todos los miembros del equipo si no lo consideras necesario._\n"
               "_Si en algún momento quieres cancelar, escribe SOS en el hilo._"),
        "en": ("_Reminder: this evaluation is optional. We recommend completing it, but it's not mandatory._\n"
               "_You don't need to evaluate every team member if you don't think it's necessary._\n"
               "_If at any point you want to cancel, type SOS in the thread._"),
        "pt": ("_Lembrete: esta avaliação é opcional. Recomendamos que a realizes, mas não é obrigatória._\n"
               "_Não é necessário avaliar todos os membros da equipa se não considerares necessário._\n"
               "_Se em algum momento quiseres cancelar, escreve SOS no tópico._"),
    },
    # Aviso de privacidad que se muestra DESPUÉS de elegir a la persona a evaluar.
    # Depende de la jerarquía de la persona evaluada respecto al evaluador.
    "bm.privacidad_arriba": {
        "es": "🔒 _Esta evaluación solo la verá Ana, Head of People._",
        "en": "🔒 _Only Ana, Head of People, will see this evaluation._",
        "pt": "🔒 _Esta avaliação só será vista pela Ana, Head of People._",
    },
    "bm.privacidad_abajo": {
        "es": "🔒 _Esta evaluación la verá el CA de la persona evaluada._",
        "en": "🔒 _The CA of the evaluated person will see this evaluation._",
        "pt": "🔒 _Esta avaliação será vista pelo CA da pessoa avaliada._",
    },
    "bm.example_label": {"es": ":point_right: Ejemplo:", "en": ":point_right: Example:"},
    "bm.see_example": {"es": "Ver ejemplo", "en": "See example"},
    "bm.send_to_start": {"es": ":point_right: *Envía cualquier mensaje en el hilo para comenzar la evaluación*", "en": ":point_right: *Send any message in the thread to start the evaluation*"},
    "bm.updated_suffix": {
        "es": ("\n\n✅ Respuesta actualizada. ¿Quieres cambiar algo más o sigo?\n"
               "Haz click en *Modificar* para cambiar otra respuesta o en *Sí, guardar* para continuar."),
        "en": ("\n\n✅ Answer updated. Do you want to change anything else or shall I continue?\n"
               "Click *Edit* to change another answer or *Yes, save* to continue."),
    },
    "bm.satisfied_suffix": {
        "es": ("\n\n¿Estás satisfecho con tus respuestas?\n"
               "Responde o haz click en sí para guardar en Notion o modificar para cambiar una respuesta concreta."),
        "en": ("\n\nAre you happy with your answers?\n"
               "Reply or click yes to save to Notion, or edit to change a specific answer."),
    },
    "bm.summary_head": {"es": "*Resumen de tus respuestas:*", "en": "*Summary of your answers:*"},
    "bm.summary_evaluado": {"es": "- *Persona evaluada*: {v}", "en": "- *Person evaluated*: {v}"},
    "bm.summary_proyecto": {"es": "- *Proyecto*: {v}", "en": "- *Project*: {v}"},
    "bm.summary_satisfaccion": {"es": "- *Satisfacción*: {v}", "en": "- *Satisfaction*: {v}"},
    "bm.mod_which": {"es": "¿Qué respuesta quieres modificar?", "en": "Which answer do you want to edit?"},
    "bm.mod_which_bold": {"es": "*¿Qué respuesta quieres modificar?*", "en": "*Which answer do you want to edit?*"},
    "bm.mod_persona": {"es": "Persona evaluada", "en": "Person evaluated"},
    "bm.mod_proyecto": {"es": "Proyecto", "en": "Project"},
    "bm.mod_reply_number": {"es": "\nResponde con el número.", "en": "\nReply with the number."},
    "bm.tap_area_button": {"es": "Por favor, pulsa el botón del área al que perteneces 😊", "en": "Please tap the button for the area you belong to 😊"},
    "bm.ask_project": {"es": "Escribe el nombre de uno de los proyectos en los que estás trabajando. Más adelante podrás evaluar el resto", "en": "Type the name of one of the projects you're working on. You'll be able to evaluate the rest later"},
    "bm.ask_barbecho": {"es": "¿Qué labores estás realizando?", "en": "What tasks are you working on?"},
    "bm.rewrite_tasks": {"es": "Escribe de nuevo tus labores:", "en": "Type your tasks again:"},
    "bm.project_ok": {"es": "Perfecto 😊, vamos con el proyecto *{proy}*. Dime el nombre de uno de los miembros de tu equipo, podrás evaluar al resto después.", "en": "Great 😊, let's go with the project *{proy}*. Tell me the name of one of your team members; you can evaluate the rest afterwards."},
    "bm.ask_project_long": {"es": "¿En qué proyecto estás trabajando ahora? Si estás en más de uno, elige solo uno y escribe el nombre, después podrás evaluar otros proyectos.", "en": "Which project are you working on now? If you're on more than one, pick just one and type its name; you'll be able to evaluate other projects afterwards."},
    "bm.still_here": {"es": "Sigo aquí. Dime el nombre de uno de los miembros, podrás evaluar al resto después.", "en": "I'm still here. Tell me the name of one of the members; you can evaluate the rest afterwards."},
    "bm.already_evaluated": {"es": "Ya has evaluado a *{emp}* en *{proy}* en esta sesión. Dime el nombre de otro miembro del proyecto.", "en": "You've already evaluated *{emp}* in *{proy}* this session. Tell me another project member's name."},
    "bm.no_questions_area": {"es": "⚠️ No hay preguntas configuradas en Notion para esta área.", "en": "⚠️ No questions configured in Notion for this area."},
    "bm.reply_1_4": {"es": "Por favor, responde con un número del 1 al 5 🔢", "en": "Please reply with a number from 1 to 5 🔢"},
    "bm.reply_1_n": {"es": "Por favor, responde con un número del 1 al {max} 🔢", "en": "Please reply with a number from 1 to {max} 🔢"},
    "bm.enter_new_answer": {"es": "Escribe la nueva respuesta.", "en": "Type the new answer."},
    "bm.enter_person": {"es": "Indica el nombre de la persona a evaluar.", "en": "Enter the name of the person to evaluate."},
    "bm.enter_new_project": {"es": "Escribe el nuevo nombre del proyecto.", "en": "Type the new project name."},
    "bm.ask_other_member_proj": {"es": "Perfecto. ¿Qué otro miembro del proyecto *{proy}* quieres evaluar?", "en": "Great. Which other member of the project *{proy}* do you want to evaluate?"},
    "bm.ask_other_member": {"es": "Perfecto. ¿Qué otro miembro quieres evaluar?", "en": "Great. Which other member do you want to evaluate?"},
    "bm.more_projects_q": {"es": "Si hay más proyectos en los que estés trabajando, por favor, dímelo. ¿Hay más proyectos? (`sí` / `no`)", "en": "If there are more projects you're working on, please tell me. Any more projects? (`yes` / `no`)"},
    "bm.reply_yes_no_persons": {"es": "Responde `sí` o `no` para indicar si hay más personas que evaluar.", "en": "Reply `yes` or `no` to say whether there are more people to evaluate."},
    "bm.ask_project_more": {"es": "Perfecto. Escribe el nombre de uno de los proyectos en los que estás trabajando. Más adelante podrás evaluar el resto", "en": "Great. Type the name of one of the projects you're working on. You'll be able to evaluate the rest later"},
    "bm.reply_yes_no_projects": {"es": "Responde `sí` o `no` para indicar si hay más proyectos.", "en": "Reply `yes` or `no` to say whether there are more projects."},
    "bm.reply_yes_no": {"es": "Responde `sí` o `no`.", "en": "Reply `yes` or `no`."},
    "bm.situation_q": {"es": "¿Estás actualmente en proyecto o en barbecho?", "en": "Are you currently on a project or on the bench?"},
    "bm.btn_in_project": {"es": "🏗️ En proyecto", "en": "🏗️ On a project"},
    "bm.btn_in_bench": {"es": "⏸️ En barbecho", "en": "⏸️ On the bench"},
    "bm.barbecho_summary": {"es": "📋 Tus labores:\n_{labores}_\n\n¿Lo entrego o prefieres modificarlo?", "en": "📋 Your tasks:\n_{labores}_\n\nShall I submit it or do you prefer to edit it?"},
    "bm.btn_submit": {"es": "✅ Entregar", "en": "✅ Submit"},
    "bm.btn_edit": {"es": "✏️ Modificar", "en": "✏️ Edit"},
    "bm.barbecho_saved": {"es": "✅ Registrado. Perfecto, muchas gracias por tu tiempo ❤️. Ya puedes salir del hilo 👋", "en": "✅ Recorded. Great, thank you very much for your time ❤️. You can leave the thread now 👋"},
    "bm.err_save_notion": {"es": "⚠️ No se pudo guardar en Notion. Revisa permisos/logs.", "en": "⚠️ Could not save to Notion. Check permissions/logs."},
    "bm.ask_area_q": {"es": "¿A qué área perteneces? Pulsa el botón correspondiente", "en": "Which area do you belong to? Tap the corresponding button"},
    "bm.err_update_notion": {"es": "⚠️ No se pudo actualizar en Notion. Revisa permisos/logs.", "en": "⚠️ Could not update in Notion. Check permissions/logs."},
    "bm.ask_who_list": {"es": "¿A quién quieres evaluar?\n{lista}", "en": "Who do you want to evaluate?\n{lista}"},
    "bm.ask_who": {"es": "¿A quién quieres evaluar? Dime el nombre de la persona.", "en": "Who do you want to evaluate? Tell me the person's name."},
    "bm.already_completed": {"es": "Ya has completado tu evaluación mensual 👏 ¡Muchas gracias por tu tiempo! 👋", "en": "You've already completed your monthly evaluation 👏 Thank you very much for your time! 👋"},
    "bm.thanks_end": {"es": "Perfecto, muchas gracias por tu tiempo ❤️. Ya puedes salir del hilo 👋", "en": "Great, thank you very much for your time ❤️. You can leave the thread now 👋"},
    "bm.done_finished": {"es": "Perfecto, muchas gracias por tu tiempo ❤️. Ya puedes salir del hilo 👋", "en": "Great, thank you very much for your time ❤️. You can leave the thread now 👋"},
    "bm.dm_completada": {"es": "📍 *Evaluación mensual completada.*\nTienes 2 días de gracia para modificar tus respuestas, si lo deseas, entra en el hilo y pulsa modificar en el último mensaje.\nMuchas gracias por tu tiempo ❤️", "en": "📍 *Monthly evaluation completed.*\nYou have a 2-day grace period to edit your answers if you like—open the thread and tap edit on the last message.\nThank you very much for your time ❤️"},
    "bm.dm_expirada": {"es": "⌛ *Esta evaluación mensual ya está caducada.*\nHa llegado una evaluación más reciente; contéstala en su hilo.", "en": "⌛ *This monthly evaluation has expired.*\nA more recent evaluation has arrived; reply in its thread instead."},
    "bm.already_concluded": {"es": "Esta evaluación ya ha concluido, por favor salga del hilo. 👋", "en": "This evaluation has already concluded, please leave the thread. 👋"},
    "bm.no_active_eval": {"es": "⚠️ No hay ninguna evaluación activa en este momento.", "en": "⚠️ There's no active evaluation right now."},
    "bm.no_active_eval_short": {"es": "⚠️ No hay ninguna evaluación activa.", "en": "⚠️ There's no active evaluation."},
    "bm.edit_window_expired": {"es": "⚠️ El plazo de modificación de 2 días ha expirado.", "en": "⚠️ The 2-day editing window has expired."},
    "bm.yes_btn": {"es": "✅ Sí", "en": "✅ Yes"},
    "bm.no_btn": {"es": "❌ No", "en": "❌ No"},
    "bm.save_yes_btn": {"es": "✅ Sí, guardar", "en": "✅ Yes, save"},
    "bm.edit_btn": {"es": "✏️ Modificar", "en": "✏️ Edit"},
    "bm.edit_answers_btn": {"es": "✏️ Modificar respuestas", "en": "✏️ Edit answers"},
    "bm.more_projects_send": {"es": "¿Estás trabajando en algún otro proyecto?", "en": "Are you working on any other project?"},
    "bm.saved_more_members": {"es": "✅ *Evaluación guardada en Notion*.\n\n¿Quieres evaluar a otro miembro en el equipo?", "en": "✅ *Evaluation saved to Notion*.\n\nDo you want to evaluate another team member?"},
    "bm.edit_window_notice": {"es": "💬 Si quieres modificar tus respuestas, tienes un plazo de 2 días.", "en": "💬 If you want to edit your answers, you have a 2-day window."},
    "bm.whose_to_edit": {"es": "✏️ ¿La evaluación de quién quieres modificar?", "en": "✏️ Whose evaluation do you want to edit?"},
    "bm.answers_updated_more": {"es": "✅ ¡Respuestas actualizadas! ¿Quieres modificar la evaluación de alguien más?", "en": "✅ Answers updated! Do you want to edit someone else's evaluation?"},
    "bm.not_found_suggest": {"es": "*{nombre}* no aparece en la lista de empleados.\n¿Querías decir alguno de estos nombres?", "en": "*{nombre}* is not in the employee list.\nDid you mean one of these names?"},
    "bm.not_found": {"es": "*{nombre}* no aparece en la lista de empleados. Escribe nombre y apellido como aparece en la lista.", "en": "*{nombre}* is not in the employee list. Type the first and last name as they appear in the list."},
    "bm.rating_updated": {"es": "Valoración: *{v} / 5* ✅", "en": "Rating: *{v} / 5* ✅"},
    "bm.self_eval": {"es": "No puedes evaluarte a ti mismo. Dime el nombre de otro compañero del proyecto.", "en": "You can't evaluate yourself. Tell me another project member's name."},
    "bm.btn_alone_project": {"es": "🙋 Estoy solo en el proyecto", "en": "🙋 I'm alone on the project"},
    "bm.self_eval_selected": {"es": "🙋 *Autoevaluación* — estás solo en el proyecto, así que vas a evaluarte a ti mismo.", "en": "🙋 *Self-evaluation* — you're alone on the project, so you'll evaluate yourself."},
    "bm.rating_fallback": {"es": "Valoración: {v} / 5", "en": "Rating: {v} / 5"},
    "bm.situation_updated": {"es": "Situación: *{v}* ✅", "en": "Situation: *{v}* ✅"},
    "bm.situation_fallback": {"es": "Situación: {v}", "en": "Situation: {v}"},
    "bm.area_updated": {"es": "Área: *{v}* ✅", "en": "Area: *{v}* ✅"},
    "bm.area_fallback": {"es": "Área: {v}", "en": "Area: {v}"},
    "bm.situ_proyecto": {"es": "En proyecto 🏗️", "en": "On a project 🏗️"},
    "bm.situ_barbecho": {"es": "En barbecho ⏸️", "en": "On the bench ⏸️"},
    "bm.area_negocio": {"es": "Negocio", "en": "Business"},
    "bm.thread_not_eval": {"es": "⌛ Esta evaluación ha caducado. Tienes una evaluación más reciente pendiente: contéstala en su hilo, más abajo.", "en": "⌛ This evaluation has expired. You have a more recent evaluation pending: reply in its thread, further down."},
    "bm.eval_cancelled": {"es": "Evaluación *cancelada* voluntariamente. Si quieres volver a empezar, escribe cualquier mensaje en este hilo.", "en": "Evaluation *cancelled* voluntarily. If you want to start over, type any message in this thread."},
    "bm.which_member": {"es": "¿Qué miembro del proyecto quieres evaluar?", "en": "Which project member do you want to evaluate?"},
    "bm.not_found_full": {"es": "No encontré a *{nombre}* en la base de datos. Escribe nombre y apellido completos.", "en": "I couldn't find *{nombre}* in the database. Type the full first and last name."},
    "bm.reminder": {"es": "*⏰ Recuerda realizar tu evaluación mensual.* Abre el hilo del mensaje de evaluación y responde.", "en": "*⏰ Remember to complete your monthly evaluation.* Open the evaluation message thread and reply."},
    "bm.guide_example_title": {"es": "Ejemplo de guía", "en": "Guide example"},
    "bm.guide_example_header": {"es": "💡 *Ejemplo de guía — Evaluación Mensual*", "en": "💡 *Guide example — Monthly evaluation*"},
    "bm.close": {"es": "Cerrar", "en": "Close", "pt": "Fechar"},
    "bm.no_example": {"es": "_No hay ejemplo disponible_", "en": "_No example available_", "pt": "_Sem exemplo disponível_"},
    "bm.btn_show_item": {"es": "▶ Ver", "en": "▶ View", "pt": "▶ Ver"},
    "bm.btn_hide_item": {"es": "▼ Ocultar", "en": "▼ Hide", "pt": "▼ Ocultar"},
    "bp.examples_title": {"es": "Ejemplos de guía", "en": "Guide examples", "pt": "Exemplos de guia"},
    "bp.examples_intro": {"es": "💡 *Ejemplos de guía — Seguimiento personal*\nPulsa *Ver* en cada apartado para expandirlo:", "en": "💡 *Guide examples — Personal tracking*\nClick *View* on each item to expand it:", "pt": "💡 *Exemplos de guia — Acompanhamento pessoal*\nClica em *Ver* em cada secção para a expandir:"},
    "bp.examples_header": {"es": "💡 *Ejemplos de guía — Seguimiento personal*", "en": "💡 *Guide examples — Personal tracking*", "pt": "💡 *Exemplos de guia — Acompanhamento pessoal*"},
    "bp.no_personal_examples": {"es": "_No hay ejemplos personales disponibles_", "en": "_No personal examples available_", "pt": "_Sem exemplos pessoais disponíveis_"},
    "bp.criteria_title": {"es": "Criterios de evaluación", "en": "Evaluation criteria", "pt": "Critérios de avaliação"},
    "bp.criteria_title_short": {"es": "Criterios", "en": "Criteria", "pt": "Critérios"},
    "bp.criteria_intro": {"es": "📊 *Criterios de evaluación — {display}*\nPulsa *Ver* en cada subárea para expandirla:", "en": "📊 *Evaluation criteria — {display}*\nClick *View* on each sub-area to expand it:", "pt": "📊 *Critérios de avaliação — {display}*\nClica em *Ver* em cada subárea para a expandir:"},
    "bp.criteria_which_area": {"es": "¿Para qué área quieres ver los criterios?", "en": "Which area do you want to see the criteria for?", "pt": "Para que área queres ver os critérios?"},
    "bp.criteria_select_area": {"es": "Selecciona un área...", "en": "Select an area...", "pt": "Seleciona uma área..."},
    "bp.criteria_leadership_note": {"es": " _(solo Asociado Sr y Manager)_", "en": " _(Senior Associate and Manager only)_", "pt": " _(apenas Associado Sr e Manager)_"},
    "bm.no_reply_outside": {"es": "Por favor, no contestes a las evaluaciones fuera de los hilos 😊", "en": "Please don't reply to evaluations outside the threads 😊"},
    "bm.err_temp_data": {"es": "⚠️ Error temporal consultando datos. Vuelve a intentarlo.", "en": "⚠️ Temporary error fetching data. Please try again."},
    "bm.submitted": {"es": "✅ Entregado", "en": "✅ Submitted"},
    "bm.employee_selected": {"es": "Empleado seleccionado: *{nombre}* ✅", "en": "Selected employee: *{nombre}* ✅"},
    "bm.employee_selected_plain": {"es": "Empleado seleccionado: {nombre}", "en": "Selected employee: {nombre}"},

    # --- Bot Slack: seguimiento personal (personal_eval.py) ---
    "bp.back_btn": {"es": "⬅️ Atrás", "en": "⬅️ Back"},
    "bp.back_done": {"es": "⬅️ Volviste atrás", "en": "⬅️ Went back"},
    "bp.pendientes_link": {"es": "📋 También la tienes en tu <{url}|lista de pendientes>", "en": "📋 You can also find it in your <{url}|pending list>"},
    "bp.pendientes_titulo": {"es": "Seguimiento personal", "en": "Personal tracking"},
    "bp.opp_header": {"es": "*Esta es tu oportunidad para:*", "en": "*This is your chance to:*"},
    "bp.current_goals_header": {"es": "\U0001F4CC *Tus objetivos actuales:*", "en": "\U0001F4CC *Your current goals:*"},
    "bp.no_current_goals": {"es": "\U0001F4CC No tienes objetivos registrados actualmente.", "en": "\U0001F4CC You don't have any goals recorded at the moment."},
    "bp.opp_1": {"es": '*1.* Explicar cómo estás ayudando en _"Contribution to the firm"_', "en": '*1.* Explain how you\'re helping with _"Contribution to the firm"_'},
    "bp.opp_2": {"es": "*2.* Cómo te estás acercando a tus objetivos", "en": "*2.* How you're getting closer to your goals"},
    "bp.btn_view_goals": {"es": "📋 Ver mis objetivos", "en": "📋 View my goals"},
    "bp.opp_3": {"es": "*3.* Señalar limitaciones o aspectos relevantes respecto al cumplimiento de los criterios de evaluación", "en": "*3.* Point out limitations or relevant aspects regarding meeting the evaluation criteria"},
    "bp.btn_view_criteria": {"es": "📊 Ver criterios", "en": "📊 View criteria"},
    "bp.opp_4": {"es": "*4.* Si necesitas ayuda con algún tema o has tenido alguna dificultad que quieras comentar\n_El botón de urgencia notifica a tu CA por Slack. Si no lo pulsas, el problema no se notifica automáticamente y solo quedará registrado._", "en": "*4.* If you need help with anything or have had any difficulty you'd like to raise\n_The urgent button notifies your CA on Slack. If you don't press it, the issue isn't notified automatically and will only be recorded._"},
    "bp.pending_header": {"es": "📝 *Tienes opción de seguimiento personal pendiente*", "en": "📝 *You have a personal tracking option pending*"},
    "bp.pending_body": {"es": "_Recordatorio: esta evaluación es opcional. Recomendamos realizarla, pero no es obligatoria._\n_Esta evaluación es totalmente privada, solo podrá verla tu CA._\n_Si en algún momento quieres cancelar, escribe SOS en el hilo._", "en": "_Reminder: this evaluation is optional. We recommend completing it, but it's not mandatory._\n_This evaluation is fully private; only your CA can see it._\n_If at any point you want to cancel, type SOS in the thread._"},
    "bp.pending_fallback": {"es": "📝 Tienes opción de seguimiento personal pendiente", "en": "📝 You have a personal tracking option pending"},
    "bp.example_label": {"es": ":point_right: Ejemplo:", "en": ":point_right: Example:"},
    "bp.see_example": {"es": "Ver ejemplo", "en": "See example"},
    "bp.send_to_start": {"es": ":point_right: *Envía cualquier mensaje en el hilo para comenzar la evaluación*", "en": ":point_right: *Send any message in the thread to start the evaluation*"},
    "bp.saved_more_q": {"es": "✅ Evaluación guardada. ¿Quieres añadir otro comentario?", "en": "✅ Evaluation saved. Do you want to add another comment?"},
    "bp.urgency_to_ca": {"es": "🚨 *Urgencia de {nombre}*\n\n*Descripción:* {desc}\n\nPor favor, contacta con él/ella lo antes posible.", "en": "🚨 *Urgent from {nombre}*\n\n*Description:* {desc}\n\nPlease get in touch with them as soon as possible."},
    "bp.eval_finished": {"es": "Perfecto, muchas gracias por tu tiempo ❤️. Ya puedes salir del hilo 👋", "en": "Great, thank you very much for your time ❤️. You can leave the thread now 👋"},
    "bp.dm_completada": {"es": "📝 *Seguimiento personal completado*\nMuchas gracias por tu tiempo ❤️", "en": "📝 *Personal follow-up completed*\nThank you very much for your time ❤️"},
    "bp.dm_expirada": {"es": "⌛ *Este seguimiento personal ya está caducado.*\nHa llegado uno más reciente; contéstalo en su hilo.", "en": "⌛ *This personal follow-up has expired.*\nA more recent one has arrived; reply in its thread instead."},
    "bp.comment_summary": {"es": "📋 Tu comentario:\n_{texto}_\n\n¿Lo guardo? Responde *sí* para guardar o *modificar* para cambiar.", "en": "📋 Your comment:\n_{texto}_\n\nShall I save it? Reply *yes* to save or *edit* to change it."},
    "bp.can_reply": {"es": "Ya puedes responder.", "en": "You can reply now."},
    "bp.rewrite_comment": {"es": "Escribe de nuevo tu comentario:", "en": "Type your comment again:"},
    "bp.comment_summary_opts": {"es": "📋 Tu comentario:\n_{texto}_\n\nLas únicas opciones son elegir uno de los botones o escribir *SOS* para terminar y perder el contenido de la evaluación.", "en": "📋 Your comment:\n_{texto}_\n\nThe only options are to pick one of the buttons or type *SOS* to finish and lose the evaluation content."},
    "bp.what_else": {"es": "¿Qué más me quieres contar? Responde con tu comentario.", "en": "What else would you like to tell me? Reply with your comment."},
    "bp.q_topic": {"es": "¿Sobre qué vas a querer hablar hoy?", "en": "What would you like to talk about today?"},
    "bp.write_comment": {"es": "✍️ Escribe tu comentario.", "en": "✍️ Write your comment."},
    "bp.topic_cttf": {"es": "CTTF", "en": "CTTF"},
    "bp.topic_objetivos": {"es": "Objetivos", "en": "Goals"},
    "bp.topic_dificultades": {"es": "Dificultades", "en": "Difficulties"},
    "bp.topic_trayectoria": {"es": "Trayectoria", "en": "Trajectory"},
    "bp.topic_otro": {"es": "Otro", "en": "Other"},
    "bp.opportunity_share": {"es": "Esta es tu oportunidad para compartir tu progreso", "en": "This is your chance to share your progress"},
    "bp.btn_save_yes": {"es": "✅ Sí, guardar", "en": "✅ Yes, save"},
    "bp.err_save": {"es": "⚠️ No se pudo guardar en Notion. Revisa los permisos o contacta con soporte.", "en": "⚠️ Could not save to Notion. Check permissions or contact support."},
    "bp.reminder": {"es": "⏰ Recuerda que tienes una evaluación personal pendiente. Responde en este hilo cuando puedas.", "en": "⏰ Remember you have a personal evaluation pending. Reply in this thread when you can."},

    # --- Recordatorios de evaluaciones web pendientes (recordatorios_web.py) ---
    "web.reminder_proyecto": {"es": "⏰ Recuerda que tienes evaluaciones del proyecto *{proyecto}* pendientes. Complétalas en la web de evaluaciones cuando puedas.", "en": "⏰ Remember you have pending evaluations for project *{proyecto}*. Complete them on the evaluations website when you can."},
    "web.reminder_extra": {"es": "⏰ Recuerda que tienes una evaluación extra pendiente. Complétala en la web de evaluaciones cuando puedas.", "en": "⏰ Remember you have a pending extra evaluation. Complete it on the evaluations website when you can."},
    "web.eval_proyecto_activada": {"es": "📋 *Evaluaciones de proyecto activas* para el proyecto *{proyecto}*.\nRecuerda completarlas en la web de evaluaciones.", "en": "📋 *Project evaluations are now active* for project *{proyecto}*.\nRemember to complete them on the evaluations website."},
    "web.eval_proyecto_completada": {"es": "✅ Todos los miembros de tu equipo han terminado las evaluaciones del proyecto *{proyecto}*. Se cerrará el apartado en la web relacionado con este proyecto.", "en": "✅ All your team members have completed the evaluations for project *{proyecto}*. The section for this project will be closed on the website."},

    # --- Bot Slack: revision del CA sobre sus advisees (ca_reviews.py) ---
    "bc.informe_final_disponible": {"es": "🎉 Ya tienes disponible tu informe final. Puedes verlo en la web.", "en": "🎉 Your final report is now available. You can view it on the web."},
    "bc.back_btn": {"es": "⬅️ Atrás", "en": "⬅️ Back"},
    "bc.back_done": {"es": "⬅️ Volviste atrás", "en": "⬅️ Went back"},
    "bc.pendientes_link": {"es": "📋 También la tienes en tu <{url}|lista de pendientes>", "en": "📋 You can also find it in your <{url}|pending list>"},
    "bc.pendientes_titulo": {"es": "Opiniones CA", "en": "CA opinions"},
    "bc.mod_which": {"es": "¿Qué respuesta quieres modificar?\n1. Advisee\n2. Opinión\n\nResponde con el número o el nombre del campo.", "en": "Which answer do you want to edit?\n1. Advisee\n2. Opinion\n\nReply with the number or the field name."},
    "bc.mod_which_bold": {"es": "*¿Qué respuesta quieres modificar?*", "en": "*Which answer do you want to edit?*"},
    "bc.opinion_label": {"es": "Opinión", "en": "Opinion"},
    "bc.ask_advisee_name": {"es": "¿Cuál es el nombre de tu advisee?", "en": "What's the name of your advisee?"},
    "bc.ask_opinion": {"es": "Añade a continuación tus opiniones/puntos a añadir sobre esta información.", "en": "Add below your opinions/points to add about this information."},
    "bc.enter_new_answer": {"es": "Escribe la nueva respuesta.", "en": "Type the new answer."},
    "bc.not_found_suggest": {"es": "*{nombre}* no aparece tal cual en la lista de empleados.\n¿Querías decir alguno de estos nombres? Responde copiando el nombre exacto:\n{opciones}", "en": "*{nombre}* doesn't appear exactly like that in the employee list.\nDid you mean one of these names? Reply by copying the exact name:\n{opciones}"},
    "bc.not_found": {"es": "*{nombre}* no aparece tal cual en la lista de empleados. Escribe nombre y apellido como aparece en la lista.", "en": "*{nombre}* doesn't appear exactly like that in the employee list. Type the first and last name as they appear in the list."},
    "bc.pending_fallback": {"es": "📋 CA: Tienes seguimiento de tus advisees pendiente", "en": "📋 CA: You have advisee follow-up pending"},
    "bc.pending_intro": {"es": "📋 *CA: Tienes seguimiento de tus advisees pendiente*\n\n_Recordatorio: este seguimiento es opcional. Recomendamos realizarlo, pero no es obligatorio._\n_No es necesario realizar seguimiento de todos tus advisees si no lo consideras necesario._\n_Los comentarios registrados son totalmente privados, solo podrás verlos tú._\n_Si en algún momento quieres cancelar, escribe SOS en el hilo._", "en": "📋 *CA: You have advisee follow-up pending*\n\n_Reminder: this follow-up is optional. We recommend completing it, but it's not mandatory._\n_You don't need to follow up on every one of your advisees if you don't think it's necessary._\n_The comments you record are fully private; only you can see them._\n_If at any point you want to cancel, type SOS in the thread._"},
    "bc.all_advisees_done": {"es": "Perfecto, muchas gracias por tu tiempo ❤️. Ya puedes salir del hilo 👋", "en": "Great, thank you very much for your time ❤️. You can leave the thread now 👋"},
    "bc.which_advisee": {"es": "¿De qué advisee te gustaría hacer seguimiento?", "en": "Which advisee would you like to review?"},
    "bc.btn_finish": {"es": "❌ Terminar", "en": "❌ Finish"},
    "bc.advisee_not_in_list": {"es": "*{advisee}* no aparece en tu lista de advisees.\n\nPor favor, escribe el nombre o número correspondiente del advisee a evaluar. Si quieres terminar la evaluación escribe *no*", "en": "*{advisee}* is not in your advisee list.\n\nPlease type the name or number of the advisee to evaluate. If you want to finish the evaluation, type *no*"},
    "bc.evals_received_header": {"es": "📋 *EVALUACIONES DE {advisee} RECIBIDAS*", "en": "📋 *EVALUATIONS RECEIVED BY {advisee}*"},
    "bc.evals_proyecto_header": {"es": "📁 *EVALUACIONES DE PROYECTO DE {advisee}*", "en": "📁 *PROJECT EVALUATIONS OF {advisee}*"},
    "bc.evals_mensual_header": {"es": "🗓️ *EVALUACIONES MENSUALES DE {advisee}*", "en": "🗓️ *MONTHLY EVALUATIONS OF {advisee}*"},
    "bc.evals_personal_header": {"es": "🙋 *SEGUIMIENTO PERSONAL DE {advisee}*", "en": "🙋 *PERSONAL FOLLOW-UP OF {advisee}*"},
    "bc.no_new_evals": {"es": "*{advisee}*: no hay evaluaciones nuevas desde tu última revisión.", "en": "*{advisee}*: no new evaluations since your last review."},
    "bc.sin_evals_tipo": {"es": "_Sin evaluaciones nuevas de este tipo._", "en": "_No new evaluations of this type._"},
    "bc.btn_show_evals": {"es": "Ver evaluaciones", "en": "Show evaluations"},
    "bc.btn_hide_evals": {"es": "Ocultar", "en": "Hide"},
    "bc.evals_modal_title": {"es": "Evaluaciones", "en": "Evaluations"},
    "bc.claude_summary_q": {"es": "¿Quieres un resumen estructurado por competencias generado por Claude?", "en": "Do you want a competency-structured summary generated by Claude?"},
    "bc.claude_summary_q_full": {"es": "¿Quieres un resumen estructurado por competencias generado por Claude?\n_Evitar el uso excesivo por favor._", "en": "Do you want a competency-structured summary generated by Claude?\n_Please avoid overuse._"},
    "bc.yes": {"es": "Sí", "en": "Yes"},
    "bc.no": {"es": "No", "en": "No"},
    "bc.conf_summary": {"es": "*Resumen de tu valoración:*\n• Advisee: *{advisee}*\n• Opinión: {opinion}\n\nResponde o haz click en sí para guardar en Notion o modificar para cambiar una respuesta concreta.", "en": "*Summary of your assessment:*\n• Advisee: *{advisee}*\n• Opinion: {opinion}\n\nReply or click yes to save to Notion, or edit to change a specific answer."},
    "bc.btn_save_yes": {"es": "✅ Sí, guardar", "en": "✅ Yes, save"},
    "bc.no_associated_advisees": {"es": "- No tienes advisees asociados en Lista CA.", "en": "- You have no advisees associated in the CA List."},
    "bc.error_advisee_not_associated": {"es": "*{advisee}* existe en la lista de empleados, pero no aparece asociado a ti en `Lista CA`.\nTus advisees actuales:\n{opciones}\n\nEscribe uno de esos nombres.", "en": "*{advisee}* exists in the employee list, but is not associated with you in `CA List`.\nYour current advisees:\n{opciones}\n\nType one of those names."},
    "bc.error_advisee_suggest": {"es": "*{advisee}* no está en la lista de empleados.\n¿Querías decir alguno de estos? Copia el nombre exacto:\n{opciones}", "en": "*{advisee}* is not in the employee list.\nDid you mean one of these? Copy the exact name:\n{opciones}"},
    "bc.error_advisee_no_suggest": {"es": "*{advisee}* no está en la lista de empleados. Escríbelo sin tildes, primera letra del nombre y primer apellido en mayúscula, solo primer apellido.", "en": "*{advisee}* is not in the employee list. Type it without accents, first letter of the first name and first surname capitalised, first surname only."},
    "bc.claude_summary_result": {"es": "📊 *Resumen generado por Claude:*\n\n{resumen}\n\nAñade a continuación tus opiniones/puntos a añadir sobre esta información.", "en": "📊 *Summary generated by Claude:*\n\n{resumen}\n\nAdd below your opinions/points to add about this information.", "pt": "📊 *Resumo gerado pelo Claude:*\n\n{resumen}\n\nAdiciona a seguir as tuas opiniões/pontos a acrescentar sobre esta informação."},
    "bc.claude_summary_header": {"es": "📊 *Resumen generado por Claude:*", "en": "📊 *Summary generated by Claude:*", "pt": "📊 *Resumo gerado pelo Claude:*"},
    "bc.claude_summary_error": {"es": "⚠️ No se pudo generar el resumen con Claude.\n\nAñade a continuación tus opiniones/puntos a añadir sobre esta información.", "en": "⚠️ Could not generate the summary with Claude.\n\nAdd below your opinions/points to add about this information."},
    "bc.claude_summary_fail": {"es": "No se pudo generar el resumen con Claude.", "en": "Could not generate the summary with Claude.", "pt": "Não foi possível gerar o resumo com o Claude."},
    # Fallos de la API de Claude, por el `codigo` del ErrorIA (ver backend/ia.py). Se buscan
    # con texto_error_ia(): el mensaje del propio error va siempre en español, y estas claves
    # son las que ve un usuario en inglés o portugués.
    "ia.ia_sin_saldo": {"es": "La API de Claude asociada a esta herramienta se ha quedado sin saldo. Contacta con el organizador de la cuenta de Claude (tech@igeneris.com) o con el responsable de la herramienta.", "en": "The Claude API used by this tool has run out of credit. Please contact the Claude account owner (tech@igeneris.com) or the tool's owner.", "pt": "A API do Claude associada a esta ferramenta ficou sem saldo. Contacta o organizador da conta do Claude (tech@igeneris.com) ou o responsável pela ferramenta."},
    "ia.ia_config": {"es": "La API de Claude asociada a esta herramienta no está bien configurada y ha rechazado la petición. Contacta con el responsable de la herramienta (tech@igeneris.com).", "en": "The Claude API used by this tool is misconfigured and rejected the request. Please contact the tool's owner (tech@igeneris.com).", "pt": "A API do Claude associada a esta ferramenta não está bem configurada e rejeitou o pedido. Contacta o responsável pela ferramenta (tech@igeneris.com)."},
    "ia.ia_no_configurada": {"es": "La IA no está disponible: a esta herramienta le falta la clave de la API de Claude. Contacta con el responsable de la herramienta (tech@igeneris.com).", "en": "The AI is unavailable: this tool is missing the Claude API key. Please contact the tool's owner (tech@igeneris.com).", "pt": "A IA não está disponível: falta a chave da API do Claude nesta ferramenta. Contacta o responsável pela ferramenta (tech@igeneris.com)."},
    "ia.ia_saturada": {"es": "La IA está saturada en este momento. Espera un par de minutos y vuelve a intentarlo; si sigue fallando, avisa al responsable de la herramienta (tech@igeneris.com).", "en": "The AI is overloaded right now. Wait a couple of minutes and try again; if it keeps failing, let the tool's owner know (tech@igeneris.com).", "pt": "A IA está sobrecarregada neste momento. Espera uns minutos e tenta de novo; se continuar a falhar, avisa o responsável pela ferramenta (tech@igeneris.com)."},
    "ia.ia_conexion": {"es": "No se ha podido conectar con la IA. Comprueba tu conexión y vuelve a intentarlo; si sigue fallando, avisa al responsable de la herramienta (tech@igeneris.com).", "en": "Could not connect to the AI. Check your connection and try again; if it keeps failing, let the tool's owner know (tech@igeneris.com).", "pt": "Não foi possível ligar à IA. Verifica a tua ligação e tenta de novo; se continuar a falhar, avisa o responsável pela ferramenta (tech@igeneris.com)."},
    "ia.ia_entrada_larga": {"es": "Hay demasiada información para que la IA la procese de una vez. Acorta el texto y vuelve a intentarlo; si no puedes, avisa al responsable de la herramienta (tech@igeneris.com).", "en": "There is too much information for the AI to process at once. Shorten the text and try again; if you can't, let the tool's owner know (tech@igeneris.com).", "pt": "Há demasiada informação para a IA processar de uma vez. Encurta o texto e tenta de novo; se não for possível, avisa o responsável pela ferramenta (tech@igeneris.com)."},
    "ia.ia_error": {"es": "La IA no ha podido responder ahora mismo. Vuelve a intentarlo; si sigue fallando, avisa al responsable de la herramienta (tech@igeneris.com).", "en": "The AI could not answer right now. Try again; if it keeps failing, let the tool's owner know (tech@igeneris.com).", "pt": "A IA não conseguiu responder neste momento. Tenta de novo; se continuar a falhar, avisa o responsável pela ferramenta (tech@igeneris.com)."},
    "bc.ask_comment": {"es": "¿Te gustaría opinar o comentar algo extra sobre la información disponible para hacer seguimiento de tu advisee?", "en": "Would you like to share any opinion or extra comment about the information available to follow up on your advisee?"},
    "bc.clarify_claude": {"es": "Responde `sí` para generar un resumen con Claude, o `no` para continuar directamente.", "en": "Reply `yes` to generate a summary with Claude, or `no` to continue directly."},
    "bc.opinion_not_saved": {"es": "De acuerdo, no se guardará esta opinión.\n\n", "en": "Okay, this opinion won't be saved.\n\n"},
    "bc.cannot_save_not_associated": {"es": "No puedo guardar esta opinión: *{advisee}* no aparece asociado a ti en `Lista CA`.\nTus advisees actuales:\n{opciones}", "en": "I can't save this opinion: *{advisee}* is not associated with you in `CA List`.\nYour current advisees:\n{opciones}"},
    "bc.opinion_saved": {"es": "✅ Opinión guardada en Notion.\n\n", "en": "✅ Opinion saved to Notion.\n\n"},
    "bc.opinion_save_error": {"es": "⚠️ No se pudo guardar en Notion: `{error}`\n\n", "en": "⚠️ Could not save to Notion: `{error}`\n\n"},
    "bc.thanks_end": {"es": "Perfecto, muchas gracias por tu tiempo ❤️. Ya puedes salir del hilo 👋", "en": "Great, thank you very much for your time ❤️. You can leave the thread now 👋"},
    "bc.dm_completada": {"es": "📋 *Evaluación de advisees completada*\nMuchas gracias por tu tiempo ❤️", "en": "📋 *Advisee evaluation completed*\nThank you very much for your time ❤️"},
    "bc.dm_expirada": {"es": "⌛ *Esta evaluación de advisees ya está caducada.*\nHa llegado una más reciente; contéstala en su hilo.", "en": "⌛ *This advisee evaluation has expired.*\nA more recent one has arrived; reply in its thread instead."},
    "bc.already_concluded": {"es": "Esta evaluación ya ha concluido. 👋", "en": "This evaluation has already concluded. 👋"},
    "bc.info_intro": {"es": "Vamos a mostrarte toda la información disponible de tu advisee *{advisee}* (evaluaciones que le han hecho, seguimiento del propio advisee y objetivos).", "en": "Here's all the available information about your advisee *{advisee}* (evaluations received, the advisee's own follow-up, and objectives)."},
    "bc.advisee_selected": {"es": "Advisee: *{name}* ✅", "en": "Advisee: *{name}* ✅"},
    "bc.advisee_selected_plain": {"es": "Advisee: {name}", "en": "Advisee: {name}"},
    "bc.finished_update": {"es": "❌ Terminado", "en": "❌ Finished"},
    "bc.reminder": {"es": "*📋 Recuerda realizar tu revisión de Career Advisor.* Abre el hilo del mensaje CA y responde.", "en": "*📋 Remember to complete your Career Advisor review.* Open the CA message thread and reply."},
    "bc.guide_example_header": {"es": "💡 *Ejemplo de guía — Evaluación CA*", "en": "💡 *Guide example — CA evaluation*"},
    "bc.claude_yes_update": {"es": "✅ Sí, generar resumen con Claude", "en": "✅ Yes, generate summary with Claude"},
    "bc.claude_no_update": {"es": "❌ No, continuar sin resumen", "en": "❌ No, continue without summary"},

    # --- Evaluaciones de proyecto: errores de API (project_evals.py) ---
    "pe.err_db_access_notion": {"es": "No se pudo acceder a la BD de activaciones en Notion.", "en": "Could not access the activations database in Notion."},
    "pe.err_project_exists": {"es": "Ya existe un proyecto activo con el nombre «{proyecto}». Elige un nombre diferente.", "en": "There's already an active project named «{proyecto}». Choose a different name."},
    "pe.err_db_access": {"es": "No se pudo acceder a la BD de activaciones.", "en": "Could not access the activations database."},
    "pe.err_add_member": {"es": "Error interno al añadir miembro.", "en": "Internal error while adding member."},
    "pe.err_member_not_found": {"es": "No se encontró ese miembro en el proyecto.", "en": "That member was not found in the project."},
    "pe.err_remove_member": {"es": "Error interno al eliminar miembro.", "en": "Internal error while removing member."},

    # --- Evaluaciones extra (fuera de proyecto) (evaluaciones_extra.py) ---
    "evex.err_db_access": {"es": "No se pudo acceder a la BD de solicitudes en Notion.", "en": "Could not access the requests database in Notion."},
    "evex.err_request": {"es": "Error interno al crear la solicitud.", "en": "Internal error while creating the request."},
    "evex.slack_solicitud": {"es": "📩 *{evaluado}* te ha pedido que le evalúes sobre: _{contexto}_\nEs opcional: si quieres responder, hazlo en la web de evaluaciones.", "en": "📩 *{evaluado}* has asked you to evaluate them on: _{contexto}_\nIt's optional: if you want to answer, do it on the evaluations website."},
    "pe.err_missing_project": {"es": "Falta el nombre del proyecto.", "en": "The project name is missing."},
    "pe.err_select_employee": {"es": "Debes seleccionar al menos un empleado.", "en": "You must select at least one employee."},
    "pe.err_missing_fields": {"es": "Faltan campos obligatorios.", "en": "Required fields are missing."},
    "pe.err_not_your_project": {"es": "Este proyecto no es tuyo o no está activo.", "en": "This project isn't yours or isn't active."},
    # --- Recordatorios de evaluaciones de proyecto (DM de Slack) ---
    "rec.reminder": {
        "es": "Tu manager de proyecto *{manager}* te recuerda que tienes *{n}* evaluación(es) pendiente(s) del proyecto *{proyecto}*:\n{lista}\nEntra en la web y rellénalas. ¡Gracias!",
        "en": "Your project manager *{manager}* reminds you that you have *{n}* pending evaluation(s) for the project *{proyecto}*:\n{lista}\nLog in to the web app and complete them. Thanks!",
    },
    "rec.item_self": {"es": "tu autoevaluación", "en": "your self-evaluation"},
    "rec.item_eval": {"es": "la evaluación de {nombre}", "en": "the evaluation of {nombre}"},
    "bm.editing": {"es": "✏️ Modificando...", "en": "✏️ Editing..."},
    "report.word_meta": {
        "es": "Generado el {fecha}. Evaluado: {evaluado}. Evaluaciones analizadas: {n}.",
        "en": "Generated on {fecha}. Employee: {evaluado}. Evaluations analyzed: {n}.",
    },
    # --- Visualizacion "trayectoria" (reports.py guardar_trayectoria_react) ---
    "traj.title": {"es": "Trayectoria", "en": "Journey"},
    "traj.h1": {"es": "Tu trayectoria de evaluación.", "en": "Your evaluation journey."},
    "traj.subtitle": {"es": "Navega por fecha, proyecto y satisfacción.", "en": "Browse by date, project and satisfaction."},
    "traj.prev": {"es": "Anterior", "en": "Previous"},
    "traj.next": {"es": "Siguiente", "en": "Next"},
    "traj.project": {"es": "Proyecto:", "en": "Project:"},
    "traj.no_project": {"es": "Sin proyecto", "en": "No project"},
    "traj.rating": {"es": "Valoración", "en": "Rating"},
    "traj.example": {"es": "Ejemplo", "en": "Example"},
    "traj.no_answer": {"es": "Sin respuesta", "en": "No answer"},
    "traj.no_evals": {"es": "No hay evaluaciones todavía.", "en": "No evaluations yet."},
    "traj.no_date": {"es": "Sin fecha", "en": "No date"},
    "traj.nivel_superior": {"es": "Superior", "en": "Superior"},
    "traj.nivel_igual": {"es": "Igual nivel", "en": "Same level"},
    "traj.nivel_inferior": {"es": "Subordinado", "en": "Subordinate"},
    # --- Informe anual (skill_informes_anual.py) ---
    "anual.title_web": {"es": "Informe anual — {emp}", "en": "Annual report — {emp}"},
    "anual.eval_year": {"es": "Evaluación anual {anio}", "en": "Annual evaluation {anio}"},
    "anual.generated": {"es": "Generado el {fecha}", "en": "Generated on {fecha}"},
    "anual.name": {"es": "Nombre", "en": "Name"},
    "anual.role": {"es": "Cargo", "en": "Position"},
    "anual.rating_year": {"es": "CALIFICACIÓN {anio}", "en": "RATING {anio}"},
    "anual.col_dimension": {"es": "Dimensión", "en": "Dimension"},
    "anual.col_score": {"es": "Nota", "en": "Score"},
    "anual.col_eval_comments": {"es": "Comentarios del evaluador", "en": "Evaluator comments"},
    "anual.leadership": {"es": "LIDERAZGO", "en": "LEADERSHIP"},
    "anual.result": {"es": "RESULTADO", "en": "RESULT"},
    "anual.overall_score": {"es": "Nota global", "en": "Overall score"},
    "anual.objectives_year": {"es": "OBJETIVOS {anio}", "en": "OBJECTIVES {anio}"},
    "anual.no_goals": {"es": "Sin objetivos registrados.", "en": "No goals recorded."},
    "anual.rev_warn_title": {"es": "⚠ Afirmaciones a revisar (posiblemente no respaldadas por su cita)", "en": "⚠ Statements to review (possibly not backed by their citation)"},
    "anual.rev_discarded_title": {"es": "🗑 Bullets descartados automáticamente (no citaban ninguna fuente)", "en": "🗑 Bullets automatically discarded (they cited no source)"},
    "anual.rev_title": {"es": "Revisión del Career Advisor", "en": "Career Advisor review"},
    "anual.rev_sub": {"es": "Este bloque solo aparece en el borrador. Revísalo y edítalo antes de publicar el informe final.", "en": "This block only appears in the draft. Review and edit it before publishing the final report."},
    "anual.sources_evidence": {"es": "FUENTES / EVIDENCIA", "en": "SOURCES / EVIDENCE"},
    "anual.sources_intro_web": {"es": "Cada cita [X#] del informe enlaza aquí. Esta es la evidencia en bruto (proyecto, evaluador, fecha y texto) para que puedas contrastar cada afirmación.", "en": "Each citation [X#] in the report links here. This is the raw evidence (project, evaluator, date and text) so you can check every statement."},
    "anual.sources_intro_docx": {"es": "Cada cita [X#] del informe enlaza a su ficha aquí: la evidencia en bruto para contrastar.", "en": "Each citation [X#] in the report links to its entry here: the raw evidence to check against."},
    "anual.src_opinion": {"es": "Opinión CA", "en": "CA opinion"},
    "anual.src_evaluacion": {"es": "Evaluación mensual", "en": "Monthly evaluation"},
    "anual.src_proyecto": {"es": "Evaluación de proyecto", "en": "Project evaluation"},
    "anual.src_seguimiento": {"es": "Seguimiento personal", "en": "Personal tracking"},
    "anual.src_barbecho": {"es": "Barbecho", "en": "Bench"},
    "anual.src_extra": {"es": "Evaluación extra", "en": "Extra evaluation"},
    "anual.src_aportacion_ca": {"es": "Aportación del CA", "en": "CA input"},
    "anual.additional_evals": {"es": "EVALUACIONES ADICIONALES", "en": "ADDITIONAL EVALUATIONS"},
    "anual.doc_title": {"es": "EVALUACIÓN ANUAL", "en": "ANNUAL EVALUATION"},
    "anual.employee": {"es": "Empleado", "en": "Employee"},
    "anual.date": {"es": "Fecha", "en": "Date"},
    "anual.current_position": {"es": "Posición actual", "en": "Current position"},
    "anual.current_salary": {"es": "Salario actual", "en": "Current salary"},
    "anual.projects": {"es": "PROYECTOS", "en": "PROJECTS"},
    "anual.score_up": {"es": "NOTA", "en": "SCORE"},
    "anual.comments_up": {"es": "COMENTARIOS", "en": "COMMENTS"},
    "anual.final_projects": {"es": "Nota final Proyectos", "en": "Final Projects score"},
    "anual.variable_60": {"es": "Variable (60%)", "en": "Variable (60%)"},
    "anual.final_contrib": {"es": "Nota final Contrib. To the firm (10%)", "en": "Final Contrib. to the firm score (10%)"},
    "anual.variable": {"es": "Variable", "en": "Variable"},
    "anual.corp_objectives": {"es": "Consecución Objetivos corp.", "en": "Corp. objectives achievement"},
    "anual.variable_30": {"es": "Variable (30%)", "en": "Variable (30%)"},
    "anual.total_variable": {"es": "Total Variable {yy} =", "en": "Total Variable {yy} ="},
    "anual.eval_result": {"es": "RESULTADO EVAL {yy}", "en": "EVAL RESULT {yy}"},
    "anual.promotion": {"es": "PROMOCIÓN", "en": "PROMOTION"},
    "anual.position_next": {"es": "POSICIÓN {yy}", "en": "POSITION {yy}"},
    "anual.new_fixed_salary": {"es": "Nuevo salario fijo =", "en": "New fixed salary ="},
    "anual.improvement_objectives": {"es": "OPORTUNIDADES DE MEJORA / OBJETIVOS {yy}", "en": "IMPROVEMENT OPPORTUNITIES / OBJECTIVES {yy}"},
    # Instruccion de idioma para el prompt de Claude (informe principal)
    "report.prompt": {
        "es": (
            "Eres un consultor senior de People Analytics. Genera un informe profesional en espanol "
            "sobre las evaluaciones recibidas. Usa este formato exacto con titulos claros:\n"
            "1. Resumen ejecutivo\n2. Metricas principales\n3. Fortalezas detectadas\n"
            "4. Riesgos o areas de mejora\n5. Recomendaciones accionables\n6. Conclusion\n\n"
            "Se concreto, no inventes datos y menciona patrones repetidos si los hay. "
            "Si hay comentarios de evaluaciones personales, integralos en el analisis."
        ),
        "en": (
            "You are a senior People Analytics consultant. Write a professional report in English "
            "about the evaluations received. Use this exact format with clear headings:\n"
            "1. Executive summary\n2. Key metrics\n3. Detected strengths\n"
            "4. Risks or areas for improvement\n5. Actionable recommendations\n6. Conclusion\n\n"
            "Be specific, do not make up data, and mention repeated patterns if any. "
            "If there are personal evaluation comments, integrate them into the analysis. "
            "The source data may be in Spanish; write your report in English regardless."
        ),
        "pt": (
            "És um consultor sénior de People Analytics. Gera um relatório profissional em português europeu "
            "sobre as avaliações recebidas. Usa este formato exato com títulos claros:\n"
            "1. Resumo executivo\n2. Métricas principais\n3. Pontos fortes detetados\n"
            "4. Riscos ou áreas de melhoria\n5. Recomendações acionáveis\n6. Conclusão\n\n"
            "Sê concreto, não inventes dados e menciona padrões repetidos, se os houver. "
            "Se houver comentários de avaliações pessoais, integra-os na análise. "
            "Os dados de origem podem estar em espanhol; escreve o teu relatório em português na mesma."
        ),
    },
}


def normalizar_idioma(idioma: str | None) -> str:
    """Devuelve un codigo de idioma valido; cae a 'es' si es None o desconocido."""
    if idioma in IDIOMAS_SOPORTADOS:
        return idioma
    return IDIOMA_POR_DEFECTO


def botones_idioma_slack(action_id_prefix: str) -> dict:
    """Bloque 'actions' con un botón por idioma (bandera + código), para elegirlo
    directamente en lugar de ir rotando. `action_id` de cada botón: '{prefix}_{es|en|pt}'."""
    return {
        "type": "actions",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": f"{_BANDERA_IDIOMA[code]} {_ETIQUETA_IDIOMA[code]}", "emoji": True},
                "action_id": f"{action_id_prefix}_{code}",
                "value": code,
            }
            for code in IDIOMAS_SOPORTADOS
        ],
    }


def t(clave: str, idioma: str = IDIOMA_POR_DEFECTO, **kwargs) -> str:
    """Traduce `clave` al `idioma` dado. Aplica str.format con kwargs si los hay.

    - Si la clave no existe en el catalogo, avisa por log y devuelve la clave
      (para detectar textos sin traducir durante el desarrollo).
    - Si falta la traduccion en el idioma pedido, cae al idioma por defecto.
    """
    entrada = TEXTOS.get(clave)
    if entrada is None:
        logging.warning("i18n: clave sin traducir '%s'", clave)
        return clave.format(**kwargs) if kwargs else clave
    idioma = normalizar_idioma(idioma)
    texto = entrada.get(idioma) or entrada.get(IDIOMA_POR_DEFECTO) or clave
    if kwargs:
        try:
            return texto.format(**kwargs)
        except (KeyError, IndexError):
            return texto
    return texto


def texto_error_ia(codigo: str, respaldo: str, idioma: str = IDIOMA_POR_DEFECTO) -> str:
    """Mensaje de un ErrorIA en el idioma del usuario.

    `respaldo` es el mensaje del propio error (español), que se usa si ese código todavía
    no está traducido: mejor un texto útil en otro idioma que la clave en crudo, así que
    esto no pasa por t() a propósito.
    """
    entrada = TEXTOS.get(f"ia.{codigo}")
    if entrada is None:
        return respaldo
    return entrada.get(normalizar_idioma(idioma)) or respaldo


# --- Overlay de traducciones PT (generado por backend/generar_i18n_pt.py) ---
# Se fusiona sobre TEXTOS si existe backend/i18n_pt.py. Si falta, 'pt' cae a 'es'.
try:
    from .i18n_pt import TEXTOS_PT as _TEXTOS_PT
    for _clave_pt, _texto_pt in _TEXTOS_PT.items():
        if _clave_pt in TEXTOS and _texto_pt and "pt" not in TEXTOS[_clave_pt]:
            TEXTOS[_clave_pt]["pt"] = _texto_pt
except Exception:
    pass


# --- Etiquetas fijas de evaluación (dimensiones/criterios + enunciados) -------
# Los nombres de criterio y algunos enunciados recurrentes se guardan en español
# (clave estable en Notion y en el código: _DIMS_*, CRITERIOS, _PREGUNTAS_*).
# Este mapa los traduce solo al mostrarlos. Clave = texto ES en minúsculas.
# "Contribution to the firm" y las opciones Exceeds/Achieves/Expects more se dejan
# en inglés a propósito (no están en el mapa → pasan tal cual).
_ETIQUETAS_EVAL: dict[str, dict[str, str]] = {
    # Dimensiones / categorías
    "gestión del proyecto":    {"en": "Project management",  "pt": "Gestão do projeto"},
    "gestión de proyecto":     {"en": "Project management",  "pt": "Gestão do projeto"},
    "calidad técnica":         {"en": "Technical quality",   "pt": "Qualidade técnica"},
    "trabajo en equipo":       {"en": "Teamwork",            "pt": "Trabalho em equipa"},
    "comunicación":            {"en": "Communication",       "pt": "Comunicação"},
    "relación con el cliente": {"en": "Client relationship", "pt": "Relação com o cliente"},
    "liderazgo":               {"en": "Leadership",          "pt": "Liderança"},
    "desarrollo de talento":   {"en": "Talent Development",  "pt": "Desenvolvimento de Talento"},
    "motivación":              {"en": "Motivation",          "pt": "Motivação"},
    "referente":               {"en": "Role model",          "pt": "Referência"},
    "resultado global":        {"en": "Overall result",      "pt": "Resultado global"},
    # Enunciados recurrentes (autoevaluación / genéricos)
    "grado de satisfacción contigo mismo":         {"en": "Level of satisfaction with yourself", "pt": "Grau de satisfação contigo mesmo"},
    "grado de satisfacción con tu equipo":         {"en": "Level of satisfaction with your team", "pt": "Grau de satisfação com a tua equipa"},
    "justifica tu respuesta":                      {"en": "Justify your answer", "pt": "Justifica a tua resposta"},
    "justifica tu respuesta anterior con ejemplos":{"en": "Justify your previous answer with examples", "pt": "Justifica a tua resposta anterior com exemplos"},
    "añadir comentarios que aporten información":  {"en": "Add comments that provide information", "pt": "Adiciona comentários que acrescentem informação"},
}


def traducir_dimension(nombre: str, idioma: str = IDIOMA_POR_DEFECTO) -> str:
    """Traduce una etiqueta fija de evaluación (dimensión/criterio o enunciado
    recurrente). Si no está en el mapa (p.ej. 'Contribution to the firm', un
    enunciado largo de Notion) o el idioma es 'es', devuelve el texto tal cual.
    La búsqueda ignora mayúsculas/minúsculas y espacios."""
    idioma = normalizar_idioma(idioma)
    if idioma == "es":
        return nombre
    return _ETIQUETAS_EVAL.get((nombre or "").strip().lower(), {}).get(idioma, nombre)