#!/usr/bin/env python3
"""
SimpleWebServer — 基于 HTTP/1.1 协议的简易 Web 服务器。

=== 功能特性 ===
- 支持 HTTP/1.1 协议 (GET 和 POST 请求)
- 多线程并发处理 (基于 ThreadPoolExecutor)
- 静态文件服务和 MIME 类型自动识别
- 自定义错误页面 (404, 403, 500 等)
- 用户认证: HTTP Basic Auth 或 Session 表单登录
- Common Log Format 访问日志
- 文件上传功能
- 路径遍历攻击防御
- 多种安全头部自动添加

=== 使用方法 ===
    # 使用默认配置启动
    python server.py

    # 指定配置文件
    python server.py --config myconfig.json

    # 指定端口
    python server.py --port 9000

=== 架构概述 ===
    客户端 ──> socket.accept() ──> ThreadPoolExecutor ──> ClientHandler
                                                              │
                                   ┌──────────────────────────┘
                                   ▼
                             HttpParser.parse()     ──> HttpRequest
                                   ▼
                             Auth.authenticate()    ──> AuthResult
                                   ▼
                             Router.dispatch()      ──> DispatchResult
                                   ▼
                             ResponseBuilder.build() ──> HttpResponse
                                   ▼
                             socket.sendall()
                                   ▼
                             Logger.log()
"""

import argparse
import json
import os
import socket
import subprocess
import sys
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from typing import Optional

# 导入项目模块
from http_parser import HttpParser, HttpParseError, HttpMethod
from http_response import HttpResponse, ResponseBuilder
from router import Router, DispatchResult
from admin_panel import ServerStats, AdminPanel
from auth import (
    AuthResult,
    Authenticator,
    BasicAuthAuthenticator,
    SessionAuthAuthenticator,
)
from error_handler import ErrorHandler
from logger import AccessLogger


# ============================================================================
# 默认配置
# ============================================================================

DEFAULT_CONFIG = {
    "server": {
        "host": "127.0.0.1",
        "port": 8080,
        "document_root": "./static",
        "upload_dir": "./uploads",
        "max_upload_size_mb": 10,
        "max_workers": 10,
        "socket_timeout": 30,
        "backlog": 5,
    },
    "authentication": {
        "enabled": True,
        "type": "session",
        "secret_key": "change-me-to-a-random-string",
        "users": {"admin": "admin123", "user": "user123"},
        "protected_paths": ["/admin", "/upload"],
    },
    "logging": {
        "enabled": True,
        "log_file": None,
        "format": "common",
    },
}


# ============================================================================
# 服务器主类
# ============================================================================

class SimpleWebServer:
    """
    SimpleWebServer — 简易 HTTP/1.1 Web 服务器主类。

    负责:
    - 从配置文件加载运行时配置
    - 创建并绑定监听 socket
    - 初始化所有子模块（解析器、认证器、路由器等）
    - 管理线程池，实现并发请求处理
    - 优雅关闭（捕获 KeyboardInterrupt，清理资源）

    使用示例:
        server = SimpleWebServer("config.json")
        server.start()
    """

    def __init__(self, config_path: str = "config.json"):
        """
        初始化 Web 服务器。

        参数:
            config_path: JSON 配置文件路径
        """
        self._config_path = config_path
        self._config = DEFAULT_CONFIG.copy()

        # 运行时组件
        self._socket: Optional[socket.socket] = None
        self._executor: Optional[ThreadPoolExecutor] = None
        self._running = False
        self._shutdown_lock = threading.Lock()

        # 统计信息（请求计数、运行时长）
        self._stats: Optional[ServerStats] = None

        # 子模块（在 load_config 后初始化）
        self._logger: Optional[AccessLogger] = None
        self._authenticator: Optional[Authenticator] = None
        self._error_handler: Optional[ErrorHandler] = None
        self._router: Optional[Router] = None
        self._response_builder: Optional[ResponseBuilder] = None
        self._parser: Optional[HttpParser] = None
        self._admin_panel: Optional[AdminPanel] = None

    # ------------------------------------------------------------------
    # 配置管理
    # ------------------------------------------------------------------

    def load_config(self, override_port: Optional[int] = None) -> None:
        """
        加载服务器配置。

        加载顺序:
        1. 使用 DEFAULT_CONFIG 作为基础
        2. 从 JSON 文件读取用户配置（深度合并）
        3. 应用命令行覆盖参数

        参数:
            override_port: 命令行指定的端口号（覆盖配置文件）
        """
        # 尝试从 JSON 文件加载
        try:
            if os.path.isfile(self._config_path):
                with open(self._config_path, "r", encoding="utf-8") as f:
                    user_config = json.load(f)
                self._deep_merge(self._config, user_config)
                print(f"[配置] 已加载配置文件: {self._config_path}")
            else:
                print(f"[配置] 配置文件不存在 ({self._config_path})，使用默认配置")
        except json.JSONDecodeError as e:
            print(f"[警告] 配置文件 JSON 格式错误: {e}")
            print("[警告] 使用默认配置")
        except OSError as e:
            print(f"[警告] 无法读取配置文件: {e}")
            print("[警告] 使用默认配置")

        # 命令行参数覆盖
        if override_port is not None:
            self._config["server"]["port"] = override_port

        # 确保上传目录存在
        upload_dir = os.path.abspath(self._config["server"].get("upload_dir", "./uploads"))
        os.makedirs(upload_dir, exist_ok=True)
        self._config["server"]["upload_dir"] = upload_dir

        # 确保文档根目录存在
        doc_root = os.path.abspath(self._config["server"].get("document_root", "./static"))
        os.makedirs(doc_root, exist_ok=True)
        self._config["server"]["document_root"] = doc_root

    def _deep_merge(self, base: dict, override: dict) -> None:
        """
        深度合并两个字典。

        将 override 中的值递归合并到 base 中。
        对于嵌套字典，递归合并；对于其他类型，直接覆盖。
        """
        for key, value in override.items():
            if key in base and isinstance(base[key], dict) and isinstance(value, dict):
                self._deep_merge(base[key], value)
            else:
                base[key] = value

    # ------------------------------------------------------------------
    # 初始化和启动
    # ------------------------------------------------------------------

    def _init_modules(self) -> None:
        """
        初始化所有子模块。

        在加载配置后调用，创建各个处理器实例。
        """
        server_config = self._config["server"]
        auth_config = self._config["authentication"]
        log_config = self._config["logging"]

        # 日志记录器
        self._logger = AccessLogger(log_config)

        # 身份验证器
        if auth_config.get("enabled", False):
            auth_type = auth_config.get("type", "session")
            users = auth_config.get("users", {})
            if auth_type == "basic":
                self._authenticator = BasicAuthAuthenticator(users)
                self._logger.log_info("认证模块已启用: HTTP Basic Authentication")
            elif auth_type == "session":
                secret_key = auth_config.get("secret_key", "change-me")
                # 每次启动拼接时间戳，确保重启后旧 session 全部失效
                secret_key = f"{secret_key}:{int(time.time())}"
                self._authenticator = SessionAuthAuthenticator(users, secret_key)
                self._logger.log_info("认证模块已启用: Session 表单登录（每次启动需重新登录）")
            else:
                self._logger.log_error(f"未知的认证类型: {auth_type}，认证已禁用")
                self._authenticator = None
        else:
            self._authenticator = None
            self._logger.log_info("认证模块已禁用（公开访问模式）")

        # 错误页面处理器
        self._error_handler = ErrorHandler(server_config["document_root"])

        # URL 路由器
        self._router = Router(self._config)

        # 响应构建器
        self._response_builder = ResponseBuilder(self._error_handler, self._config)

        # HTTP 请求解析器
        self._parser = HttpParser()

        # 统计信息
        self._stats = ServerStats()

        # 管理面板渲染器
        self._admin_panel = AdminPanel(self._stats, self._config, self._logger)

    def start(self) -> None:
        """
        启动 HTTP 服务器。

        启动流程:
        1. 创建 TCP socket (IPv4)
        2. 设置 SO_REUSEADDR 选项（支持快速重启）
        3. 绑定地址和端口
        4. 开始监听
        5. 创建线程池
        6. 进入 accept 循环，将每个连接提交到线程池
        7. 捕获 KeyboardInterrupt 执行优雅关闭
        """
        server_config = self._config["server"]
        host = server_config["host"]
        port = server_config["port"]
        backlog = server_config["backlog"]
        max_workers = server_config["max_workers"]

        print("=" * 60)
        print("  SimpleWebServer/1.0 — 简易 HTTP 服务器")
        print(f"  监听地址: http://{host}:{port}")
        print(f"  文档根目录: {server_config['document_root']}")
        print(f"  上传目录: {server_config['upload_dir']}")
        print(f"  工作线程数: {max_workers}")
        auth_config = self._config["authentication"]
        if auth_config.get("enabled", False):
            print(f"  认证模式: {auth_config.get('type', 'session')}")
            print(f"  注册用户: {', '.join(auth_config.get('users', {}).keys())}")
        else:
            print(f"  认证: 已禁用")
        print("=" * 60)

        # 创建 TCP socket
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        # SO_REUSEADDR: 允许在 TIME_WAIT 状态下重新绑定端口（快速重启）
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

        try:
            self._socket.bind((host, port))
        except OSError as e:
            self._logger.log_error(f"绑定地址 {host}:{port} 失败: {e}")
            print(f"\n错误: 无法绑定端口 {port}。可能原因:")
            print(f"  1. 端口已被其他程序占用")
            print(f"  2. 权限不足（端口 < 1024 需要管理员权限）")
            print(f"  3. 请尝试更换端口: python server.py --port 9000")
            sys.exit(1)

        self._socket.listen(backlog)
        self._running = True
        self._logger.log_info(f"服务器已启动，监听 http://{host}:{port}")

        # 创建线程池
        self._executor = ThreadPoolExecutor(max_workers=max_workers)

        # 接受连接循环
        try:
            while self._running:
                try:
                    client_sock, client_addr = self._socket.accept()
                    self._logger.log_info(f"新连接: {client_addr[0]}:{client_addr[1]}")

                    # 提交到线程池处理
                    handler = ClientHandler(
                        client_socket=client_sock,
                        client_addr=client_addr,
                        config=self._config,
                        parser=self._parser,
                        authenticator=self._authenticator,
                        router=self._router,
                        response_builder=self._response_builder,
                        logger=self._logger,
                        stats=self._stats,
                        admin_panel=self._admin_panel,
                    )
                    self._executor.submit(handler.handle)

                except socket.timeout:
                    continue
                except OSError as e:
                    if self._running:
                        self._logger.log_error(f"接受连接时出错: {e}")
                    continue

        except KeyboardInterrupt:
            print("\n")
            self._logger.log_info("收到中断信号 (Ctrl+C)，正在关闭服务器...")

        finally:
            self.stop()

    def stop(self) -> None:
        """
        优雅关闭服务器。

        关闭顺序:
        1. 停止接受新连接
        2. 等待所有活跃线程完成（或超时）
        3. 关闭监听 socket
        4. 清理日志文件句柄
        """
        with self._shutdown_lock:
            if not self._running:
                return
            self._running = False

        self._logger.log_info("正在关闭服务器...")

        # 关闭监听 socket（不再接受新连接）
        if self._socket:
            try:
                self._socket.close()
            except OSError:
                pass

        # 关闭线程池（等待活跃线程完成）
        if self._executor:
            self._executor.shutdown(wait=True, cancel_futures=False)
            self._logger.log_info("所有工作线程已关闭")

        # 关闭日志
        if self._logger:
            self._logger.log_info("服务器已关闭")
            self._logger.close()

        print("服务器已成功关闭。")


# ============================================================================
# 客户端连接处理器
# ============================================================================

class ClientHandler:
    """
    客户端连接处理器 — 每个客户端连接创建一个实例，在线程池中执行。

    完整处理流程:
    1. 设置 socket 超时（防止慢速连接占用线程）
    2. 接收 HTTP 请求数据
    3. 解析 HTTP 请求 → HttpRequest 对象
    4. 认证检查 → AuthResult 对象
    5. URL 路由分发 → DispatchResult 对象
    6. 根据分发结果构建 HTTP 响应 → HttpResponse 对象
    7. 发送响应到客户端
    8. 记录访问日志
    9. 关闭连接

    错误处理:
    - HttpParseError → 返回对应的 HTTP 错误响应
    - socket.timeout → 静默关闭连接
    - 其他异常 → 返回 500 错误响应
    """

    # 最大请求大小（防止内存耗尽攻击）
    MAX_REQUEST_SIZE = 50 * 1024 * 1024  # 50 MB

    def __init__(
        self,
        client_socket: socket.socket,
        client_addr: tuple,
        config: dict,
        parser: HttpParser,
        authenticator: Optional[Authenticator],
        router: Router,
        response_builder: ResponseBuilder,
        logger: AccessLogger,
        stats: Optional[ServerStats] = None,
        admin_panel: Optional[AdminPanel] = None,
    ):
        """
        初始化客户端处理器。

        参数:
            client_socket: 已 accept 的客户端 TCP socket
            client_addr: 客户端 (ip, port) 元组
            config: 服务器配置字典
            parser: HTTP 请求解析器
            authenticator: 认证器实例（None 表示无认证）
            router: URL 路由器
            response_builder: 响应构建器
            logger: 访问日志记录器
            stats: 服务器统计信息（用于请求计数）
            admin_panel: 管理面板渲染器
        """
        self._client_socket = client_socket
        self._client_addr = client_addr
        self._client_ip = client_addr[0]
        self._config = config
        self._parser = parser
        self._authenticator = authenticator
        self._router = router
        self._response_builder = response_builder
        self._logger = logger
        self._stats = stats
        self._admin_panel = admin_panel

        server_config = config.get("server", {})
        self._timeout = server_config.get("socket_timeout", 30)
        self._upload_dir = server_config.get("upload_dir", "./uploads")
        self._max_upload_size = server_config.get("max_upload_size_mb", 10) * 1024 * 1024

    # ------------------------------------------------------------------
    # 主处理流程
    # ------------------------------------------------------------------

    def handle(self) -> None:
        """
        执行完整的请求-响应处理周期。

        这是 ClientHandler 的唯一公开方法，在线程池中执行。
        所有异常在此方法内捕获和处理，不会传播到线程池。
        """
        try:
            # 步骤 1: 设置超时
            self._client_socket.settimeout(self._timeout)

            # 步骤 2: 接收数据
            raw_data = self._receive_all()

            if not raw_data:
                return  # 空数据，静默关闭

            # 步骤 3: 解析 HTTP 请求
            try:
                request = self._parser.parse(raw_data)
            except HttpParseError as e:
                self._send_error(e.status_code)
                self._logger.log_error(
                    f"解析错误 (HTTP {e.status_code}): {e.message} [客户端: {self._client_ip}]"
                )
                return

            # 步骤 4: 认证检查
            auth_result = AuthResult(authenticated=True)  # 默认通过
            if self._authenticator:
                auth_result = self._authenticator.authenticate(request)

            # 步骤 5: 路由分发
            dispatch_result = self._router.dispatch(request, auth_result)

            # 步骤 6: 构建并发送响应
            response = self._build_response(request, dispatch_result, auth_result)

            # 步骤 7: 发送响应
            response_bytes = response.to_bytes()
            self._client_socket.sendall(response_bytes)

            # 步骤 8: 记录日志
            username = auth_result.username or "-"
            self._logger.log(
                request=request,
                status_code=response.status_code,
                response_size=len(response.body),
                client_ip=self._client_ip,
                username=username,
            )

            # 步骤 9: 递增请求计数器（排除管理面板自动刷新）
            if self._stats and not request.path.startswith("/admin"):
                self._stats.increment_request()

        except socket.timeout:
            # 连接超时 — 静默关闭，不记录为错误
            pass
        except ConnectionError:
            # 客户端断开连接 — 静默关闭
            pass
        except Exception:
            # 未预期的错误 — 尝试返回 500 并记录
            traceback.print_exc()
            try:
                self._send_error(500)
            except Exception:
                pass
            self._logger.log_error(
                f"处理请求时发生未知错误 [客户端: {self._client_ip}]"
            )
        finally:
            # 始终关闭连接
            try:
                self._client_socket.close()
            except OSError:
                pass

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _receive_all(self) -> bytes:
        """
        从 socket 接收完整的 HTTP 请求数据。

        使用循环接收确保获得完整数据，直到:
        - 找到头部结束标记 (\r\n\r\n) 且 body 接收完毕
        - socket 缓冲区为空（对方关闭写端或超时）

        Body 接收策略:
        - 解析 Content-Length 头来确定 body 大小
        - 最大接收 MAX_REQUEST_SIZE 字节（防止内存耗尽）

        返回:
            bytes: 完整的 HTTP 请求原始数据
        """
        data = bytearray()
        header_end = -1

        while len(data) < self.MAX_REQUEST_SIZE:
            try:
                chunk = self._client_socket.recv(4096)
                if not chunk:
                    break  # 客户端关闭了连接

                data.extend(chunk)

                # 查找头部结束位置
                if header_end == -1:
                    header_end = data.find(b"\r\n\r\n")
                    if header_end == -1:
                        continue  # 头部还未接收完

                # 检查 body 是否已接收完
                header_section = data[:header_end].decode("utf-8", errors="replace")
                content_length = 0
                for line in header_section.split("\r\n"):
                    if line.lower().startswith("content-length:"):
                        try:
                            content_length = int(line.split(":", 1)[1].strip())
                        except ValueError:
                            pass
                        break

                total_expected = header_end + 4 + content_length
                if len(data) >= total_expected:
                    break

            except socket.timeout:
                break

        return bytes(data)

    def _build_response(
        self,
        request,
        dispatch_result: DispatchResult,
        auth_result: AuthResult,
    ) -> HttpResponse:
        """
        根据路由分发结果和认证结果构建 HTTP 响应。

        这是响应构建的中央决策点，根据 dispatch_result.type 决定响应类型。

        参数:
            request: HttpRequest 对象
            dispatch_result: 路由分发结果
            auth_result: 认证结果

        返回:
            HttpResponse: 构建完成的响应对象
        """
        result_type = dispatch_result.type

        # ---- 错误响应 ----
        if result_type == "error":
            status_code = dispatch_result.status_code or 500

            # 401 需要特殊处理：附加 WWW-Authenticate 头部
            if status_code == 401:
                response = self._response_builder.build_error(401)
                for name, value in auth_result.response_headers.items():
                    response.set_header(name, value)
                return response

            return self._response_builder.build_error(status_code)

        # ---- 重定向响应 ----
        elif result_type == "redirect":
            return self._response_builder.build_redirect(
                location=dispatch_result.location or "/",
                status=dispatch_result.status_code or 302,
            )

        # ---- 文件响应 ----
        elif result_type == "file":
            if dispatch_result.file_path:
                return self._response_builder.build_file_response(dispatch_result.file_path)
            return self._response_builder.build_error(404)

        # ---- POST 处理 (登录、上传等) ----
        elif result_type == "post_upload":
            return self._handle_post_action(request, dispatch_result)

        # ---- 注销处理 ----
        elif result_type == "auth_logout":
            return self._handle_logout(request)

        # ---- 管理面板页面 ----
        elif result_type == "admin_page":
            return self._handle_admin_page(request, dispatch_result, auth_result)

        # ---- 管理面板操作 (删除文件等) ----
        elif result_type == "admin_action":
            return self._handle_admin_action(request, dispatch_result, auth_result)

        # ---- 未知类型 ----
        else:
            return self._response_builder.build_error(500, f"未知分发类型: {result_type}")

    def _handle_post_action(self, request, dispatch_result: DispatchResult) -> HttpResponse:
        """
        处理 POST 请求的具体操作。

        支持的操作:
        - login: 处理登录表单提交
        - upload: 处理文件上传

        参数:
            request: HttpRequest 对象
            dispatch_result: 包含 action 信息的分发结果

        返回:
            HttpResponse
        """
        action = dispatch_result.extra.get("action", "")

        if action == "login":
            return self._handle_login(request)

        elif action == "upload":
            return self._handle_upload(request)

        elif action == "account":
            return self._handle_account(request)

        else:
            return self._response_builder.build_error(400, "未知的 POST 操作")

    def _handle_login(self, request) -> HttpResponse:
        """
        处理用户登录表单提交。

        流程:
        1. 从 POST body 中提取 username 和 password
        2. 验证凭据
        3. 成功: 创建 session，设置 cookie，302 重定向
        4. 失败: 302 重定向回 /login?error=1

        安全: 使用恒定时间比较防止用户名枚举攻击（生产环境优化）。

        参数:
            request: HttpRequest 对象

        返回:
            HttpResponse
        """
        params = request.get_all_post_params()
        username = params.get("username", "").strip()
        password = params.get("password", "")

        if not username or not password:
            return self._response_builder.build_redirect("/login?error=1")

        # 验证凭据
        creds_valid = False
        if self._authenticator:
            creds_valid = self._authenticator.verify_credentials(username, password)
        else:
            # 认证模块被禁用时，直接用配置文件中的用户字典验证
            auth_config = self._config.get("authentication", {})
            users = auth_config.get("users", {})
            creds_valid = username in users and users[username] == password

        if creds_valid:
            # 登录成功 — 创建 session（如果使用 session 认证）
            redirect_url = params.get("next", "/admin/")

            if isinstance(self._authenticator, SessionAuthAuthenticator):
                session_token = self._authenticator.create_session(username)
                response = self._response_builder.build_redirect(redirect_url)
                response.set_cookie(
                    "session_token",
                    session_token,
                    path="/",
                    max_age=3600,
                    http_only=True,
                )
                self._logger.log_info(f"用户 '{username}' 登录成功 [IP: {self._client_ip}]")
                return response

            # Basic Auth 登录成功
            response = self._response_builder.build_redirect(redirect_url)
            self._logger.log_info(f"用户 '{username}' 登录成功 [IP: {self._client_ip}]")
            return response

        # 登录失败
        self._logger.log_info(f"用户 '{username}' 登录失败 [IP: {self._client_ip}]")
        return self._response_builder.build_redirect("/login?error=1")

    def _handle_account(self, request) -> HttpResponse:
        """
        处理账户管理操作（POST /account）。

        操作类型（由 post body 参数 action 决定）:
        - get_info: 返回当前登录用户的用户名和密码
        - change_password: 修改当前登录用户的密码
        """
        session_token = request.get_cookie("session_token", "")

        # 读取 session 中的用户名
        username = None
        if session_token and isinstance(self._authenticator, SessionAuthAuthenticator):
            username = self._authenticator.validate_session(session_token)

        if not username:
            body = json.dumps({"ok": False, "error": "未登录"}).encode("utf-8")
            resp = HttpResponse(401, body=body)
            resp.set_header("Content-Type", "application/json; charset=utf-8")
            return resp

        params = request.get_all_post_params()
        sub_action = params.get("action", "")

        # --- 获取账户信息（含密码） ---
        if sub_action == "get_info":
            auth_config = self._config.get("authentication", {})
            users = auth_config.get("users", {})
            password = users.get(username, "")
            body = json.dumps({
                "ok": True,
                "username": username,
                "password": password,
            }, ensure_ascii=False).encode("utf-8")
            resp = HttpResponse(200, body=body)
            resp.set_header("Content-Type", "application/json; charset=utf-8")
            return resp

        # --- 修改密码 ---
        elif sub_action == "change_password":
            old_password = params.get("old_password", "")
            new_password = params.get("new_password", "")

            if not old_password or not new_password:
                body = json.dumps({"ok": False, "error": "密码不能为空"}).encode("utf-8")
                resp = HttpResponse(400, body=body)
                resp.set_header("Content-Type", "application/json; charset=utf-8")
                return resp

            # 验证旧密码
            if not self._authenticator or not self._authenticator.verify_credentials(username, old_password):
                body = json.dumps({"ok": False, "error": "原密码错误"}).encode("utf-8")
                resp = HttpResponse(200, body=body)
                resp.set_header("Content-Type", "application/json; charset=utf-8")
                return resp

            # 更新密码（写入配置中的 users 字典）
            auth_config = self._config.get("authentication", {})
            users = auth_config.get("users", {})
            users[username] = new_password

            # 同时更新 authenticator 中的 users
            if self._authenticator:
                self._authenticator._users[username] = new_password

            self._logger.log_info(f"用户 '{username}' 修改了密码 [IP: {self._client_ip}]")
            body = json.dumps({"ok": True, "message": "密码修改成功"}).encode("utf-8")
            resp = HttpResponse(200, body=body)
            resp.set_header("Content-Type", "application/json; charset=utf-8")
            return resp

        else:
            body = json.dumps({"ok": False, "error": "未知操作"}).encode("utf-8")
            resp = HttpResponse(400, body=body)
            resp.set_header("Content-Type", "application/json; charset=utf-8")
            return resp

    def _handle_upload(self, request) -> HttpResponse:
        """
        处理文件上传（POST /upload）。

        支持的内容类型:
        - multipart/form-data: 标准文件上传格式
        - 原始二进制: 直接保存为文件

        安全限制:
        - 最大文件大小由配置决定
        - 文件名进行安全处理（去除路径分隔符）
        - 保存到配置的 upload_dir 目录

        参数:
            request: HttpRequest 对象

        返回:
            HttpResponse
        """
        content_type = request.get_header("content-type", "")

        try:
            if "multipart/form-data" in content_type:
                # 解析 multipart 表单数据，提取文件
                boundary = self._extract_boundary(content_type)
                if not boundary:
                    return self._response_builder.build_post_response(
                        "upload_fail", {"error": "无法解析上传表单的 boundary"}
                    )

                files = self._parse_multipart(request.body, boundary)
                if not files:
                    return self._response_builder.build_post_response(
                        "upload_fail", {"error": "未找到上传的文件，请确保选择了文件"}
                    )

                # 保存第一个文件（简化实现，多文件上传可扩展）
                filename, file_data = files[0]
                safe_filename = self._sanitize_filename(filename)
                save_path = os.path.join(self._upload_dir, safe_filename)

                with open(save_path, "wb") as f:
                    f.write(file_data)

                self._logger.log_info(
                    f"文件上传成功: {safe_filename} "
                    f"({len(file_data)} 字节) [IP: {self._client_ip}]"
                )
                return self._response_builder.build_post_response(
                    "upload_success", {"filename": safe_filename}
                )

            else:
                # 原始二进制数据（非 multipart）
                if not request.body:
                    return self._response_builder.build_post_response(
                        "upload_fail", {"error": "请求体为空"}
                    )

                if len(request.body) > self._max_upload_size:
                    return self._response_builder.build_post_response(
                        "upload_fail",
                        {"error": f"文件大小超过限制 ({self._max_upload_size // (1024*1024)} MB)"},
                    )

                # 生成唯一文件名
                filename = f"upload_{int(time.time())}.bin"
                save_path = os.path.join(self._upload_dir, filename)

                with open(save_path, "wb") as f:
                    f.write(request.body)

                self._logger.log_info(
                    f"原始数据上传成功: {filename} "
                    f"({len(request.body)} 字节) [IP: {self._client_ip}]"
                )
                return self._response_builder.build_post_response(
                    "upload_success", {"filename": filename}
                )

        except OSError as e:
            self._logger.log_error(f"文件上传失败: {e}")
            return self._response_builder.build_post_response(
                "upload_fail", {"error": str(e)}
            )

    def _handle_logout(self, request) -> HttpResponse:
        """
        处理用户注销。

        清除 session token cookie 并将用户重定向到首页。

        参数:
            request: HttpRequest 对象

        返回:
            HttpResponse: 带有清除 cookie 指令的重定向响应
        """
        session_token = request.get_cookie("session_token", "")
        if session_token and isinstance(self._authenticator, SessionAuthAuthenticator):
            self._authenticator.destroy_session(session_token)

        self._logger.log_info(f"用户注销 [IP: {self._client_ip}]")
        return self._response_builder.build_post_response("logout", {})

    def _handle_admin_page(self, request, dispatch_result: DispatchResult, auth_result: AuthResult) -> HttpResponse:
        """
        处理管理面板页面请求。

        根据 dispatch_result.extra["section"] 渲染对应的管理面板子页面。

        参数:
            request: HttpRequest 对象
            dispatch_result: 路由分发结果（type="admin_page"）
            auth_result: 认证结果（用于提取真实用户名）

        返回:
            HttpResponse
        """
        if not self._admin_panel:
            return self._response_builder.build_error(500, "管理面板未初始化")

        section = dispatch_result.extra.get("section", "dashboard")
        username = auth_result.username or "管理员"

        if section == "dashboard":
            html = self._admin_panel.render_dashboard(username)
        elif section == "logs":
            # 支持 ?lines=N 查询参数
            lines_param = request.query_params.get("lines", "200")
            try:
                lines = int(lines_param)
                lines = max(10, min(lines, 1000))  # 限制 10-1000 行
            except ValueError:
                lines = 200
            html = self._admin_panel.render_logs(username, lines)
        elif section == "files":
            html = self._admin_panel.render_files(username)
        else:
            return self._response_builder.build_error(404, f"未知管理面板页面: {section}")

        body = html.encode("utf-8")
        response = HttpResponse(200, body=body)
        response.set_header("Content-Type", "text/html; charset=utf-8")
        return response

    def _handle_admin_action(
        self, request, dispatch_result: DispatchResult, auth_result: AuthResult
    ) -> HttpResponse:
        """
        处理管理面板的操作请求（如删除文件）。

        仅在用户已认证的情况下允许操作。

        参数:
            request: HttpRequest 对象
            dispatch_result: 路由分发结果（type="admin_action"）
            auth_result: 认证结果

        返回:
            HttpResponse（通常是重定向）
        """
        # 安全检查: 必须已认证
        if not auth_result.authenticated:
            return self._response_builder.build_error(403)

        action = dispatch_result.extra.get("action", "")

        if action == "delete_file":
            filename = dispatch_result.extra.get("filename", "")
            if not filename:
                return self._response_builder.build_redirect("/admin/files")

            # 安全检查: 清理文件名
            safe_name = self._sanitize_filename(filename)
            file_path = os.path.join(self._upload_dir, safe_name)

            # 验证路径在上传目录内
            try:
                real_upload = os.path.realpath(self._upload_dir)
                real_file = os.path.realpath(file_path)
                if not real_file.startswith(real_upload + os.sep):
                    self._logger.log_info(
                        f"管理面板: 拒绝删除文件（路径不安全）: {safe_name} [IP: {self._client_ip}]"
                    )
                    return self._response_builder.build_redirect("/admin/files")
            except (ValueError, OSError):
                return self._response_builder.build_redirect("/admin/files")

            if os.path.isfile(file_path):
                try:
                    file_size = os.path.getsize(file_path)
                    os.remove(file_path)
                    self._logger.log_info(
                        f"管理面板: 删除文件 {safe_name} ({file_size} 字节) [IP: {self._client_ip}]"
                    )
                except OSError as e:
                    self._logger.log_error(f"管理面板: 删除文件失败 {safe_name}: {e}")
            else:
                self._logger.log_error(
                    f"管理面板: 删除失败，文件不存在: {safe_name}"
                )

            return self._response_builder.build_redirect("/admin/files")

        if action == "view_file":
            filename = dispatch_result.extra.get("filename", "")
            if not filename:
                return self._response_builder.build_error(400, "缺少文件名参数")

            safe_name = self._sanitize_filename(filename)
            file_path = os.path.join(self._upload_dir, safe_name)

            # 验证路径在上传目录内
            try:
                real_upload = os.path.realpath(self._upload_dir)
                real_file = os.path.realpath(file_path)
                if not real_file.startswith(real_upload + os.sep):
                    return self._response_builder.build_error(403, "路径不安全")
            except (ValueError, OSError):
                return self._response_builder.build_error(403, "路径解析失败")

            if not os.path.isfile(file_path):
                return self._response_builder.build_error(404, f"文件不存在: {safe_name}")

            # 判断文件类型：尝试按文本读取，成功则预览，失败则下载
            # 不依赖扩展名，按文件内容自动识别
            max_preview_size = 10 * 1024 * 1024  # 超过 10MB 的文本文件直接下载
            file_size = os.path.getsize(file_path)

            if file_size > max_preview_size:
                # 文件太大，直接下载
                return self._response_builder.build_file_response(file_path)

            try:
                is_text = False
                content = ""
                with open(file_path, "rb") as f:
                    raw = f.read()
                # 先尝试 UTF-8 解码
                try:
                    content = raw.decode("utf-8")
                    is_text = True
                except UnicodeDecodeError:
                    try:
                        content = raw.decode("gbk")
                        is_text = True
                    except UnicodeDecodeError:
                        is_text = False

                if is_text:
                    # 文本文件 → 渲染预览页面
                    size_str = self._admin_panel._format_size(file_size) if self._admin_panel else str(file_size)
                    html = self._admin_panel.render_file_view(safe_name, content, size_str)
                    return HttpResponse(200, body=html.encode("utf-8"))
                else:
                    # 二进制文件 → 直接下载
                    return self._response_builder.build_file_response(file_path)
            except OSError:
                return self._response_builder.build_error(500, "读取文件失败")

        if action == "run_file":
            filename = dispatch_result.extra.get("filename", "")
            if not filename:
                return self._response_builder.build_error(400, "缺少文件名参数")

            safe_name = self._sanitize_filename(filename)
            file_path = os.path.join(self._upload_dir, safe_name)

            # 验证路径在上传目录内
            try:
                real_upload = os.path.realpath(self._upload_dir)
                real_file = os.path.realpath(file_path)
                if not real_file.startswith(real_upload + os.sep):
                    return self._response_builder.build_error(403, "路径不安全")
            except (ValueError, OSError):
                return self._response_builder.build_error(403, "路径解析失败")

            if not os.path.isfile(file_path):
                return self._response_builder.build_error(404, f"文件不存在: {safe_name}")

            # 判断文件类型是否可执行
            executable_extensions = {".py", ".exe", ".bat", ".cmd", ".com"}
            _, ext = os.path.splitext(safe_name.lower())
            if ext not in executable_extensions:
                return self._response_builder.build_error(400, f"不支持运行此文件类型: {ext}")

            # 最大执行时间 60 秒，防止死循环
            timeout = 60

            exit_msg = ""

            try:
                if ext == ".exe" or ext == ".com":
                    # .exe / .com：在新终端窗口中启动，这样用户可以交互和看到画面
                    subprocess.Popen(
                        [file_path],
                        creationflags=subprocess.CREATE_NEW_CONSOLE,
                        cwd=self._upload_dir,
                    )
                    stdout = ""
                    stderr = ""
                    returncode = -1
                    timed_out = False
                    exit_msg = "<div class='run-exit-info'>🖥 程序已在新终端窗口启动，请在桌面上查看。服务器不等待程序退出。</div>"

                elif ext == ".py":
                    python_exe = sys.executable
                    proc = subprocess.run(
                        [python_exe, file_path],
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        cwd=self._upload_dir,
                    )
                    stdout = proc.stdout or ""
                    stderr = proc.stderr or ""
                    returncode = proc.returncode
                    timed_out = False
                    if returncode == 0 and stdout.strip() == "" and stderr.strip() == "":
                        exit_msg = "<div class='run-exit-info'>💡 程序无输出。如果是 GUI 程序（tkinter/PyQt），窗口已在桌面启动。</div>"
                    elif returncode == 0:
                        exit_msg = "<div class='run-exit-ok'>✅ 程序正常退出（退出码 0）</div>"
                    else:
                        exit_msg = f"<div class='run-exit-error'>⚠ 程序异常退出（退出码 {returncode}）</div>"
                else:
                    # .bat / .cmd：有超时执行
                    proc = subprocess.run(
                        [file_path],
                        capture_output=True,
                        text=True,
                        timeout=timeout,
                        cwd=self._upload_dir,
                    )
                    stdout = proc.stdout or ""
                    stderr = proc.stderr or ""
                    returncode = proc.returncode
                    timed_out = False
                    if returncode == 0:
                        exit_msg = "<div class='run-exit-ok'>✅ 程序正常退出（退出码 0）</div>"
                    else:
                        exit_msg = f"<div class='run-exit-error'>⚠ 程序异常退出（退出码 {returncode}）</div>"

            except subprocess.TimeoutExpired:
                stdout = f"(程序执行超过 {timeout} 秒，已被终止)"
                stderr = ""
                exit_msg = f"<div class='run-exit-timeout'>⏱ 执行超时（{timeout} 秒限制）</div>"
            except FileNotFoundError:
                stdout = ""
                stderr = f"无法找到可执行文件: {safe_name}"
                exit_msg = "<div class='run-exit-error'>⚠ 文件未找到</div>"
            except OSError as e:
                stdout = ""
                stderr = f"执行出错: {e}"
                exit_msg = "<div class='run-exit-error'>⚠ 执行失败</div>"

            html = self._admin_panel.render_file_run(
                filename=safe_name,
                stdout=stdout,
                stderr=stderr,
                exit_msg=exit_msg,
            )
            return HttpResponse(200, body=html.encode("utf-8"))

        return self._response_builder.build_error(400, "未知的管理操作")

    def _send_error(self, status_code: int) -> None:
        """
        快速发送错误响应（用于解析失败等早期错误）。

        在无法构建完整 HttpRequest 时使用此方法直接发送错误。

        参数:
            status_code: HTTP 错误状态码
        """
        try:
            error_body = self._error_handler.get_error_page(status_code)
            response = HttpResponse(status_code, body=error_body)
            self._client_socket.sendall(response.to_bytes())
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------

    def _extract_boundary(self, content_type: str) -> str:
        """
        从 Content-Type 头部提取 multipart boundary。

        格式: multipart/form-data; boundary=----WebKitFormBoundaryXXXX

        参数:
            content_type: Content-Type 头部值

        返回:
            str: boundary 字符串（不带前缀的 "--"）
        """
        for part in content_type.split(";"):
            part = part.strip()
            if part.lower().startswith("boundary="):
                return part.split("=", 1)[1].strip('"').strip("'")
        return ""

    def _parse_multipart(self, body: bytes, boundary: str) -> list:
        """
        解析 multipart/form-data 请求体，提取上传文件。

        简化的 multipart 解析器：
        - 按 boundary 分割各部分
        - 从各部分的头部提取 filename
        - 提取文件内容

        注意: 这是一个教育用途的简化实现。生产环境应使用
        Python 标准库的 email.parser 或 cgi 模块进行完整解析。

        参数:
            body: 请求体原始字节
            boundary: multipart boundary 字符串

        返回:
            list[tuple[str, bytes]]: [(filename, file_data), ...]
        """
        files = []
        boundary_bytes = boundary.encode("utf-8")
        delimiter = b"--" + boundary_bytes + b"\r\n"
        end_delimiter = b"--" + boundary_bytes + b"--"

        # 按 boundary 分割请求体
        parts = body.split(delimiter)
        for part in parts:
            if not part or part.startswith(b"--"):
                continue

            # 去除末尾的 boundary 标记
            end_pos = part.rfind(b"\r\n" + end_delimiter[: len(boundary_bytes) + 4])
            if end_pos != -1:
                part = part[:end_pos]

            # 分离头部和体（先分离再做 strip，避免把 header 后的空行吃掉）
            header_end = part.find(b"\r\n\r\n")
            if header_end == -1:
                continue

            header_bytes = part[:header_end]
            file_data = part[header_end + 4:]

            # 去除尾部的 boundary 结尾标记和多余 \r\n
            end_delim_pos = file_data.rfind(b"\r\n--")
            if end_delim_pos != -1:
                file_data = file_data[:end_delim_pos]
            file_data = file_data.rstrip(b"\r\n")

            # 从头部提取 filename
            header_text = header_bytes.decode("utf-8", errors="replace")
            filename = "uploaded_file"
            for header_line in header_text.split("\r\n"):
                if "filename=" in header_line:
                    # 提取 filename="..." 中的文件名
                    fn_start = header_line.find('filename="')
                    if fn_start != -1:
                        fn_start += 10  # 跳过 filename="
                        fn_end = header_line.find('"', fn_start)
                        if fn_end != -1:
                            filename = header_line[fn_start:fn_end]
                            break

            # 允许空文件上传
            if filename != "uploaded_file":
                files.append((filename, file_data))

        return files

    def _sanitize_filename(self, filename: str) -> str:
        """
        清理文件名，移除危险字符。

        安全处理:
        - 去除路径分隔符（/ 和 \）
        - 保留扩展名
        - 空文件名替换为 "unnamed"
        - 截断过长的文件名

        参数:
            filename: 原始文件名

        返回:
            str: 安全的文件名
        """
        # 去除路径分隔符
        filename = filename.replace("/", "_").replace("\\", "_")

        # 去除文件名前后的空白
        filename = filename.strip()

        # 空文件名处理
        if not filename:
            filename = f"upload_{int(time.time())}"

        # 截断过长文件名（保留扩展名）
        max_name_len = 200
        if len(filename) > max_name_len:
            name_parts = filename.rsplit(".", 1)
            if len(name_parts) > 1:
                filename = name_parts[0][:max_name_len - len(name_parts[1]) - 1] + "." + name_parts[1]
            else:
                filename = filename[:max_name_len]

        return filename


# ============================================================================
# 命令行接口
# ============================================================================

def parse_args() -> argparse.Namespace:
    """
    解析命令行参数。

    可用参数:
    --config, -c:  指定 JSON 配置文件路径
    --port, -p:    指定监听端口（覆盖配置文件）

    返回:
        Namespace: 解析后的参数
    """
    parser = argparse.ArgumentParser(
        description="SimpleWebServer/1.0 — 基于 HTTP/1.1 的简易 Web 服务器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用示例:
  python server.py                          # 使用默认配置
  python server.py --config myconfig.json   # 指定配置文件
  python server.py --port 9000              # 指定监听端口
        """,
    )
    parser.add_argument(
        "-c", "--config",
        default="config.json",
        help="JSON 配置文件路径 (默认: config.json)",
    )
    parser.add_argument(
        "-p", "--port",
        type=int,
        default=None,
        help="监听端口 (默认: 8080, 覆盖配置文件)",
    )
    return parser.parse_args()


# ============================================================================
# 主入口
# ============================================================================

def main():
    """
    SimpleWebServer 主入口函数。

    启动流程:
    1. 解析命令行参数
    2. 创建服务器实例
    3. 加载配置
    4. 初始化模块
    5. 启动服务器
    """
    args = parse_args()

    # 确定配置文件路径（相对于当前工作目录）
    config_path = args.config
    if not os.path.isabs(config_path):
        # 默认在服务器脚本所在目录寻找配置文件
        script_dir = os.path.dirname(os.path.abspath(__file__))
        config_path = os.path.join(script_dir, config_path)

    # 创建并启动服务器
    server = SimpleWebServer(config_path)
    server.load_config(override_port=args.port)
    server._init_modules()
    server.start()


if __name__ == "__main__":
    main()
