"""Bot de Discord que informa el estado de un servidor de Minecraft Java (vía túnel playit.gg)."""

import asyncio
import json
import logging
import os
import re
import time
from typing import Literal

import asyncssh
import discord
from discord import app_commands
from discord.ext import tasks
from mcstatus import JavaServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logging.getLogger("asyncssh").setLevel(logging.WARNING)  # en INFO registra el usuario SFTP
log = logging.getLogger("bot-mc")

TOKEN = os.getenv("DISCORD_TOKEN")
DIRECCION = os.getenv("MC_SERVER_ADDRESS", "").strip()
GUILD_ID = os.getenv("GUILD_ID", "").strip()
TIMEOUT = 5  # segundos máximos por consulta (lookup + ping)

# Estadísticas: se leen por SFTP (solo lectura) del host del server de Minecraft.
SFTP_HOST = os.getenv("SFTP_HOST", "").strip()
SFTP_PORT = os.getenv("SFTP_PORT", "").strip() or "22"
SFTP_USER = os.getenv("SFTP_USER", "")
SFTP_PASSWORD = os.getenv("SFTP_PASSWORD", "")
SFTP_HOST_KEY = os.getenv("SFTP_HOST_KEY", "").strip()
MC_WORLD = os.getenv("MC_WORLD", "").strip() or "world"
STATS_ACTIVAS = bool(SFTP_HOST and SFTP_USER and SFTP_PASSWORD)
TIMEOUT_SFTP = 120  # segundos máximos para descargar todo
BLOQUE_SFTP = 16_384  # bytes por lectura: tamaño seguro para cualquier servidor SFTP

# Bloques que NO cuentan como "picados": plantas, flores, pasto, cultivos, fuego y antorchas.
# Para editarla, agrega o quita nombres (sin el prefijo "minecraft:"), separados por espacios.
# Además se excluye todo lo que termine en SUFIJOS_EXCLUIDOS (todas las hojas, también las de mods).
SUFIJOS_EXCLUIDOS = ("_leaves",)
BLOQUES_EXCLUIDOS = {"minecraft:" + b for b in """
    short_grass tall_grass fern large_fern dead_bush seagrass tall_seagrass kelp kelp_plant
    vine glow_lichen lily_pad sugar_cane bamboo bamboo_sapling cactus sweet_berry_bush
    cave_vines cave_vines_plant weeping_vines weeping_vines_plant twisting_vines twisting_vines_plant
    hanging_roots small_dripleaf big_dripleaf big_dripleaf_stem spore_blossom azalea flowering_azalea
    oak_sapling spruce_sapling birch_sapling jungle_sapling acacia_sapling dark_oak_sapling
    cherry_sapling mangrove_propagule brown_mushroom red_mushroom
    crimson_fungus warped_fungus crimson_roots warped_roots nether_sprouts
    dandelion poppy blue_orchid allium azure_bluet red_tulip orange_tulip white_tulip pink_tulip
    oxeye_daisy cornflower lily_of_the_valley wither_rose torchflower sunflower lilac rose_bush
    peony pitcher_plant pink_petals
    wheat carrots potatoes beetroots melon_stem pumpkin_stem attached_melon_stem attached_pumpkin_stem
    nether_wart cocoa torchflower_crop pitcher_crop
    fire soul_fire
    torch wall_torch soul_torch soul_wall_torch redstone_torch redstone_wall_torch
""".split()}

cache = {"jugadores": {}, "actualizado": None}  # nombre -> métricas; actualizado = time.time()

VERDE = discord.Color.green()
ROJO = discord.Color.red()


async def consultar_server():
    """Devuelve el JavaStatusResponse, o None si el server/túnel no responde. Nunca lanza."""
    async def _consulta():
        server = await JavaServer.async_lookup(DIRECCION, timeout=TIMEOUT)  # resuelve el SRV de playit
        return await server.async_status(tries=1)

    try:
        return await asyncio.wait_for(_consulta(), timeout=TIMEOUT)
    except Exception as e:
        log.warning("No se pudo consultar %r: %s: %s", DIRECCION, type(e).__name__, e)
        return None


def limpiar(texto):
    """Quita códigos de color § y escapa Markdown (nombres como juan_mc_ saldrían en cursiva)."""
    return discord.utils.escape_markdown(re.sub(r"§.", "", texto)).strip()


def lista_jugadores(online, nombres):
    """Texto con los jugadores conectados; el ping solo trae ~12 nombres."""
    if online == 0:
        return "Nadie conectado ahora mismo"
    if not nombres:
        return f"{online} conectado(s), pero el servidor no envía los nombres"
    texto = "\n".join(f"• {n}" for n in nombres)
    if online > len(nombres):
        texto += f"\n…y {online - len(nombres)} más"
    return texto


def armar_embed(estado):
    if estado is None:
        return discord.Embed(
            title="🔴 Servidor de Minecraft caído",
            description=(
                "No hubo respuesta en "
                f"{TIMEOUT} s. El servidor está apagado o el túnel de playit.gg está caído."
            ),
            color=ROJO,
        ).add_field(name="Dirección", value=f"`{DIRECCION}`", inline=False)

    nombres = [limpiar(p.name) for p in (estado.players.sample or [])]
    motd = discord.utils.escape_markdown(estado.motd.to_plain().strip()) or "—"
    embed = discord.Embed(title="🟢 Servidor de Minecraft en línea", description=motd[:4000], color=VERDE)
    embed.add_field(name="Dirección", value=f"`{DIRECCION}`", inline=False)
    embed.add_field(name="Versión", value=limpiar(estado.version.name)[:1024] or "—")
    embed.add_field(name="Ping", value=f"{round(estado.latency)} ms")
    embed.add_field(
        name=f"Jugadores ({estado.players.online}/{estado.players.max})",
        value=lista_jugadores(estado.players.online, nombres)[:1024],
        inline=False,
    )
    return embed


def calcular_metricas(datos):
    """Métricas de un archivo <uuid>.json de stats. Las claves que falten cuentan como 0."""
    s = datos.get("stats", {})
    custom = s.get("minecraft:custom", {})
    minado = s.get("minecraft:mined", {})
    killed_by = s.get("minecraft:killed_by", {})
    return {
        "muertes": custom.get("minecraft:deaths", 0),
        "tiempo": custom.get("minecraft:play_time", 0) // 20,  # ticks -> segundos
        "bloques": sum(v for k, v in minado.items()
                       if k not in BLOQUES_EXCLUIDOS and not k.endswith(SUFIJOS_EXCLUIDOS)),
        "diamantes": minado.get("minecraft:diamond_ore", 0) + minado.get("minecraft:deepslate_diamond_ore", 0),
        "mobs": custom.get("minecraft:mob_kills", 0),
        "distancia": sum(v for k, v in custom.items() if k.endswith("_one_cm")) / 100_000,  # cm -> km
        # todas las causas empatadas en el máximo (lista vacía si nunca murió)
        "causa": [k for k, v in killed_by.items() if v == max(killed_by.values())],
    }


def procesar(usercache_txt, stats):
    """Convierte usercache.json + {uuid: json en bytes o texto} en {nombre: métricas}."""
    nombres = {e["uuid"]: e["name"] for e in json.loads(usercache_txt)}
    jugadores = {}
    for uuid, texto in stats.items():
        try:
            jugadores[nombres.get(uuid, uuid[:8])] = calcular_metricas(json.loads(texto))
        except (ValueError, AttributeError) as e:  # archivo a medio escribir o corrupto: se salta
            log.warning("Stats ilegibles para %s: %s", uuid, e)
    return jugadores


async def descargar_stats():
    """Descarga usercache.json y todos los <mundo>/stats/*.json. Solo abre archivos para leer."""
    # Riesgo: known_hosts=None acepta cualquier clave de host, así que alguien que intercepte la conexión
    # (man-in-the-middle) podría hacerse pasar por el servidor y quedarse con la contraseña SFTP.
    # Para fijar la clave, pon en SFTP_HOST_KEY la línea "ssh-ed25519" que imprime
    # `ssh-keyscan -p <puerto> <host>` (formato "host tipo clave") y solo se aceptará esa clave.
    known_hosts = asyncssh.import_known_hosts(SFTP_HOST_KEY) if SFTP_HOST_KEY else None
    async with asyncssh.connect(
        SFTP_HOST, port=int(SFTP_PORT), username=SFTP_USER, password=SFTP_PASSWORD,
        known_hosts=known_hosts, client_keys=None,
    ) as conn, conn.start_sftp_client() as sftp:
        return await descargar_de(sftp)


class FalloSFTP(Exception):
    """Error SFTP con la operación y la ruta que fallaron (nunca incluye credenciales)."""


def fallo(operacion, ruta, e):
    return FalloSFTP(f"{operacion} '{ruta}': {type(e).__name__} (código {e.code}): {e.reason}")


async def leer_archivo(sftp, ruta):
    """Abre en "rb" (solo lectura) y lee por bloques hasta el final.

    No usa read() sin tamaño: asyncssh haría un fstat para saber el largo, y Wings puede rechazarlo.
    """
    try:
        f = await sftp.open(ruta, "rb", block_size=0)  # 0 = un FXP_READ simple por read(), sin paralelismo
    except asyncssh.SFTPError as e:
        raise fallo("abrir", ruta, e) from e
    try:
        partes = []
        while parte := await f.read(BLOQUE_SFTP):  # b"" al llegar al final
            partes.append(parte)
        return b"".join(partes)
    except asyncssh.SFTPError as e:
        raise fallo("leer", ruta, e) from e
    finally:
        try:
            await f.close()
        except asyncssh.SFTPError:
            pass  # cerrar un archivo de solo lectura que ya leímos no afecta los datos


async def descargar_de(sftp):
    """usercache.json + {uuid: bytes} de <mundo>/stats. Solo usa listdir, open "rb" y read."""
    usercache = await leer_archivo(sftp, "usercache.json")
    carpeta = f"{MC_WORLD}/stats"
    try:
        archivos = [a for a in await sftp.listdir(carpeta) if a.endswith(".json")]
    except asyncssh.SFTPError as e:
        raise fallo("listar", carpeta, e) from e
    stats = {}
    for archivo in archivos:
        try:
            stats[archivo[:-5]] = await leer_archivo(sftp, f"{carpeta}/{archivo}")
        except FalloSFTP as e:  # un archivo que falla no tumba la actualización
            log.warning("Se salta un archivo de stats: %s", e)
    if archivos and not stats:
        raise FalloSFTP(f"no se pudo leer ninguno de los {len(archivos)} archivos de '{carpeta}'")
    return usercache, stats


async def refrescar_cache():
    """Actualiza la caché. Si algo falla, conserva la última buena. Nunca lanza."""
    try:
        usercache, stats = await asyncio.wait_for(descargar_stats(), timeout=TIMEOUT_SFTP)
        jugadores = procesar(usercache, stats)
    except Exception as e:
        mensaje = str(e)
        for secreto in (SFTP_USER, SFTP_PASSWORD):  # nunca credenciales en el log
            if secreto:
                mensaje = mensaje.replace(secreto, "***")
        log.warning("No se pudieron actualizar las estadísticas (se conserva la caché anterior): %s: %s",
                    type(e).__name__, mensaje)
        return
    cache.update(jugadores=jugadores, actualizado=time.time())
    log.info("Estadísticas actualizadas: %d jugador(es)", len(jugadores))


def num(n):
    return f"{n:,}".replace(",", ".")


def duracion(segundos):
    return f"{segundos // 3600} h {segundos % 3600 // 60} min"


# clave -> (título, formato). Se usa en /stats y en /top.
CATEGORIAS = {
    "muertes": ("💀 Muertes", lambda m: num(m["muertes"])),
    "tiempo": ("⏱️ Tiempo jugado", lambda m: duracion(m["tiempo"])),
    "bloques": ("⛏️ Bloques picados", lambda m: num(m["bloques"])),
    "diamantes": ("💎 Diamantes minados", lambda m: num(m["diamantes"])),
    "mobs": ("⚔️ Mobs matados", lambda m: num(m["mobs"])),
    "distancia": ("🧭 Distancia recorrida", lambda m: f"{m['distancia']:.1f} km".replace(".", ",")),
}
MEDALLAS = ["🥇", "🥈", "🥉"]
SIN_CACHE = ("⏳ Todavía no hay estadísticas cargadas. Si el bot acaba de arrancar, "
             "espera unos minutos y vuelve a intentarlo.")
SIN_CONFIG = "⚙️ Las estadísticas no están configuradas en este bot (faltan los datos de acceso SFTP)."


def aviso_sin_datos():
    """Mensaje para responder si no hay estadísticas que mostrar; None si las hay."""
    if not STATS_ACTIVAS:
        return SIN_CONFIG
    if not cache["jugadores"]:
        return SIN_CACHE
    return None


def con_pie(embed):
    minutos = int((time.time() - cache["actualizado"]) // 60)
    return embed.set_footer(text=f"Actualizado hace {minutos} min")


def buscar_jugador(nombre):
    return next((n for n in cache["jugadores"] if n.lower() == nombre.strip().lower()), None)


def embed_stats(nombre):
    m = cache["jugadores"][nombre]
    embed = discord.Embed(title=f"📊 Estadísticas de {limpiar(nombre)}", color=VERDE)
    for titulo, formato in CATEGORIAS.values():
        embed.add_field(name=titulo, value=formato(m))
    embed.add_field(name="☠️ Muere más por", value=texto_causa(m["causa"]))
    return con_pie(embed)


def texto_causa(causas):
    if not causas:
        return "—"
    if len(causas) > 3:
        return "Variadas"
    return ", ".join(c.split(":")[-1].replace("_", " ") for c in causas)


def embed_top(categoria):
    titulo, formato = CATEGORIAS[categoria]
    ranking = sorted(cache["jugadores"].items(), key=lambda kv: kv[1][categoria], reverse=True)[:10]
    lineas = [
        f"{MEDALLAS[i] if i < 3 else f'`{i + 1}.`'} **{limpiar(nombre)}**: {formato(m)}"
        for i, (nombre, m) in enumerate(ranking)
    ]
    return con_pie(discord.Embed(title=f"🏆 Top 10: {titulo}", description="\n".join(lineas), color=VERDE))


class Bot(discord.Client):
    def __init__(self):
        super().__init__(intents=discord.Intents.default())  # sin intents privilegiados
        self.tree = app_commands.CommandTree(self)
        self.ultimo_texto = None

    async def setup_hook(self):
        if GUILD_ID:
            guild = discord.Object(id=int(GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            comandos = await self.tree.sync(guild=guild)
            log.info("Sincronizados %d comando(s) en el servidor %s", len(comandos), GUILD_ID)
        else:
            comandos = await self.tree.sync()
            log.info("Sincronizados %d comando(s) globalmente (pueden tardar en aparecer)", len(comandos))
        self.actualizar_presencia.start()
        if STATS_ACTIVAS:
            self.actualizar_stats.start()
        else:
            log.info("Estadísticas desactivadas: faltan SFTP_HOST, SFTP_USER o SFTP_PASSWORD")

    async def on_ready(self):
        log.info("Conectado como %s (id %s). Vigilando %s", self.user, self.user.id, DIRECCION)

    @tasks.loop(seconds=60)
    async def actualizar_presencia(self):
        estado = await consultar_server()
        if estado is None:
            texto, status = "Server apagado", discord.Status.dnd
        else:
            texto, status = f"{estado.players.online}/{estado.players.max} en Minecraft", discord.Status.online
        if texto == self.ultimo_texto:
            return
        try:
            await self.change_presence(status=status, activity=discord.CustomActivity(name=texto))
            self.ultimo_texto = texto
            log.info("Presencia actualizada: %s", texto)
        except Exception:
            log.exception("No se pudo actualizar la presencia")

    @actualizar_presencia.before_loop
    async def _esperar_listo(self):
        await self.wait_until_ready()

    @tasks.loop(minutes=5)
    async def actualizar_stats(self):
        await refrescar_cache()


bot = Bot()


@bot.tree.command(name="mc", description="Muestra el estado del servidor de Minecraft")
@app_commands.checks.cooldown(1, 10, key=lambda i: i.channel_id)
async def mc(interaction: discord.Interaction):
    await interaction.response.defer()
    estado = await consultar_server()
    await interaction.followup.send(embed=armar_embed(estado))


@bot.tree.command(name="stats", description="Estadísticas de un jugador del servidor")
@app_commands.describe(jugador="Nombre del jugador")
async def stats_cmd(interaction: discord.Interaction, jugador: str):
    if aviso := aviso_sin_datos():
        return await interaction.response.send_message(aviso, ephemeral=True)
    nombre = buscar_jugador(jugador)
    if nombre is None:
        return await interaction.response.send_message(
            f"No encontré a **{limpiar(jugador)}** en las estadísticas.", ephemeral=True)
    await interaction.response.send_message(embed=embed_stats(nombre))


@stats_cmd.autocomplete("jugador")
async def autocompletar_jugador(interaction: discord.Interaction, actual: str):
    actual = actual.lower()
    nombres = sorted((n for n in cache["jugadores"] if actual in n.lower()), key=str.lower)
    return [app_commands.Choice(name=n, value=n) for n in nombres[:25]]  # Discord admite máx. 25


@bot.tree.command(name="top", description="Top 10 de jugadores en una categoría")
@app_commands.describe(categoria="Qué estadística comparar")
async def top_cmd(interaction: discord.Interaction,
                  categoria: Literal["muertes", "bloques", "tiempo", "diamantes", "mobs", "distancia"]):
    if aviso := aviso_sin_datos():
        return await interaction.response.send_message(aviso, ephemeral=True)
    await interaction.response.send_message(embed=embed_top(categoria))


@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.CommandOnCooldown):
        mensaje = f"⏳ Espera {error.retry_after:.0f} s antes de volver a usar este comando en este canal."
    else:
        log.exception("Error en el comando /%s", interaction.command.name if interaction.command else "?", exc_info=error)
        mensaje = "⚠️ Algo salió mal al consultar el servidor. Inténtalo de nuevo en un momento."
    try:
        if interaction.response.is_done():
            await interaction.followup.send(mensaje, ephemeral=True)
        else:
            await interaction.response.send_message(mensaje, ephemeral=True)
    except discord.HTTPException:
        log.warning("No se pudo enviar el aviso de error al usuario")


if __name__ == "__main__":
    if not TOKEN or not DIRECCION:
        raise SystemExit("Faltan variables de entorno: DISCORD_TOKEN y MC_SERVER_ADDRESS son obligatorias.")
    if GUILD_ID and not GUILD_ID.isdigit():
        raise SystemExit("GUILD_ID debe ser el ID numérico del servidor de Discord.")
    if not SFTP_PORT.isdigit():
        raise SystemExit("SFTP_PORT debe ser un número de puerto.")
    bot.run(TOKEN, log_handler=None)  # ya configuramos logging arriba
