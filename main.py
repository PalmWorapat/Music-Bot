import asyncio
import glob
import os
from collections import deque
from ctypes.util import find_library
from dataclasses import dataclass
from typing import Deque, Optional
from urllib.parse import urlparse

import discord
import imageio_ffmpeg
import yt_dlp
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
from myserver import server_on

load_dotenv()

# =========================
# CONFIG - เติมค่าตรงนี้ผ่าน Replit Secrets หรือไฟล์ .env
# =========================

# REQUIRED:
# ใส่ Token Bot ใน Replit Secrets ชื่อ DISCORD_TOKEN
# ห้ามแปะ token ลง GitHub หรือส่งในแชตสาธารณะ
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

# OPTIONAL:
# ใส่ Server/Guild ID สำหรับทดสอบ slash command ให้ sync ทันที
# ถ้าไม่ใส่ จะ sync แบบ global ซึ่งอาจใช้เวลาสักพักกว่าจะเห็นคำสั่งใน Discord
# ตัวอย่าง: TEST_GUILD_ID=123456789012345678
TEST_GUILD_ID = os.getenv("TEST_GUILD_ID")

# OPTIONAL:
# ถ้าใช้ Windows/VS Code แล้วไม่ได้ติดตั้ง ffmpeg ใน PATH โค้ดจะใช้ ffmpeg จาก imageio-ffmpeg ให้เอง
# ถ้าติดตั้ง ffmpeg ไว้เองแล้ว อยากระบุ path ชัด ๆ ให้ใส่แบบนี้ใน .env:
# FFMPEG_EXECUTABLE=C:\ffmpeg\bin\ffmpeg.exe
FFMPEG_EXECUTABLE = os.getenv("FFMPEG_EXECUTABLE") or imageio_ffmpeg.get_ffmpeg_exe()

# OPTIONAL:
# จำกัดคิวและความยาวคลิป ปรับได้ผ่าน Replit Secrets
MAX_QUEUE_SIZE = int(os.getenv("MAX_QUEUE_SIZE", "50"))
MAX_VIDEO_SECONDS = int(os.getenv("MAX_VIDEO_SECONDS", "10800"))


def load_discord_opus() -> None:
    if discord.opus.is_loaded():
        return

    candidates = [
        os.getenv("OPUS_LIBRARY"),
        find_library("opus"),
        "libopus.so.0",
        "libopus.so",
        "libopus.dylib",
        "opus.dll",
        "libopus-0.dll",
        "opus",
    ]
    candidates.extend(glob.glob("/nix/store/*-opus-*/lib/libopus.so*"))
    candidates.extend(glob.glob("/nix/store/*-libopus-*/lib/libopus.so*"))

    seen = set()
    errors = []
    for candidate in candidates:
        if not candidate or candidate in seen:
            continue
        seen.add(candidate)
        try:
            discord.opus.load_opus(candidate)
        except (AttributeError, OSError) as exc:
            errors.append(f"{candidate}: {exc}")
            continue
        if discord.opus.is_loaded():
            print(f"Loaded Opus library: {candidate}")
            return

    details = "\n".join(errors[-5:])
    raise RuntimeError(
        "Discord voice requires libopus, but it could not be loaded. "
        "On Replit, make sure replit.nix includes pkgs.opus."
        + (f"\nRecent load errors:\n{details}" if details else "")
    )


YOUTUBE_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "m.youtube.com",
    "music.youtube.com",
    "youtu.be",
}


def is_youtube_url(value: str) -> bool:
    try:
        parsed = urlparse(value.strip())
    except ValueError:
        return False

    return parsed.scheme in {"http", "https"} and parsed.hostname in YOUTUBE_HOSTS


def format_duration(total_seconds: Optional[int]) -> str:
    if not total_seconds:
        return "unknown"

    hours, remainder = divmod(int(total_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)

    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


@dataclass
class Song:
    title: str
    webpage_url: str
    stream_url: str
    duration: int
    channel: str
    requested_by: str


COLOR_MUSIC = 0xFF8A00
COLOR_SUCCESS = 0x2ECC71
COLOR_INFO = 0x3498DB
COLOR_WARNING = 0xF1C40F
COLOR_ERROR = 0xE74C3C


def truncate_text(value: str, limit: int = 1024) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def status_embed(title: str, description: str, color: int = COLOR_INFO) -> discord.Embed:
    return discord.Embed(title=title, description=description, color=color)


def song_embed(title: str, song: Song, color: int = COLOR_MUSIC) -> discord.Embed:
    embed = discord.Embed(
        title=title,
        description=f"**[{truncate_text(song.title, 240)}]({song.webpage_url})**",
        color=color,
    )
    embed.add_field(name="⏱️ ระยะเวลา", value=format_duration(song.duration), inline=True)
    embed.add_field(name="📺 ช่อง", value=truncate_text(song.channel, 256), inline=True)
    embed.add_field(name="🙋 ขอโดย", value=truncate_text(song.requested_by, 256), inline=True)
    return embed


async def extract_song(url: str, requested_by: str) -> Song:
    def _extract() -> Song:
        ydl_options = {
            "format": "bestaudio/best",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "skip_download": True,
            "default_search": "auto",
        }

        with yt_dlp.YoutubeDL(ydl_options) as ydl:
            info = ydl.extract_info(url, download=False)

        if not info:
            raise RuntimeError("yt-dlp did not return video information.")

        stream_url = info.get("url")
        if not stream_url:
            formats = info.get("formats") or []
            audio_formats = [fmt for fmt in formats if fmt.get("acodec") != "none" and fmt.get("url")]
            if not audio_formats:
                raise RuntimeError("yt-dlp did not return an audio stream URL.")
            stream_url = audio_formats[-1]["url"]

        return Song(
            title=info.get("title") or "Unknown title",
            webpage_url=info.get("webpage_url") or url,
            stream_url=stream_url,
            duration=int(info.get("duration") or 0),
            channel=info.get("channel") or info.get("uploader") or "Unknown channel",
            requested_by=requested_by,
        )

    return await asyncio.to_thread(_extract)


class MusicQueue:
    def __init__(self, bot: commands.Bot, guild_id: int):
        self.bot = bot
        self.guild_id = guild_id
        self.voice_client: Optional[discord.VoiceClient] = None
        self.text_channel: Optional[discord.abc.Messageable] = None
        self.songs: Deque[Song] = deque()
        self.current: Optional[Song] = None
        self.lock = asyncio.Lock()

    async def connect(self, voice_channel: discord.VoiceChannel, text_channel: discord.abc.Messageable) -> None:
        self.text_channel = text_channel

        if self.voice_client and self.voice_client.is_connected():
            if self.voice_client.channel != voice_channel:
                await self.voice_client.move_to(voice_channel)
            return

        self.voice_client = await voice_channel.connect(self_deaf=True)

    async def enqueue(self, song: Song) -> int:
        async with self.lock:
            self.songs.append(song)
            position = len(self.songs)

        if not self.is_playing_or_paused():
            await self.play_next()

        return position

    def is_playing_or_paused(self) -> bool:
        return bool(self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()))

    async def play_next(self) -> None:
        async with self.lock:
            if not self.voice_client or not self.voice_client.is_connected():
                self.current = None
                return

            if not self.songs:
                self.current = None
                return

            self.current = self.songs.popleft()
            song = self.current

        before_options = "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5"
        ffmpeg_options = "-vn"

        audio = discord.FFmpegPCMAudio(
            song.stream_url,
            executable=FFMPEG_EXECUTABLE,
            before_options=before_options,
            options=ffmpeg_options,
        )
        source = discord.PCMVolumeTransformer(audio, volume=0.7)

        def after_playback(error: Optional[Exception]) -> None:
            if error:
                print(f"Playback error: {error}")
            self.bot.loop.call_soon_threadsafe(lambda: asyncio.create_task(self.play_next()))

        self.voice_client.play(source, after=after_playback)

        if self.text_channel:
            await self.text_channel.send(embed=song_embed("▶️ กำลังเล่นแล้ว", song))

    def pause(self) -> bool:
        if self.voice_client and self.voice_client.is_playing():
            self.voice_client.pause()
            return True
        return False

    def resume(self) -> bool:
        if self.voice_client and self.voice_client.is_paused():
            self.voice_client.resume()
            return True
        return False

    def skip(self) -> bool:
        if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            self.voice_client.stop()
            return True
        return False

    def stop(self) -> None:
        self.songs.clear()
        self.current = None
        if self.voice_client and (self.voice_client.is_playing() or self.voice_client.is_paused()):
            self.voice_client.stop()

    async def leave(self) -> None:
        self.stop()
        if self.voice_client and self.voice_client.is_connected():
            await self.voice_client.disconnect(force=True)
        self.voice_client = None


intents = discord.Intents.default()
intents.guilds = True
intents.voice_states = True


class MusicBot(commands.Bot):
    async def setup_hook(self) -> None:
        if TEST_GUILD_ID:
            guild = discord.Object(id=int(TEST_GUILD_ID))
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            print(f"Synced {len(synced)} commands to test guild {TEST_GUILD_ID}.")
        else:
            synced = await self.tree.sync()
            print(f"Synced {len(synced)} global commands.")


bot = MusicBot(command_prefix="!", intents=intents)
queues: dict[int, MusicQueue] = {}


def get_queue(guild_id: int) -> MusicQueue:
    queue = queues.get(guild_id)
    if queue is None:
        queue = MusicQueue(bot, guild_id)
        queues[guild_id] = queue
    return queue


def get_member_voice_channel(interaction: discord.Interaction) -> Optional[discord.VoiceChannel]:
    if not interaction.guild or not isinstance(interaction.user, discord.Member):
        return None

    voice_state = interaction.user.voice
    if not voice_state or not voice_state.channel:
        return None

    return voice_state.channel


@bot.event
async def on_ready() -> None:
    if not bot.user:
        return

    print(f"Logged in as {bot.user} ({bot.user.id})")


@bot.tree.command(name="play", description="เล่นเพลงจาก YouTube URL ในห้องเสียงที่คุณอยู่")
@app_commands.describe(url="ลิงก์ YouTube หรือ youtu.be")
async def play(interaction: discord.Interaction, url: str) -> None:
    if not interaction.guild:
        await interaction.response.send_message(
            embed=status_embed("⚠️ ใช้ในเซิร์ฟเวอร์เท่านั้น", "คำสั่งนี้ใช้ได้เฉพาะใน Discord server", COLOR_WARNING),
            ephemeral=True,
        )
        return

    if not is_youtube_url(url):
        await interaction.response.send_message(
            embed=status_embed("❌ URL ไม่ถูกต้อง", "ส่งได้เฉพาะลิงก์จาก YouTube หรือ youtu.be เท่านั้น", COLOR_ERROR),
            ephemeral=True,
        )
        return

    voice_channel = get_member_voice_channel(interaction)
    if not voice_channel:
        await interaction.response.send_message(
            embed=status_embed("🎧 ยังไม่ได้เข้าห้องเสียง", "เข้าห้องเสียงก่อน แล้วค่อยใช้คำสั่ง `/play`", COLOR_WARNING),
            ephemeral=True,
        )
        return

    queue = get_queue(interaction.guild.id)
    if len(queue.songs) >= MAX_QUEUE_SIZE:
        await interaction.response.send_message(
            embed=status_embed("📚 คิวเต็มแล้ว", f"จำกัดคิวไว้ที่ **{MAX_QUEUE_SIZE}** เพลง", COLOR_WARNING),
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        song = await extract_song(url, requested_by=str(interaction.user))
    except Exception as error:
        print(f"yt-dlp error: {error}")
        await interaction.followup.send(
            embed=status_embed(
                "❌ ดึงข้อมูลไม่สำเร็จ",
                "ลองลิงก์อื่น หรืออัปเดต `yt-dlp` แล้วลองใหม่อีกครั้ง",
                COLOR_ERROR,
            )
        )
        return

    if song.duration > MAX_VIDEO_SECONDS:
        await interaction.followup.send(
            embed=status_embed(
                "⏳ เพลงยาวเกินกำหนด",
                f"เพลงนี้ยาว **{format_duration(song.duration)}** แต่จำกัดไว้ที่ **{format_duration(MAX_VIDEO_SECONDS)}**",
                COLOR_WARNING,
            )
        )
        return

    await queue.connect(voice_channel, interaction.channel)
    position = await queue.enqueue(song)

    if queue.current == song:
        await interaction.followup.send(embed=song_embed("🎶 เพิ่มเพลงและเริ่มเล่น", song, COLOR_SUCCESS))
    else:
        embed = song_embed("➕ เพิ่มเพลงเข้าคิวแล้ว", song, COLOR_INFO)
        embed.add_field(name="📌 ลำดับคิว", value=f"#{position}", inline=True)
        await interaction.followup.send(embed=embed)


@bot.tree.command(name="pause", description="พักเพลงที่กำลังเล่น")
async def pause(interaction: discord.Interaction) -> None:
    queue = queues.get(interaction.guild_id or 0)
    if queue and queue.pause():
        embed = status_embed("⏸️ พักเพลงแล้ว", "ใช้ `/resume` เพื่อเล่นเพลงต่อ", COLOR_SUCCESS)
    else:
        embed = status_embed("⚠️ ไม่มีเพลงที่พักได้", "ตอนนี้ไม่มีเพลงที่กำลังเล่นอยู่", COLOR_WARNING)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="resume", description="เล่นเพลงต่อ")
async def resume(interaction: discord.Interaction) -> None:
    queue = queues.get(interaction.guild_id or 0)
    if queue and queue.resume():
        embed = status_embed("▶️ เล่นต่อแล้ว", "กลับมาเปิดเพลงต่อให้แล้ว", COLOR_SUCCESS)
    else:
        embed = status_embed("⚠️ ไม่มีเพลงที่เล่นต่อได้", "ตอนนี้ไม่มีเพลงที่ถูกพักไว้", COLOR_WARNING)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="skip", description="ข้ามเพลงปัจจุบัน")
async def skip(interaction: discord.Interaction) -> None:
    queue = queues.get(interaction.guild_id or 0)
    if queue and queue.skip():
        embed = status_embed("⏭️ ข้ามเพลงแล้ว", "กำลังไปเพลงถัดไปในคิว", COLOR_SUCCESS)
    else:
        embed = status_embed("⚠️ ไม่มีเพลงให้ข้าม", "ตอนนี้ไม่มีเพลงที่กำลังเล่นอยู่", COLOR_WARNING)
    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="stop", description="หยุดเพลงและล้างคิว")
async def stop(interaction: discord.Interaction) -> None:
    queue = queues.get(interaction.guild_id or 0)
    if not queue:
        await interaction.response.send_message(
            embed=status_embed("📭 ไม่มีคิวเพลง", "ตอนนี้ยังไม่มีเพลงในคิว", COLOR_WARNING)
        )
        return

    queue.stop()
    await interaction.response.send_message(
        embed=status_embed("⏹️ หยุดเพลงแล้ว", "ล้างคิวเพลงทั้งหมดเรียบร้อย", COLOR_SUCCESS)
    )


@bot.tree.command(name="queue", description="ดูคิวเพลง")
async def show_queue(interaction: discord.Interaction) -> None:
    queue = queues.get(interaction.guild_id or 0)
    if not queue or (not queue.current and not queue.songs):
        await interaction.response.send_message(
            embed=status_embed("📭 คิวว่างอยู่", "ใช้ `/play <youtube_url>` เพื่อเพิ่มเพลงแรก", COLOR_INFO)
        )
        return

    embed = discord.Embed(title="📚 คิวเพลง", color=COLOR_MUSIC)
    if queue.current:
        embed.add_field(
            name="▶️ กำลังเล่น",
            value=(
                f"**[{truncate_text(queue.current.title, 200)}]({queue.current.webpage_url})**\n"
                f"⏱️ {format_duration(queue.current.duration)} • 🙋 {truncate_text(queue.current.requested_by, 120)}"
            ),
            inline=False,
        )

    if queue.songs:
        upcoming = [
            f"`{index}.` **{truncate_text(song.title, 80)}** • {format_duration(song.duration)}"
            for index, song in enumerate(list(queue.songs)[:10], start=1)
        ]
        embed.add_field(name="⏭️ ถัดไป", value=truncate_text("\n".join(upcoming), 1024), inline=False)

    if len(queue.songs) > 10:
        embed.set_footer(text=f"และอีก {len(queue.songs) - 10} เพลงในคิว")
    else:
        embed.set_footer(text=f"เพลงในคิวทั้งหมด {len(queue.songs)} เพลง")

    await interaction.response.send_message(embed=embed)


@bot.tree.command(name="nowplaying", description="ดูเพลงที่กำลังเล่น")
async def now_playing(interaction: discord.Interaction) -> None:
    queue = queues.get(interaction.guild_id or 0)
    if not queue or not queue.current:
        await interaction.response.send_message(
            embed=status_embed("📭 ยังไม่มีเพลง", "ตอนนี้ไม่ได้เล่นเพลงอยู่", COLOR_INFO)
        )
        return

    await interaction.response.send_message(embed=song_embed("🎧 กำลังเล่นอยู่ตอนนี้", queue.current))


@bot.tree.command(name="leave", description="ให้บอทออกจากห้องเสียง")
async def leave(interaction: discord.Interaction) -> None:
    queue = queues.get(interaction.guild_id or 0)
    if not queue:
        await interaction.response.send_message(
            embed=status_embed("⚠️ ยังไม่ได้อยู่ในห้องเสียง", "บอทยังไม่ได้เชื่อมต่อห้องเสียง", COLOR_WARNING)
        )
        return

    await queue.leave()
    queues.pop(interaction.guild_id or 0, None)
    await interaction.response.send_message(
        embed=status_embed("👋 ออกจากห้องเสียงแล้ว", "ไว้เปิดเพลงกันใหม่รอบหน้า", COLOR_SUCCESS)
    )


if not DISCORD_TOKEN:
    raise RuntimeError("Missing DISCORD_TOKEN. Add it in Replit Secrets or .env")

load_discord_opus()

server_on()  # Start the Flask server in a separate thread
bot.run(DISCORD_TOKEN)
