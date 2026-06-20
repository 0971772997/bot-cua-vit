import requests
from bs4 import BeautifulSoup
import discord
from discord.ext import commands
import asyncio
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os
import sys
from dotenv import load_dotenv
import re
from keep_alive import keep_alive

# ==========================================
# 1. KHỞI TẠO BIẾN MÔI TRƯỜNG & API
# ==========================================
load_dotenv() # Tải các cấu hình bảo mật từ file .env

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')

# Khởi tạo thư viện kết nối Spotify API
sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET
))

# ==========================================
# 2. CẤU HÌNH LIÊN KẾT LUỒNG ÂM THANH (FFMPEG & YT-DLP)
# ==========================================
YTDL_OPTIONS = {
    'format': 'm4a/bestaudio/best',
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    'default_search': 'auto',
    'source_address': '0.0.0.0',
    'cookiefile': 'cookies.txt'
}

FFMPEG_OPTIONS = {
    # Tự động kết nối lại nếu luồng stream từ YouTube/SoundCloud bị ngắt quãng giữa chừng
    'before_options': '-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5',
    'options': '-vn' # Tối ưu hóa: Bỏ qua luồng video, chỉ lấy luồng audio để tiết kiệm băng thông
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
        # Chạy yt-dlp trong một luồng bất đồng bộ để tránh làm đơ bot
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        if 'entries' in data:
            data = data['entries'][0]
        filename = data['url'] if stream else ytdl.prepare_filename(data)
        return cls(discord.FFmpegPCMAudio(filename, **FFMPEG_OPTIONS), data=data)

# ==========================================
# 3. KHỞI TẠO CẤU HÌNH BOT DISCORD
# ==========================================
intents = discord.Intents.default()
intents.message_content = True 

# Tiền tố lệnh là '!kitty '
bot = commands.Bot(command_prefix='!kitty ', intents=intents)

music_queues = {}

def check_queue(ctx):
    """Hàm kiểm tra hàng đợi tự động sau khi một bài hát kết thúc"""
    if ctx.guild.id in music_queues and music_queues[ctx.guild.id]:
        next_track = music_queues[ctx.guild.id].pop(0)
        
        async def play_next():
            async with ctx.typing():
                try:
                    player = await YTDLSource.from_url(next_track['url'], loop=bot.loop, stream=True)
                    ctx.voice_client.play(player, after=lambda e: check_queue(ctx))
                    await ctx.send(f"🎵 **Đang phát bài tiếp theo:** `{player.title}`")
                except Exception as e:
                    await ctx.send(f"❌ Gặp lỗi khi cố phát bài tiếp theo: {e}")
                    check_queue(ctx)
                    
        asyncio.run_coroutine_threadsafe(play_next(), bot.loop)
    else:
        # Hàng đợi trống: Gửi thông báo và CHỈ ĐỨNG IM CHỜ LỆNH (không tự động out)
        async def notify_empty_queue():
            await ctx.send("📭 Đã hát hết danh sách! Mình sẽ ở lì đây chờ các bạn gọi bài tiếp nhé.")
        asyncio.run_coroutine_threadsafe(notify_empty_queue(), bot.loop)

def parse_spotify_track(url):
    match = re.search(r"track/([a-zA-Z0-9]+)", url)
    if match:
        return match.group(1)
    return None

# ==========================================
# 4. ĐỊNH NGHĨA CÁC CÂU LỆNH ĐIỀU KHIỂN
# ==========================================
@bot.event
async def on_ready():
    print(f'✅ Khởi tạo thành công! Bot đã sẵn sàng hoạt động với tên: {bot.user}')

@bot.command(name='join', help='Gọi bot vào kênh thoại của bạn')
async def join(ctx):
    if not ctx.author.voice:
        return await ctx.send("❌ Bạn phải tham gia vào một kênh thoại (Voice Channel) trước!")
    
    if not ctx.voice_client:
        await ctx.author.voice.channel.connect()
        await ctx.send(f"✅ Đã tham gia kênh thoại: `{ctx.author.voice.channel.name}`")
    elif ctx.voice_client.channel != ctx.author.voice.channel:
        await ctx.voice_client.move_to(ctx.author.voice.channel)
        await ctx.send(f"✅ Đã di chuyển sang kênh thoại: `{ctx.author.voice.channel.name}`")
    else:
        await ctx.send("✅ Mình đã ở sẵn trong kênh thoại của bạn rồi mà!")

@bot.command(name='play', help='Phát nhạc từ link YouTube, SoundCloud, Spotify hoặc tìm bằng từ khóa')
async def play(ctx, *, search: str):
    if not ctx.author.voice:
        return await ctx.send("❌ Bạn phải tham gia vào một kênh thoại (Voice Channel) trước!")
        
    if not ctx.voice_client:
        await ctx.author.voice.channel.connect()
    elif ctx.voice_client.channel != ctx.author.voice.channel:
        return await ctx.send("❌ Bot đang bận phát nhạc ở một phòng thoại khác rồi!")

    # [XỬ LÝ ĐẶC BIỆT] Nhận diện link Spotify
    if "spotify.com" in search and "track" in search:
        await ctx.send("🔍 Đang luồn lách để đọc tên bài hát từ Spotify...")
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            res = await bot.loop.run_in_executor(None, lambda: requests.get(search, headers=headers))
            soup = BeautifulSoup(res.text, 'html.parser')
            title = soup.find('title').text
            clean_title = title.replace(" - song and lyrics by", "").replace(" - song by", "").replace(" | Spotify", "")
            search = f"{clean_title} official audio"
            await ctx.send(f"✅ Đã tìm ra: **{clean_title}**. Đang chuyển hướng sang YouTube...")
        except Exception as e:
            return await ctx.send(f"❌ Lách luật Spotify thất bại: {e}")

    if ctx.guild.id not in music_queues:
        music_queues[ctx.guild.id] = []

    async with ctx.typing():
        try:
            data = await bot.loop.run_in_executor(None, lambda: ytdl.extract_info(search, download=False))
            if 'entries' in data:
                data = data['entries'][0]
                
            track_data = {
                'url': data['webpage_url'],
                'title': data['title']
            }
            
            if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
                music_queues[ctx.guild.id].append(track_data)
                await ctx.send(f"⏳ Đã thêm vào hàng đợi vị trí #{len(music_queues[ctx.guild.id])}: `{track_data['title']}`")
            else:
                player = await YTDLSource.from_url(track_data['url'], loop=bot.loop, stream=True)
                ctx.voice_client.play(player, after=lambda e: check_queue(ctx))
                await ctx.send(f"🎵 **Đang phát:** `{player.title}`")
                
        except Exception as e:
            await ctx.send(f"❌ Không thể xử lý yêu cầu âm nhạc này. Lỗi: {e}")

@bot.command(name='skip', help='Bỏ qua bài hát hiện tại')
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ Đã bỏ qua bài hát hiện tại!")
    else:
        await ctx.send("❌ Hiện tại bot đang không phát bài nhạc nào cả.")

@bot.command(name='stop', help='Dừng nhạc và xóa sạch hàng đợi (Nhưng bot vẫn ở lại phòng)')
async def stop(ctx):
    if ctx.voice_client:
        music_queues[ctx.guild.id] = []
        if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
            ctx.voice_client.stop()
        await ctx.send("⏹️ Đã dừng phát nhạc và xóa sạch hàng đợi.")
    else:
        await ctx.send("❌ Hiện tại mình đang không ở trong kênh thoại nào cả.")

@bot.command(name='queue', help='Hiển thị danh sách hàng đợi nhạc')
async def queue(ctx):
    if ctx.guild.id not in music_queues or not music_queues[ctx.guild.id]:
        return await ctx.send("📭 Hàng đợi hiện đang trống rỗng.")
        
    message = "📋 **Danh sách bài hát tiếp theo:**\n"
    for i, track in enumerate(music_queues[ctx.guild.id][:10], 1):
        message += f"{i}. `{track['title']}`\n"
    if len(music_queues[ctx.guild.id]) > 10:
        message += f"...và {len(music_queues[ctx.guild.id]) - 10} bài hát khác."
    await ctx.send(message)

@bot.command(name='leave', help='Đuổi bot khỏi kênh thoại')
async def leave(ctx):
    if ctx.voice_client:
        music_queues[ctx.guild.id] = [] 
        await ctx.voice_client.disconnect()
        await ctx.send("👋 Tạm biệt mọi người, mình đi đây!")
    else:
        await ctx.send("❌ Mình hiện tại không ở trong kênh thoại nào cả.")

@bot.command(name='restart', help='Khởi động lại bot (Chỉ dành cho Admin)')
async def restart(ctx):
    if not ctx.author.guild_permissions.administrator:
        return await ctx.send("❌ Bạn cần có quyền **Quản trị viên** để khởi động lại bot!")
    
    await ctx.send("🔄 Đang tắt nguồn... Render sẽ tự động khởi động lại mình trong vòng 15-30 giây nữa nhé!")
    await bot.close()
    sys.exit(0)

# Kích hoạt chạy bot bằng token bảo mật
keep_alive()
bot.run(DISCORD_TOKEN)
