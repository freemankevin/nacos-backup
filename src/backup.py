import os
import sys
import time
import yaml
import logging
import requests
import zipfile
import threading
import argparse
import signal
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, HTTPServer
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from urllib.parse import urlparse
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type

import schedule
from croniter import croniter
from minio import Minio
from minio.error import S3Error

# =================== 接口路径定义 ===================
API_ENDPOINTS = {
    "v2": {
        "login": "/auth/users/login",
        "state": "/console/server/state",
        "config_list": "/cs/configs",
        "config_content": "/cs/configs",
        "namespace": "/console/namespaces"
    },
    "v3": {
        "login": "/auth/user/login",
        "state": "/console/server/state",
        "config_list": "/console/cs/config/list",
        "config_content": "/console/cs/config",
        "namespace": "/console/core/namespace/list"
    }
}

# =================== 初始化函数 ===================
def init_thread_local():
    """初始化线程局部存储，用于 Nacos 令牌缓存。"""
    global _thread_local
    _thread_local = threading.local()
    logger.debug("线程局部存储已初始化")

def init_logging(log_level="INFO", log_file=None, reconfigure=False):
    """初始化日志配置，支持控制台和文件输出。"""
    global logger
    if 'logger' not in globals() or reconfigure:
        logger = logging.getLogger(__name__)
        logger.handlers.clear()  # 清除现有处理器
        logger.setLevel(getattr(logging, log_level.upper(), logging.INFO))

        # 控制台处理器
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
        logger.addHandler(console_handler)

        # 修复 Windows 控制台编码
        if sys.platform == "win32" and sys.stdout.encoding != 'UTF-8':
            sys.stdout = open(sys.stdout.fileno(), mode='w', encoding='utf-8', buffering=1)

        # 文件处理器
        if log_file:
            try:
                os.makedirs(os.path.dirname(log_file), exist_ok=True)
                file_handler = logging.FileHandler(log_file, encoding='utf-8')
                file_handler.setLevel(getattr(logging, log_level.upper(), logging.INFO))
                file_handler.setFormatter(logging.Formatter('%(asctime)s [%(levelname)s] %(message)s'))
                logger.addHandler(file_handler)
                logger.info(f"日志文件已配置: {log_file}")
            except Exception as e:
                logger.error(f"配置日志文件失败: {e}")

# =================== 信号处理 ===================
def handle_shutdown(signum, frame):
    """处理 SIGTERM/SIGINT 信号，优雅退出。"""
    logger.info("收到终止信号，正在优雅退出...")
    schedule.clear()  # 清除定时任务
    sys.exit(0)

signal.signal(signal.SIGTERM, handle_shutdown)
signal.signal(signal.SIGINT, handle_shutdown)

# =================== 配置加载与版本检测 ===================
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type((requests.RequestException,)),
    before_sleep=lambda retry_state: logger.warning(f"Nacos 版本检测失败，重试 {retry_state.attempt_number}/3")
)
def detect_nacos_version(config):
    """通过 /console/server/state 接口检测 Nacos 版本。"""
    nacos = config['nacos']
    scheme = nacos['scheme']
    hostname = nacos['hostname']
    console_port = nacos.get('console_port', 8080)
    port = nacos['port']

    # 尝试 v3 接口
    url = f"{scheme}://{hostname}:{console_port}/nacos/v3{API_ENDPOINTS['v3']['state']}"
    params = {"username": nacos['username']}
    logger.debug(f"检测 Nacos 版本 (v3): {url}")
    try:
        resp = requests.get(url, params=params, timeout=10)
        logger.debug(f"v3 状态响应: 状态码={resp.status_code}, 响应={resp.text}")
        if resp.status_code == 200:
            try:
                data = resp.json()
                version = data.get("version", "")
                if version.startswith("3."):
                    logger.info(f"检测到 Nacos 版本: v3 ({version})")
                    return "v3", version
            except ValueError:
                logger.warning(f"v3 版本响应非 JSON 格式: {resp.text}")
    except Exception as e:
        logger.debug(f"v3 版本检测失败: {e}")

    # 回退到 v2 接口
    url = f"{scheme}://{hostname}:{port}/nacos/v1{API_ENDPOINTS['v2']['state']}"
    logger.debug(f"检测 Nacos 版本 (v2): {url}")
    try:
        resp = requests.get(url, params=params, timeout=10)
        logger.debug(f"v2 状态响应: 状态码={resp.status_code}, 响应={resp.text}")
        if resp.status_code == 200:
            try:
                data = resp.json()
                version = data.get("version", "")
                if version.startswith("2."):
                    logger.info(f"检测到 Nacos 版本: v2 ({version})")
                    return "v2", version
            except ValueError:
                logger.warning(f"v2 版本响应非 JSON 格式: {resp.text}")
    except Exception as e:
        logger.debug(f"v2 版本检测失败: {e}")

    logger.error("无法检测 Nacos 版本")
    raise Exception("无法检测 Nacos 版本")

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

        # 解析 nacos.host
        nacos = config.get('nacos', {})
        parsed_url = urlparse(nacos.get('host', ''))
        nacos['scheme'] = parsed_url.scheme or 'http'
        nacos['hostname'] = parsed_url.hostname or nacos.get('host', '')
        nacos['port'] = parsed_url.port or 8848
        config['nacos'] = nacos

        # 检测 Nacos 版本
        try:
            version, full_version = detect_nacos_version(config)
            nacos['version'] = version
            logger.info(f"Nacos 配置: 版本={version} ({full_version}), 主机={nacos['hostname']}, "
                       f"协议={nacos['scheme']}, 端口={nacos['port']}, "
                       f"console 端口={nacos.get('console_port', '未配置')}")
        except Exception as e:
            logger.error(f"版本检测失败，将在下次调度重试: {e}")
            config['nacos']['version'] = 'v2'  # 默认回退到 v2
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
        "@hourly": "0 * * * *",
        "@daily": "0 0 * * *",
        "@weekly": "0 0 * * 0",
        "@monthly": "0 0 1 * *",
    }
    expr = expr.strip().lower()
    if expr in CRON_ALIASES:
        return CRON_ALIASES[expr]
    elif ":" in expr:
        try:
            hour, minute = expr.split(":")
            cron_str = f"{minute} {hour} * * *"
            croniter(cron_str)
            return cron_str
        except Exception as e:
            logger.error(f"无效的时间格式 '{expr}': {e}")
            raise ValueError(f"无效的时间格式 '{expr}': {e}")
    else:
        try:
            croniter(expr)
            return expr
        except Exception as e:
            logger.error(f"无效的 cron 表达式 '{expr}': {e}")
            raise ValueError(f"无效的 cron 表达式 '{expr}': {e}")

# =================== Nacos 登录与命名空间获取 ===================
def _get_nacos_base_url(config):
    """根据版本返回 Nacos 基础 URL。"""
    nacos = config['nacos']
    version = nacos.get('version', 'v2')
    if version == 'v3':
        console_port = nacos.get('console_port', 8080)
        return f"{nacos['scheme']}://{nacos['hostname']}:{console_port}/nacos/v3"
    return f"{nacos['scheme']}://{nacos['hostname']}:{nacos['port']}/nacos/v1"

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type((requests.RequestException, requests.HTTPError)),
    before_sleep=lambda retry_state: logger.warning(f"登录 Nacos 失败，重试 {retry_state.attempt_number}/3")
)
def login_nacos():
    """登录 Nacos 并缓存令牌（线程安全）。"""
    if hasattr(_thread_local, 'token') and hasattr(_thread_local, 'expiry') and time.time() < _thread_local.expiry:
        return _thread_local.token

    version = config['nacos']['version']
    url = f"{_get_nacos_base_url(config)}{API_ENDPOINTS[version]['login']}"
    data = {
        "username": config['nacos']['username'],
        "password": config['nacos']['password']
    }
    logger.debug(f"尝试登录 Nacos: {url}, 用户: {data['username']}")
    try:
        resp = requests.post(url, data=data, timeout=10)
        resp.raise_for_status()  # 触发 HTTPError for 4xx/5xx
        logger.debug(f"Nacos 登录响应: 状态码={resp.status_code}, 响应={resp.text}")
        try:
            response_json = resp.json()
            token = response_json.get("accessToken")
            if not token:
                logger.error(f"登录响应中缺少 accessToken: {response_json}")
                raise Exception("缺少 accessToken")
            _thread_local.token = token
            _thread_local.expiry = time.time() + 60 * 60  # 假设令牌有效期 1 小时
            logger.info("Nacos 登录成功")
            return _thread_local.token
        except ValueError:
            logger.error(f"登录响应非 JSON 格式: {resp.text}")
            raise Exception("登录响应非 JSON 格式")
    except requests.HTTPError as e:
        logger.error(f"Nacos 登录失败: 状态码={e.response.status_code}, 响应={e.response.text}")
        if version == 'v3' and e.response.status_code == 500:
            logger.debug("v3 登录失败，尝试 v2 登录接口")
            url = f"{config['nacos']['scheme']}://{config['nacos']['hostname']}:{config['nacos']['port']}/nacos/v1{API_ENDPOINTS['v2']['login']}"
            try:
                resp = requests.post(url, data=data, timeout=10)
                resp.raise_for_status()
                logger.debug(f"v2 登录响应: 状态码={resp.status_code}, 响应={resp.text}")
                response_json = resp.json()
                token = response_json.get("accessToken")
                if token:
                    _thread_local.token = token
                    _thread_local.expiry = time.time() + 60 * 60
                    logger.info("Nacos 登录成功 (v2 回退)")
                    return _thread_local.token
                logger.error(f"v2 登录失败: 缺少 accessToken, 响应={resp.text}")
                raise Exception("v2 登录失败")
            except requests.HTTPError as e2:
                logger.error(f"v2 登录失败: 状态码={e2.response.status_code}, 响应={e2.response.text}")
                raise
            except Exception as e2:
                logger.error(f"v2 登录回退失败: {e2}")
                raise
        raise
    except Exception as e:
        logger.error(f"Nacos 连接失败: {e}")
        raise

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type((requests.RequestException,)),
    before_sleep=lambda retry_state: logger.warning(f"获取命名空间失败，重试 {retry_state.attempt_number}/3")
)
def get_namespace_mapping():
    """获取 Nacos 命名空间映射。"""
    version = config['nacos']['version']
    url = f"{_get_nacos_base_url(config)}{API_ENDPOINTS[version]['namespace']}"
    params = {"namespaceId": "public"} if version == 'v3' else {}

    headers = {"Authorization": f"Bearer {login_nacos()}"}
    logger.debug(f"访问命名空间 API: {url}, 参数: {params}")
    resp = requests.get(url, headers=headers, params=params, timeout=10)
    logger.debug(f"命名空间响应: 状态码={resp.status_code}, 响应={resp.text}")
    if resp.status_code != 200:
        logger.error(f"获取命名空间失败，状态码: {resp.status_code}, 响应: {resp.text}")
        raise Exception("获取命名空间失败")
    try:
        data = resp.json()
        if version == 'v2':
            if data.get("code") != 200:
                logger.error(f"获取命名空间失败: {data.get('message', '未知错误')}")
                raise Exception("获取命名空间失败")
            namespaces = data.get("data", [])
        else:  # v3
            if data.get("code") != 0:
                logger.error(f"获取命名空间失败: {data.get('message', '未知错误')}")
                raise Exception("获取命名空间失败")
            namespaces = data.get("data", [])
        mapping = {}
        for item in namespaces:
            name = item.get("namespaceShowName", "public") or item.get("id", "default")
            ns_id = item.get("namespace", "") or item.get("id", "")
            mapping[name] = ns_id
        logger.info(f"命名空间: {mapping}")
        return mapping
    except Exception as e:
        logger.error(f"解析命名空间响应失败: {e}, 响应: {resp.text}")
        raise

# =================== 获取配置列表 & 内容 ===================
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type((requests.RequestException,)),
    before_sleep=lambda retry_state: logger.warning(f"获取配置列表失败，重试 {retry_state.attempt_number}/3")
)
def get_config_list(namespace_id):
    """获取指定命名空间的配置。"""
    version = config['nacos']['version']
    url = f"{_get_nacos_base_url(config)}{API_ENDPOINTS[version]['config_list']}"
    if version == 'v3':
        params = {
            "dataId": "", "groupName": "", "appName": "", "configTags": "",
            "pageNo": 1, "pageSize": 1000, "namespaceId": namespace_id,
            "type": "", "search": "blur", "username": config['nacos']['username']
        }
    else:
        params = {
            "dataId": "", "group": "", "appName": "", "config_tags": "",
            "pageNo": 1, "pageSize": 1000, "tenant": namespace_id,
            "types": [], "search": "blur", "username": config['nacos']['username']
        }

    headers = {"Authorization": f"Bearer {login_nacos()}"}
    logger.debug(f"获取配置列表: {url}, 参数: {params}")
    configs = []
    while True:
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        logger.debug(f"配置列表响应: 状态码={resp.status_code}, 响应={resp.text}")
        if resp.status_code != 200:
            logger.error(f"获取配置失败，状态码: {resp.status_code}, 响应: {resp.text}")
            return configs
        try:
            data = resp.json()
            if version == 'v3' and data.get("code") != 0:
                logger.error(f"获取配置列表失败: {data.get('message', '未知错误')}")
                return configs
            inner_data = data if version == 'v2' else data.get("data", {})
            if not isinstance(inner_data, dict):
                logger.error(f"配置响应格式错误，data={inner_data}")
                return configs
            page_items = inner_data.get("pageItems", [])
            total_count = inner_data.get("totalCount", 0)
            logger.debug(f"配置项数量: 当前页={len(page_items)}, 总计={total_count}")
            if not page_items:
                logger.debug("无配置项")
                break
            for item in page_items:
                config_item = {
                    "dataId": item.get("dataId"),
                    "group": item.get("group" if version == "v2" else "groupName")
                }
                if config_item["dataId"] and config_item["group"]:
                    configs.append(config_item)
            logger.info(f"当前页配置项: {len(page_items)}, 总计: {len(configs)}")
            if params["pageNo"] * params["pageSize"] >= total_count:
                break
            params["pageNo"] += 1
        except ValueError as e:
            logger.error(f"配置响应非 JSON 格式: {e}, 响应: {resp.text}")
            return configs
        except Exception as e:
            logger.error(f"解析配置列表失败: {e}")
            return configs
    return configs

@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type((requests.RequestException,)),
    before_sleep=lambda retry_state: logger.warning(f"获取配置内容失败，重试 {retry_state.attempt_number}/3")
)
def get_config_content(namespace_id, group, data_id):
    """获取指定配置内容。"""
    version = config['nacos']['version']
    url = f"{_get_nacos_base_url(config)}{API_ENDPOINTS[version]['config_content']}"
    params = {
        "tenant" if version == 'v2' else "namespaceId": namespace_id,
        "group" if version == 'v2' else "groupName": group,
        "dataId": data_id
    }
    headers = {"Authorization": f"Bearer {login_nacos()}"}
    logger.debug(f"获取配置: {url}, 参数={params}")
    resp = requests.get(url, headers=headers, params=params, timeout=10)
    logger.debug(f"配置内容: 状态={resp.status_code}, 响应={resp.text}")
    if resp.status_code == 200:
        return resp.text
    logger.warning(f"获取配置失败: 状态码={resp.status_code}, 响应={resp.text}")
    return ""

# =================== ZIP 打包 ===================
def create_zip(namespace_name, namespace_id, configs, output_dir):
    """创建备份 ZIP 文件。"""
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    zip_path = os.path.join(output_dir, f"nacos-backup-{namespace_name}-{timestamp}.zip")
    try:
        os.makedirs(output_dir, exist_ok=True)
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
@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=4, max=10),
    retry=retry_if_exception_type((S3Error,)),
    before_sleep=lambda retry_state: logger.warning(f"MinIO 上传失败，重试 {retry_state.attempt_number}/3")
)
def upload_to_minio(zip_path):
    """上传备份文件到 MinIO。"""
    minio_config = config['backup']['minio']
    endpoint = minio_config['endpoint']
    if not endpoint.startswith(('http://', 'https://')):
        logger.error(f"MinIO endpoint 必须包含协议 (http:// 或 https://): {endpoint}")
        return

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

# =================== 单个命名空间备份任务 ===================
def backup_namespace(namespace):
    """备份单个命名空间的配置。"""
    namespace_id = namespace.get("id", "")
    namespace_name = namespace.get("name", "default")
    logger.info(f"开始备份命名空间: {namespace_name}({namespace_id})")
    try:
        configs = get_config_list(namespace_id)
        if not configs:
            logger.info(f"命名空间 {namespace_name}({namespace_id}) 无配置文件，跳过备份")
            return
        logger.info(f"发现 {len(configs)} 个配置项")
        output_dir = config['backup']['output_dir']
        zip_path = create_zip(namespace_name, namespace_id, configs, output_dir)
        if zip_path and config['backup']['upload_to_minio']:
            upload_to_minio(zip_path)
        if config['backup'].get('auto_clean_days', 0) > 0:
            clean_old_backups(output_dir, config['backup']['auto_clean_days'])
    except Exception as e:
        logger.error(f"备份命名空间 {namespace_name} 失败，将在下次调度重试: {e}")

# =================== 定时任务调度器 ===================
def setup_scheduler(schedule_config):
    """设置定时备份任务，支持 cron 或 interval。"""
    cron_expr = schedule_config.get('cron', '@hourly')
    interval = schedule_config.get('interval')
    timezone_str = config.get('timezone', 'Asia/Shanghai')
    try:
        tz = ZoneInfo(timezone_str)
        logger.info(f"使用时区: {timezone_str}")
    except ZoneInfoNotFoundError:
        logger.warning(f"无效时区: {timezone_str}，回退到 Asia/Shanghai")
        tz = ZoneInfo("Asia/Shanghai")
    now = datetime.now(tz)

    def scheduled_task():
        logger.info("开始执行定时备份任务...")
        run_backup()

    if cron_expr:
        parsed = parse_human_cron(cron_expr)
        logger.info(f"解析后的 cron 表达式: {parsed}")
        now = datetime.now(tz)
        next_run = croniter(parsed, now).get_next(datetime)
        logger.info(f"定时任务已启动（时区: {tz}），下次执行时间: {next_run}")
        if parsed == "0 * * * *":
            schedule.every().hour.at(":00").do(scheduled_task)
        else:
            minute, hour, *_ = parsed.split()
            schedule.every().day.at(f"{hour.zfill(2)}:{minute.zfill(2)}").do(scheduled_task)
    elif interval:
        try:
            if isinstance(interval, str) and interval.endswith('s'):
                interval_secs = int(interval[:-1])
            else:
                interval_secs = int(interval)
            logger.info(f"使用 interval 调度，周期: {interval_secs} 秒")
            schedule.every(interval_secs).seconds.do(scheduled_task)
            next_run = now + timedelta(seconds=interval_secs)
            logger.info(f"定时任务已启动（时区: {tz}），下次执行时间: {next_run}")
        except ValueError as e:
            logger.error(f"无效的 interval 配置: {interval}, 错误: {e}")
            sys.exit(1)
    else:
        logger.error("未配置 cron 或 interval，退出")
        sys.exit(1)

    def run_scheduler():
        while True:
            try:
                schedule.run_pending()
                time.sleep(1)
            except Exception as e:
                logger.error(f"调度器异常，将继续运行: {e}")

    thread = threading.Thread(target=run_scheduler, daemon=True)
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
    """启动健康检查 HTTP 服务。"""
    server_address = ('', port)
    httpd = HTTPServer(server_address, HealthCheckHandler)
    logger.info(f"启动健康检查服务，监听端口 {port}")
    httpd.serve_forever()

def run_health_check_in_background(port=8080):
    """在后台线程运行健康检查服务。"""
    logger.info(f"启动健康检查线程，端口: {port}")
    thread = threading.Thread(target=start_health_check_server, args=(port,), daemon=True)
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

    # 验证 nacos.host
    parsed_url = urlparse(config['nacos']['host'])
    if not parsed_url.scheme or not parsed_url.hostname:
        logger.error(f"无效的 nacos.host 格式: {config['nacos']['host']}, 需包含协议和主机")
        sys.exit(1)

def run_backup():
    """执行备份任务。"""
    logger.info(f"===== 备份任务开始于 {datetime.now():%Y-%m-%d %H:%M:%S} =====")
    try:
        ns_mapping = get_namespace_mapping()
        logger.info(f"命名空间: {ns_mapping}")
        namespaces = [{"id": ns_id, "name": name} for name, ns_id in ns_mapping.items()]
        logger.info(f"目标命名空间: {[ns['name'] for ns in namespaces]}")
        if not namespaces:
            logger.warning("没有可用的命名空间，跳过备份")
            return
        max_workers = config['backup'].get('concurrent_threads', min(3, len(namespaces)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            executor.map(backup_namespace, namespaces)
    except Exception as e:
        logger.error(f"备份失败，将在下次调度重试: {e}")
    logger.info(f"===== 备份任务结束于 {datetime.now():%Y-%m-%d %H:%M:%S} =====")

def main():
    """主入口函数，协调初始化和备份流程。"""
    # 初始日志配置
    init_logging(log_level="INFO")

    # 加载配置
    global config
    config = load_config()

    # 应用配置文件中的日志设置
    log_level = config.get("logging", {}).get('level', 'INFO')
    log_file = config.get("logging", {}).get('file')
    init_logging(log_level=log_level, log_file=log_file, reconfigure=True)

    # 初始化线程局部存储
    init_thread_local()

    # 验证配置
    validate_config(config)

    # 启动健康检查
    health_check_port = config.get("health-check", {}).get("port", config.get("health", {}).get("port", 8080))
    run_health_check_in_background(health_check_port)

    # 执行备份
    if config.get('schedule', {}).get('enabled', False):
        logger.info(f"启用定时任务，周期: {config['schedule'].get('cron', '@hourly')}")
        logger.info("执行首次启动备份...")
        run_backup()
        setup_scheduler(config['schedule'])
        # 保持主线程运行
        while True:
            time.sleep(1)
    else:
        logger.info("开始执行单次备份任务...")
        run_backup()

if __name__ == "__main__":
    main()