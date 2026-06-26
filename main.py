import os
import sys
import subprocess
import asyncio

# =====================================================================
# ⚙️ KHỐI VÁ LỖI HỆ THỐNG: ĐẢM BẢO CÓ PYNACL CHO VOICE CHANNEL
# =====================================================================
try:
    import nacl
except ImportError:
    print("📦 Không tìm thấy thư viện mã hóa âm thanh (PyNaCl). Đang cài đặt cưỡng chế...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "--force-reinstall", "PyNaCl==1.5.0"])
    print("✅ Cài đặt PyNaCl thành công!")

import discord
from discord.ext import commands
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

# ==========================================
# 1. KHỞI TẠO BIẾN MÔI TRƯỜNG & KIỂM TRA
# ==========================================
DISCORD_TOKEN = os.environ.get('DISCORD_TOKEN', '').strip()
SPOTIFY_CLIENT_ID = os.environ.get('SPOTIFY_CLIENT_ID', '').strip()
SPOTIFY_CLIENT_SECRET = os.environ.get('SPOTIFY_CLIENT_SECRET', '').strip()

# Mặc định là !kitty nếu không cấu hình trên Render
BOT_PREFIX = os.environ.get('BOT_PREFIX', '!kitty').strip()

if not DISCORD_TOKEN:
    raise ValueError("❌ KHÔNG CÓ TOKEN! Hãy nhập DISCORD_TOKEN lên hệ thống Render.")

# Kết nối Spotify API
sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET
))

# ==========================================
# 2. CẤU HÌNH ÂM THANH CHỐNG QUÉT IP
# ==========================================
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'quiet': True,
    'source_address': '0.0.0.0',
    'nocheckcertificate': True,
    'geo_bypass': True,
    'user_agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36'
}

# Cấu hình giả lập app mobile để vượt rào chặn YouTube Sign-in
YOUTUBE_BYPASS_ARGS = {
    'youtube': {
        'player_client': ['ios', 'android'],
        'skip': ['webpage']
    }
}

FFMPEG_OPTIONS = {
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 15 -headers "User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"',
    'options': '-vn'
}

class YTDLSource(discord.PCMVolumeTransformer):
    def __init__(self, source, *, data, volume=0.5):
        super().__init__(source, volume)
        self.data = data
        self.title = data.get('title')
        self.url = data.get('url')

    @classmethod
    async def from_url(cls, url, *, loop=None, stream=True):
        loop = loop or asyncio.get_event_loop()
        opts = YTDL_OPTIONS.copy()
        
        # --- CƠ CHẾ DỰ PHÒNG KÉP (FALLBACK): KHẮC PHỤC HOÀN TOÀN LỖI CHẶN IP 403 ---
if not url.startswith("http"):
            # KẾ HOẠCH A: Ưu tiên tìm kiếm bằng SoundCloud
            sc_search = f"scsearch:{url}"
            try:
                print(f"🔍 [Plan A] Đang tìm kiếm trên SoundCloud: {url}")
                data = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(opts).extract_info(sc_search, download=not stream))
                if 'entries' in data and data['entries']:
                    extracted_data = data['entries'][0]
                    filename = extracted_data['url'] if stream else yt_dlp.YoutubeDL(opts).prepare_filename(extracted_data)
                    return cls(discord.FFmpegPCMAudio(filename, **FFMPEG_OPTIONS), data=extracted_data)
            except Exception as e:
                print(f"⚠️ SoundCloud bị lỗi hoặc chặn IP (403): {e}")
                print("🔄 [Plan B] Tự động kích hoạt luồng cứu hộ sang YouTube Mobile...")
            
            # KẾ HOẠCH B: Nếu SoundCloud lỗi, tự động chuyển sang YouTube mã hóa Mobile
            url = f"ytsearch:{url}"
            opts['extractor_args'] = YOUTUBE_BYPASS_ARGS
        else:
            # Nếu người dùng đưa link trực tiếp
            if "youtube.com" in url or "youtu.be" in url:
                opts['extractor_args'] = YOUTUBE_BYPASS_ARGS

        data = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(opts).extract_info(url, download=not stream))
        
        if 'entries' in data:
            if not data['entries']:
                raise Exception("Không tìm thấy kết quả phù hợp trên cả SoundCloud lẫn YouTube.")
            data = data['entries'][0]
            
        filename = data['url'] if stream else yt_dlp.YoutubeDL(opts).prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **FFMPEG_OPTIONS), data=data)

# ==========================================
# 3. KHỞI TẠO BOT & HÀNG ĐỢI
# ==========================================
intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix=f"{BOT_PREFIX} ", intents=intents)
music_queues = {}

def check_queue(ctx):
    if ctx.guild.id in music_queues and music_queues[ctx.guild.id]:
        next_track = music_queues[ctx.guild.id].pop(0)
        async def play_next():
            async with ctx.typing():
                try:
                    player = await YTDLSource.from_url(next_track, loop=bot.loop, stream=True)
                    ctx.voice_client.play(player, after=lambda e: check_queue(ctx))
                    await ctx.send(f"🎵 **Đang phát bài tiếp theo:** `{player.title}`")
                except Exception as e:
                    await ctx.send(f"❌ Lỗi phát bài tiếp theo: {e}")
                    check_queue(ctx)
        asyncio.run_coroutine_threadsafe(play_next(), bot.loop)
    else:
        async def stay_in_vc():
await ctx.send(f"💿 Đã phát hết danh sách chờ! Hãy gõ thêm bài bằng lệnh `{BOT_PREFIX} play` để nghe tiếp nhé.")
        asyncio.run_coroutine_threadsafe(stay_in_vc(), bot.loop)

# ==========================================
# 4. LỆNH ĐIỀU KHIỂN
# ==========================================
@bot.event
async def on_ready():
    # Nạp thư viện âm thanh nền tảng của Discord
    if not discord.opus.is_loaded():
        try:
            discord.opus.load_opus()
        except Exception:
            pass
    print(f'✅ Bot đã online thành công: {bot.user}')
    print(f'🔑 Lệnh hiện tại của bot là: {BOT_PREFIX} [lệnh]')

@bot.command(name='play', help='Phát nhạc')
async def play(ctx, *, search: str):
    if not ctx.author.voice:
        return await ctx.send("❌ Bạn phải vào Voice Channel trước!")
        
    # --- ĐOẠN KHỬ LỖI KẸT 4006: ÉP LÀM SẠCH KÊNH THOẠI ---
    target_channel = ctx.author.voice.channel
    if ctx.voice_client:
        if ctx.voice_client.channel.id != target_channel.id:
            await ctx.voice_client.move_to(target_channel)
    else:
        try:
            await target_channel.connect(timeout=10.0, reconnect=True)
        except Exception:
            try:
                await ctx.guild.voice_client.disconnect(force=True)
            except Exception:
                pass
            await asyncio.sleep(1)
            await target_channel.connect(timeout=10.0, reconnect=True)
    # ---------------------------------------------------

    # Sửa logic nhận diện link Spotify chuẩn xác hơn
    if "spotify.com" in search or "open.spotify.com" in search:
        try:
            track_info = sp.track(search)
            track_name = track_info['name']
            artist_name = track_info['artists'][0]['name']
            search = f"{track_name} {artist_name}"
            await ctx.send(f"🔍 Đã nhận diện Spotify: **{track_name} - {artist_name}**. Đang phân tích luồng nhạc...")
        except Exception:
            return await ctx.send("❌ Lỗi đọc link Spotify. Vui lòng kiểm tra cấu hình hoặc link nhạc!")

    if ctx.guild.id not in music_queues:
        music_queues[ctx.guild.id] = []

    async with ctx.typing():
        try:
            if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
                music_queues[ctx.guild.id].append(search)
                clean_name = search.replace("scsearch:", "").replace("ytsearch:", "")
                await ctx.send(f"⏳ Đã thêm vào hàng đợi bài: `{clean_name}` (Vị trí #{len(music_queues[ctx.guild.id])})")
            else:
                player = await YTDLSource.from_url(search, loop=bot.loop, stream=True)
                ctx.voice_client.play(player, after=lambda e: check_queue(ctx))
                await ctx.send(f"🎵 **Đang phát:** `{player.title}`")
        except Exception as e:
print(f"Lỗi hệ thống nghiêm trọng: {e}")
            await ctx.send("❌ Cả SoundCloud lẫn YouTube đều từ chối kết nối do nghẽn IP dải phòng máy Render. Vui lòng thử lại sau!")

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

@bot.command(name='queue', aliases=['q'], help='Xem danh sách')
async def queue_cmd(ctx):
    if ctx.guild.id in music_queues and music_queues[ctx.guild.id]:
        queue_list = music_queues[ctx.guild.id]
        msg = "**🎶 Danh sách chờ:**\n"
        for i, track in enumerate(queue_list, 1):
            clean_name = track.replace("scsearch:", "").replace("ytsearch:", "")
            msg += f"**{i}.** `{clean_name}`\n"
        await ctx.send(msg)
    else:
        await ctx.send(f"📭 Hàng đợi trống. Thêm nhạc bằng lệnh `{BOT_PREFIX} play` nha!")

# ==========================================
# 5. MỞ CỔNG WEB ẢO (ĐỒNG BỘ VỚI RENDER)
# ==========================================
class DummyHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot dang hoat dong!")
        
    def do_HEAD(self):
        self.send_response(200)
        self.end_headers()

    def log_message(self, format, *args):
        pass

def keep_alive():
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), DummyHandler)
    server.serve_forever()

threading.Thread(target=keep_alive, daemon=True).start()
bot.run(DISCORD_TOKEN)
