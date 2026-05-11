# 設定讀取
import os
from dotenv import load_dotenv

load_dotenv()

CONTEXT_WINDOW_DIALOGS = int(os.getenv("CONTEXT_WINDOW_DIALOGS", 5))

# 每 N 則訊息（user+assistant 各算 1 則）觸發一次
# 每次問答 = 2 則訊息；interval=2 代表每 1 次問答觸發
SESSION_MEMORY_INTERVAL = int(os.getenv("SESSION_MEMORY_INTERVAL", 2))   # 短期：每 1 次問答
PERSONA_UPDATE_INTERVAL = int(os.getenv("PERSONA_UPDATE_INTERVAL", 2))   # 長期 facts：每 1 次問答
KB_COMPILE_INTERVAL     = int(os.getenv("KB_COMPILE_INTERVAL", 10))      # 長期 KB：每 5 次問答
