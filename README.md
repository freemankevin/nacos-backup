# NACOS BACKUP 🚀

🌍 **中文** | [ENGLISH](README_EN.md)

一个简单而强大的 Python 脚本，用于自动备份 Nacos 配置，支持定时任务、MinIO 上传和健康检查。📦

## 项目简介 ℹ️

本项目提供了一个守护进程风格的备份工具，专为 Nacos 配置管理设计。它通过 Nacos API 定期获取配置，生成 ZIP 文件，并可选上传至 MinIO 存储。脚本支持 Nacos v2/v3，具备重试机制、优雅退出和日志持久化，适合生产环境部署。

**核心功能**：

- ⏰ **定时备份**：支持 cron 或 interval 调度，灵活配置备份周期。
- 📂 **ZIP 打包**：按命名空间生成备份文件，便于归档。
- ☁️ **MinIO 集成**：支持将备份上传至 MinIO，安全存储。
- 🔄 **重试机制**：自动处理网络或服务临时不可用（如 HTTP 500）。
- 🛡️ **健康检查**：提供 `/healthz` 端点，方便监控。
- 📜 **日志记录**：支持控制台和文件日志，持久化调试信息。

## 快速开始 🏃‍♂️

### 环境要求

- Python 3.8+
- Docker（推荐）
- Nacos 服务（v2 或 v3）
- MinIO 服务（可选）

### 安装步骤

1. **克隆仓库**：

   ```bash
   https://github.com/freemankevin/nacos-backup.git
   cd nacos-backup
   ```

2. **准备配置文件**：
   复制 `config.yaml` 为 `config-local.yaml`，并更新以下字段：

   ```yaml
   nacos:
     host: "http://your-nacos-host:8080"
     username: "nacos"
     password: "your_password"
     console_port: 8080  # v3 必需，v2 可忽略
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
     cron: "@hourly" # 可选值："@hourly", "03:00", "15:30", "@daily", "@weekly"
   logging:
     file: "./data/nacos-backup/logs/backup.log"
     level: INFO
   health-check:
     port: 8082
   ```

3. **构建并运行 Docker 容器**：

   ```bash
   docker-compose up -d
   ```

4. **验证运行**：

   - 检查容器状态：

     ```bash
     docker ps
     ```

   - 查看日志：

     ```bash
     docker logs nacos-backup
     ```

   - 验证健康检查：

     ```bash
     curl http://localhost:8082/healthz
     ```

     预期返回：`OK`

## 配置说明 🔧

`config-local.yaml` 支持以下关键配置：

- `nacos`：Nacos 服务地址、用户名和密码。
- `backup`：备份输出目录、MinIO 配置、自动清理天数。
- `schedule`：定时任务开关和周期（支持 cron 表达式或 `@hourly` 等别名）。
- `logging`：日志级别（DEBUG/INFO）和文件路径。
- `health-check`：健康检查端口。

**示例日志输出**：

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

## 故障排查 ❓

- **登录失败**：检查 `config-local.yaml` 中的 `nacos.username` 和 `password`。
- **MinIO 上传失败**：确保 MinIO 端点和凭证正确，网络可达。
- **日志丢失**：确认 Docker 卷挂载正确。
- **重试失败**：查看日志中的重试信息，检查 Nacos 服务状态。

## 许可证 📜

本项目采用 MIT 许可证，欢迎自由使用和修改！🎉

## 贡献 🤝

欢迎提交 Issue 或 Pull Request！如果有新功能建议或 Bug 报告，请随时联系。😊