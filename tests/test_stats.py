"""Pruebas de estadísticas sin Discord ni SFTP reales: python -m unittest discover tests"""

import copy
import json
import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import main  # noqa: E402

EJEMPLO = (Path(__file__).parent / "fixtures" / "ejemplo_stats.json").read_text(encoding="utf-8")


def jugadores_falsos():
    """3 jugadores generados a partir del ejemplo, con valores escalados."""
    base = json.loads(EJEMPLO)
    usercache, stats = [], {}
    for i, (nombre, factor) in enumerate([("Steve_mc_", 1), ("alex", 2), ("Notch", 0)]):
        datos = copy.deepcopy(base)
        for grupo in datos["stats"].values():
            for k in grupo:
                grupo[k] *= factor
        uuid = f"0000000{i}-0000-0000-0000-000000000000"
        usercache.append({"name": nombre, "uuid": uuid, "expiresOn": "2027-01-01 00:00:00 +0000"})
        stats[uuid] = json.dumps(datos)
    return json.dumps(usercache), stats


class TestMetricas(unittest.TestCase):
    def test_ejemplo(self):
        m = main.calcular_metricas(json.loads(EJEMPLO))
        self.assertEqual(m["muertes"], 4)
        self.assertEqual(main.duracion(m["tiempo"]), "8 h 5 min")
        self.assertEqual(m["diamantes"], 90)
        self.assertEqual(m["mobs"], 354)
        self.assertAlmostEqual(m["distancia"], 63.02, places=2)
        # 5462 en total - 903 (pasto, cultivos, flores, fuego, antorchas) - 48 (oak_leaves)
        self.assertEqual(m["bloques"], 4511)
        self.assertEqual(main.texto_causa(m["causa"]), "Variadas")  # empate 1-1-1-1

    def test_hojas_excluidas_por_sufijo(self):
        minado = {"minecraft:pale_oak_leaves": 5, "minecraft:flowering_azalea_leaves": 3,
                  "algunmod:magic_leaves": 2, "minecraft:stone": 7}
        self.assertEqual(main.calcular_metricas({"stats": {"minecraft:mined": minado}})["bloques"], 7)

    def test_causa_con_empates(self):
        causa = lambda kb: main.texto_causa(main.calcular_metricas({"stats": {"minecraft:killed_by": kb}})["causa"])
        self.assertEqual(causa({"minecraft:zombie": 3, "minecraft:creeper": 1}), "zombie")
        self.assertEqual(causa({"minecraft:zombie": 2, "minecraft:cave_spider": 2, "minecraft:skeleton": 1}),
                         "zombie, cave spider")
        self.assertEqual(causa({f"minecraft:mob{i}": 1 for i in range(3)}), "mob0, mob1, mob2")
        self.assertEqual(causa({}), "—")

    def test_claves_faltantes_cuentan_cero(self):
        m = main.calcular_metricas({"stats": {}})
        self.assertEqual({k: v for k, v in m.items() if k != "causa"},
                         dict(muertes=0, tiempo=0, bloques=0, diamantes=0, mobs=0, distancia=0))
        self.assertEqual(m["causa"], [])

    def test_uuid_sin_nombre_y_archivo_corrupto(self):
        jugadores = main.procesar("[]", {"abcdef12-xxxx": EJEMPLO, "roto": "{no es json"})
        self.assertEqual(list(jugadores), ["abcdef12"])


class TestComandos(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        main.cache.update(jugadores=main.procesar(*jugadores_falsos()), actualizado=time.time() - 180)

    def tearDown(self):
        main.cache.update(jugadores={}, actualizado=None)

    def test_comandos_registrados(self):
        self.assertEqual({c.name for c in main.bot.tree.get_commands()}, {"mc", "stats", "top"})
        top = main.bot.tree.get_command("top")
        self.assertEqual([c.value for c in top.parameters[0].choices],
                         ["muertes", "bloques", "tiempo", "diamantes", "mobs", "distancia"])

    async def test_autocompletado_sin_mayusculas(self):
        nombres = lambda opciones: [o.value for o in opciones]
        self.assertEqual(nombres(await main.autocompletar_jugador(None, "")), ["alex", "Notch", "Steve_mc_"])
        self.assertEqual(nombres(await main.autocompletar_jugador(None, "STE")), ["Steve_mc_"])
        self.assertEqual(nombres(await main.autocompletar_jugador(None, "zzz")), [])

    def test_top_con_medallas_y_markdown_escapado(self):
        embed = main.embed_top("mobs")
        lineas = embed.description.splitlines()
        self.assertEqual(lineas[0], "🥇 **alex**: 708")
        self.assertEqual(lineas[1], r"🥈 **Steve\_mc\_**: 354")
        self.assertEqual(lineas[2], "🥉 **Notch**: 0")
        self.assertEqual(embed.footer.text, "Actualizado hace 3 min")

    def test_stats_embed(self):
        embed = main.embed_stats(main.buscar_jugador("steve_MC_"))
        campos = {f.name: f.value for f in embed.fields}
        self.assertEqual(embed.title, r"📊 Estadísticas de Steve\_mc\_")
        self.assertEqual(campos["💀 Muertes"], "4")
        self.assertEqual(campos["⏱️ Tiempo jugado"], "8 h 5 min")
        self.assertEqual(campos["🧭 Distancia recorrida"], "63,0 km")

    async def responder(self, activas):
        """Ejecuta /stats y /top y devuelve qué mensaje recibió cada uno."""
        mensajes = []
        with mock.patch.object(main, "STATS_ACTIVAS", activas):
            for comando, args in ((main.stats_cmd, ("alex",)), (main.top_cmd, ("mobs",))):
                interaccion = mock.MagicMock()
                interaccion.response.send_message = mock.AsyncMock()
                await comando.callback(interaccion, *args)
                mensajes.append(interaccion.response.send_message.await_args)
        return mensajes

    async def test_cache_vacia_responde_mensaje_claro(self):
        main.cache.update(jugadores={}, actualizado=None)
        for llamada in await self.responder(activas=True):
            self.assertEqual(llamada, mock.call(main.SIN_CACHE, ephemeral=True))

    async def test_sin_sftp_responde_no_configuradas(self):
        for llamada in await self.responder(activas=False):
            self.assertEqual(llamada, mock.call(main.SIN_CONFIG, ephemeral=True))

    async def test_con_datos_responde_embed(self):
        for llamada in await self.responder(activas=True):
            self.assertIn("embed", llamada.kwargs)

    async def test_arranca_sin_sftp(self):
        bot = main.bot
        with mock.patch.object(main, "STATS_ACTIVAS", False), mock.patch.object(main, "GUILD_ID", ""), \
                mock.patch.object(bot.tree, "sync", mock.AsyncMock(return_value=[])), \
                mock.patch.object(bot, "actualizar_presencia") as presencia, \
                mock.patch.object(bot, "actualizar_stats") as stats:
            await bot.setup_hook()
        presencia.start.assert_called_once()
        stats.start.assert_not_called()

    async def test_fallo_sftp_conserva_cache(self):
        antes = copy.deepcopy(main.cache)
        falsos = dict(SFTP_HOST="127.0.0.1", SFTP_PORT="1", SFTP_USER="usuario_secreto", SFTP_PASSWORD="clave_secreta")
        with mock.patch.multiple(main, **falsos), self.assertLogs("bot-mc", "WARNING") as logs:
            await main.refrescar_cache()  # no debe lanzar
        self.assertEqual(main.cache, antes)
        self.assertNotIn("secreta", "".join(logs.output))
        self.assertNotIn("secreto", "".join(logs.output))


class TestPresencia(unittest.IsolatedAsyncioTestCase):
    async def test_presencia_solo_cambia_si_cambia_el_texto(self):
        bot = main.bot
        bot.ultimo_texto = None
        with mock.patch.object(main, "consultar_server", mock.AsyncMock(return_value=None)), \
                mock.patch.object(bot, "change_presence", mock.AsyncMock()) as cambiar:
            await bot.actualizar_presencia.coro(bot)
            await bot.actualizar_presencia.coro(bot)
        cambiar.assert_awaited_once()
        self.assertEqual(bot.ultimo_texto, "Server apagado")


if __name__ == "__main__":
    unittest.main()
