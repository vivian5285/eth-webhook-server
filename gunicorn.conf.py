# gunicorn.conf.py

import multiprocessing

# 绑定地址
bind = "0.0.0.0:5000"

# Worker 数量（建议 2-4 个）
workers = 2

# 使用 sync worker（最稳定）
worker_class = "sync"

# 超时设置
timeout = 120

# 预加载应用（重要）
preload_app = True

# 日志
accesslog = "-"
errorlog = "-"
loglevel = "info"


def post_fork(server, worker):
    """每个 worker 进程 fork 后执行"""
    from profit_taker import profit_taker
    if not profit_taker.running:
        profit_taker.start()
        server.log.info(f"[Gunicorn] Worker {worker.pid} - ProfitTaker 后台线程已启动")
