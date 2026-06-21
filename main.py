import discord
from discord.ext import commands
import asyncio
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==========================================
# 1. KHỞI TẠO BIẾN MÔI TRƯỜNG & KIỂM TRA
# ==========================================
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN', '').strip()
SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID', '').strip()
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET', '').strip()

if not DISCORD_TOKEN:
    raise ValueError("❌ KHÔNG CÓ TOKEN! Hãy nhập DISCORD_TOKEN lên hệ thống Render.")

# Kết nối Spotify API
sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET
))

# ==========================================
# 2. CẤU HÌNH ÂM THANH (SOUNDCLOUD)
# ==========================================
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'default_search': 'scsearch', # Ép tìm qua SoundCloud
    'source_address': '0.0.0.0'
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn'
}

ytdl = yt_dlp.YoutubeDL(YTDL_OPTIONS)

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        if 'entries' in data:
            data = data['entries'][0]
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **FFMPEG_OPTIONS), data=data)

# ==========================================
# 3. KHỞI TẠO BOT & HÀNG ĐỢI
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!kitty ', intents=intents)
music_queues = {}

def check_queue(ctx):
    if ctx.guild.id in music_queues and music_queues[ctx.guild.id]:
        next_track = music_queues[ctx.guild.id].pop(0)
        async def play_next():
            async with ctx.typing():
                try:
                    player = await YTDLSource.from_url(next_track, loop=bot.loop, stream=True)
                    ctx.voice_client.play(player, after=lambda e: check_queue(ctx))
                    await ctx.send(f"🎵 **Đang phát:** `{player.title}`")
                except Exception as e:
                    await ctx.send(f"❌ Lỗi phát bài tiếp theo: {e}")
                    check_queue(ctx)
        asyncio.run_coroutine_threadsafe(play_next(), bot.loop)
    else:
        async def auto_leave():
            await asyncio.sleep(180)
            if ctx.voice_client and not ctx.voice_client.is_playing():
                await ctx.voice_client.disconnect()
                await ctx.send("💤 Hết nhạc rồi, mình đi ngủ đây!")
        asyncio.run_coroutine_threadsafe(auto_leave(), bot.loop)

# ==========================================
# 4. LỆNH ĐIỀU KHIỂN
# ==========================================
@bot.event
async def on_ready():
    print(f'✅ Bot đã online thành công: {bot.user}')

@bot.command(name='play', help='Phát nhạc từ SoundCloud hoặc Spotify')
async def play(ctx, *, search: str):
    if not ctx.author.voice:
        return await ctx.send("❌ Bạn phải vào Voice Channel trước!")
        
    if not ctx.voice_client:
        await ctx.author.voice.channel.connect()

    # Nhận diện link Spotify chuẩn
    if "open.spotify.com" in search:
        try:
            track_info = sp.track(search)
            track_name = track_info['name']
            artist_name = track_info['artists'][0]['name']
            search = f"scsearch:{track_name} {artist_name}"
            await ctx.send(f"🔍 Đã nhận diện Spotify: **{track_name} - {artist_name}**. Đang lấy nhạc...")
        except Exception as e:
            return await ctx.send("❌ Lỗi đọc link Spotify. Bạn kiểm tra lại link nhé!")
    
    # Ép tìm qua SoundCloud nếu là chữ bình thường
    elif not search.startswith("http"):
        search = f"scsearch:{search}"

    if ctx.guild.id not in music_queues:
        music_queues[ctx.guild.id] = []

    async with ctx.typing():
        try:
            if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
                music_queues[ctx.guild.id].append(search)
                await ctx.send(f"⏳ Đã thêm vào hàng đợi vị trí #{len(music_queues[ctx.guild.id])}")
            else:
                player = await YTDLSource.from_url(search, loop=bot.loop, stream=True)
                ctx.voice_client.play(player, after=lambda e: check_queue(ctx))
                await ctx.send(f"🎵 **Đang phát:** `{player.title}`")
        except Exception as e:
            await ctx.send("❌ Không tìm thấy bản nhạc này trên SoundCloud!")

@bot.command(name='skip', help='Bỏ qua bài hiện tại')
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ Đã chuyển bài!")

@bot.command(name='leave', help='Đuổi bot')
async def leave(ctx):
    if ctx.voice_client:
        music_queues[ctx.guild.id] = []
        await ctx.voice_client.disconnect()
        await ctx.send("👋 Bye bye!")

# ==========================================
# 5. MỞ CỔNG WEB ẢO (CHỐNG RENDER TIMEOUT)
# ==========================================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot Vit De Thuong dang hoat dong!")
        
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass

def keep_alive():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    print(f"🌐 Đã mở cổng Web ảo tại Port {port} để Render kiểm tra...")
    server.serve_forever()

threading.Thread(target=keep_alive, daemon=True).start()

# Chạy bot (Chỉ 1 dòng duy nhất)
bot.run(DISCORD_TOKEN)
