# 使用 phiên bản Python Linux rút gọn chuẩn
FROM python:3.11-slim

# Cài đặt công cụ FFmpeg hệ thống và các công cụ biên dịch cần thiết
RUN apt-get update && apt-get install -y ffmpeg gcc g++ --no-install-recommends && rm -rf /var/lib/apt/lists/*

# Đặt thư mục làm việc trong máy chủ
WORKDIR /app

# Sao chép toàn bộ mã nguồn vào máy chủ
COPY . .

# Cài đặt toàn bộ các thư viện Python từ file requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Mở cổng 8080 cho Flask (Render cần cổng này để duy trì dịch vụ Web)
EXPOSE 8080

# Lệnh khởi chạy Bot
CMD ["python", "main.py"]
