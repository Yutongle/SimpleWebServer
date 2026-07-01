"""
URL 路由器 — 将 HTTP 请求映射到处理结果。

路由规则（按优先级）:
    1. 特殊路径处理:
       - GET  /login  → 返回登录页面 (login/login.html)
       - POST /login  → 处理登录表单提交
       - GET  /logout → 注销登录会话
       - POST /upload → 处理文件上传

    2. 安全验证（任意路径）:
       - 路径遍历攻击检测（..、%2e%2e%2f 等）
       - 符号链接解析后必须在 document_root 内

    3. 认证检查:
       - 检查请求路径是否在 protected_paths 列表中
       - 未认证用户重定向到 /login 或返回 401

    4. 静态文件路由（GET 请求）:
       - 路径以 "/" 结尾 → 追回 "index.html"
       - 映射到文件系统: <document_root>/<path>
       - 检查文件存在性和可读性
       - POST 请求（非登录/上传）→ 405 Method Not Allowed

安全防御:
    - 拒绝包含 ".." 的路径（路径遍历攻击）
    - 使用 os.path.realpath() 解析符号链接后验证在 document_root 内
    - URL 解码后再验证（防止 %2e%2e%2f 编码绕过）
"""

import os
import re
from dataclasses import dataclass, field
from typing import Optional
from urllib.parse import unquote

from http_parser import HttpMethod


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class DispatchResult:
    """
    路由分发结果。

    根据 type 字段的不同，其他字段的含义如下:

    type='file':
        file_path: 文件系统上的绝对路径
    type='redirect':
        location: 重定向目标 URL
        status_code: HTTP 状态码（302/301）
    type='auth_login':
        需要显示登录页面 → file_path 指向 login.html
    type='auth_logout':
        需要处理注销 → 清除 session
    type='post_upload':
        需要处理文件上传 → extra 包含上传参数
    type='error':
        status_code: HTTP 错误状态码
    """
    type: str                                    # 'file' | 'redirect' | 'auth_login' | 'auth_logout' | 'post_upload' | 'error'
    file_path: Optional[str] = None              # 文件系统路径
    status_code: Optional[int] = None            # HTTP 状态码
    location: Optional[str] = None               # 重定向目标
    extra: dict = field(default_factory=dict)    # 附加数据


# ============================================================================
# 路由器
# ============================================================================

class Router:
    """
    URL 路由器 — 核心请求分发逻辑。

    将 HTTP 请求的方法、路径等信息映射到具体的处理结果。
    根据配置的不同，路由器会结合认证模块来决定是否允许访问受保护资源。

    线程安全: Router 实例是无状态的（配置在初始化后只读），可被多线程共享。

    使用示例:
        router = Router(config)
        result = router.dispatch(request)
        if result.type == 'file':
            serve_file(result.file_path)
        elif result.type == 'error':
            return_error(result.status_code)
    """

    def __init__(self, config: dict):
        """
        初始化路由器。

        参数:
            config: 完整的服务器配置字典
        """
        server_config = config.get("server", {})
        auth_config = config.get("authentication", {})

        # 路径配置 — 解析为绝对路径
        self._document_root = os.path.abspath(server_config.get("document_root", "./static"))
        self._upload_dir = os.path.abspath(server_config.get("upload_dir", "./uploads"))
        self._max_upload_size = server_config.get("max_upload_size_mb", 10) * 1024 * 1024

        # 认证配置
        self._auth_enabled = auth_config.get("enabled", False)
        self._auth_type = auth_config.get("type", "basic")  # 'basic' 或 'session'
        self._protected_paths = auth_config.get("protected_paths", [])

    # ------------------------------------------------------------------
    # 路由分发入口
    # ------------------------------------------------------------------

    def dispatch(self, request, auth_result) -> DispatchResult:
        """
        将 HTTP 请求分发到相应的处理器。

        这是路由器的主入口方法，按优先级顺序依次检查:

        参数:
            request: HttpRequest 对象
            auth_result: AuthResult 对象（由 Authenticator.authenticate() 返回）

        返回:
            DispatchResult: 包含处理指令的结果对象
        """
        method = request.method
        path = request.path

        # ---- 第一层: 特殊路径（登录/注销） ----
        # 这些路径无论认证状态如何都可以访问

        if path == "/login":
            if method == HttpMethod.POST:
                # 处理登录表单提交
                return DispatchResult(
                    type="post_upload",  # 借用 post_upload 类型，在 server 中特殊处理
                    extra={"action": "login"},
                )
            elif method == HttpMethod.GET:
                # 如果已登录，重定向带上用户名参数（绕过 HttpOnly cookie 限制）
                # 但如果 URL 已有 user 参数，直接返回页面，避免无限重定向
                if auth_result.authenticated and auth_result.username:
                    if not request.query_params.get("user", ""):
                        return DispatchResult(
                            type="redirect",
                            location=f"/login?user={auth_result.username}",
                            status_code=302,
                        )
                # 返回登录表单页面
                login_page = os.path.join(
                    os.path.dirname(self._document_root), "login", "login.html"
                )
                return DispatchResult(type="file", file_path=login_page)

        if path == "/logout":
            if method == HttpMethod.GET:
                return DispatchResult(type="auth_logout")

        # ---- 账户管理 API (POST) ----
        if path == "/account" and method == HttpMethod.POST:
            # 获取当前登录用户信息或修改密码
            return DispatchResult(
                type="post_upload",
                extra={"action": "account"},
            )

        # ---- 文件上传页面 (GET) ----
        if path == "/upload" and method == HttpMethod.GET:
            # 需要登录才能访问上传页面
            if self._auth_enabled and not auth_result.authenticated:
                if self._auth_type == "session":
                    return DispatchResult(
                        type="redirect",
                        location="/login?next=/upload",
                        status_code=302,
                    )
                else:
                    return DispatchResult(type="error", status_code=401)
            # 已登录或认证未启用 → 返回上传页面
            upload_page = os.path.join(
                os.path.dirname(self._document_root), "login", "upload.html"
            )
            return DispatchResult(type="file", file_path=upload_page)

        # ---- Admin 管理面板路由 ----
        # 所有 /admin/* 路径需要认证

        if path == "/admin" or path == "/admin/" or path.startswith("/admin/"):
            # 认证检查：未登录用户重定向到登录页
            if self._auth_enabled and not auth_result.authenticated:
                if self._auth_type == "session":
                    return DispatchResult(
                        type="redirect",
                        location=f"/login?next={path}",
                        status_code=302,
                    )
                else:
                    return DispatchResult(type="error", status_code=401)

            # 管理面板首页仪表盘
            if path == "/admin" or path == "/admin/":
                return DispatchResult(
                    type="admin_page",
                    extra={"section": "dashboard"},
                )

            # 日志查看: /admin/logs?lines=200
            if path == "/admin/logs":
                return DispatchResult(
                    type="admin_page",
                    extra={"section": "logs"},
                )

            # 文件管理: /admin/files
            if path == "/admin/files" or path == "/admin/files/":
                return DispatchResult(
                    type="admin_page",
                    extra={"section": "files"},
                )

            # 文件删除: POST /admin/files/delete
            if path == "/admin/files/delete" and method == HttpMethod.POST:
                # 优先从 POST body 取，其次从 query string
                params = request.get_all_post_params()
                filename = params.get("filename", "")
                if not filename:
                    filename = request.query_params.get("name", "")
                if not filename:
                    return DispatchResult(
                        type="redirect",
                        location="/admin/files",
                        status_code=302,
                    )
                return DispatchResult(
                    type="admin_action",
                    extra={"action": "delete_file", "filename": filename},
                )

            # 文件查看: GET /admin/files/view?name=xxx
            if path == "/admin/files/view" and method == HttpMethod.GET:
                filename = request.query_params.get("name", "")
                return DispatchResult(
                    type="admin_action",
                    extra={"action": "view_file", "filename": filename},
                )

            # 文件运行: GET /admin/files/run?name=xxx
            if path == "/admin/files/run" and method == HttpMethod.GET:
                filename = request.query_params.get("name", "")
                return DispatchResult(
                    type="admin_action",
                    extra={"action": "run_file", "filename": filename},
                )

            # 其他 /admin/* 子路径 → 404
            return DispatchResult(type="error", status_code=404)

        # ---- 第二层: POST 文件上传 ----
        if path == "/upload" and method == HttpMethod.POST:
            return DispatchResult(
                type="post_upload",
                extra={
                    "action": "upload",
                    "upload_dir": self._upload_dir,
                    "max_size": self._max_upload_size,
                },
            )

        # ---- 第三层: 安全验证 ----
        # 防御路径遍历攻击

        security_result = self._check_path_security(path)
        if security_result is not None:
            return security_result

        # ---- 第四层: 认证检查 ----
        # 检查访问的路径是否在受保护列表中

        if self._auth_enabled and self._path_needs_auth(path):
            if not auth_result.authenticated:
                if self._auth_type == "session":
                    # Session 认证: 重定向到登录页面
                    return DispatchResult(
                        type="redirect",
                        location=f"/login?next={path}",
                        status_code=302,
                    )
                else:
                    # Basic 认证: 返回 401（由 server.py 处理）
                    return DispatchResult(type="error", status_code=401)

        # ---- 第五层: 静态文件路由 ----
        if method == HttpMethod.GET:
            return self._resolve_static_file(path)

        # ---- 第六层: 方法不允许 ----
        # 非 GET/POST 方法（已在 parse 层过滤，但 POST 到非特殊路径也返回 405）
        if method == HttpMethod.POST:
            return DispatchResult(type="error", status_code=405)

        return DispatchResult(type="error", status_code=405)

    # ------------------------------------------------------------------
    # 内部路由方法
    # ------------------------------------------------------------------

    def _resolve_static_file(self, url_path: str) -> DispatchResult:
        """
        将 URL 路径映射到文件系统路径并检查文件是否存在。

        处理逻辑:
        - "/" 或 "/dirname/" → 追加 "index.html"
        - URL 解码
        - 安全检查
        - 文件存在性检查

        参数:
            url_path: URL 路径部分（不含查询字符串）

        返回:
            DispatchResult
        """
        # 解码 URL 编码字符 (如 %20 → 空格, %2F → /)
        decoded_path = unquote(url_path)

        # 如果路径以 "/" 结尾，追加 index.html
        if decoded_path.endswith("/"):
            decoded_path = decoded_path + "index.html"

        # 构建文件系统路径
        # 去除开头的 / 以便 os.path.join 正确拼接
        relative_path = decoded_path.lstrip("/")
        file_path = os.path.join(self._document_root, relative_path)
        file_path = os.path.normpath(file_path)

        # 安全检查: 确认解析后的路径在文档根目录内
        if not self._is_safe_path(file_path):
            return DispatchResult(type="error", status_code=403)

        # 检查文件是否存在
        if not os.path.exists(file_path):
            return DispatchResult(type="error", status_code=404)

        if not os.path.isfile(file_path):
            return DispatchResult(type="error", status_code=404)

        # 检查文件是否可读
        if not os.access(file_path, os.R_OK):
            return DispatchResult(type="error", status_code=403)

        return DispatchResult(type="file", file_path=file_path)

    def _check_path_security(self, url_path: str) -> Optional[DispatchResult]:
        """
        检查 URL 路径的安全性，防止路径遍历攻击。

        攻击示例:
        - /../../../etc/passwd
        - /..%2f..%2f..%2fwindows%2fwin.ini (URL 编码绕过)
        - /....//....//etc/passwd (双写绕过)

        防御策略:
        1. 先对 URL 进行解码
        2. 拒绝包含 ".." 的路径
        3. 使用 os.path.realpath() 解析后验证在 document_root 内

        参数:
            url_path: 原始 URL 路径

        返回:
            DispatchResult | None: 不安全时返回 403，安全时返回 None
        """
        # 解码 URL 编码
        decoded = unquote(url_path)

        # 拒绝包含 ".." 的路径（最简单的防御）
        if ".." in decoded:
            return DispatchResult(type="error", status_code=403)

        # 拒绝包含 null 字节的路径
        if "\x00" in decoded:
            return DispatchResult(type="error", status_code=400)

        return None

    def _is_safe_path(self, file_path: str) -> bool:
        """
        验证文件路径是否在文档根目录内。

        使用 os.path.realpath() 解析所有符号链接后，
        检查解析后的路径是否以文档根目录为前缀。

        参数:
            file_path: 待检查的文件系统路径

        返回:
            bool: True 表示安全
        """
        try:
            real_root = os.path.realpath(self._document_root)
            real_path = os.path.realpath(file_path)
            common = os.path.commonpath([real_root, real_path])
            return common == real_root
        except (ValueError, OSError):
            return False

    def _path_needs_auth(self, url_path: str) -> bool:
        """
        检查指定路径是否需要认证。

        如果 url_path 以任何一个 protected_paths 中的路径为前缀，
        则该路径需要认证。

        参数:
            url_path: URL 路径

        返回:
            bool: True 表示需要认证
        """
        for protected in self._protected_paths:
            if url_path.startswith(protected):
                return True
        return False
