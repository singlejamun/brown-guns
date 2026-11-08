# === FamilyBot class + bot instance ===
# Source lines 1143-1165 from original bot.py
# Loaded into shared namespace by bot.py — do not run this file alone.

class FamilyBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True
        intents.message_content = True
        super().__init__(
            command_prefix="!",
            intents=intents,
            help_command=None,
            case_insensitive=True,
        )

    async def setup_hook(self) -> None:
        log.info("Part 1 loaded. Persistent views will be added later.")

    async def on_disconnect(self) -> None:
        log.warning("Gateway connection lost. discord.py will attempt to reconnect automatically.")

    async def on_resumed(self) -> None:
        log.info("Gateway connection resumed.")


bot = FamilyBot()
