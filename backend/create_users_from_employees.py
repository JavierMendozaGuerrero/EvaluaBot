import argparse
import csv
import os
import secrets
import unicodedata

from . import config
from .notion_service import obtener_registros_empleados
from .users import cargar_usuarios, guardar_usuarios, hash_password, validar_password_segura
from .utils import normalizar_nombre


def _sin_acentos(valor):
    return "".join(
        char for char in unicodedata.normalize("NFD", valor)
        if unicodedata.category(char) != "Mn"
    )


def _parte_usuario(valor):
    limpio = _sin_acentos(valor)
    return "".join(char for char in limpio if char.isalnum())


def username_base(nombre):
    partes = [parte for parte in nombre.split() if _parte_usuario(parte)]
    if not partes:
        return ""
    if len(partes) == 1:
        elegidas = partes
    else:
        elegidas = partes[:2]
    return "".join(_parte_usuario(parte[:1]).upper() + _parte_usuario(parte[1:]) for parte in elegidas)


def username_unico(nombre, usados):
    base = username_base(nombre)
    if not base:
        return ""
    candidato = base
    indice = 2
    while normalizar_nombre(candidato) in usados:
        candidato = f"{base}{indice}"
        indice += 1
    usados.add(normalizar_nombre(candidato))
    return candidato


def password_temporal():
    return f"Cambio-{secrets.token_urlsafe(8)}"


def crear_usuarios(apply=False, output=None, password=None):
    if password:
        validar_password_segura(password)
    registros = [
        {
            "nombre": (registro.get("nombre") or "").strip(),
            "email": (registro.get("email") or "").strip().lower(),
        }
        for registro in obtener_registros_empleados()
        if registro.get("nombre") and registro.get("nombre").strip()
    ]
    empleados_por_nombre = {
        normalizar_nombre(registro["nombre"]): registro
        for registro in registros
    }
    empleados = sorted(empleados_por_nombre.values(), key=lambda item: normalizar_nombre(item["nombre"]))
    usuarios = cargar_usuarios()
    usados = set(usuarios.keys())
    personas_existentes = {
        normalizar_nombre(usuario.get("persona") or usuario.get("username")): clave
        for clave, usuario in usuarios.items()
    }
    creados = []
    actualizados = []
    saltos = []

    for registro in empleados:
        empleado = registro["nombre"]
        email = registro["email"]
        persona_clave = normalizar_nombre(empleado)
        usuario_existente_clave = personas_existentes.get(persona_clave)
        if usuario_existente_clave:
            usuario = usuarios[usuario_existente_clave]
            email_actualizado = False
            if email and usuario.get("email") != email:
                usuario["email"] = email
                email_actualizado = True
            if usuario.get("salt") and usuario.get("password_hash"):
                if email_actualizado:
                    actualizados.append((empleado, usuario.get("username", empleado), ""))
                saltos.append((empleado, usuario.get("username", empleado), "ya existe un usuario para esta persona"))
                continue

            temporal = password or password_temporal()
            salt, password_hash = hash_password(temporal)
            usuario["username"] = usuario.get("username") or username_base(empleado)
            usuario["persona"] = usuario.get("persona") or empleado
            usuario["email"] = email
            usuario["salt"] = salt
            usuario["password_hash"] = password_hash
            actualizados.append((empleado, usuario["username"], temporal))
            continue

        username = username_unico(empleado, usados)
        if not username:
            saltos.append((empleado, "", "nombre no valido para usuario"))
            continue

        clave = normalizar_nombre(username)
        temporal = password or password_temporal()
        salt, password_hash = hash_password(temporal)
        usuarios[clave] = {
            "username": username,
            "persona": empleado,
            "email": email,
            "is_admin": False,
            "salt": salt,
            "password_hash": password_hash,
        }
        creados.append((empleado, username, temporal))

    if apply and (creados or actualizados):
        guardar_usuarios(usuarios)

    output = output or os.path.join(config.BASE_DIR, "dashboard_web", "usuarios_web_creados.csv")
    os.makedirs(os.path.dirname(output), exist_ok=True)
    with open(output, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["Nombre", "Usuario", "Email", "Password temporal"])
        for empleado, username, temporal in creados:
            writer.writerow([empleado, username, usuarios[normalizar_nombre(username)].get("email", ""), temporal])
        for empleado, username, temporal in actualizados:
            writer.writerow([empleado, username, usuarios.get(normalizar_nombre(username), {}).get("email", ""), temporal])

    return empleados, creados, actualizados, saltos, output


def main():
    parser = argparse.ArgumentParser(description="Crea usuarios web desde Lista de empleados.")
    parser.add_argument("--apply", action="store_true", help="Escribe los usuarios en Notion.")
    parser.add_argument("--output", default="", help="Ruta del CSV con usuarios y passwords temporales.")
    parser.add_argument("--password", default="", help="Password temporal comun para todos los usuarios creados.")
    args = parser.parse_args()

    empleados, creados, actualizados, saltos, output = crear_usuarios(
        apply=args.apply,
        output=args.output or None,
        password=args.password or None,
    )

    modo = "APLICADO" if args.apply else "DRY-RUN"
    print(
        f"{modo}: empleados leidos={len(empleados)}, "
        f"usuarios nuevos={len(creados)}, usuarios reparados={len(actualizados)}, saltados={len(saltos)}"
    )
    print(f"CSV generado: {output}")
    if not args.apply:
        print("No se ha escrito en Notion. Ejecuta con --apply para crear los usuarios.")
    if saltos:
        print("Saltados:")
        for empleado, username, motivo in saltos:
            print(f"- {empleado} ({username}): {motivo}")


if __name__ == "__main__":
    main()
