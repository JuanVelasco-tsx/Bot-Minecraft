"""Bot de Discord que informa el estado de un servidor de Minecraft Java (vía túnel playit.gg)."""

import asyncio
import logging
import os
import re

import discord
from discord import app_commands
from discord.ext import tasks
from mcstatus import JavaServer

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
log = logging.getLogger("bot-mc")

TOKEN = os.getenv("DISCORD_TOKEN")
DIRECCION = os.getenv("MC_SERVER_ADDRESS", "").strip()
GUILD_ID = os.getenv("GUILD_ID", "").strip()
TIMEOUT = 5  # segundos máximos por consulta (lookup + ping)

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


bot = Bot()


@bot.tree.command(name="mc", description="Muestra el estado del servidor de Minecraft")
@app_commands.checks.cooldown(1, 10, key=lambda i: i.channel_id)
async def mc(interaction: discord.Interaction):
    await interaction.response.defer()
    estado = await consultar_server()
    await interaction.followup.send(embed=armar_embed(estado))


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
    bot.run(TOKEN, log_handler=None)  # ya configuramos logging arriba
