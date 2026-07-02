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
IDIOMAS_SOPORTADOS = ("es", "en")

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
    # --- Bot Slack: evaluacion mensual de proyecto (slack_bot.py) ---
    "bm.back_btn": {"es": "⬅️ Atrás", "en": "⬅️ Back"},
    "bm.back_done": {"es": "⬅️ Volviste atrás", "en": "⬅️ Went back"},
    "bm.pendientes_link": {"es": "📋 También la tienes en tu <{url}|lista de pendientes>", "en": "📋 You can also find it in your <{url}|pending list>"},
    "bm.pendientes_titulo": {"es": "Evaluación mensual", "en": "Monthly evaluation"},
    "bm.pending_fallback": {"es": "📍 Tienes una evaluación mensual pendiente", "en": "📍 You have a monthly evaluation pending"},
    "bm.pending_intro": {
        "es": ("📍 *Tienes una evaluación mensual pendiente.*\n\n"
               "_Esta evaluación es totalmente privada, solo podrá verla el CA de la persona evaluada._\n"
               "_Si en algún momento quieres cancelar, escribe SOS en el hilo._"),
        "en": ("📍 *You have a monthly evaluation pending.*\n\n"
               "_This evaluation is fully private; only the evaluated person's CA can see it._\n"
               "_If at any point you want to cancel, type SOS in the thread._"),
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
    "bm.reply_1_4": {"es": "Por favor, responde con un número del 1 al 4 🔢", "en": "Please reply with a number from 1 to 4 🔢"},
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
    "bm.barbecho_saved": {"es": "✅ Registrado. Muchas gracias, ya puedes salir del hilo 👋", "en": "✅ Recorded. Thank you very much, you can leave the thread now 👋"},
    "bm.err_save_notion": {"es": "⚠️ No se pudo guardar en Notion. Revisa permisos/logs.", "en": "⚠️ Could not save to Notion. Check permissions/logs."},
    "bm.ask_area_q": {"es": "¿A qué área perteneces? Pulsa el botón correspondiente", "en": "Which area do you belong to? Tap the corresponding button"},
    "bm.err_update_notion": {"es": "⚠️ No se pudo actualizar en Notion. Revisa permisos/logs.", "en": "⚠️ Could not update in Notion. Check permissions/logs."},
    "bm.ask_who_list": {"es": "¿A quién quieres evaluar?\n{lista}", "en": "Who do you want to evaluate?\n{lista}"},
    "bm.ask_who": {"es": "¿A quién quieres evaluar? Dime el nombre de la persona.", "en": "Who do you want to evaluate? Tell me the person's name."},
    "bm.already_completed": {"es": "Ya has completado tu evaluación mensual 👏 ¡Muchas gracias por tu tiempo! 👋", "en": "You've already completed your monthly evaluation 👏 Thank you very much for your time! 👋"},
    "bm.thanks_end": {"es": "Perfecto, muchas gracias por tu tiempo ❤️. Ya puedes salir del hilo 👋", "en": "Great, thank you very much for your time ❤️. You can leave the thread now 👋"},
    "bm.done_finished": {"es": "✅ ¡Listo! Evaluación finalizada. Muchas gracias 👋", "en": "✅ Done! Evaluation finished. Thank you very much 👋"},
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
    "bm.saved_more_members": {"es": "✅ *Evaluación guardada en Notion*.\n\n¿Hay más miembros en el equipo que quieras evaluar?", "en": "✅ *Evaluation saved to Notion*.\n\nAre there more team members you'd like to evaluate?"},
    "bm.edit_window_notice": {"es": "💬 Si quieres modificar tus respuestas, tienes un plazo de 2 días.", "en": "💬 If you want to edit your answers, you have a 2-day window."},
    "bm.whose_to_edit": {"es": "✏️ ¿La evaluación de quién quieres modificar?", "en": "✏️ Whose evaluation do you want to edit?"},
    "bm.answers_updated_more": {"es": "✅ ¡Respuestas actualizadas! ¿Quieres modificar la evaluación de alguien más?", "en": "✅ Answers updated! Do you want to edit someone else's evaluation?"},
    "bm.not_found_suggest": {"es": "*{nombre}* no aparece en la lista de empleados.\n¿Querías decir alguno de estos nombres?", "en": "*{nombre}* is not in the employee list.\nDid you mean one of these names?"},
    "bm.not_found": {"es": "*{nombre}* no aparece en la lista de empleados. Escribe nombre y apellido como aparece en la lista.", "en": "*{nombre}* is not in the employee list. Type the first and last name as they appear in the list."},
    "bm.rating_updated": {"es": "Valoración: *{v} / 4* ✅", "en": "Rating: *{v} / 4* ✅"},
    "bm.rating_fallback": {"es": "Valoración: {v} / 4", "en": "Rating: {v} / 4"},
    "bm.situation_updated": {"es": "Situación: *{v}* ✅", "en": "Situation: *{v}* ✅"},
    "bm.situation_fallback": {"es": "Situación: {v}", "en": "Situation: {v}"},
    "bm.area_updated": {"es": "Área: *{v}* ✅", "en": "Area: *{v}* ✅"},
    "bm.area_fallback": {"es": "Área: {v}", "en": "Area: {v}"},
    "bm.situ_proyecto": {"es": "En proyecto 🏗️", "en": "On a project 🏗️"},
    "bm.situ_barbecho": {"es": "En barbecho ⏸️", "en": "On the bench ⏸️"},
    "bm.area_negocio": {"es": "Negocio", "en": "Business"},
    "bm.thread_not_eval": {"es": "Este hilo no es una evaluación. Por favor, ve al mensaje de la evaluación y contesta ahí.", "en": "This thread is not an evaluation. Please go to the evaluation message and reply there."},
    "bm.eval_cancelled": {"es": "Evaluación *cancelada* voluntariamente. Si quieres volver a empezar, escribe cualquier mensaje en este hilo.", "en": "Evaluation *cancelled* voluntarily. If you want to start over, type any message in this thread."},
    "bm.which_member": {"es": "¿Qué miembro del proyecto quieres evaluar?", "en": "Which project member do you want to evaluate?"},
    "bm.not_found_full": {"es": "No encontré a *{nombre}* en la base de datos. Escribe nombre y apellido completos.", "en": "I couldn't find *{nombre}* in the database. Type the full first and last name."},
    "bm.reminder": {"es": "*⏰ Recuerda realizar tu evaluación mensual.* Abre el hilo del mensaje de evaluación y responde.", "en": "*⏰ Remember to complete your monthly evaluation.* Open the evaluation message thread and reply."},
    "bm.guide_example_title": {"es": "Ejemplo de guía", "en": "Guide example"},
    "bm.guide_example_header": {"es": "💡 *Ejemplo de guía — Evaluación Mensual*", "en": "💡 *Guide example — Monthly evaluation*"},
    "bm.close": {"es": "Cerrar", "en": "Close"},
    "bm.no_example": {"es": "_No hay ejemplo disponible_", "en": "_No example available_"},
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
    "bp.pending_intro": {"es": "📝 *Tienes opción de seguimiento personal pendiente*\n\n_Esta evaluación es totalmente privada, solo podrá verla tu CA._\n_Si en algún momento quieres cancelar, escribe SOS en el hilo._", "en": "📝 *You have a personal tracking option pending*\n\n_This evaluation is fully private; only your CA can see it._\n_If at any point you want to cancel, type SOS in the thread._"},
    "bp.pending_fallback": {"es": "📝 Tienes opción de seguimiento personal pendiente", "en": "📝 You have a personal tracking option pending"},
    "bp.example_label": {"es": ":point_right: Ejemplo:", "en": ":point_right: Example:"},
    "bp.see_example": {"es": "Ver ejemplo", "en": "See example"},
    "bp.send_to_start": {"es": ":point_right: *Envía cualquier mensaje en el hilo para comenzar la evaluación*", "en": ":point_right: *Send any message in the thread to start the evaluation*"},
    "bp.saved_more_q": {"es": "✅ Evaluación guardada. ¿Quieres añadir otro comentario?", "en": "✅ Evaluation saved. Do you want to add another comment?"},
    "bp.urgency_to_ca": {"es": "🚨 *Urgencia de {nombre}*\n\n*Descripción:* {desc}\n\nPor favor, contacta con él/ella lo antes posible.", "en": "🚨 *Urgent from {nombre}*\n\n*Description:* {desc}\n\nPlease get in touch with them as soon as possible."},
    "bp.eval_finished": {"es": "Evaluación finalizada, por favor salga del hilo. 👋", "en": "Evaluation finished, please leave the thread. 👋"},
    "bp.comment_summary": {"es": "📋 Tu comentario:\n_{texto}_\n\n¿Lo guardo? Responde *sí* para guardar o *modificar* para cambiar.", "en": "📋 Your comment:\n_{texto}_\n\nShall I save it? Reply *yes* to save or *edit* to change it."},
    "bp.can_reply": {"es": "Ya puedes responder.", "en": "You can reply now."},
    "bp.rewrite_comment": {"es": "Escribe de nuevo tu comentario:", "en": "Type your comment again:"},
    "bp.comment_summary_opts": {"es": "📋 Tu comentario:\n_{texto}_\n\nLas únicas opciones son elegir uno de los botones o escribir *SOS* para terminar y perder el contenido de la evaluación.", "en": "📋 Your comment:\n_{texto}_\n\nThe only options are to pick one of the buttons or type *SOS* to finish and lose the evaluation content."},
    "bp.what_else": {"es": "¿Qué más me quieres contar? Responde con tu comentario.", "en": "What else would you like to tell me? Reply with your comment."},
    "bp.opportunity_share": {"es": "Esta es tu oportunidad para compartir tu progreso", "en": "This is your chance to share your progress"},
    "bp.btn_save_yes": {"es": "✅ Sí, guardar", "en": "✅ Yes, save"},
    "bp.err_save": {"es": "⚠️ No se pudo guardar en Notion. Revisa los permisos o contacta con soporte.", "en": "⚠️ Could not save to Notion. Check permissions or contact support."},
    "bp.reminder": {"es": "⏰ Recuerda que tienes una evaluación personal pendiente. Responde en este hilo cuando puedas.", "en": "⏰ Remember you have a personal evaluation pending. Reply in this thread when you can."},

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
    "bc.ask_opinion": {"es": "¿Qué opinas de las evaluaciones?", "en": "What's your opinion on the evaluations?"},
    "bc.enter_new_answer": {"es": "Escribe la nueva respuesta.", "en": "Type the new answer."},
    "bc.not_found_suggest": {"es": "*{nombre}* no aparece tal cual en la lista de empleados.\n¿Querías decir alguno de estos nombres? Responde copiando el nombre exacto:\n{opciones}", "en": "*{nombre}* doesn't appear exactly like that in the employee list.\nDid you mean one of these names? Reply by copying the exact name:\n{opciones}"},
    "bc.not_found": {"es": "*{nombre}* no aparece tal cual en la lista de empleados. Escribe nombre y apellido como aparece en la lista.", "en": "*{nombre}* doesn't appear exactly like that in the employee list. Type the first and last name as they appear in the list."},
    "bc.pending_fallback": {"es": "📋 CA: Tienes evaluación de advisees pendiente", "en": "📋 CA: You have an advisee evaluation pending"},
    "bc.pending_intro": {"es": "📋 *CA: Tienes evaluación de advisees pendiente*\n\n_Esta evaluación es totalmente privada, solo podrás verla tú._\n_Si en algún momento quieres cancelar, escribe SOS en el hilo._", "en": "📋 *CA: You have an advisee evaluation pending*\n\n_This evaluation is fully private; only you can see it._\n_If at any point you want to cancel, type SOS in the thread._"},
    "bc.all_advisees_done": {"es": "Ya has opinado sobre todos tus advisees. ¡Perfecto, gracias por tu tiempo! 🎉", "en": "You've now given your opinion on all your advisees. Great, thank you for your time! 🎉"},
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
    "bc.claude_summary_result": {"es": "📊 *Resumen generado por Claude:*\n\n{resumen}\n\n¿Qué opinas de esto?", "en": "📊 *Summary generated by Claude:*\n\n{resumen}\n\nWhat's your opinion on this?"},
    "bc.claude_summary_error": {"es": "⚠️ No se pudo generar el resumen con Claude.\n\n¿Qué opinas de esto?", "en": "⚠️ Could not generate the summary with Claude.\n\nWhat's your opinion on this?"},
    "bc.ask_comment": {"es": "¿Qué comentario deseas registrar sobre las evaluaciones de tu advisee?", "en": "What comment would you like to record about your advisee's evaluations?"},
    "bc.clarify_claude": {"es": "Responde `sí` para generar un resumen con Claude, o `no` para continuar directamente.", "en": "Reply `yes` to generate a summary with Claude, or `no` to continue directly."},
    "bc.opinion_not_saved": {"es": "De acuerdo, no se guardará esta opinión.\n\n", "en": "Okay, this opinion won't be saved.\n\n"},
    "bc.cannot_save_not_associated": {"es": "No puedo guardar esta opinión: *{advisee}* no aparece asociado a ti en `Lista CA`.\nTus advisees actuales:\n{opciones}", "en": "I can't save this opinion: *{advisee}* is not associated with you in `CA List`.\nYour current advisees:\n{opciones}"},
    "bc.opinion_saved": {"es": "✅ Opinión guardada en Notion.\n\n", "en": "✅ Opinion saved to Notion.\n\n"},
    "bc.opinion_save_error": {"es": "⚠️ No se pudo guardar en Notion: `{error}`\n\n", "en": "⚠️ Could not save to Notion: `{error}`\n\n"},
    "bc.thanks_end": {"es": "¡Perfecto, gracias por tu tiempo! 🎉", "en": "Great, thank you for your time! 🎉"},
    "bc.already_concluded": {"es": "Esta evaluación ya ha concluido. 👋", "en": "This evaluation has already concluded. 👋"},
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
        "es": "👋 ¡Hola! Tienes *{n}* evaluación(es) pendiente(s) del proyecto *{proyecto}*:\n{lista}\n\nPor favor, entra en la web y rellénalas. ¡Gracias! 🙏",
        "en": "👋 Hi! You have *{n}* pending evaluation(s) for the project *{proyecto}*:\n{lista}\n\nPlease log in to the web app and complete them. Thanks! 🙏",
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
    },
}


def normalizar_idioma(idioma: str | None) -> str:
    """Devuelve un codigo de idioma valido; cae a 'es' si es None o desconocido."""
    if idioma in IDIOMAS_SOPORTADOS:
        return idioma
    return IDIOMA_POR_DEFECTO


def boton_idioma_slack(idioma: str, action_id: str) -> dict:
    """Botón de Slack para cambiar el idioma del bot. Muestra el idioma AL QUE se cambia (ES<->EN)."""
    label = "🌐 EN" if idioma == "es" else "🌐 ES"
    return {"type": "button", "text": {"type": "plain_text", "text": label, "emoji": True}, "action_id": action_id}


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