# Bot de estado de Minecraft

Bot de Discord que informa si el servidor de Minecraft Java (expuesto con un túnel de playit.gg) está en línea.

- `/mc`: embed verde con MOTD, dirección, versión, ping y jugadores conectados, o rojo si el server o el túnel no responden. Cooldown de 10 s por canal.
- Presencia automática cada 60 s: `X/Y en Minecraft` (en línea) o `Server apagado` (no molestar).
- `/stats jugador`: muertes, tiempo jugado, bloques picados, diamantes, mobs, distancia y causa de muerte más común (con autocompletado de nombres).
- `/top categoria`: top 10 de muertes, bloques, tiempo, diamantes, mobs o distancia.
- Las estadísticas se leen cada 5 min por SFTP (solo lectura) y los comandos responden desde esa caché.
- Solo slash commands; no necesita ningún Privileged Gateway Intent.

## Variables de entorno

| Variable | Obligatoria | Descripción |
|---|---|---|
| `DISCORD_TOKEN` | Sí | Token del bot (Developer Portal → Bot → Reset Token). |
| `MC_SERVER_ADDRESS` | Sí | Dirección **pública** de playit (ej. `algo.ply.gg:12345` o el dominio con SRV). Nunca `localhost`. |
| `GUILD_ID` | No | ID de tu servidor de Discord. Si está, los comandos aparecen al instante ahí; si no, se registran globalmente (puede tardar). |
| `SFTP_HOST` | Para stats | Host SFTP del panel de WiseHosting. Sin `SFTP_HOST`, `SFTP_USER` y `SFTP_PASSWORD` el bot funciona igual, pero `/stats` y `/top` avisan que las estadísticas no están configuradas. |
| `SFTP_PORT` | No | Puerto SFTP (por defecto `22`; revisa el que muestra el panel). |
| `SFTP_USER` | Para stats | Usuario SFTP del subusuario de **solo lectura**. |
| `SFTP_PASSWORD` | Para stats | Contraseña de ese subusuario. |
| `MC_WORLD` | No | Carpeta del mundo (por defecto `world`). |
| `SFTP_HOST_KEY` | No (recomendada) | La línea `ssh-ed25519` completa que imprime `ssh-keyscan -p <puerto> <host>` (formato `host tipo clave`). El host de esa línea debe coincidir con `SFTP_HOST`. Si falta, se acepta cualquier clave de host, lo que expone la contraseña a un ataque man-in-the-middle. |

Las métricas salen de `<mundo>/stats/<uuid>.json` y los nombres de `usercache.json`. Para cambiar qué bloques no cuentan como "picados", edita `BLOQUES_EXCLUIDOS` (o `SUFIJOS_EXCLUIDOS`, que ya excluye todas las `*_leaves`) en `main.py`.

## Invitar el bot

Developer Portal → tu aplicación → **OAuth2 → URL Generator**:
- Scopes: `bot` y `applications.commands`
- Permisos: `Send Messages` y `Embed Links`

Abre la URL generada y elige tu servidor.

## Desplegar en Railway

1. Sube este repo a GitHub.
2. En Railway: **New Project → Deploy from GitHub repo** y elige el repo.
3. En el servicio → **Variables**, agrega `DISCORD_TOKEN`, `MC_SERVER_ADDRESS`, las de SFTP y (opcional) `GUILD_ID`, `MC_WORLD`, `SFTP_HOST_KEY`.
4. Railway instala `requirements.txt` y ejecuta `python main.py` (Python 3.12 por `.python-version`). No necesita dominio público ni puerto.
5. Revisa los logs del deploy: debe aparecer `Conectado como ...`.

## Probar en local

```powershell
python -m venv .venv; .venv\Scripts\activate; pip install -r requirements.txt
$env:DISCORD_TOKEN="..."; $env:MC_SERVER_ADDRESS="..."; python main.py
```

Pruebas (sin Discord ni SFTP reales): `python -m unittest discover tests`
