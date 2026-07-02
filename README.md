# SimpleWebServer — 基于 HTTP 协议的简易 Web 服务器

> **课程设计项目** — 基于 HTTP/1.1 协议的简易 Web 服务器设计与实现

---

## 📋 项目简介

SimpleWebServer 是一个使用 **Python** 实现的轻量级 HTTP/1.1 Web 服务器。它使用原始 socket 编程，不依赖任何第三方 Web 框架，从底层实现了 HTTP 协议的请求解析、路由分发、认证授权、静态文件服务、错误处理等核心功能。

### 核心特性

| 特性 | 说明 |
|------|------|
| 🌐 **HTTP/1.1 协议** | 从零实现 HTTP 请求解析（RFC 7230） |
| 📄 **静态文件服务** | 自动 MIME 类型识别，目录默认页 |
| 🔐 **用户认证** | Session 表单登录，HMAC-SHA256 签名 |
| 🧵 **多线程并发** | ThreadPoolExecutor 线程池 |
| ⚙️ **管理面板** | 仪表盘、日志查看、文件管理（查看/运行/删除） |
| 📤 **文件上传** | multipart/form-data 文件接收 |
| 🛡️ **安全防护** | 路径遍历防御、请求大小限制、Session 签名、中文编码兼容 |
| 📝 **访问日志** | Common Log Format (CLF)，内存环形缓冲区 |
| 🌍 **公网暴露** | Cloudflare Tunnel 隧道，启动即自动连接 |
| ⚙️ **配置驱动** | 内置默认配置 + 命令行参数覆盖 |

---

## 🚀 快速开始

### 环境要求

- Python 3.10+
- Windows / Linux / macOS
- [Cloudflared](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/) (公网访问需要，可选)

### 启动服务器

```bash
# 进入项目目录
cd SimpleWebServer

# 使用默认配置启动（监听 127.0.0.1:8080，自动启动 Cloudflare Tunnel）
python server.py

# 指定端口
python server.py --port 9000

# 禁用公网隧道（仅本地访问）
python server.py --no-tunnel
```

启动后:
- 本地访问: **http://127.0.0.1:8080/**
- 公网访问: **https://simpleweb.eu.cc/** (需要 `cloudflared.exe` 和 `tunnel-config.yml`)

### 测试账户

| 用户名 | 密码 | 角色 |
|--------|------|------|
| `admin` | `admin123` | 管理员 |
| `user` | `user123` | 普通用户 |

---

## 📁 项目结构

```
SimpleWebServer/
├── README.md                   # 项目说明文档
├── DESIGN.md                   # 系统设计文档
├── server.py                   # 主入口程序
│   ├── SimpleWebServer         #   服务器主类
│   └── ClientHandler           #   客户端连接处理器
├── http_parser.py              # HTTP 请求解析器
│   ├── HttpRequest             #   请求数据结构
│   ├── HttpParser              #   状态机解析器
│   └── HttpParseError          #   解析异常
├── http_response.py            # HTTP 响应构建器
│   ├── HttpResponse            #   响应数据结构
│   ├── ContentType             #   MIME 类型映射
│   └── ResponseBuilder         #   响应构建工厂
├── router.py                   # URL 路由器
│   ├── Router                  #   核心路由逻辑
│   └── DispatchResult          #   分发结果
├── auth.py                     # 用户认证模块
│   ├── BasicAuthAuthenticator  #   HTTP Basic Auth
│   └── SessionAuthAuthenticator #  Session 表单登录 (HMAC-SHA256)
├── admin_panel.py              # 管理面板模块
│   ├── ServerStats             #   服务器统计
│   └── AdminPanel              #   仪表盘/日志/文件管理
├── error_handler.py            # 错误页面处理器
├── logger.py                   # 访问日志记录器
├── tunnel-config.yml           # Cloudflare Tunnel 配置
├── cloudflared.exe             # Cloudflare Tunnel 客户端 (可选)
├── static/                     # 静态文件根目录
│   ├── index.html              #   默认首页
│   ├── 403.html                #   自定义 403 页面
│   ├── 404.html                #   自定义 404 页面
│   ├── 405.html                #   自定义 405 页面
│   ├── 500.html                #   自定义 500 页面
│   ├── admin/index.html        #   管理面板入口
│   └── private/index.html      #   私密区域（受保护）
├── login/                      # 登录相关页面
│   ├── login.html              #   登录表单
│   ├── upload.html             #   文件上传页面
│   └── welcome.html            #   欢迎页面
├── uploads/                    # 文件上传目录
└── tests/                      # 测试文件
    ├── test_plan.md            #   测试计划
    └── test_requests.sh        #   curl 测试脚本
```

---

## 📖 使用指南

### 静态文件访问

将 HTML、CSS、JS 等文件放入 `static/` 目录即可通过 HTTP 访问：

```bash
echo "<h1>Hello World</h1>" > static/hello.html
curl http://127.0.0.1:8080/hello.html
```

### 用户认证

#### Session 表单登录 (默认)

1. 访问 `http://127.0.0.1:8080/admin/`
2. 自动重定向到登录页面
3. 输入用户名密码 → 登录成功
4. 访问 `/logout` 退出登录

#### HTTP Basic Auth

修改 `server.py` 中的 `DEFAULT_CONFIG`：
```json
{
    "authentication": {
        "enabled": true,
        "type": "basic"
    }
}
```

使用 curl 测试：
```bash
curl -u admin:admin123 http://127.0.0.1:8080/admin/
```

### 文件管理

管理面板提供完整的文件操作功能：

| 操作 | 说明 |
|------|------|
| 👁 **查看** | 自动识别文本编码（UTF-8/GBK），预览文件内容 |
| ▶ **运行** | .exe 在新窗口启动，.py 在服务器执行并显示输出 |
| 🗑 **删除** | 支持中文文件名，路径安全验证 |

### 配置说明

配置在 `server.py` 的 `DEFAULT_CONFIG` 字典中，主要配置项：

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `server.host` | 127.0.0.1 | 监听地址 |
| `server.port` | 8080 | 监听端口 |
| `server.document_root` | ./static | 文档根目录 |
| `server.upload_dir` | ./uploads | 文件上传目录 |
| `server.max_workers` | 10 | 线程池大小 |
| `server.socket_timeout` | 30 | 连接超时(秒) |
| `authentication.enabled` | true | 启用认证 |
| `authentication.type` | session | 认证类型 |
| `authentication.users` | {...} | 用户凭据 |
| `tunnel.enabled` | true | 启动时自动开启 Cloudflare Tunnel |

---

## 🧪 测试

### 自动化测试

```bash
# 先启动服务器，然后运行测试脚本
bash tests/test_requests.sh
```

### 手动测试

```bash
# GET 请求
curl -v http://127.0.0.1:8080/

# 404 错误
curl -v http://127.0.0.1:8080/nonexistent

# 路径遍历攻击测试
curl -v http://127.0.0.1:8080/../../../etc/passwd

# 登录测试
curl -v -X POST http://127.0.0.1:8080/login \
  -d "username=admin&password=admin123" -c cookies.txt

# 认证访问
curl -v -b cookies.txt http://127.0.0.1:8080/admin/

# 并发测试
for i in {1..30}; do
  curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/ &
done
wait
```

---

## 🛡️ 安全特性

1. **路径遍历防御**: 拒绝 `..` 路径，realpath 验证
2. **请求大小限制**: 请求行 8KB、头部 64KB、上传 10MB (可配置)
3. **Session 安全**: HMAC-SHA256 签名、过期时间、HttpOnly Cookie
4. **连接超时**: 默认 30 秒，防止慢速连接攻击
5. **安全头部**: `X-Content-Type-Options: nosniff`
6. **编码兼容**: POST 表单参数自动识别 UTF-8/GBK，支持中文文件名

---

## 🔮 扩展方向

以下功能在当前版本中未实现，但架构已预留扩展点：

- [ ] HTTPS/TLS 支持
- [ ] Transfer-Encoding: chunked 完整支持
- [ ] Gzip/Deflate 压缩
- [ ] CGI/FastCGI 支持
- [ ] WebSocket 升级
- [ ] 正则路由匹配
- [ ] 密码 bcrypt 哈希存储
- [x] ~~管理面板（文件管理/日志/仪表盘）~~ ✅ 已实现
- [x] ~~公网部署（Cloudflare Tunnel）~~ ✅ 已实现
- [x] ~~中文文件名支持（UTF-8/GBK 自动识别）~~ ✅ 已实现
- [ ] Keep-Alive 长连接
- [ ] 虚拟主机支持

---

## 📄 许可证

本项目为课程设计项目，仅供学习和教育用途。
