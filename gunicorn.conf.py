# gunicorn.conf.py（推荐稳定配置）

import multiprocessing

# 绑定地址和端口
bind = "0.0.0.0:5000"

# Worker 数量（建议 2~4 个，内存占用和稳定性平衡）
workers = 2

# Worker 类型（sync 最稳定）
worker_class = "sync"

# 每个 worker 处理的最大请求数（防止内存泄漏）
max_requests = 1000
max_requests_jitter = 100

# 超时设置
timeout = 120
graceful_timeout = 30

# 预加载应用（建议关闭，避免和后台线程冲突）
preload_app = False

# 日志配置
accesslog = "-"
errorlog = "-"
loglevel = "info"
access_log_format = '%(t)s %(h)s "%(r)s" %(s)s %(b)s'

# 进程名称
proc_name = "eth-webhook-server"

# 守护进程（systemd 管理时设为 False）
daemon = False
