import os

TOKEN = os.environ.get("TOKEN", "")
CHANNEL_IDS = [int(x) for x in os.environ.get("CHANNEL_IDS", "").split(",") if x.strip()]
OWNER_ID = int(os.environ.get("OWNER_ID", "0"))
OWNER_IDS = [int(x) for x in os.environ.get("OWNER_IDS", str(OWNER_ID)).split(",") if x.strip()]

# AI deliberately left empty / unused
GROQ_API_KEY = ""
DEEPSEEK_KEYS = []
NVIDIA_API_KEY = ""
NVIDIA_MODEL = ""
