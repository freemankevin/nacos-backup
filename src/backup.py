import os
import sys
import time
import yaml
import logging
import requests
import zipfile
import threading

from datetime import datetime
from concurrent.futures import ThreadPoolExecutor
from minio import Minio
from http.server import BaseHTTPRequestHandler, HTTPServer
from croniter import croniter

# =================== 日志设置 ===================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)

logger = logging.getLogger(__name__)


# =================== 加载配置 ===================
CONFIG_PATH = os.getenv("CONFIG_PATH", "../config.yaml")

def load_config():
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    except Exception as e:
        logger.error(f"加载配置失败: {e}")
        sys.exit(1)

config = load_config()


# =================== 工具函数 ===================
def parse_human_cron(expr: str) -> str:
    HUMAN_CRON_MAP = {
        "@hourly": "0 0 * * *",
        "@daily": "0 0 0 * *",
        "@weekly": "0 0 0 * * 0",
        "@monthly": "0 0 0 1 * *",
    }
    expr = expr.strip().lower()
    if expr in HUMAN_CRON_MAP:
        return HUMAN_CRON_MAP[expr]
    elif ":" in expr:
        try:
            hour, minute = expr.split(":")
            return f"{minute} {hour} * * *"
        except Exception as e:
            raise ValueError(f"无法解析时间格式 '{expr}'：{e}")
    else:
        return expr


# =================== Nacos 登录与命名空间获取 ===================
def login_nacos():
    url = f"{config['nacos']['host']}/v1/auth/login"
    data = {
        "username": config['nacos']['username'],
        "password": config['nacos']['password']
    }
    resp = requests.post(url, data=data)
    if resp.status_code == 200:
        return resp.cookies.get('acookie')
    else:
        logger.error(f"Nacos 登录失败: {resp.text}")
        sys.exit(1)


def get_namespace_mapping():
    url = f"{config['nacos']['host']}/v1/console/namespaces"
    auth = (config['nacos']['username'], config['nacos']['password'])
    try:
        resp = requests.get(url, auth=auth, timeout=10)
        if resp.status_code == 200:
            mapping = {}
            for item in resp.json():
                name = item["namespaceShowName"]
                ns_id = item["namespace"]
                mapping[name] = ns_id
            return mapping
        else:
            raise Exception(f"获取命名空间失败，状态码：{resp.status_code}, 响应：{resp.text}")
    except Exception as e:
        logger.error(f"无法获取命名空间信息：{e}")
        sys.exit(1)


# =================== 获取配置列表 & 内容 ===================
def get_config_list(namespace_id):
    url = f"{config['nacos']['host']}/v1/cs/configs"
    params = {
        "group": "DEFAULT_GROUP",
        "dataId": "",
        "namespaceId": namespace_id,
        "pageNo": 1,
        "pageSize": 1000
    }
    cookies = {'acookie': login_nacos()}
    configs = []
    while True:
        resp = requests.get(url, params=params, cookies=cookies)
        if resp.status_code != 200:
            logger.warning(f"获取配置失败: {resp.text}")
            break
        data = resp.json()
        configs.extend(data.get("pageItems", []))
        if params["pageNo"] * params["pageSize"] >= data["totalCount"]:
            break
        params["pageNo"] += 1
    return configs


def get_config_content(namespace_id, group, data_id):
    url = f"{config['nacos']['host']}/v1/cs/configs"
    params = {
        "namespaceId": namespace_id,
        "group": group,
        "dataId": data_id
    }
    cookies = {'acookie': login_nacos()}
    resp = requests.get(url, params=params, cookies=cookies)
    return resp.text if resp.status_code == 200 else ""


# =================== ZIP 打包 ===================
def create_zip(namespace_name, namespace_id, configs, output_dir):
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    zip_path = os.path.join(output_dir, f"nacos-backup-{namespace_name}-{timestamp}.zip")
    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for item in configs:
            content = get_config_content(namespace_id, item["group"], item["dataId"])
            file_path = os.path.join(namespace_name, item["group"], item["dataId"])
            zipf.writestr(file_path, content)
    logger.info(f"已创建备份文件: {zip_path}")
    return zip_path


# =================== 清理旧备份 ===================
def clean_old_backups(output_dir, days=7):
    now = time.time()
    for filename in os.listdir(output_dir):
        filepath = os.path.join(output_dir, filename)
        if os.path.isfile(filepath) and (now - os.path.getmtime(filepath)) > days * 86400:
            os.remove(filepath)
            logger.info(f"已删除过期备份: {filepath}")


# =================== MinIO 上传 ===================
def upload_to_minio(zip_path):
    mc = Minio(
        config['minio']['endpoint'],
        access_key=config['minio']['access_key'],
        secret_key=config['minio']['secret_key'],
        secure=config['minio'].get('secure', False)
    )
    bucket = config['minio']['bucket']
    object_name = os.path.basename(zip_path)
    if not mc.bucket_exists(bucket):
        mc.make_bucket(bucket)
    mc.fput_object(bucket, object_name, zip_path)
    logger.info(f"已上传至 MinIO: {bucket}/{object_name}")


# =================== 单个命名空间备份任务 ===================
def backup_namespace(namespace):
    namespace_id = namespace.get("id", "")
    namespace_name = namespace.get("name", "default")
    logger.info(f"开始备份命名空间: {namespace_name}({namespace_id})")
    configs = get_config_list(namespace_id)
    logger.info(f"发现 {len(configs)} 个配置项")
    output_dir = config['backup']['output_dir']
    zip_path = create_zip(namespace_name, namespace_id, configs, output_dir)
    if config['backup']['upload_to_minio']:
        upload_to_minio(zip_path)
    if config['backup'].get('auto_clean_days', 0) > 0:
        clean_old_backups(output_dir, config['backup']['auto_clean_days'])


# =================== 定时任务调度器 ===================
def setup_scheduler(cron_expr):
    parsed = parse_human_cron(cron_expr)
    minute, hour, *_ = parsed.split()
    logger.info(f"解析后的 cron 表达式: {parsed}")

    from schedule import Scheduler
    import schedule
    import threading

    def run_continuously(interval=1):
        """持续运行 schedule"""
        cease_continuous_run = threading.Event()

        class ScheduleThread(threading.Thread):
            @classmethod
            def run(cls):
                while not cease_continuous_run.is_set():
                    schedule.run_pending()
                    time.sleep(interval)

        thread = ScheduleThread()
        thread.daemon = True
        thread.start()
        return cease_continuous_run

    def scheduled_task():
        logger.info("开始执行定时备份任务...")
        main_inner()

    schedule.every().day.at(f"{hour.zfill(2)}:{minute.zfill(2)}").do(scheduled_task)
    logger.info(f"定时任务已启动，下次执行时间为: {croniter(parsed).get_next(datetime)}")

    # 启动定时线程
    run_continuously()


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
    thread = threading.Thread(target=start_health_check_server, args=(port,))
    thread.daemon = True
    thread.start()


# =================== 主入口 ===================
def main_inner():
    # 获取命名空间映射
    ns_mapping = get_namespace_mapping()

    # 构建 namespace 列表
    target_namespaces = config['nacos'].get('namespaces', ['default'])
    namespaces = [
        {"id": ns_mapping.get(name, ""), "name": name} for name in target_namespaces
    ]

    max_workers = config['backup'].get('concurrent_threads', 3)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        executor.map(backup_namespace, namespaces)


def main():
    # 日志文件设置
    log_file = config.get("logging", {}).get("file")
    if log_file:
        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        logger.addHandler(file_handler)

    # 启动健康检查服务
    health_check_port = config.get("health", {}).get("port", 8080)
    run_health_check_in_background(health_check_port)

    # 主程序逻辑
    if config['schedule'].get('enabled', False):
        cron_expr = config['schedule'].get('cron', '@hourly')
        logger.info(f"启用定时任务，周期：{cron_expr}")
        setup_scheduler(cron_expr)
    else:
        logger.info("开始执行单次备份任务...")
        main_inner()


if __name__ == "__main__":
    main()