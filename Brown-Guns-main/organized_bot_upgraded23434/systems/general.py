# === General commands ===
# Source lines 12007-12323 from original bot.py
# Loaded into shared namespace by bot.py — do not run this file alone.

# =========================================================
# GENERAL COMMANDS
# =========================================================


def build_member_help_embed() -> discord.Embed:
    """Help view for normal members — only what they can actually use."""
    embed = discord.Embed(
        title="📋 Grand RP Family Bot",
        description=(
            "Here's what you can do. Staff and owner-only tools are hidden."
        ),
        colour=Theme.PRIMARY,
    )

    embed.add_field(
        name="📋 Informal Registration",
        value=(
            "• Opens automatically every hour at `:00`\n"
            "• Skips the 4 PM and 10 PM hours (RP Factory)\n"
            "• 10 main registration slots\n"
            "• 5 waiting-list slots\n"
            "• Registration closes at `:10`\n"
            "• Informal Roster role is removed at `:40`\n"
            "• First waiting member is promoted automatically"
        ),
        inline=False,
    )

    embed.add_field(
        name="👤 Role Request",
        value=(
            "Click the **Rank Request** button in the configured channel.\n"
            "Fill in your In-Game Name, ID, and Rank.\n"
            "Then send one proof screenshot to the bot in DM.\n"
            "Staff receive Accept and Deny buttons.\n"
            "Accepted nickname format: `Vikku Smurf | 109862`"
        ),
        inline=False,
    )

    embed.add_field(
        name="📨 Complaints",
        value=(
            "Use the **Submit Complaint** button in the complaint channel.\n"
            "Staff will mark it solved or reject it with a reason.\n"
            "Need someone's POV of an event? Press **POV Request** on the same panel."
        ),
        inline=False,
    )

    embed.add_field(
        name="⚠️ Warnings",
        value=(
            "`!warnings` — Check your own active warnings\n"
            "Warnings expire automatically after 7 days."
        ),
        inline=False,
    )

    embed.add_field(
        name="🎮 Events",
        value=(
            "Watch the event ping channel for Foundry, RP Factory, "
            "Bizwar, and Weps announcements.\n"
            "Use the buttons on those posts to participate."
        ),
        inline=False,
    )

    embed.add_field(
        name="General",
        value=(
            "`!help` — Show this menu\n"
            "`!ping` — Bot latency\n"
            "`!botstatus` — Bot status"
        ),
        inline=False,
    )

    embed.set_footer(
        text="Grand RP Family Management Bot • Member Help"
    )
    return embed


def build_owner_help_embed() -> discord.Embed:
    """Full help view — every command the bot has, owner only."""
    embed = discord.Embed(
        title="👑 Grand RP Family Bot — Owner Command List",
        description=(
            "Every command the bot has, organised by system. "
            "Only visible to the bot owner."
        ),
        colour=Theme.WARNING,
    )

    embed.add_field(
        name="General",
        value=(
            "`!help` — Owner: full list · Member: limited list\n"
            "`!ping` — Bot latency\n"
            "`!botstatus` / `!status` — Scheduler + bot status"
        ),
        inline=False,
    )

    embed.add_field(
        name="🔒 Server Security",
        value=(
            "`!security` — Lock every channel (no one can send messages)\n"
            "`!securityoff` — Unlock the server again\n"
            "`!reloadpatterns` — Reload hack-detection patterns\n"
            "`!testhack <text>` — Test if text would trigger hack detection\n"
            "`!testrpreminder` — Post a test RP Ticket reminder"
        ),
        inline=False,
    )

    embed.add_field(
        name="⚠️ Punishment & Warnings",
        value=(
            "`!jailer @role` — Set the Jailer role (high command). Only this role can punish, "
            "remove strikes and review complaints / POV requests. `!jailer` shows it, `!jailer off` clears it\n"
            "`!punish @member [reason]` — Jailer: warn / remove strike / history\n"
            "`!warnings [@member]` — Own warnings, or anyone's for Jailers\n"
            "`!allwarning` — Jailer: list every member with active warnings\n"
            "`!remove @member 1|2|3|all <reason>` — Jailer: remove a warning (reason is shown publicly)\n"
            "Warned members can appeal from the punishment board post."
        ),
        inline=False,
    )

    embed.add_field(
        name="🛠️ Role Requests",
        value=(
            "`!requests` (alias of `!pendingrequests`) — Show pending requests\n"
            "`!requestinfo <request_id>` — Show a specific request"
        ),
        inline=False,
    )

    embed.add_field(
        name="📨 Complaints & Ranks",
        value=(
            "`!complaintpanel` — Deploy / refresh the complaint panel (has the 🎥 POV Request button)\n"
            "`!rankpanel` — Deploy the rank request panel"
        ),
        inline=False,
    )

    embed.add_field(
        name="🎮 Events & Voice",
        value=(
            "`!eventpanel` — Deploy the event panel (control server)\n"
            "`!voicepanel` — Deploy the voice panel\n"
            "`!testsystem` — Run system self-test"
        ),
        inline=False,
    )

    embed.add_field(
        name="⚙️ System Controls",
        value=(
            "`!systemoff <system>` — Disable a system\n"
            "`!systemon <system>` — Enable a system\n"
            "`!systemstatus` (alias `!systems`) — View system status\n"
            "Systems: `informal_registration`, `event_panel`, `rp_ticket_reminder`, `hack_detection`"
        ),
        inline=False,
    )

    embed.add_field(
        name="🗄️ Data Management",
        value=(
            "`!startdata` — Start auto data collection\n"
            "`!checkdata` — Check data status\n"
            "`!recreatedata` — Recreate data categories/channels"
        ),
        inline=False,
    )

    embed.add_field(
        name="🕹️ Control Server",
        value=(
            "`!controlaccess @member` (alias `!controllaccess`) — Grant control access\n"
            "`!removecontrolaccess @member` (alias `!revokecontrolaccess`) — Revoke it\n"
            "`!listcontrolaccess` — List members with control access\n"
            "`!ui @member` — Open the control member UI\n"
            "`!unban <user_id>` — Unban from the main server\n"
            "`!ban @member` — Ban from the main server\n"
            "`!muteall` / `!unmuteall` — Mute or unmute everyone in voice\n"
            "`!serverstatus` — Main server status\n"
            "`!announce <#channel> <message>` — Post an announcement remotely"
        ),
        inline=False,
    )

    embed.add_field(
        name="👑 Owner Role Recovery",
        value=(
            "`!imback` — Open the role recovery panel\n"
            "`!rolelist` — List assignable roles\n"
            "`!recoverystatus` — Recovery system status"
        ),
        inline=False,
    )

    embed.add_field(
        name="🎉 Fun / Utility",
        value=(
            "`!lol @member` — Toggle continuous VC dragging "
            "(owner + members given access only)\n"
            "`!lolaccess @member` — Give/remove `!lol` access (owner only)"
        ),
        inline=False,
    )

    embed.set_footer(
        text="Grand RP Family Management Bot • Owner Help"
    )
    return embed


@bot.command(
    name="help",
)
async def help_command(
    ctx: commands.Context,
) -> None:
    is_owner = BOT_OWNER_ID > 0 and ctx.author.id == BOT_OWNER_ID

    embed = (
        build_owner_help_embed()
        if is_owner
        else build_member_help_embed()
    )

    await ctx.reply(
        embed=embed,
        mention_author=False,
    )


@bot.command(
    name="ping",
)
async def ping_command(
    ctx: commands.Context,
) -> None:
    latency = round(
        bot.latency * 1000
    )

    await ctx.reply(
        f"🏓 Bot latency: **{latency}ms**",
        mention_author=False,
    )


@bot.command(
    name="botstatus",
    aliases=["status"],
)
async def bot_status_command(
    ctx: commands.Context,
) -> None:
    if ctx.guild is None:
        return

    registration_status = (
        "Running"
        if registration_scheduler.is_running()
        else "Stopped"
    )

    warning_status = (
        "Running"
        if warning_expiry_scheduler.is_running()
        else "Stopped"
    )

    embed = discord.Embed(
        title="🤖 Bot Status",
        colour=Theme.SUCCESS,
    )

    embed.add_field(
        name="Informal Scheduler",
        value=f"**{registration_status}**",
        inline=True,
    )

    embed.add_field(
        name="Warning Scheduler",
        value=f"**{warning_status}**",
        inline=True,
    )

    embed.add_field(
        name="Latency",
        value=f"**{round(bot.latency * 1000)}ms**",
        inline=True,
    )

    embed.add_field(
        name="Database",
        value=f"`{DATABASE_PATH}`",
        inline=False,
    )

    await ctx.reply(
        embed=embed,
        mention_author=False,
    )


