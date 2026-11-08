"""
Grand RP Family Bot — organized entry point.

All system files live in folders for clarity, but are executed in a
single shared namespace (same as the original monolithic bot.py),
so every cross-reference keeps working.

Run:  python bot.py
"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Files are executed in order into one shared namespace (like the original single file).
LOAD_ORDER = [
    "config/settings.py",
    "core/theme.py",
    "core/utils.py",
    "core/database.py",
    "core/bot_class.py",
    "systems/db_maintenance.py",
    "systems/rp_list.py",
    "systems/registration.py",
    "systems/role_request.py",
    "systems/punishment.py",
    "systems/rank_request.py",
    "systems/complaints.py",
    "systems/jailer.py",
    "systems/pov_requests.py",
    "systems/system_control.py",
    "systems/events_system.py",
    "systems/voice_control.py",
    "systems/startup.py",
    "systems/data_setup.py",
    "systems/control_commands.py",
    "systems/general.py",
    "systems/security.py",
    "systems/lol.py",
    "systems/error_handler.py",
    "systems/recovery.py",
    "systems/config_manager.py",   # !config — set IDs from Discord (must be after voice_control/rank_request)
    "systems/validation.py"
]

def main() -> None:
    namespace: dict = {"__name__": "__main__", "__file__": str(ROOT / "bot.py")}
    for rel in LOAD_ORDER:
        path = ROOT / rel
        code = path.read_text(encoding="utf-8")
        compiled = compile(code, str(path), "exec")
        exec(compiled, namespace)

    # Start (same as original __main__ block)
    final_validate = namespace.get("final_validate_configuration")
    load_saved = namespace.get("load_saved_config")
    bot = namespace.get("bot")
    token = namespace.get("TOKEN")
    log = namespace.get("log")

    if final_validate is None or load_saved is None or bot is None:
        raise RuntimeError("Bot failed to load correctly (missing bot/validate/config loader).")
    if not token:
        raise RuntimeError("DISCORD_TOKEN is not set. It is the only environment variable you need.")

    # Values saved with !config (database) override environment variables.
    load_saved()
    final_validate()  # only warns now — finish the setup with !config in Discord
    if log:
        log.info("Starting Grand RP Family Management Bot...")
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
