import threading


lock = threading.RLock()
evaluaciones_dm_activas: set = set()        # user_ids con evaluación DM activa
evaluaciones_dm_expiradas: set = set()      # user_ids de la ronda anterior
evaluacion_dm_canal: dict = {}              # user_id -> dm_channel_id
evaluacion_dm_ts: dict = {}                 # user_id -> ts del mensaje inicial (raíz del hilo)
evaluacion_hora: dict = {}                  # user_id -> timestamp de envío
evaluacion_ultimo_recordatorio: dict = {}   # user_id -> timestamp del último recordatorio
conversaciones = {}                         # user_id -> estado de conversación
bbdd_por_evaluado = {}
sesiones_web = {}
password_reset_tokens = {}
evaluaciones_pendientes = []
audio_pendiente_transcripcion: dict = {}  # (channel, ts) -> evento original
