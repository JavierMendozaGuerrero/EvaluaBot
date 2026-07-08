import threading


lock = threading.RLock()
evaluaciones_dm_activas: set = set()        # user_ids con evaluación DM activa
evaluaciones_dm_expiradas: set = set()      # user_ids de la ronda anterior
evaluacion_dm_canal: dict = {}              # user_id -> dm_channel_id
evaluacion_dm_ts: dict = {}                 # user_id -> ts del mensaje inicial (raíz del hilo)
evaluacion_dm_ts_anterior: dict = {}        # user_id -> ts de la evaluación mensual anterior (caducada)
evaluacion_hora: dict = {}                  # user_id -> timestamp de envío
evaluacion_ultimo_recordatorio: dict = {}   # user_id -> timestamp del último recordatorio
conversaciones = {}                         # user_id -> estado de conversación
bbdd_por_evaluado = {}
sesiones_web = {}
sesiones_expira = {}          # token -> datetime (UTC) de caducidad de la sesión
password_reset_tokens = {}
registros_pendientes = {}     # email_norm -> registro a la espera de código de verificación
evaluaciones_pendientes = []
audio_pendiente_transcripcion: dict = {}  # (channel, ts) -> evento original
