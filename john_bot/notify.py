"""discord alerts + command bot.

three modes, chosen automatically from env:
  - BotNotifier      : DISCORD_BOT_TOKEN + DISCORD_CHANNEL_ID set -> full bot with
                       slash commands (/status /position /pnl /pause /resume
                       /flatten /params) AND channel alerts. runs in its own thread.
  - WebhookNotifier  : only DISCORD_WEBHOOK_URL set -> alerts only, no commands.
  - NullNotifier     : nothing set -> silent.

alerts fire on trade ENTRY (with estimated profit/loss) and EXIT (with result +
overall profits). if DISCORD_USER_ID is set, alerts ping you.

the bot reads live state through the engine reference passed in; command actions
(pause/resume/flatten) are handled thread-safely by the engine on its next tick.
"""
from __future__ import annotations

import threading
from typing import List, Optional, Tuple

import requests

from .logutil import get_logger

log = get_logger()

Field = Tuple[str, str, bool]  # (name, value, inline)

GREEN = 0x2ECC71
RED = 0xE74C3C
BLUE = 0x3498DB
GREY = 0x95A5A6


def money(x: float) -> str:
    sign = "-" if x < 0 else ""
    return f"{sign}${abs(x):,.2f}"


class NullNotifier:
    def start(self) -> None:  # noqa: D401
        pass

    def send_embed(self, title: str, fields: List[Field], color: int = BLUE, ping: bool = False) -> None:
        pass


class WebhookNotifier:
    def __init__(self, cfg):
        self.url = cfg.discord_webhook_url
        self.user_id = cfg.discord_user_id

    def start(self) -> None:
        log.info("[discord] webhook alerts enabled (no commands)")

    def send_embed(self, title: str, fields: List[Field], color: int = BLUE, ping: bool = False) -> None:
        embed = {
            "title": title,
            "color": color,
            "fields": [{"name": n, "value": v, "inline": i} for n, v, i in fields],
        }
        payload = {"embeds": [embed]}
        if ping and self.user_id:
            payload["content"] = f"<@{self.user_id}>"
        try:
            requests.post(self.url, json=payload, timeout=8)
        except Exception as e:
            log.warning("[discord] webhook send failed: %s", e)


class BotNotifier:
    def __init__(self, cfg, engine):
        self.cfg = cfg
        self.engine = engine
        self.user_id = cfg.discord_user_id
        self.bot = None
        self.loop = None
        self.channel = None
        self._ready = threading.Event()

    # -- public API used by the engine ------------------------------------
    def start(self) -> None:
        t = threading.Thread(target=self._run, name="discord-bot", daemon=True)
        t.start()
        if self._ready.wait(timeout=25):
            log.info("[discord] bot ready, commands live")
        else:
            log.warning("[discord] bot not ready after 25s (alerts may still work)")

    def send_embed(self, title: str, fields: List[Field], color: int = BLUE, ping: bool = False) -> None:
        import asyncio

        if not self.loop or not self.channel:
            return
        content = f"<@{self.user_id}>" if (ping and self.user_id) else None
        coro = self._send(title, fields, color, content)
        try:
            asyncio.run_coroutine_threadsafe(coro, self.loop)
        except Exception as e:
            log.warning("[discord] schedule send failed: %s", e)

    async def _send(self, title, fields, color, content):
        import discord

        embed = discord.Embed(title=title, color=color)
        for n, v, inline in fields:
            embed.add_field(name=n, value=v, inline=inline)
        try:
            await self.channel.send(content=content, embed=embed)
        except Exception as e:
            log.warning("[discord] send failed: %s", e)

    # -- bot thread --------------------------------------------------------
    def _run(self) -> None:
        import asyncio

        import discord
        from discord import app_commands
        from discord.ext import commands

        intents = discord.Intents.default()  # slash commands need no privileged intents
        bot = commands.Bot(command_prefix="!", intents=intents)
        self.bot = bot
        engine = self.engine
        cfg = self.cfg

        @bot.event
        async def on_ready():
            self.loop = asyncio.get_running_loop()
            try:
                cid = int(cfg.discord_channel_id)
                self.channel = bot.get_channel(cid) or await bot.fetch_channel(cid)
            except Exception as e:
                log.warning("[discord] channel resolve failed: %s", e)
            try:
                if cfg.discord_guild_id:
                    guild = discord.Object(id=int(cfg.discord_guild_id))
                    bot.tree.copy_global_to(guild=guild)
                    await bot.tree.sync(guild=guild)
                else:
                    await bot.tree.sync()
            except Exception as e:
                log.warning("[discord] command sync failed: %s", e)
            self._ready.set()
            log.info("[discord] logged in as %s", bot.user)

        def _stats_fields() -> List[Field]:
            s = engine.snapshot()
            wl = f"{s['wins']}/{s['losses']}"
            return [
                ("mode", s["mode"], True),
                ("active symbol", s["active_symbol"], True),
                ("paused", "yes" if s["paused"] else "no", True),
                ("sizing base", money(s["sizing_base"]), True),
                ("banked (60%)", money(s["banked_reserve"]), True),
                ("realized pnl", money(s["realized_pnl"]), True),
                ("win / loss", f"{wl}  ({s['winrate']:.0f}%)", True),
                ("roi", f"{s['roi']:+.1f}%", True),
                ("cooldown", f"{s['cooldown_remaining']:.0f}s", True),
            ]

        @bot.tree.command(name="status", description="bot status + stats")
        async def status(interaction: discord.Interaction):
            e = discord.Embed(title="john algro bot — status", color=BLUE)
            for n, v, i in _stats_fields():
                e.add_field(name=n, value=v, inline=i)
            pos = engine.position_report()
            if pos:
                e.add_field(name="open position",
                            value=f"{pos['symbol']} {pos['side']} @ {pos['entry']:.6g}\n"
                                  f"unreal: {money(pos['unrealized'])}", inline=False)
            await interaction.response.send_message(embed=e)

        @bot.tree.command(name="position", description="current open trade")
        async def position(interaction: discord.Interaction):
            pos = engine.position_report()
            if not pos:
                await interaction.response.send_message("flat — no open position.")
                return
            e = discord.Embed(title=f"open position — {pos['symbol']} {pos['side']}", color=BLUE)
            e.add_field(name="entry", value=f"{pos['entry']:.6g}", inline=True)
            e.add_field(name="mark", value=f"{pos['mark']:.6g}", inline=True)
            e.add_field(name="size", value=f"{pos['size']}", inline=True)
            e.add_field(name="stop / target", value=f"{pos['sl']:.6g} / {pos['tp']:.6g}", inline=True)
            e.add_field(name="unrealized", value=money(pos["unrealized"]), inline=True)
            e.add_field(name="held", value=f"{pos['held_sec']:.0f}s", inline=True)
            await interaction.response.send_message(embed=e)

        @bot.tree.command(name="pnl", description="profit summary + compounding")
        async def pnl(interaction: discord.Interaction):
            s = engine.snapshot()
            e = discord.Embed(title="pnl & compounding", color=GREEN if s["realized_pnl"] >= 0 else RED)
            e.add_field(name="realized pnl", value=money(s["realized_pnl"]), inline=True)
            e.add_field(name="sizing base", value=money(s["sizing_base"]), inline=True)
            e.add_field(name="banked (60%)", value=money(s["banked_reserve"]), inline=True)
            e.add_field(name="start base", value=money(s["start_base"]), inline=True)
            e.add_field(name="roi", value=f"{s['roi']:+.1f}%", inline=True)
            e.add_field(name="trades", value=f"{s['wins']+s['losses']}", inline=True)
            await interaction.response.send_message(embed=e)

        @bot.tree.command(name="pause", description="stop opening new trades")
        async def pause(interaction: discord.Interaction):
            engine.set_paused(True)
            await interaction.response.send_message("paused — no new entries. open trades still managed.")

        @bot.tree.command(name="resume", description="resume opening new trades")
        async def resume(interaction: discord.Interaction):
            engine.set_paused(False)
            await interaction.response.send_message("resumed.")

        @bot.tree.command(name="flatten", description="close the current position now (market)")
        async def flatten(interaction: discord.Interaction):
            if not engine.position_report():
                await interaction.response.send_message("nothing to flatten — already flat.")
                return
            engine.request_flatten()
            await interaction.response.send_message("flatten requested — closing at next tick.")

        @bot.tree.command(name="params", description="key strategy parameters")
        async def params(interaction: discord.Interaction):
            e = discord.Embed(title="parameters", color=GREY)
            e.add_field(name="symbols", value=",".join(cfg.symbols), inline=True)
            e.add_field(name="interval", value=cfg.interval, inline=True)
            e.add_field(name="leverage", value=f"{cfg.leverage}x", inline=True)
            e.add_field(name="risk/trade", value=f"{cfg.risk_frac*100:.1f}%", inline=True)
            e.add_field(name="compound", value=f"{cfg.compound_frac*100:.0f}%", inline=True)
            e.add_field(name="war window", value=str(cfg.war_window), inline=True)
            e.add_field(name="dominance thr", value=f"{cfg.dominance_threshold}", inline=True)
            e.add_field(name="echo min len", value=str(cfg.echo_min_len), inline=True)
            e.add_field(name="cooldown", value=cfg.cooldown_mode, inline=True)
            await interaction.response.send_message(embed=e)

        try:
            bot.run(cfg.discord_bot_token, log_handler=None)
        except Exception as e:
            log.error("[discord] bot crashed: %s", e)
            self._ready.set()


def make_notifier(cfg, engine):
    if cfg.discord_bot_enabled:
        return BotNotifier(cfg, engine)
    if cfg.discord_webhook_url:
        return WebhookNotifier(cfg)
    return NullNotifier()
