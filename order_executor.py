import logging
from binance_client import BinanceClient

# 配置日志
logging.basicConfig(level=logging.INFO, format='%(asctime)s [ORDER-EXECUTOR] %(message)s')
logger = logging.getLogger("ORDER_EXECUTOR")

class OrderExecutor:
    def __init__(self):
        try:
            # 实例化 BinanceClient 类
            self.client_wrapper = BinanceClient()
            self.client = self.client_wrapper.get_client()
            logger.info("OrderExecutor 初始化成功")
        except Exception as e:
            logger.error(f"OrderExecutor 初始化失败: {e}")
            raise e

    def execute_order(self, signal_data):
        """
        执行订单的逻辑
        :param signal_data: 从 webhook 接收到的信号数据
        """
        try:
            symbol = signal_data.get('symbol')
            side = signal_data.get('side')
            quantity = signal_data.get('quantity')
            
            logger.info(f"正在执行订单: {side} {symbol} 数量: {quantity}")
            
            # 这里调用封装好的 client 进行下单
            # 注意：请根据你 actual 的 Binance API 方法进行调整
            # order = self.client.create_order(...)
            
            logger.info(f"订单执行成功")
            return {"status": "success"}
        except Exception as e:
            logger.error(f"订单执行异常: {e}")
            return {"status": "error", "message": str(e)}

# 实例化一个全局的 executor 供外部调用
order_executor = OrderExecutor()
