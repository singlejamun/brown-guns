# === UI theme ===
# Source lines 143-210 from original bot.py
# Loaded into shared namespace by bot.py — do not run this file alone.

# =========================================================
# UI KIT — SHARED FUTURISTIC VISUAL THEME
# One place to control the look of every embed in the bot.
# Change a colour/icon here and it updates everywhere.
# =========================================================

class Theme:
    """Central colour + icon palette. Neon-on-dark 'HUD' style."""

    # Core neon palette
    PRIMARY = discord.Colour.from_str("#7C5CFF")      # violet — neutral/info
    SUCCESS = discord.Colour.from_str("#00E6A8")      # neon mint — approved/positive
    DANGER = discord.Colour.from_str("#FF3B6B")       # neon rose — denied/negative
    WARNING = discord.Colour.from_str("#FFB020")      # amber — caution/pending
    ACCENT = discord.Colour.from_str("#00C2FF")       # cyan — highlights/events

    # Recurring icons (kept consistent across every embed)
    ICON_ONLINE = "🟢"
    ICON_PENDING = "🟡"
    ICON_OFFLINE = "🔴"
    ICON_ARROW = "›"
    ICON_SPARK = "✦"
    ICON_RANK = "🎖️"
    ICON_WARN = "⚠️"
    ICON_EVENT = "🛰️"
    ICON_COMPLAINT = "📡"
    ICON_ROLE = "🔰"
    ICON_LOCK = "🔒"

    DIVIDER = "╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌╌"


def hud_embed(
    title: str,
    *,
    description: str | None = None,
    colour: discord.Colour = Theme.PRIMARY,
    icon: str = Theme.ICON_SPARK,
    footer: str | None = None,
) -> discord.Embed:
    """Base builder for the bot's futuristic HUD-style embeds."""
    embed = discord.Embed(
        title=f"{icon}  {title}",
        description=description,
        colour=colour,
        timestamp=now_local(),
    )
    embed.set_footer(text=footer or "Brown Guns Family • Command System")
    return embed


def hud_field_block(pairs: list[tuple[str, str]]) -> str:
    """Render a list of (label, value) pairs as a tidy monospace-aligned block."""
    if not pairs:
        return ""
    width = max(len(label) for label, _ in pairs)
    lines = [f"`{label.ljust(width)}` {Theme.ICON_ARROW} {value}" for label, value in pairs]
    return "\n".join(lines)


def hud_progress_bar(current: int, total: int, *, length: int = 10) -> str:
    """Render a compact filled/empty bar, e.g. `███████░░░ 7/10`."""
    total = max(total, 1)
    filled = min(length, round((current / total) * length))
    bar = "█" * filled + "░" * (length - filled)
    return f"`{bar}` **{current}/{total}**"


