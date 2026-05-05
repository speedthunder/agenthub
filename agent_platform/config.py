# 設定讀取
import os
from dotenv import load_dotenv

load_dotenv()

CONTEXT_WINDOW_DIALOGS = int(os.getenv("CONTEXT_WINDOW_DIALOGS", 5))
PERSONA_UPDATE_INTERVAL = int(os.getenv("PERSONA_UPDATE_INTERVAL", 20))
