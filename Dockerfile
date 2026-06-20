# Sử dụng phiên bản Python Linux rút gọn
FROM python:3.11-slim

# Cài đặt công cụ FFmpeg hệ thống và các công cụ biên dịch cần thiết
RUN apt-get update && apt-get install -y ffmpeg gcc g++ --no-install-recommends && rm -rf /var/lib/apt/lists/*

# Đặt thư mục làm việc trong máy chủ
WORKDIR /app

# Copy toàn bộ code từ máy bạn vào máy chủ
COPY . .

# Cài đặt các thư viện Python
RUN pip install --no-cache-dir -U yt-dlp
RUN pip install --no-cache-dir -r requirements.txt

# Mở cổng 8080 cho Flask
EXPOSE 8080

# Lệnh khởi chạy Bot
CMD ["python", "main.py"]
