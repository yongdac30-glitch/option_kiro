# Ubuntu 服务器部署指南

## 前提条件

- Ubuntu 20.04 / 22.04 / 24.04
- 有 sudo 权限
- 服务器已开放 80 端口（或你想用的端口）

---

## 1. 安装系统依赖

```bash
sudo apt update && sudo apt upgrade -y
sudo apt install -y python3 python3-pip python3-venv nodejs npm nginx git
```

确认版本（Python >= 3.10, Node >= 18）：

```bash
python3 --version
node --version
```

如果 Node 版本太低，用 NodeSource 安装新版：

```bash
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
```

---

## 2. 上传项目代码

把项目代码传到服务器，比如放在 `/opt/options-monitor`：

```bash
# 方式一：git clone（如果有仓库）
sudo mkdir -p /opt/options-monitor
cd /opt/options-monitor
sudo git clone <你的仓库地址> .

# 方式二：scp 上传
# 本地执行：
scp -r ./* user@your-server:/opt/options-monitor/
```

设置目录权限：

```bash
sudo chown -R $USER:$USER /opt/options-monitor
```

---

## 3. 部署后端（FastAPI + Uvicorn）

```bash
cd /opt/options-monitor/backend

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt
```

创建 `.env` 文件：

```bash
cp .env.example .env
```

编辑 `.env`，根据需要修改：

```ini
DATABASE_URL=sqlite:///./options_monitor.db
API_HOST=0.0.0.0
API_PORT=8000
API_RELOAD=False
CORS_ORIGINS=http://your-server-ip,http://your-domain.com
RISK_FREE_RATE=0.05
```

测试后端能否正常启动：

```bash
source venv/bin/activate
uvicorn app.main:app --host 0.0.0.0 --port 8000
# 看到 "Uvicorn running on http://0.0.0.0:8000" 就OK，Ctrl+C 退出
```

### 创建 systemd 服务（后台自动运行）

```bash
sudo tee /etc/systemd/system/options-monitor-backend.service << 'EOF'
[Unit]
Description=Options Monitor Backend
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=/opt/options-monitor/backend
Environment=PATH=/opt/options-monitor/backend/venv/bin:/usr/bin
ExecStart=/opt/options-monitor/backend/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --workers 2
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF
```

> 注意：上面的 `$USER` 需要替换成你的实际用户名，比如 `ubuntu`。

启动服务：

```bash
sudo systemctl daemon-reload
sudo systemctl enable options-monitor-backend
sudo systemctl start options-monitor-backend

# 查看状态
sudo systemctl status options-monitor-backend

# 查看日志
sudo journalctl -u options-monitor-backend -f
```

---

## 4. 构建前端（Vite 静态文件）

```bash
cd /opt/options-monitor/frontend

# 安装依赖
npm install
```

创建 `.env` 文件，指向后端地址：

```bash
# 如果用 Nginx 反向代理（推荐），前端和后端同域：
echo 'VITE_API_BASE_URL=' > .env

# 如果不用反向代理，直接指向后端端口：
# echo 'VITE_API_BASE_URL=http://your-server-ip:8000' > .env
```

构建：

```bash
npm run build
```

构建产物在 `frontend/dist/` 目录。

---

## 5. 配置 Nginx（反向代理 + 静态文件）

```bash
sudo tee /etc/nginx/sites-available/options-monitor << 'EOF'
server {
    listen 80;
    server_name your-domain.com;  # 改成你的域名或 IP

    # 前端静态文件
    root /opt/options-monitor/frontend/dist;
    index index.html;

    # 前端路由 - SPA fallback
    location / {
        try_files $uri $uri/ /index.html;
    }

    # 后端 API 反向代理
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE/流式响应支持（回测用到）
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }

    # WebSocket 支持（如果用到）
    location /ws {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
    }

    # 健康检查
    location /health {
        proxy_pass http://127.0.0.1:8000;
    }
}
EOF
```

启用站点：

```bash
sudo ln -sf /etc/nginx/sites-available/options-monitor /etc/nginx/sites-enabled/
sudo rm -f /etc/nginx/sites-enabled/default  # 移除默认站点

# 测试配置
sudo nginx -t

# 重启 Nginx
sudo systemctl restart nginx
sudo systemctl enable nginx
```

---

## 6. 防火墙设置

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp   # 如果后续加 HTTPS
sudo ufw allow 22/tcp    # SSH
sudo ufw enable
```

---

## 7. 验证部署

```bash
# 后端健康检查
curl http://localhost:8000/health

# 通过 Nginx 访问
curl http://your-server-ip/health
curl http://your-server-ip/api/portfolios
```

浏览器访问 `http://your-server-ip` 应该能看到前端页面。

---

## 8. （可选）HTTPS 配置

用 Let's Encrypt 免费证书：

```bash
sudo apt install -y certbot python3-certbot-nginx
sudo certbot --nginx -d your-domain.com
```

证书会自动续期。配好后前端 `.env` 里的 `VITE_API_BASE_URL` 如果有填写，记得改成 `https://`。

---

## 常用运维命令

```bash
# 查看后端日志
sudo journalctl -u options-monitor-backend -f

# 重启后端
sudo systemctl restart options-monitor-backend

# 重新构建前端（代码更新后）
cd /opt/options-monitor/frontend && npm run build

# 重启 Nginx
sudo systemctl restart nginx

# 查看后端是否在运行
sudo systemctl status options-monitor-backend
```

---

## 更新部署（代码更新后）

```bash
cd /opt/options-monitor

# 拉取最新代码
git pull

# 更新后端依赖（如果 requirements.txt 有变）
cd backend && source venv/bin/activate && pip install -r requirements.txt

# 重启后端
sudo systemctl restart options-monitor-backend

# 重新构建前端
cd ../frontend && npm install && npm run build

# Nginx 不需要重启（静态文件直接生效）
```
