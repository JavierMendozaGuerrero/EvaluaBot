import threading


lock = threading.Lock()
evaluacion_ts = set()
conversaciones = {}
bbdd_por_evaluado = {}
sesiones_web = {}
evaluaciones_pendientes = []
