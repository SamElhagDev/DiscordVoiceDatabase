import discord
from discord.ext import commands
from discord.ext.commands import Context


# Per-command icons and short display descriptions.
# Descriptions here are intentionally shorter than the slash command descriptions,
# which need to be verbose for Discord's autocomplete tooltip.
_ICONS = {
    "join":            "✅",
    "leave":           "🚫",
    "participants":    "👥",
    "record":          "⏺️",
    "stoprecord":      "⏹️",
    "recordingstatus": "📊",
    "setchannel":      "📌",
    "retention":       "🗓️",
    "listclips":       "📋",
    "listtext":        "📄",
    "playclip":        "▶️",
    "clip":            "💾",
    "search":          "🔍",
    "searchtext":      "🔎",
    "stop":            "🛑",
    "transcribe":      "📝",
    "perfstats":       "⚡",
    "help":            "❓",
    "ping":            "🏓",
    "sync":            "🔄",
    "unsync":          "🔇",
    "load":            "📦",
    "unload":          "📤",
    "reload":          "🔁",
    "shutdown":        "🔌",
}

_DESCS = {
    "join":            "Opt in to voice recording",
    "leave":           "Opt out of recording",
    "participants":    "List opted-in members",
    "record":          "Start recording",
    "stoprecord":      "Stop recording & disconnect",
    "recordingstatus": "Recording status & talk-time stats",
    "setchannel":      "Set primary auto-join channel",
    "retention":       "Set how long recordings are kept",
    "listclips":       "Browse recorded segments by date",
    "listtext":        "View transcript text for a segment",
    "playclip":        "Play a clip in voice chat",
    "clip":            "Download a clip",
    "search":          "Search transcripts & play a match",
    "searchtext":      "Search transcripts & view text",
    "stop":            "Stop current playback",
    "transcribe":      "Backfill missing transcripts",
    "perfstats":       "Pipeline processing time stats",
    "help":            "Show this menu",
    "ping":            "Check bot latency",
    "sync":            "Sync slash commands (global or guild)",
    "unsync":          "Remove slash commands",
    "load":            "Load a cog",
    "unload":          "Unload a cog",
    "reload":          "Reload a cog",
    "shutdown":        "Shut down the bot",
}

# Voice database sections — order matters for Discord's inline column layout.
# (title, [command names], inline)
# Layout produced:
#   Row 1 │ 👥 Participation  │ ⏺️ Recording          │
#   Row 2 │ 🎵 Playback (full width)                   │
#   Row 3 │ ⚙️ Settings       │ 📝 Transcription │ 🔧 General │
#   Row 4 │ 👑 Owner (full width, owner-only)           │
_VDB_SECTIONS = [
    ("👥  Participation", ["join", "leave", "participants"],           True),
    ("⏺️  Recording",     ["record", "stoprecord", "recordingstatus"], True),
    ("🎵  Playback",      ["listclips", "listtext", "playclip", "clip", "search", "searchtext", "stop"], False),
    ("⚙️  Settings",      ["setchannel", "retention"],                 True),
    ("📝  Transcription", ["transcribe", "perfstats"],                 True),
]


def _fmt_line(prefix: str, name: str, cmd) -> str:
    icon = _ICONS.get(name, "•")
    desc = _DESCS.get(name, cmd.description.partition("\n")[0])
    return f"{icon} `{prefix}{name}` — {desc}"


class General(commands.Cog, name="general"):
    def __init__(self, bot) -> None:
        self.bot = bot

    @commands.hybrid_command(
        name="help", description="List all commands the bot has loaded."
    )
    async def help(self, context: Context) -> None:
        prefix = self.bot.bot_prefix

        embed = discord.Embed(
            title="📖  Voice Database — Commands",
            description=f"Use `{prefix}command` or `/command` — all commands support both.",
            color=0x5865F2,
        )

        # ── Voice Database sections (explicit order) ────────────────────
        vdb = self.bot.get_cog("voicedatabase")
        if vdb:
            vdb_cmds = {cmd.name: cmd for cmd in vdb.get_commands()}
            for title, names, inline in _VDB_SECTIONS:
                lines = [
                    _fmt_line(prefix, n, vdb_cmds[n])
                    for n in names
                    if n in vdb_cmds
                ]
                if lines:
                    embed.add_field(name=title, value="\n".join(lines), inline=inline)

        # ── General (inline=True → sits in 3rd column of the Settings row) ──
        gen = self.bot.get_cog("general")
        if gen:
            gen_cmds = {cmd.name: cmd for cmd in gen.get_commands()}
            lines = [_fmt_line(prefix, n, c) for n, c in gen_cmds.items()]
            if lines:
                embed.add_field(name="🔧  General", value="\n".join(lines), inline=True)

        # ── Owner (full-width, only shown to the bot owner) ─────────────
        if await self.bot.is_owner(context.author):
            owner = self.bot.get_cog("owner")
            if owner:
                owner_cmds = {cmd.name: cmd for cmd in owner.get_commands()}
                lines = [_fmt_line(prefix, n, c) for n, c in owner_cmds.items()]
                if lines:
                    embed.add_field(name="👑  Owner", value="\n".join(lines), inline=False)

        embed.set_footer(text=f"💡  Slash commands support parameter hints and autocomplete.  |  v{self.bot.version}")
        await context.send(embed=embed)

    @commands.hybrid_command(
        name="ping",
        description="Check if the bot is alive.",
    )
    async def ping(self, context: Context) -> None:
        embed = discord.Embed(
            title="Pong!",
            description=f"The bot latency is {round(self.bot.latency * 1000)}ms.",
            color=0xBEBEFE,
        )
        await context.send(embed=embed)


async def setup(bot) -> None:
    await bot.add_cog(General(bot))
