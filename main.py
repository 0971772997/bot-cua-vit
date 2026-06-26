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
    'noplaylist': True,  # Mặc định bật khi tìm kiếm để tối ưu tốc độ
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
        
        # --- XỬ LÝ LINK TRỰC TIẾP ---
        if url.startswith("http"):
            if "playlist" in url or "sets" in url or "on.soundcloud.com" in url or "soundcloud.com" in url:
                opts['noplaylist'] = False
            
            if "youtube.com" in url or "youtu.be" in url:
                opts['extractor_args'] = YOUTUBE_BYPASS_ARGS
                
            try:
                # KẾ HOẠCH A: Thử tải link trực tiếp (Cả SoundCloud/YouTube)
                data = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(opts).extract_info(url, download=False))
                return data
            except Exception as e:
                # KẾ HOẠCH B: Nếu SoundCloud chặn IP (403), biến link thành từ khóa để tìm Album trên YouTube
                if "403" in str(e) or "Forbidden" in str(e):
                    print(f"⚠️ Link trực tiếp bị chặn IP (403). Tiến hành chuyển đổi sang YouTube Search...")
                    # Làm sạch link, lấy phần đuôi tên playlist làm từ khóa tìm kiếm
                    clean_keyword = url.split('/')[-1].replace('-', ' ')
                    yt_search = f"ytsearch:{clean_keyword} album"
                    opts['noplaylist'] = True  # Bật lại để lấy kết quả tìm kiếm tốt nhất
                    opts['extractor_args'] = YOUTUBE_BYPASS_ARGS
                    data = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(opts).extract_info(yt_search, download=not stream))
                    if 'entries' in data and data['entries']:
                        return data['entries'][0]
                raise e
            
        # --- XỬ LÝ TÌM KIẾM TỪ KHÓA THƯỜNG ---
        else:
            sc_search = f"scsearch:{url}"
            try:
                data = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(opts).extract_info(sc_search, download=not stream))
                if 'entries' in data and data['entries']:
                    return data['entries'][0]
            except Exception:
                pass
            
            yt_search = f"ytsearch:{url}"
            opts['extractor_args'] = YOUTUBE_BYPASS_ARGS
            data = await loop.run_in_executor(None, lambda: yt_dlp.YoutubeDL(opts).extract_info(yt_search, download=not stream))
            if 'entries' in data and data['entries']:
                return data['entries'][0]
                
        raise Exception("Không tìm thấy nguồn kết quả phù hợp.")

    @classmethod
    def create_audio_source(cls, entry):
        filename = entry['url']
        return cls(discord.FFmpegPCMAudio(filename, **FFMPEG_OPTIONS), data=entry)

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
                    # Trích xuất dữ liệu trực tiếp từ đường link đã được lưu trong hàng đợi
                    raw_data = await YTDLSource.from_url(next_track, loop=bot.loop, stream=True)
                    entry = raw_data['entries'][0] if 'entries' in raw_data else raw_data
                    player = YTDLSource.create_audio_source(entry)
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

    # Giải mã link Spotify
    if "spotify.com" in search or "open.spotify.com" in search:
        try:
            track_info = sp.track(search)
            search = f"{track_info['name']} {track_info['artists'][0]['name']}"
            await ctx.send(f"🔍 Đã nhận diện Spotify: **{track_info['name']}**. Đang phân tích luồng nhạc...")
        except Exception:
            return await ctx.send("❌ Lỗi đọc link Spotify. Vui lòng kiểm tra cấu hình hoặc link nhạc!")

    if ctx.guild.id not in music_queues:
        music_queues[ctx.guild.id] = []

    async with ctx.typing():
        try:
            raw_data = await YTDLSource.from_url(search, loop=bot.loop, stream=True)
            
            # TRƯỜNG HỢP 1: Phát hiện có danh sách bài hát (Playlist/Album) từ link trực tiếp
            if raw_data and 'entries' in raw_data and not search.startswith("ytsearch") and not search.startswith("scsearch"):
                entries = list(raw_data['entries'])
                total_tracks = len(entries)
                
                await ctx.send(f"📚 Đã nhận diện Playlist! Đang nạp **{total_tracks} bài hát** vào danh sách chờ...")
                
                # Nếu bot đang không phát nhạc, lấy ngay bài đầu tiên ra chạy mở màn
                if not ctx.voice_client.is_playing() and not ctx.voice_client.is_paused():
                    first_entry = entries.pop(0)
                    player = YTDLSource.create_audio_source(first_entry)
                    ctx.voice_client.play(player, after=lambda e: check_queue(ctx))
                    await ctx.send(f"🎵 **Đang phát bài mở đầu:** `{player.title}`")
                
                # Đẩy toàn bộ link bài viết đơn lẻ của các bài còn lại vào hàng đợi
                for entry in entries:
                    if entry and 'webpage_url' in entry:
                        music_queues[ctx.guild.id].append(entry['webpage_url'])
                
                await ctx.send(f"✅ Đã xếp xong {len(entries)} bài tiếp theo vào hàng đợi.")

            # TRƯỜNG HỢP 2: Bài hát đơn lẻ hoặc từ khoá tìm kiếm thường
            else:
                entry = raw_data['entries'][0] if 'entries' in raw_data else raw_data
                
                if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
                    url_to_queue = entry['webpage_url'] if 'webpage_url' in entry else search
                    music_queues[ctx.guild.id].append(url_to_queue)
                    clean_name = entry.get('title', search)
                    await ctx.send(f"⏳ Đã thêm vào hàng đợi bài: `{clean_name}` (Vị trí #{len(music_queues[ctx.guild.id])})")
                else:
                    player = YTDLSource.create_audio_source(entry)
                    ctx.voice_client.play(player, after=lambda e: check_queue(ctx))
                    await ctx.send(f"🎵 **Đang phát:** `{player.title}`")
                    
        except Exception as e:
            print(f"Lỗi hệ thống phát nhạc: {e}")
            await ctx.send("❌ Cả SoundCloud lẫn YouTube đều từ chối kết nối hoặc không đọc được định dạng link này. Vui lòng kiểm tra lại!")

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
        msg = f"**🎶 Danh sách chờ (Tổng cộng {len(queue_list)} bài):**\n"
        # Chỉ hiển thị tối đa 15 bài đầu để tránh tràn ký tự gửi của Discord
        for i, track in enumerate(queue_list[:15], 1):
            clean_name = track.split('/')[-1].replace('-', ' ').capitalize()
            msg += f"**{i}.** `{clean_name}`\n"
        if len(queue_list) > 15:
            msg += f"*...và {len(queue_list) - 15} bài hát khác phía sau.*"
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
