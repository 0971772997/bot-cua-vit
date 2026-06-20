import requests
from bs4 import BeautifulSoup
import discord
from discord.ext import commands
import asyncio
import yt_dlp
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os
from dotenv import load_dotenv
import re
from keep_alive import keep_alive

# ==========================================
# 1. KHỞI TẠO BIẾN MÔI TRƯỜNG & API
# ==========================================
load_dotenv()

DISCORD_TOKEN = os.getenv('DISCORD_TOKEN')
SPOTIFY_CLIENT_ID = os.getenv('SPOTIFY_CLIENT_ID')
SPOTIFY_CLIENT_SECRET = os.getenv('SPOTIFY_CLIENT_SECRET')

# Khởi tạo Spotify API
sp = spotipy.Spotify(auth_manager=SpotifyClientCredentials(
    client_id=SPOTIFY_CLIENT_ID,
    client_secret=SPOTIFY_CLIENT_SECRET
))

# ==========================================
# 2. CẤU HÌNH LUỒNG ÂM THANH (TỐI ƯU CHO SOUNDCLOUD)
# ==========================================
YTDL_OPTIONS = {
    'format': 'bestaudio/best',
    'noplaylist': True,
    'nocheckcertificate': True,
    'ignoreerrors': False,
    'logtostderr': False,
    'quiet': True,
    'no_warnings': True,
    # ÉP BUỘC TÌM KIẾM MẶC ĐỊNH QUA SOUNDCLOUD THAY VÌ YOUTUBE
    'default_search': 'scsearch',
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
# 3. KHỞI TẠO CẤU HÌNH BOT DISCORD
# ==========================================
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='!', intents=intents)

music_queues = {}

def check_queue(ctx):
    """Kiểm tra hàng đợi tự động"""
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
        async def auto_leave():
            await asyncio.sleep(180)
            if ctx.voice_client and not ctx.voice_client.is_playing():
                await ctx.voice_client.disconnect()
                await ctx.send("💤 Không có bài hát nào trong hàng đợi suốt 3 phút, mình đi ngủ đây! 👋")
        asyncio.run_coroutine_threadsafe(auto_leave(), bot.loop)

# ==========================================
# 4. ĐỊNH NGHĨA CÁC CÂU LỆNH ĐIỀU KHIỂN
# ==========================================
@bot.event
async def on_ready():
    print(f'✅ Khởi tạo thành công! Bot đã sẵn sàng hoạt động với tên: {bot.user}')

@bot.command(name='play', help='Phát nhạc từ SoundCloud, Spotify hoặc tìm bằng từ khóa qua SoundCloud')
async def play(ctx, *, search: str):
    if not ctx.author.voice:
        return await ctx.send("❌ Bạn phải tham gia vào một kênh thoại trước!")
        
    if not ctx.voice_client:
        await ctx.author.voice.channel.connect()
    elif ctx.voice_client.channel != ctx.author.voice.channel:
        return await ctx.send("❌ Bot đang bận phát nhạc ở một phòng thoại khác rồi!")

    # [XỬ LÝ SPOTIFY]: Tự động đổi hướng sang tìm kiếm trên SoundCloud thay vì YouTube
    if "spotify.com" in search and "track" in search:
        await ctx.send("🔍 Đang trích xuất thông tin bài hát từ Spotify...")
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
            res = await bot.loop.run_in_executor(None, lambda: requests.get(search, headers=headers))
            soup = BeautifulSoup(res.text, 'html.parser')
            
            title = soup.find('title').text
            clean_title = title.replace(" - song and lyrics by", "").replace(" - song by", "").replace(" | Spotify", "")
            
            # Ép tìm kiếm bằng SoundCloud
            search = f"scsearch:{clean_title}"
            await ctx.send(f"✅ Đã tìm ra bài hát trên Spotify. Đang tìm luồng âm thanh trên SoundCloud...")
        except Exception as e:
            return await ctx.send(f"❌ Trích xuất thông tin Spotify thất bại: {e}")

    # Nếu người dùng nhập chữ thường không phải link, chuyển thành lệnh tìm kiếm SoundCloud
    elif not search.startswith("http://") and not search.startswith("https://"):
        search = f"scsearch:{search}"

    if ctx.guild.id not in music_queues:
        music_queues[ctx.guild.id] = []

    async with ctx.typing():
        try:
            data = await bot.loop.run_in_executor(None, lambda: ytdl.extract_info(search, download=False))
            if 'entries' in data:
                if not data['entries']:
                    return await ctx.send("❌ Không tìm thấy bài hát nào khớp với từ khóa trên SoundCloud.")
                data = data['entries'][0]
                
            track_data = {
                'url': data.get('webpage_url') or data.get('url'),
                'title': data.get('title', 'Âm thanh từ SoundCloud')
            }
            
            if ctx.voice_client.is_playing() or ctx.voice_client.is_paused():
                music_queues[ctx.guild.id].append(track_data)
                await ctx.send(f"⏳ Đã thêm vào hàng đợi vị trí #{len(music_queues[ctx.guild.id])}: `{track_data['title']}`")
            else:
                player = await YTDLSource.from_url(track_data['url'], loop=bot.loop, stream=True)
                ctx.voice_client.play(player, after=lambda e: check_queue(ctx))
                await ctx.send(f"🎵 **Đang phát từ SoundCloud:** `{player.title}`")
                
        except Exception as e:
            await ctx.send(f"❌ Không thể xử lý yêu cầu SoundCloud. Lỗi: {e}")

@bot.command(name='skip')
async def skip(ctx):
    if ctx.voice_client and ctx.voice_client.is_playing():
        ctx.voice_client.stop()
        await ctx.send("⏭️ Đã bỏ qua bài hát hiện tại!")
    else:
        await ctx.send("❌ Hiện tại bot đang không phát bài nhạc nào cả.")

@bot.command(name='queue')
async def queue(ctx):
    if ctx.guild.id not in music_queues or not music_queues[ctx.guild.id]:
        return await ctx.send("📭 Hàng đợi hiện đang trống rỗng.")
        
    message = "📋 **Danh sách bài hát tiếp theo:**\n"
    for i, track in enumerate(music_queues[ctx.guild.id][:10], 1):
        message += f"{i}. `{track['title']}`\n"
    if len(music_queues[ctx.guild.id]) > 10:
        message += f"...và {len(music_queues[ctx.guild.id]) - 10} bài hát khác."
    await ctx.send(message)

@bot.command(name='leave')
async def leave(ctx):
    if ctx.voice_client:
        music_queues[ctx.guild.id] = []
        await ctx.voice_client.disconnect()
        await ctx.send("👋 Tạm biệt mọi người, mình đi đây!")
    else:
        await ctx.send("❌ Mình hiện tại không ở trong kênh thoại nào cả.")

keep_alive()
bot.run(DISCORD_TOKEN)
