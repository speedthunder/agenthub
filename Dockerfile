# 使用官方 Python 3.10 精簡映像
FROM python:3.10-slim

# 設定工作目錄
WORKDIR /app

# 複製所有檔案到容器
COPY . /app

# 安裝 Python 依賴
RUN pip install --no-cache-dir -r agent_platform/requirements.txt

# 建立持久化資料目錄
RUN mkdir -p /data

# 預設啟動 FastAPI 應用
CMD ["python", "-m", "agent_platform.main"]
