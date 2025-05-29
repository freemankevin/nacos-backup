# NACOS BACKUP 🚀

🌍 **ENGLISH** | [中文](README.md)

A simple yet powerful Python script for automated Nacos configuration backups, with support for scheduled tasks, MinIO uploads, and health checks. 📦

## Project Overview ℹ️

This project provides a daemon-style backup tool designed for Nacos configuration management. It periodically fetches configurations via the Nacos API, generates ZIP files, and optionally uploads them to MinIO storage. The script supports Nacos v2/v3, with retry mechanisms, graceful shutdown, and persistent logging, making it ideal for production environments.

**Key Features**:

- ⏰ **Scheduled Backups**: Flexible scheduling with cron or interval settings.
- 📂 **ZIP Packaging**: Organizes backups by namespace for easy archiving.
- ☁️ **MinIO Integration**: Uploads backups to MinIO for secure storage.
- 🔄 **Retry Mechanism**: Handles transient network or service issues (e.g., HTTP 500).
- 🛡️ **Health Checks**: Exposes `/healthz` endpoint for monitoring.
- 📜 **Logging**: Supports console and file logging for persistent debugging.

## Quick Start 🏃‍♂️

### Prerequisites

- Python 3.8+
- Docker (recommended)
- Nacos service (v2 or v3)
- MinIO service (optional)

### Installation Steps

1. **Clone the Repository**:

   ```bash
   https://github.com/freemankevin/nacos-backup.git
   cd nacos-backup
   ```

2. **Prepare Configuration**:
   Copy `config-example.yaml` to `config-local.yaml` and update the following fields:

   ```yaml
   nacos:
     host: "http://your-nacos-host:8080"
     username: "nacos"
     password: "your_password"
     console_port: 8080  
   backup:
     output_dir: "./data/nacos-backup/backups"
     upload_to_minio: true
     minio:
       endpoint: "http://your-minio-host:9000"
       access_key: "your_minio_user"
       secret_key: "your_minio_secret"
       bucket: "nacos-backups"
   schedule:
     enabled: true
     cron: "@hourly" 
   logging:
     file: "./data/nacos-backup/logs/backup.log"
     level: INFO
   health-check:
     port: 8082
   ```

3. **Build and Run Docker Container**:

   ```bash
   docker-compose up -d
   ```

4. **Verify Operation**:

   - Check container status:

     ```bash
     docker ps
     ```

   - View logs:

     ```bash
     docker logs nacos-backup
     ```

   - Verify health check:

     ```bash
     curl http://localhost:8082/healthz
     ```

     Expected output: `OK`

## Configuration Guide 🔧

The `config-local.yaml` supports the following key settings:

- `nacos`: Nacos service URL, username, and password.
- `backup`: Backup output directory, MinIO settings, and auto-cleanup days.
- `schedule`: Enable/disable scheduled tasks and set backup frequency (cron expressions or aliases like `@hourly`).
- `logging`: Log level (DEBUG/INFO) and file path.
- `health-check`: Health check port.

**Sample Log Output**:

```
2025-05-29 11:20:40,074 [INFO] 使用配置文件: ./config-local.yaml
2025-05-29 11:20:42,119 [INFO] 检测到 Nacos 版本: v2 (2.5.1)
2025-05-29 11:20:42,119 [INFO] Nacos 配置: 版本=v2 (2.5.1), 主机=10.3.2.40, 协议=http, 端口=8848, console 端口=8080
2025-05-29 11:20:42,120 [INFO] 日志文件已配置: ./data/nacos-backup/logs/backup.log
2025-05-29 11:20:42,120 [INFO] 启动健康检查线程，端口: 8082
2025-05-29 11:20:42,120 [INFO] 启用定时任务，周期: @hourly
2025-05-29 11:20:42,120 [INFO] 执行首次启动备份...
2025-05-29 11:20:42,121 [INFO] ===== 备份任务开始于 2025-05-29 11:20:42 =====
2025-05-29 11:20:42,124 [INFO] 启动健康检查服务，监听端口 8082
2025-05-29 11:20:42,240 [INFO] Nacos 登录成功
2025-05-29 11:20:42,280 [INFO] 命名空间: {'public': '', 'dev': '352242e2-158f-4e23-a632-7489b911a9bd', 'test': '91e257c4-8690-433e-b62e-7b38d74eb85c', 'prod': 'a83452dd-5582-47a1-8bac-d3f1c644f93c'}
2025-05-29 11:20:42,280 [INFO] 命名空间: {'public': '', 'dev': '352242e2-158f-4e23-a632-7489b911a9bd', 'test': '91e257c4-8690-433e-b62e-7b38d74eb85c', 'prod': 'a83452dd-5582-47a1-8bac-d3f1c644f93c'}
2025-05-29 11:20:42,280 [INFO] 目标命名空间: ['public', 'dev', 'test', 'prod']
2025-05-29 11:20:42,281 [INFO] 开始备份命名空间: public()
2025-05-29 11:20:42,281 [INFO] 开始备份命名空间: dev(352242e2-158f-4e23-a632-7489b911a9bd)
2025-05-29 11:20:42,281 [INFO] 开始备份命名空间: test(91e257c4-8690-433e-b62e-7b38d74eb85c)
2025-05-29 11:20:42,388 [INFO] Nacos 登录成功
2025-05-29 11:20:42,389 [INFO] Nacos 登录成功
2025-05-29 11:20:42,395 [INFO] 命名空间 test(91e257c4-8690-433e-b62e-7b38d74eb85c) 无配置文件，跳过备份
2025-05-29 11:20:42,396 [INFO] 开始备份命名空间: prod(a83452dd-5582-47a1-8bac-d3f1c644f93c)
2025-05-29 11:20:42,397 [INFO] Nacos 登录成功
2025-05-29 11:20:42,400 [INFO] 当前页配置项: 10, 总计: 10
2025-05-29 11:20:42,401 [INFO] 发现 10 个配置项
2025-05-29 11:20:42,407 [INFO] 当前页配置项: 10, 总计: 10
2025-05-29 11:20:42,408 [INFO] 发现 10 个配置项
2025-05-29 11:20:42,409 [INFO] 当前页配置项: 10, 总计: 10
2025-05-29 11:20:42,409 [INFO] 发现 10 个配置项
2025-05-29 11:20:42,492 [INFO] 已创建备份文件: ./data/nacos-backup/backups\nacos-backup-dev-20250529-112042.zip
2025-05-29 11:20:42,492 [INFO] 连接 MinIO: http://10.3.2.40:9000
2025-05-29 11:20:42,494 [INFO] 已创建备份文件: ./data/nacos-backup/backups\nacos-backup-prod-20250529-112042.zip
2025-05-29 11:20:42,494 [INFO] 连接 MinIO: http://10.3.2.40:9000
2025-05-29 11:20:42,496 [INFO] 已创建备份文件: ./data/nacos-backup/backups\nacos-backup-public-20250529-112042.zip
2025-05-29 11:20:42,497 [INFO] 连接 MinIO: http://10.3.2.40:9000
2025-05-29 11:20:42,517 [INFO] 已上传至 MinIO: nacos-backups/nacos-backup-public-20250529-112042.zip
2025-05-29 11:20:42,529 [INFO] 已上传至 MinIO: nacos-backups/nacos-backup-prod-20250529-112042.zip
2025-05-29 11:20:42,530 [INFO] 已上传至 MinIO: nacos-backups/nacos-backup-dev-20250529-112042.zip
2025-05-29 11:20:42,531 [INFO] ===== 备份任务结束于 2025-05-29 11:20:42 =====
2025-05-29 11:20:42,535 [INFO] 使用时区: Asia/Shanghai
2025-05-29 11:20:42,535 [INFO] 解析后的 cron 表达式: 0 * * * *
2025-05-29 11:20:42,536 [INFO] 定时任务已启动（时区: Asia/Shanghai），下次执行时间: 2025-05-29 12:00:00+08:00
```

## Troubleshooting ❓

- **Login Failure**: Verify `nacos.username` and `password` in `config-local.yaml`.
- **MinIO Upload Failure**: Ensure MinIO endpoint and credentials are correct and network is reachable.
- **Log Loss**: Confirm Docker volume mounts are correct .
- **Retry Failures**: Check logs for retry details and verify Nacos service status.

## License 📜

This project is licensed under the MIT License. Feel free to use and modify it! 🎉

## Contributing 🤝

Contributions are welcome! Feel free to open Issues or Pull Requests for new features or bug fixes. 😊