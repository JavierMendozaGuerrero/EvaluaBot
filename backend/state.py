import threading


lock = threading.Lock()
evaluacion_ts = set()
conversaciones = {}
bbdd_por_evaluado = {}
sesiones_web = {}
password_reset_tokens = {}
evaluaciones_pendientes = []
avisos_responder_en_hilo = {}
