# SimpleWebServer 系统设计文档

---

## 1. 项目概述

### 1.1 项目背景

本项目设计并实现一个基于 HTTP/1.1 协议的简易 Web 服务器，旨在深入理解 HTTP 协议的工作原理、TCP socket 编程、多线程并发处理以及 Web 服务器安全防护等核心概念。

### 1.2 技术选型

| 技术 | 选择 | 理由 |
|------|------|------|
| **编程语言** | Python 3.10+ | 语法简洁、socket 库完善、开发效率高 |
| **并发模型** | ThreadPoolExecutor | 比手动创建线程更安全，线程池复用减少开销 |
| **HTTP 解析** | 自研状态机 | 深入理解协议细节，可定制边界处理 |
| **认证方式** | Basic Auth + Session | 覆盖简单 API 认证和交互式 Web 登录两种场景 |
| **配置格式** | JSON | 通用、易读、Python 内置支持 |
| **日志格式** | Common Log Format | 业界标准，兼容日志分析工具 |

### 1.3 设计目标

- **教育性**: 从 socket 层面实现 HTTP 协议，不使用高层框架
- **实用性**: 支持静态网站托管、文件上传、用户认证等常用功能
- **安全性**: 防御常见 Web 攻击（路径遍历、慢速连接、内存耗尽）
- **可扩展性**: 模块化设计，便于添加新功能
- **可测试性**: 每个模块独立可测，提供完整的测试脚本

---

## 2. 系统架构

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────────────┐
│                      SimpleWebServer                        │
│                                                             │
│  ┌──────────┐    ┌──────────┐    ┌──────────────────────┐  │
│  │ config   │    │  main()  │    │  SimpleWebServer     │  │
│  │ .json    │───▶│  入口    │───▶│  ├─ load_config()    │  │
│  └──────────┘    └──────────┘    │  ├─ _init_modules() │  │
│                                  │  └─ start()         │  │
│                                  └─────────┬────────────┘  │
│                                            │               │
│                            ThreadPoolExecutor              │
│                          ┌────────┼────────┐               │
│                          ▼        ▼        ▼               │
│                     ClientHandler  ClientHandler  ...       │
│                          │                                 │
│               ┌──────────┼──────────┐                      │
│               ▼          ▼          ▼                      │
│         HttpParser    Router    ResponseBuilder             │
│               │          │          │                      │
│               ▼          ▼          ▼                      │
│         HttpRequest  DispatchResult  HttpResponse           │
│                                            │               │
│                                     ┌──────┴──────┐       │
│                                     ▼             ▼       │
│                               Auth.auth()   ErrorHandler   │
│                                     │             │       │
│                                     ▼             ▼       │
│                                AuthResult    Error Pages    │
│                                                             │
│                         Logger.log()                        │
└─────────────────────────────────────────────────────────────┘
```

### 2.2 请求处理流程

一个完整的 HTTP 请求处理周期包含以下步骤：

```
步骤 1: socket.accept()
        └── 接受 TCP 连接

步骤 2: ThreadPoolExecutor.submit(ClientHandler)
        └── 分配到工作线程

步骤 3: socket.recv() → 接收原始字节
        └── 循环接收直到获得完整请求

步骤 4: HttpParser.parse(raw_data) → HttpRequest
        ├── 定位 CRLF CRLF (头部结束)
        ├── 解析请求行 (METHOD, PATH, VERSION)
        ├── 解析请求头 (key: value)
        ├── 解析请求体 (根据 Content-Length)
        ├── 解析 Cookie
        └── 解析查询字符串

步骤 5: Auth.authenticate(request) → AuthResult
        ├── Basic Auth: 从 Authorization 头提取凭据
        └── Session Auth: 从 Cookie 验证 token

步骤 6: Router.dispatch(request, auth_result) → DispatchResult
        ├── 特殊路径匹配 (/login, /logout, /upload)
        ├── 安全检查 (路径遍历防御)
        ├── 认证检查 (protected_paths)
        ├── 静态文件映射
        └── 方法验证

步骤 7: ResponseBuilder.build(dispatch_result) → HttpResponse
        ├── file → build_file_response()
        ├── error → build_error()
        ├── redirect → build_redirect()
        └── post_upload → build_post_response()

步骤 8: socket.sendall(response.to_bytes())
        └── 序列化并发送响应

步骤 9: Logger.log(request, response)
        └── 写入 Common Log Format 日志

步骤 10: socket.close()
        └── 关闭 TCP 连接
```

---

## 3. 模块详细设计

### 3.1 HTTP 请求解析器 (`http_parser.py`)

#### 3.1.1 状态机设计

```
                    ┌──────────────────────────────┐
                    │          开始                │
                    └────────────┬─────────────────┘
                                 │
                                 ▼
                    ┌────────────────────────┐
                    │   REQUEST_LINE         │
                    │   解析请求行           │
                    │   METHOD URI VERSION   │
                    └───────────┬────────────┘
                                │
                    ┌───────────▼────────────┐
                    │       验证通过?        │
                    └─────┬──────────┬───────┘
                          │ 是       │ 否
                          ▼          ▼
                    ┌──────────┐  ┌──────────────┐
                    │ HEADERS  │  │ ERROR        │
                    │ 解析头部 │  │ 400/405/505  │
                    └────┬─────┘  └──────────────┘
                         │
                ┌───────▼────────┐
                │ 验证 Host 头?  │ (仅 HTTP/1.1)
                └──┬──────────┬──┘
                   │ 是       │ 否
                   ▼          ▼
              ┌──────────┐ ┌──────────┐
              │ BODY     │ │  HEADERS │ (无 body)
              │ 读取体   │ │  完成    │
              └────┬─────┘ └────┬─────┘
                   │            │
                   ▼            ▼
              ┌──────────────────────┐
              │     COMPLETE         │
              │  返回 HttpRequest    │
              └──────────────────────┘
```

#### 3.1.2 关键设计决策

**为什么使用字节级解析而不是字符级？**

HTTP 协议是基于字节的协议，请求体可以是任意二进制数据（如文件上传）。使用 `bytes` 类型作为解析输入可以避免在处理二进制数据时出现编码错误。头部部分在必要时临时解码为字符串。

**为什么对请求行和头部设置大小限制？**

这是防止 DoS(拒绝服务)攻击的必要措施。攻击者可能发送过大的请求行或头部来耗尽服务器内存。根据 RFC 7230 的建议，本服务器限制请求行 8192 字节、请求头 65536 字节。

**分块传输编码的处理**

`Transfer-Encoding: chunked` 的完整解析较为复杂。当前版本实现了基础的 chunked 解码，完整的流式解析留作扩展方向。

### 3.2 HTTP 响应构建器 (`http_response.py`)

#### 3.2.1 响应报文格式

```
HTTP/1.1 <status-code> <reason-phrase>\r\n     ← 状态行
Server: SimpleWebServer/1.0\r\n                 ← 服务器标识
Date: Mon, 22 Jun 2026 12:00:00 GMT\r\n         ← 响应时间
Content-Type: text/html; charset=utf-8\r\n       ← 内容类型
Content-Length: 1234\r\n                         ← 内容长度
Connection: close\r\n                            ← 连接管理
X-Content-Type-Options: nosniff\r\n              ← 安全头部
Set-Cookie: session=abc123; Path=/; HttpOnly\r\n ← Cookie (可选)
\r\n                                             ← 空行分隔
<body bytes>                                     ← 响应体
```

#### 3.2.2 MIME 类型映射

服务器根据文件扩展名自动设置正确的 `Content-Type`，覆盖常见的网页、图片、文档、字体等格式。未识别的扩展名使用 `application/octet-stream`（浏览器将提示下载）。

### 3.3 认证模块 (`auth.py`)

#### 3.3.1 HTTP Basic Authentication 流程

```
客户端                          服务器
   │                              │
   │──── GET /admin/ ────────────▶│
   │                              │ 检查 Authorization 头
   │                              │ 未找到 → 401
   │◀─── 401 WWW-Authenticate ───│
   │                              │
   │──── GET /admin/ ────────────▶│
   │    Authorization: Basic      │
   │    base64(admin:admin123)    │ Base64 解码
   │                              │ 验证凭据
   │◀─── 200 OK ─────────────────│
```

#### 3.3.2 Session 认证 Token 设计

Session token 使用自包含（self-contained）设计：

```
token = base64(username + ":" + expiry_timestamp + ":" + hmac_signature)
```

**优势**:
- **无需服务端存储**: token 本身包含所有验证所需信息
- **防篡改**: HMAC-SHA256 签名确保 token 未被修改
- **自带过期**: expiry_timestamp 内置在 token 中
- **可撤销**: 维护内存中的撤销列表支持主动注销

**安全性分析**:
- JavaScrip 无法读取 Cookie（HttpOnly），防御 XSS 攻击
- HMAC 使用 `compare_digest` 恒定时间比较，防止时序攻击
- token 中不包含密码等敏感信息

### 3.4 URL 路由器 (`router.py`)

#### 3.4.1 路由决策树

```
                    request.path
                         │
          ┌──────────────┼──────────────┐
          ▼              ▼              ▼
     /login         /logout         其他路径
          │              │              │
    ┌─────┴─────┐       │         ┌────▼────┐
    ▼           ▼       ▼         ▼         ▼
   GET         POST    GET    安全检查    /upload
    │           │       │         │         │
    ▼           ▼       ▼     ┌───┴───┐    POST
  返回表单   处理登录  处理注销  │ 通过  │ 失败   │
              │       │        ▼       ▼       ▼
         ┌────┴───┐   │    认证检查   403    处理上传
         ▼        ▼   │   ┌──┴──┐
       成功     失败   │   │需要?│
         │        │    │   └┬──┬┘
         ▼        ▼    │   是│  │否
      302+      302     │    ▼  ▼
     Cookie   ?error=1  │  302/401/静态文件
                     │     │
                     ▼     ▼
                  302   静态文件路由
                 清除Cookie  │
                       ┌───┴───┐
                       ▼       ▼
                    文件存在  文件不存在
                       │       │
                       ▼       ▼
                     200     404
```

#### 3.4.2 路径遍历防御

```
防御层级:

第一层: 字符串检测
    if ".." in decoded_path → 403

第二层: 符号链接解析
    real_path = os.path.realpath(file_path)
    if not real_path.startswith(document_root) → 403

第三层: URL 编码预处理
    decoded_path = unquote(url_path)  # %2e%2e → ..
    然后进行第一层检测
```

这三层防御的组合可以有效应对:
- 直接路径遍历: `/../../../etc/passwd`
- URL 编码绕过: `/%2e%2e%2f%2e%2e%2f`
- 符号链接绕过: 静态目录下放置指向外部的符号链接

---

## 4. 并发模型

### 4.1 ThreadPoolExecutor 设计

```
                    ┌─────────────────────┐
                    │    主线程           │
                    │  accept() 循环      │
                    └────────┬────────────┘
                             │
                    ┌────────▼────────────┐
                    │  ThreadPoolExecutor │
                    │  max_workers = N    │
                    └────────┬────────────┘
                             │
              ┌──────────────┼──────────────┐
              ▼              ▼              ▼
        ┌──────────┐  ┌──────────┐  ┌──────────┐
        │ Worker 1 │  │ Worker 2 │  │ Worker N │
        │ handle() │  │ handle() │  │ handle() │
        └──────────┘  └──────────┘  └──────────┘
```

**为什么使用线程池而不是进程池？**

- 线程共享内存空间，模块实例可被所有线程复用
- 线程创建和切换开销小于进程
- I/O 密集型任务（网络收发、文件读写）适合多线程
- Python GIL 对 I/O 操作影响有限

**为什么使用线程池而不是手动创建线程？**

- 线程池自动管理线程生命周期
- 线程复用减少创建/销毁开销
- 限制并发数量防止资源耗尽
- `ThreadPoolExecutor` 是 Python 标准库的一部分，无需额外依赖

### 4.2 线程安全设计

| 模块 | 线程安全策略 |
|------|--------------|
| HttpParser | 每次请求新建实例解析，无共享状态 |
| Router | 配置在初始化后只读，无状态修改 |
| ErrorHandler | 只读缓存 + 线程安全的首次写入 |
| ResponseBuilder | 无状态（通过参数传递所有数据） |
| Auth | `threading.Lock` 保护 session 存储 |
| Logger | `threading.Lock` 保护文件写入 |

---

## 5. 安全设计

### 5.1 安全威胁模型

| 威胁类型 | 攻击方式 | 防御措施 |
|----------|----------|----------|
| 路径遍历 | `/../../../etc/passwd` | 多层检测 + realpath 验证 |
| URL 编码绕过 | `%2e%2e%2f` | 解码后再检测 |
| 内存耗尽 | 超大数据包 | 请求大小限制 (50MB) |
| 慢速连接 | Slowloris 攻击 | Socket 超时 (30s) |
| Session 伪造 | 修改 Cookie token | HMAC 签名验证 |
| Session 窃取 | XSS 读取 Cookie | HttpOnly Cookie |
| 时序攻击 | 逐字符猜测签名 | `hmac.compare_digest` 恒定时间比较 |
| MIME 嗅探 | 上传 HTML 伪装图片 | `X-Content-Type-Options: nosniff` |

### 5.2 配置中的密钥管理

生产环境中，`config.json` 中的 `secret_key` 必须更换为随机生成的字符串：

```python
import secrets
print(secrets.token_hex(32))
# 输出: 'f8a7b3c1d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a'
```

当前使用的默认密钥仅用于开发测试。

---

## 6. 性能分析

### 6.1 理论容量估算

假设每个请求处理时间约 5ms（静态文件，本地访问），线程池大小 10：

```
最大吞吐量 ≈ 线程数 / 单请求时间 = 10 / 0.005 = 2000 req/s
```

实际性能受以下因素影响：
- 网络延迟
- 文件 I/O 速度
- 客户端带宽
- Python GIL (对纯 I/O 场景影响有限)

### 6.2 压力测试建议

```bash
# 安装 ApacheBench (可选)
# ab -n 1000 -c 10 http://127.0.0.1:8080/

# 或使用 curl 简单并发测试
time for i in {1..100}; do
    curl -s -o /dev/null http://127.0.0.1:8080/ &
done
wait
```

---

## 7. 测试策略

### 7.1 测试层次

```
┌───────────────────────────────┐
│      端到端测试 (E2E)        │  ← curl 脚本 / 浏览器测试
│    tests/test_requests.sh    │
├───────────────────────────────┤
│      集成测试                 │  ← 模块间接口验证
│   (parser → router → builder)│
├───────────────────────────────┤
│      单元测试                 │  ← 各模块独立功能
│  Parser 边界值 / 认证逻辑     │
└───────────────────────────────┘
```

### 7.2 测试覆盖范围

| 模块 | 测试重点 |
|------|----------|
| http_parser | 正常请求、边界请求行长度、缺失 Host 头、非法方法 |
| router | 路径遍历、文件存在性、权限检查、认证路径匹配 |
| auth | 正确凭据、错误凭据、过期 token、伪造 token、注销 |
| error_handler | 自定义页面加载、缓存、默认页面生成 |
| server | GET/POST、重定向、错误响应、并发、超时 |

---

## 8. 扩展与改进

### 8.1 短期改进

1. **密码哈希**: 使用 `bcrypt` 或 `argon2` 替代明文存储
2. **分块传输**: 完整的 `Transfer-Encoding: chunked` 支持
3. **Keep-Alive**: HTTP 持久连接减少握手开销
4. **HTTPS**: 使用 `ssl` 标准库添加 TLS 支持
5. **Range 请求**: 支持断点续传

### 8.2 长期改进

1. **CGI/FastCGI**: 支持动态内容生成
2. **反向代理**: 转发请求到后端应用服务器
3. **负载均衡**: 多进程 + 端口复用
4. **WebSocket**: 支持双向实时通信
5. **HTTP/2**: 多路复用、头部压缩、服务器推送
6. **插件系统**: 可动态加载的中间件

---

## 9. 参考资料

- [RFC 7230 — HTTP/1.1 Message Syntax and Routing](https://www.rfc-editor.org/rfc/rfc7230)
- [RFC 7231 — HTTP/1.1 Semantics and Content](https://www.rfc-editor.org/rfc/rfc7231)
- [RFC 7617 — The 'Basic' HTTP Authentication Scheme](https://www.rfc-editor.org/rfc/rfc7617)
- [Python socket 文档](https://docs.python.org/3/library/socket.html)
- [Python concurrent.futures 文档](https://docs.python.org/3/library/concurrent.futures.html)
