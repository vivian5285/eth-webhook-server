import os
from dotenv import load_dotenv
from binance.client import Client

# ==================== 三重防御机制 ====================
# 1. 绝对路径强制定位 .env，确保无论通过什么方式启动（gunicorn/shell/systemd）都能找到
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ENV_PATH = os.path.join(BASE_DIR, '.env')

if os.path.exists(ENV_PATH):
    load_dotenv(ENV_PATH)
else:
    # 尝试向上级目录寻找，以防启动路径差异
    load_dotenv(os.path.join(os.path.dirname(BASE_DIR), '.env'))

class BinanceClient:
    def __init__(self):
        # 2. 优先读取环境变量
        self.api_key = os.getenv("BINANCE_API_KEY")
        self.api_secret = os.getenv("BINANCE_API_SECRET")

        # 3. 详细环境诊断，确保失败时你能一眼看出原因
        if not self.api_key or not self.api_secret:
            raise ValueError(
                f"\n⚠️ 严重错误：Binance 凭证缺失！\n"
                f"尝试加载的 .env 路径: {ENV_PATH}\n"
                f"检查 .env 文件是否在正确位置，且 BINANCE_API_KEY 是否填写。"
            )

        # 初始化币安实例
        try:
            self.client = Client(self.api_key, self.api_secret)
        except Exception as e:
            raise ConnectionError(f"初始化币安客户端失败: {e}")

    def get_client(self):
        return self.client
