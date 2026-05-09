import logging
import os
import platform
import random
import sys

import aiosqlite
import discord
from discord.ext import commands, tasks
from discord.ext.commands import Context
from dotenv import load_dotenv

from database import DatabaseManager

load_dotenv()

# Configurable paths — override via .env or environment variables
# Defaults work for both Windows direct-run and Docker
DB_PATH = os.getenv("DB_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "database.db"))
RECORDINGS_PATH = os.getenv("RECORDINGS_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings"))
CLIPS_PATH = os.getenv("CLIPS_PATH", os.path.join(os.path.dirname(os.path.abspath(__file__)), "clips"))

# Ensure directories exist
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(RECORDINGS_PATH, exist_ok=True)
os.makedirs(CLIPS_PATH, exist_ok=True)


intents = discord.Intents.default()
intents.bans = True
intents.dm_messages = True
intents.dm_reactions = True
intents.dm_typing = True
intents.emojis = True
intents.emojis_and_stickers = True
intents.guild_messages = True
intents.guild_reactions = True
intents.guild_typing = True
intents.guilds = True
intents.integrations = True 
intents.invites = True
intents.messages = True
intents.reactions = True
intents.typing = True
intents.voice_states = True
intents.webhooks = True
intents.members = True
intents.message_content = True
intents.presences = True


class LoggingFormatter(logging.Formatter):
    black = "\x1b[30m"
    red = "\x1b[31m"
    green = "\x1b[32m"
    yellow = "\x1b[33m"
    blue = "\x1b[34m"
    gray = "\x1b[38m"
    reset = "\x1b[0m"
    bold = "\x1b[1m"

    COLORS = {
        logging.DEBUG: gray + bold,
        logging.INFO: blue + bold,
        logging.WARNING: yellow + bold,
        logging.ERROR: red,
        logging.CRITICAL: red + bold,
    }

    def format(self, record):
        log_color = self.COLORS.get(record.levelno, self.gray)
        format = "(black){asctime}(reset) (levelcolor){levelname:<8}(reset) (green){name}(reset) {message}"
        format = format.replace("(black)", self.black + self.bold)
        format = format.replace("(reset)", self.reset)
        format = format.replace("(levelcolor)", log_color)
        format = format.replace("(green)", self.green + self.bold)
        formatter = logging.Formatter(format, "%Y-%m-%d %H:%M:%S", style="{")
        return formatter.format(record)


logger = logging.getLogger("discord_bot")
logger.setLevel(logging.DEBUG)

console_handler = logging.StreamHandler()
console_handler.setFormatter(LoggingFormatter())
file_handler = logging.FileHandler(filename="discord.log", encoding="utf-8", mode="a")
file_handler_formatter = logging.Formatter(
    "[{asctime}] [{levelname:<8}] {name}: {message}", "%Y-%m-%d %H:%M:%S", style="{"
)
file_handler.setFormatter(file_handler_formatter)

logger.addHandler(console_handler)
logger.addHandler(file_handler)


class DiscordBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=commands.when_mentioned_or(os.getenv("PREFIX", "!")),
            intents=intents,
            help_command=None,
        )
        self.logger = logger
        self.database = None
        self.bot_prefix = os.getenv("PREFIX", "!")
        self.invite_link = os.getenv("INVITE_LINK", "")

    async def init_db(self) -> None:
        self.logger.info("Initializing database schema...")
        async with aiosqlite.connect(DB_PATH) as db:
            with open(
                os.path.join(os.path.realpath(os.path.dirname(__file__)), "database", "schema.sql"),
                encoding="utf-8",
            ) as file:
                await db.executescript(file.read())
            await db.commit()

            # Apply migrations (safe to re-run)
            migrations = [
                "ALTER TABLE segments ADD COLUMN transcript TEXT",
            ]
            for sql in migrations:
                try:
                    await db.execute(sql)
                    await db.commit()
                    self.logger.info(f"Applied migration: {sql}")
                except Exception:
                    pass  # column already exists
        self.logger.info("Database schema ready")

    async def load_cogs(self) -> None:
        for file in os.listdir(
            os.path.join(os.path.realpath(os.path.dirname(__file__)), "cogs")
        ):
            if file.endswith(".py"):
                extension = file[:-3]
                try:
                    await self.load_extension(f"cogs.{extension}")
                    self.logger.info(f"Loaded extension '{extension}'")
                except Exception as e:
                    exception = f"{type(e).__name__}: {e}"
                    self.logger.error(
                        f"Failed to load extension {extension}\n{exception}"
                    )

    @tasks.loop(minutes=1.0)
    async def status_task(self) -> None:
        statuses = [
            "Recording voices...",
            "Listening carefully...",
            "Archiving audio...",
        ]
        await self.change_presence(activity=discord.Game(random.choice(statuses)))

    @status_task.before_loop
    async def before_status_task(self) -> None:
        await self.wait_until_ready()

    async def setup_hook(self) -> None:
        self.logger.info(f"Logged in as {self.user.name}")
        self.logger.info(f"discord.py API version: {discord.__version__}")
        self.logger.info(f"Python version: {platform.python_version()}")
        self.logger.info(
            f"Running on: {platform.system()} {platform.release()} ({os.name})"
        )
        self.logger.info(f"Database: {DB_PATH}")
        self.logger.info(f"Recordings: {RECORDINGS_PATH}")
        self.logger.info(f"Clips: {CLIPS_PATH}")
        self.logger.info("-------------------")
        await self.init_db()
        self.database = DatabaseManager(
            connection=await aiosqlite.connect(DB_PATH)
        )
        await self.load_cogs()
        self.status_task.start()

    async def close(self) -> None:
        self.logger.info("Bot shutting down — closing database connection...")
        if self.database:
            await self.database.connection.close()
            self.logger.info("Database connection closed")
        await super().close()

    async def on_message(self, message: discord.Message) -> None:
        if message.author == self.user or message.author.bot:
            return
        await self.process_commands(message)

    async def on_command_completion(self, context: Context) -> None:
        full_command_name = context.command.qualified_name
        split = full_command_name.split(" ")
        executed_command = str(split[0])
        if context.guild is not None:
            self.logger.info(
                f"Executed {executed_command} command in {context.guild.name} (ID: {context.guild.id}) by {context.author} (ID: {context.author.id})"
            )
        else:
            self.logger.info(
                f"Executed {executed_command} command by {context.author} (ID: {context.author.id}) in DMs"
            )

    async def on_command_error(self, context: Context, error) -> None:
        if isinstance(error, commands.CommandOnCooldown):
            minutes, seconds = divmod(error.retry_after, 60)
            hours, minutes = divmod(minutes, 60)
            hours = hours % 24
            embed = discord.Embed(
                description=f"**Please slow down** - You can use this command again in {f'{round(hours)} hours' if round(hours) > 0 else ''} {f'{round(minutes)} minutes' if round(minutes) > 0 else ''} {f'{round(seconds)} seconds' if round(seconds) > 0 else ''}.",
                color=0xE02B2B,
            )
            await context.send(embed=embed)
        elif isinstance(error, commands.NotOwner):
            embed = discord.Embed(
                description="You are not the owner of the bot!", color=0xE02B2B
            )
            await context.send(embed=embed)
            if context.guild:
                self.logger.warning(
                    f"{context.author} (ID: {context.author.id}) tried to execute an owner only command in the guild {context.guild.name} (ID: {context.guild.id}), but the user is not an owner of the bot."
                )
            else:
                self.logger.warning(
                    f"{context.author} (ID: {context.author.id}) tried to execute an owner only command in the bot's DMs, but the user is not an owner of the bot."
                )
        elif isinstance(error, commands.MissingPermissions):
            embed = discord.Embed(
                description="You are missing the permission(s) `"
                + ", ".join(error.missing_permissions)
                + "` to execute this command!",
                color=0xE02B2B,
            )
            await context.send(embed=embed)
        elif isinstance(error, commands.BotMissingPermissions):
            embed = discord.Embed(
                description="I am missing the permission(s) `"
                + ", ".join(error.missing_permissions)
                + "` to fully perform this command!",
                color=0xE02B2B,
            )
            await context.send(embed=embed)
        elif isinstance(error, commands.MissingRequiredArgument):
            embed = discord.Embed(
                title="Error!",
                description=str(error).capitalize(),
                color=0xE02B2B,
            )
            await context.send(embed=embed)
        elif isinstance(error, commands.CommandNotFound):
            pass  # ignore unknown commands silently
        elif isinstance(error, commands.CommandInvokeError):
            self.logger.error(
                f"Command '{context.command}' raised an exception: {error.original}",
                exc_info=error.original,
            )
            embed = discord.Embed(
                title="Error!",
                description=f"An internal error occurred: `{type(error.original).__name__}: {error.original}`",
                color=0xE02B2B,
            )
            await context.send(embed=embed)
        else:
            raise error


bot = DiscordBot()
bot.run(os.getenv("TOKEN"))
