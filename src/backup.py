import os
import sys
import time
import yaml
import logging
import requests
import zipfile
import threading
import argparse
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo  # 用于处理时区

import schedule
from croniter import croniter
from minio import Minio
from minio.error import S3Error

# =================== 日志初始化 ===================
logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

if sys.platform == "win32" and sys.stdout.encoding != 'UTF-8':
    sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)


# =================== 配置加载 ===================
def load_config():
    """加载 YAML 配置文件，支持命令行参数和环境变量。"""
    parser = argparse.ArgumentParser(description="Nacos Backup Script")
    parser.add_argument(
        "--config",
        type=str,
        default=os.getenv("CONFIG_PATH", "./config.yaml"),
        help="Path to YAML config file (default: ./config.yaml or CONFIG_PATH env var)"
    )
    args = parser.parse_args()

    config_path = args.config
    logger.info(f"使用配置文件: {config_path}")

    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        logger.debug(f"已加载配置文件: {config_path}")
        return config
    except FileNotFoundError:
        logger.error(f"配置文件不存在: {config_path}")
        sys.exit(1)
    except yaml.YAMLError as e:
        logger.error(f"解析配置文件失败: {config_path}, 错误: {e}")
        sys.exit(1)

# =================== 工具函数 ===================
def parse_human_cron(expr: str) -> str:
    """解析 cron 表达式，支持别名和时间格式。"""
    CRON_ALIASES = {
        "@hourly": "0 * * * *",  # 每小时第0分钟
        "@daily": "0 0 * * *",   # 每天0点
        "@weekly": "0 0 * * 0",  # 每周日0点
        "@monthly": "0 0 1 * *", # 每月1日0点
    }
    expr = expr.strip().lower()
    if expr in CRON_ALIASES:
        return CRON_ALIASES[expr]
    elif ":" in expr:
        try:
            hour, minute = expr.split(":")
            cron_str = f"{minute} {hour} * * *"
            croniter(cron_str)  # 验证有效性
            return cron_str
        except Exception as e:
            raise ValueError(f"无效的时间格式 '{expr}': {e}")
    else:
        try:
            croniter(expr)  # 验证原始 cron 表达式
            return expr
        except Exception as e:
            raise ValueError(f"无效的 cron 表达式 '{expr}': {e}")

# =================== Nacos 登录与命名空间获取 ===================
_thread_local = threading.local()

def login_nacos():
    """登录 Nacos 并缓存令牌（线程安全）。"""
    if hasattr(_thread_local, 'token') and hasattr(_thread_local, 'expiry') and time.time() < _thread_local.expiry:
        return _thread_local.token

    url = f"{config['nacos']['host']}/nacos/v1/auth/login"
    data = {
        "username": config['nacos']['username'],
        "password": config['nacos']['password']
    }
    try:
        resp = requests.post(url, data=data, timeout=10)
        logger.debug(f"Nacos 登录状态码: {resp.status_code}")
        if resp.status_code == 200:
            _thread_local.token = resp.json().get("accessToken")
            _thread_local.expiry = time.time() + 60 * 60  # 1 小时过期
            return _thread_local.token
        else:
            logger.error(f"Nacos 登录失败: 状态码 {resp.status_code}")
            sys.exit(1)
    except Exception as e:
        logger.error(f"Nacos 连接失败: {e}")
        sys.exit(1)

def get_namespace_mapping():
    """获取 Nacos 命名空间映射。"""
    url = f"{config['nacos']['host']}/nacos/v1/console/namespaces"
    headers = {"Authorization": f"Bearer {login_nacos()}"}
    logger.debug(f"访问命名空间 API: {url}")
    try:
        resp = requests.get(url, headers=headers, timeout=10)
        logger.debug(f"命名空间响应状态码: {resp.status_code}")
        if resp.status_code == 200:
            mapping = {item["namespaceShowName"]: item["namespace"] for item in resp.json()['data']}
            logger.info(f"命名空间映射: {mapping}")
            return mapping
        else:
            raise Exception(f"获取命名空间失败，状态码: {resp.status_code}")
    except Exception as e:
        logger.error(f"无法获取命名空间信息: {e}")
        sys.exit(1)

# =================== 获取配置列表 & 内容 ===================
def get_config_list(namespace_id):
    """获取指定命名空间的配置列表。"""
    url = f"{config['nacos']['host']}/nacos/v1/cs/configs"
    params = {
        "dataId": "", "group": "", "appName": "", "config_tags": "",
        "pageNo": 1, "pageSize": 1000, "tenant": namespace_id,
        "types": "", "search": "blur", "username": config['nacos']['username']
    }
    headers = {"Authorization": f"Bearer {login_nacos()}"}
    logger.debug(f"获取配置列表: {url}, 参数: {params}")
    configs = []
    try:
        while True:
            resp = requests.get(url, params=params, headers=headers)
            logger.debug(f"配置列表响应状态码: {resp.status_code}")
            if resp.status_code != 200:
                logger.warning(f"获取配置失败，状态码: {resp.status_code}")
                break
            data = resp.json()
            page_items = data.get("pageItems", [])
            configs.extend(page_items)
            logger.info(f"当前页配置项: {len(page_items)}, 总计: {len(configs)}")
            if params["pageNo"] * params["pageSize"] >= data.get("totalCount", 0):
                break
            params["pageNo"] += 1
    except Exception as e:
        logger.error(f"获取配置列表失败: {e}")
    return configs

def get_config_content(namespace_id, group, data_id):
    """获取指定配置内容。"""
    url = f"{config['nacos']['host']}/nacos/v1/cs/configs"
    params = {"tenant": namespace_id, "group": group, "dataId": data_id}
    headers = {"Authorization": f"Bearer {login_nacos()}"}
    logger.debug(f"获取配置内容: {url}, 参数: {params}")
    try:
        resp = requests.get(url, params=params, headers=headers)
        logger.debug(f"配置内容响应状态码: {resp.status_code}")
        return resp.text if resp.status_code == 200 else ""
    except Exception as e:
        logger.error(f"获取配置内容失败: {e}")
        return ""

# =================== ZIP 打包 ===================
def create_zip(namespace_name, namespace_id, configs, output_dir):
    """创建备份 ZIP 文件。"""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    zip_path = os.path.join(output_dir, f"nacos-backup-{namespace_name}-{timestamp}.zip")
    try:
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for item in configs:
                content = get_config_content(namespace_id, item["group"], item["dataId"])
                file_path = os.path.join(namespace_name, item["group"], item["dataId"])
                zipf.writestr(file_path, content)
                logger.debug(f"添加配置到 ZIP: {file_path}")
        logger.info(f"已创建备份文件: {zip_path}")
        return zip_path
    except Exception as e:
        logger.error(f"创建 ZIP 文件失败: {e}")
        return None

# =================== 清理旧备份 ===================
def clean_old_backups(output_dir, days=7):
    """清理超过指定天数的备份文件。"""
    now = time.time()
    try:
        for filename in os.listdir(output_dir):
            filepath = os.path.join(output_dir, filename)
            if os.path.isfile(filepath) and (now - os.path.getmtime(filepath)) > days * 86400:
                os.remove(filepath)
                logger.info(f"已删除过期备份: {filepath}")
    except Exception as e:
        logger.error(f"清理旧备份失败: {e}")

# =================== MinIO 上传 ===================
def upload_to_minio(zip_path):
    """上传备份文件到 MinIO。"""
    minio_config = config['backup']['minio']
    endpoint = minio_config['endpoint']
    if not endpoint.startswith(('http://', 'https://')):
        logger.error(f"MinIO endpoint 必须包含协议 (http:// 或 https://): {endpoint}")
        return

    try:
        mc = Minio(
            endpoint.replace("http://", "").replace("https://", ""),
            access_key=minio_config['access_key'],
            secret_key=minio_config['secret_key'],
            secure=minio_config.get('secure', False)
        )
        logger.info(f"连接 MinIO: {endpoint}")
        bucket = minio_config['bucket']
        object_name = os.path.basename(zip_path)

        if not mc.bucket_exists(bucket):
            mc.make_bucket(bucket)
            logger.info(f"创建 MinIO 存储桶: {bucket}")
        else:
            logger.debug(f"存储桶已存在: {bucket}")

        mc.fput_object(bucket, object_name, zip_path)
        logger.info(f"已上传至 MinIO: {bucket}/{object_name}")
    except S3Error as e:
        logger.error(f"MinIO 上传失败 (S3Error): {e.code} - {e.message}")
    except Exception as e:
        logger.error(f"MinIO 上传失败: {type(e).__name__} - {str(e)}")

# =================== 单个命名空间备份任务 ===================
def backup_namespace(namespace):
    """备份单个命名空间的配置。"""
    namespace_id = namespace.get("id", "")
    namespace_name = namespace.get("name", "default")
    logger.info(f"开始备份命名空间: {namespace_name}({namespace_id})")
    configs = get_config_list(namespace_id)
    logger.info(f"发现 {len(configs)} 个配置项")
    output_dir = config['backup']['output_dir']
    zip_path = create_zip(namespace_name, namespace_id, configs, output_dir)
    if zip_path and config['backup']['upload_to_minio']:
        upload_to_minio(zip_path)
    if config['backup'].get('auto_clean_days', 0) > 0:
        clean_old_backups(output_dir, config['backup']['auto_clean_days'])

# =================== 定时任务调度器 ===================
def setup_scheduler(schedule_config):
    """设置定时备份任务，支持 cron 或 interval。"""
    cron_expr = schedule_config.get('cron', '@hourly')
    interval = schedule_config.get('interval')  # 支持 interval 配置
    cst = ZoneInfo("Asia/Shanghai")  # CST 时区 (UTC+8)

    def scheduled_task():
        logger.info("开始执行定时备份任务...")
        run_backup()

    # 处理 cron 调度
    if cron_expr:
        parsed = parse_human_cron(cron_expr)
        logger.info(f"解析后的 cron 表达式: {parsed}")

        # 计算下次运行时间（使用 CST 时区）
        now = datetime.now(cst)
        next_run = croniter(parsed, now).get_next(datetime)
        logger.info(f"定时任务已启动（时区: CST），下次执行时间: {next_run}")

        # 设置 schedule 任务
        if parsed == "0 * * * *":
            schedule.every().hour.at(":00").do(scheduled_task)
        else:
            minute, hour, *_ = parsed.split()
            schedule.every().day.at(f"{hour.zfill(2)}:{minute.zfill(2)}").do(scheduled_task)

    # 处理 interval 调度（如果配置了）
    elif interval:
        try:
            # 解析 interval（如 "60s" -> 60 秒）
            if isinstance(interval, str) and interval.endswith('s'):
                interval_secs = int(interval[:-1])
            else:
                interval_secs = int(interval)
            logger.info(f"使用 interval 调度，周期: {interval_secs} 秒")
            schedule.every(interval_secs).seconds.do(scheduled_task)
            next_run = now + timedelta(seconds=interval_secs)
            logger.info(f"定时任务已启动（时区: CST），下次执行时间: {next_run}")
        except ValueError as e:
            logger.error(f"无效的 interval 配置: {interval}, 错误: {e}")
            sys.exit(1)

    else:
        logger.error("未配置 cron 或 interval，退出")
        sys.exit(1)

    def run_scheduler():
        while True:
            schedule.run_pending()
            time.sleep(1)

    # 启动调度线程
    thread = threading.Thread(target=run_scheduler)
    thread.daemon = True
    thread.start()

# =================== 健康检查服务 ===================
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/healthz':
            self.send_response(200)
            self.send_header("Content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"OK")
        else:
            self.send_response(404)
            self.end_headers()

def start_health_check_server(port=8080):
    server_address = ('', port)
    httpd = HTTPServer(server_address, HealthCheckHandler)
    logger.info(f"启动健康检查服务，监听端口 {port}")
    httpd.serve_forever()

def run_health_check_in_background(port=8080):
    logger.info(f"启动健康检查线程，端口: {port}")
    thread = threading.Thread(target=start_health_check_server, args=(port,))
    thread.daemon = True
    thread.start()

# =================== 主入口 ===================
def validate_config(config):
    """验证配置文件中的必需字段。"""
    required = [
        ('nacos', ['host', 'username', 'password']),
        ('backup', ['output_dir']),
    ]
    for section, fields in required:
        if section not in config:
            logger.error(f"配置文件缺少 '{section}' 部分")
            sys.exit(1)
        for field in fields:
            if field not in config[section]:
                logger.error(f"配置文件缺少 '{section}.{field}' 字段")
                sys.exit(1)

def run_backup():
    """执行备份任务。"""
    logger.info("开始备份流程")
    try:
        ns_mapping = get_namespace_mapping()
        logger.info(f"命名空间映射: {ns_mapping}")
        namespaces = [{"id": ns_id, "name": name} for name, ns_id in ns_mapping.items()]
        logger.info(f"目标命名空间: {[ns['name'] for ns in namespaces]}")
        if not namespaces:
            logger.error("没有可用的命名空间，退出")
            return
        max_workers = config['backup'].get('concurrent_threads', min(3, len(namespaces)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            executor.map(backup_namespace, namespaces)
    except Exception as e:
        logger.error(f"备份失败: {e}")

def main():
    global config
    config = load_config()
    validate_config(config)

    # 动态调整日志级别和添加文件日志
    log_level = config.get("logging", {}).get("level", "INFO").upper()
    log_file = config.get("logging", {}).get("file")
    
    logger.setLevel(getattr(logging, log_level, logging.INFO))
    
    if log_file:
        try:
            file_handler = logging.FileHandler(log_file, encoding='utf-8')
            file_handler.setLevel(getattr(logging, log_level, logging.INFO))
            file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
            logger.addHandler(file_handler)
            logger.info(f"日志文件已配置: {log_file}")
        except Exception as e:
            logger.error(f"配置日志文件失败: {e}")

    health_check_port = config.get("health", {}).get("port", 8082)
    run_health_check_in_background(health_check_port)

    if config.get('schedule', {}).get('enabled', False):
        logger.info(f"启用定时任务，周期: {config['schedule'].get('cron', '@hourly')}")
        logger.info("执行首次启动备份...")
        run_backup()  # 立即触发一次备份
        setup_scheduler(config['schedule'])
    else:
        logger.info("开始执行单次备份任务...")
        run_backup()

if __name__ == "__main__":
    main()