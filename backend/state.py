import threading


lock = threading.RLock()
evaluacion_ts = set()
evaluacion_ts_expirados = set()
evaluacion_hora: dict = {}
evaluacion_ultimo_recordatorio: dict = {}
conversaciones = {}
bbdd_por_evaluado = {}
sesiones_web = {}
password_reset_tokens = {}
evaluaciones_pendientes = []
avisos_responder_en_hilo = {}
