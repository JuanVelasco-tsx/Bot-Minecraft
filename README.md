# Bot de estado de Minecraft

Bot de Discord que informa si el servidor de Minecraft Java (expuesto con un túnel de playit.gg) está en línea.

- `/mc`: embed verde con MOTD, dirección, versión, ping y jugadores conectados, o rojo si el server o el túnel no responden. Cooldown de 10 s por canal.
- Presencia automática cada 60 s: `X/Y en Minecraft` (en línea) o `Server apagado` (no molestar).
- Solo slash commands; no necesita ningún Privileged Gateway Intent.

## Variables de entorno

| Variable | Obligatoria | Descripción |
|---|---|---|
| `DISCORD_TOKEN` | Sí | Token del bot (Developer Portal → Bot → Reset Token). |
| `MC_SERVER_ADDRESS` | Sí | Dirección **pública** de playit (ej. `algo.ply.gg:12345` o el dominio con SRV). Nunca `localhost`. |
| `GUILD_ID` | No | ID de tu servidor de Discord. Si está, `/mc` aparece al instante ahí; si no, se registra globalmente (puede tardar). |

## Invitar el bot

Developer Portal → tu aplicación → **OAuth2 → URL Generator**:
- Scopes: `bot` y `applications.commands`
- Permisos: `Send Messages` y `Embed Links`

Abre la URL generada y elige tu servidor.

## Desplegar en Railway

1. Sube este repo a GitHub.
2. En Railway: **New Project → Deploy from GitHub repo** y elige el repo.
3. En el servicio → **Variables**, agrega `DISCORD_TOKEN`, `MC_SERVER_ADDRESS` y (opcional) `GUILD_ID`.
4. Railway instala `requirements.txt` y ejecuta `python main.py` (Python 3.12 por `.python-version`). No necesita dominio público ni puerto.
5. Revisa los logs del deploy: debe aparecer `Conectado como ...`.

## Probar en local

```powershell
python -m venv .venv; .venv\Scripts\activate; pip install -r requirements.txt
$env:DISCORD_TOKEN="..."; $env:MC_SERVER_ADDRESS="..."; python main.py
```
