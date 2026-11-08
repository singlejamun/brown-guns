# === Owner role recovery ===
# Source lines 12698-12944 from original bot.py
# Loaded into shared namespace by bot.py — do not run this file alone.

# =========================================================
# OWNER ROLE RECOVERY PANEL
# (merged from owner_role_recovery.py — reuses BOT_OWNER_ID / GUILD_ID
#  and the existing `bot` instance, no separate process/token needed)
# =========================================================

def _recovery_owner_only():
    async def predicate(ctx: commands.Context) -> bool:
        return BOT_OWNER_ID > 0 and ctx.author.id == BOT_OWNER_ID
    return commands.check(predicate)


def get_assignable_roles(guild: discord.Guild) -> list[discord.Role]:
    me = guild.me
    if me is None:
        return []

    roles = []
    for role in guild.roles:
        if role.is_default():
            continue
        if role.managed:
            continue
        if role >= me.top_role:
            continue
        roles.append(role)

    return sorted(roles, key=lambda r: r.position, reverse=True)


class RecoveryRoleButton(discord.ui.Button):
    def __init__(self, role: discord.Role) -> None:
        super().__init__(
            label=role.name[:80],
            emoji="🎭",
            style=discord.ButtonStyle.secondary,
            custom_id=f"owner_role_{role.id}",
        )
        self.role_id = role.id

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != BOT_OWNER_ID:
            return await interaction.response.send_message(
                "❌ Only the bot owner can use this panel.", ephemeral=True
            )

        guild = bot.get_guild(GUILD_ID)
        if guild is None:
            return await interaction.response.send_message(
                "❌ Main Server is not available.", ephemeral=True
            )

        member = guild.get_member(BOT_OWNER_ID)
        if member is None:
            try:
                member = await guild.fetch_member(BOT_OWNER_ID)
            except discord.HTTPException:
                return await interaction.response.send_message(
                    "❌ You are not a member of the Main Server.", ephemeral=True
                )

        role = guild.get_role(self.role_id)
        if role is None:
            return await interaction.response.send_message(
                "❌ This role no longer exists.", ephemeral=True
            )

        me = guild.me
        if role.is_default() or role.managed or me is None or role >= me.top_role:
            return await interaction.response.send_message(
                "❌ I cannot assign this role.", ephemeral=True
            )

        if role in member.roles:
            return await interaction.response.send_message(
                f"ℹ️ You already have **{role.name}**.", ephemeral=True
            )

        try:
            await member.add_roles(
                role,
                reason="Owner role recovery panel",
            )
            await interaction.response.send_message(
                f"✅ **{role.name}** has been given to you in **{guild.name}**.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ I need **Manage Roles**, and my highest role must be above this role.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            await interaction.response.send_message(
                f"❌ Discord API error: `{e}`", ephemeral=True
            )


class RecoveryRolePanel(discord.ui.View):
    PER_PAGE = 20

    def __init__(self, roles: list[discord.Role], page: int = 0) -> None:
        super().__init__(timeout=300)
        self.roles = roles
        self.page = page

        start = page * self.PER_PAGE
        end = start + self.PER_PAGE

        for role in roles[start:end]:
            self.add_item(RecoveryRoleButton(role))

        if len(roles) > self.PER_PAGE:
            prev = discord.ui.Button(
                label="Previous", emoji="⬅️",
                style=discord.ButtonStyle.primary,
                disabled=(page == 0),
            )
            nxt = discord.ui.Button(
                label="Next", emoji="➡️",
                style=discord.ButtonStyle.primary,
                disabled=(end >= len(roles)),
            )
            prev.callback = self.previous
            nxt.callback = self.next
            self.add_item(prev)
            self.add_item(nxt)

        close = discord.ui.Button(
            label="Close", emoji="🗑️",
            style=discord.ButtonStyle.danger,
        )
        close.callback = self.close
        self.add_item(close)

    async def previous(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != BOT_OWNER_ID:
            return await interaction.response.send_message(
                "❌ Owner only.", ephemeral=True
            )
        p = self.page - 1
        await interaction.response.edit_message(
            embed=make_recovery_embed(self.roles, p),
            view=RecoveryRolePanel(self.roles, p),
        )

    async def next(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != BOT_OWNER_ID:
            return await interaction.response.send_message(
                "❌ Owner only.", ephemeral=True
            )
        p = self.page + 1
        await interaction.response.edit_message(
            embed=make_recovery_embed(self.roles, p),
            view=RecoveryRolePanel(self.roles, p),
        )

    async def close(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != BOT_OWNER_ID:
            return await interaction.response.send_message(
                "❌ Owner only.", ephemeral=True
            )
        await interaction.message.delete()


def make_recovery_embed(roles: list[discord.Role], page: int) -> discord.Embed:
    pages = max(1, math.ceil(len(roles) / RecoveryRolePanel.PER_PAGE))
    start = page * RecoveryRolePanel.PER_PAGE
    shown = roles[start:start + RecoveryRolePanel.PER_PAGE]

    embed = discord.Embed(
        title="👑 Owner Role Recovery",
        description=(
            "Select a role below to give it to yourself in the **Main Server**.\n"
            "Only the bot owner can use these buttons."
        ),
        color=discord.Color.gold(),
    )

    embed.add_field(
        name="🎭 Available Roles",
        value="\n".join(
            f"• {r.name}" for r in shown
        ) or "No assignable roles.",
        inline=False,
    )
    embed.set_footer(text=f"Page {page + 1}/{pages}")
    return embed


@bot.command(name="imback")
@_recovery_owner_only()
@commands.guild_only()
async def imback_command(ctx: commands.Context) -> None:
    guild = bot.get_guild(GUILD_ID)

    if guild is None:
        return await ctx.send(
            "❌ I cannot access the Main Server. "
            "Check `GUILD_ID` and make sure the bot is in the server."
        )

    roles = get_assignable_roles(guild)

    if not roles:
        return await ctx.send(
            "❌ I cannot assign any roles in the Main Server.\n"
            "Give the bot **Manage Roles** and move its highest role above "
            "the roles you want it to restore."
        )

    await ctx.send(
        embed=make_recovery_embed(roles, 0),
        view=RecoveryRolePanel(roles, 0),
    )


@bot.command(name="rolelist")
@_recovery_owner_only()
async def rolelist_command(ctx: commands.Context) -> None:
    guild = bot.get_guild(GUILD_ID)
    if guild is None:
        return await ctx.send("❌ Main Server unavailable.")

    roles = get_assignable_roles(guild)
    if not roles:
        return await ctx.send("❌ No assignable roles.")

    text = "\n".join(f"`{r.id}` — {r.name}" for r in roles)
    if len(text) > 1900:
        text = text[:1900] + "\n..."
    await ctx.send(f"🎭 **Roles I can assign in {guild.name}:**\n{text}")


@bot.command(name="recoverystatus")
@_recovery_owner_only()
async def recovery_status_command(ctx: commands.Context) -> None:
    guild = bot.get_guild(GUILD_ID)
    main = f"🟢 {guild.name}" if guild else "🔴 unavailable"
    await ctx.send(
        f"🤖 **Owner Recovery Status**\n"
        f"Owner: <@{BOT_OWNER_ID}>\n"
        f"Main Server: {main}\n"
        f"Latency: `{round(bot.latency * 1000)}ms`"
    )


