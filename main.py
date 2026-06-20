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
#from keep_alive import keep_alive

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
    'format': 'bestaudio/best',
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
        # Chạy yt-dlp trong một luồng bất đồng bộ (Async Executor) để tránh làm đơ bot khi đang tải thông tin bài hát
        data = await loop.run_in_executor(None, lambda: ytdl.extract_info(url, download=not stream))
        if 'entries' in data:
            data = data['entries'][0]
        filename =
