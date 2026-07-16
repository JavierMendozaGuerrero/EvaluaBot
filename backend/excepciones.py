class ErrorIA(RuntimeError):
    """Fallo al generar o interpretar una respuesta de la IA.

    Su mensaje está escrito para el usuario final y la API lo devuelve tal cual, así que
    no debe llevar detalles internos (texto crudo del modelo, trazas, nombres de campo):
    eso va al log. Se usa para lo que el usuario puede reintentar, no para bugs.

    `codigo` identifica la causa de forma estable para que el frontend pueda traducirla
    (el mensaje viaja en español); `definitivo` distingue lo que no se arregla
    reintentando (sin saldo, API mal configurada) de lo pasajero (saturación, red).
    """

    def __init__(self, mensaje: str, codigo: str = "ia_error", definitivo: bool = False):
        super().__init__(mensaje)
        self.codigo = codigo
        self.definitivo = definitivo
