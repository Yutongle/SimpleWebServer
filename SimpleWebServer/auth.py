"""
用户认证模块 — 支持两种认证方式：HTTP Basic Authentication 和 Session 表单登录。

=== HTTP Basic Authentication (RFC 7617) ===
最简单的 HTTP 认证方式。浏览器弹出原生登录对话框，凭据经 Base64 编码
后通过 Authorization 请求头发送。适用于简单的 API 保护场景。

流程:
    1. 客户端首次访问受保护资源
    2. 服务器返回 401 + WWW-Authenticate: Basic realm="..."
    3. 客户端弹出登录对话框
    4. 后续请求携带 Authorization: Basic <base64(user:pass)>
    5. 服务器解码并验证凭据

=== Session 表单登录认证 ===
基于 Cookie 的会话认证，提供美观的 HTML 登录页面。
Session token 使用 HMAC-SHA256 签名，防篡改。

流程:
    1. 用户访问受保护资源 → 302 重定向到 /login
    2. GET /login → 返回登录表单 HTML
    3. 用户填写用户名密码，POST /login
    4. 服务器验证 → 成功: 生成 session token, 302 到目标页
                   → 失败: 302 回 /login?error=1
    5. 后续请求携带 session cookie → 验证 token → 允许/拒绝访问
    6. GET /logout → 清除 session cookie, 302 到首页

=== 安全特性 ===
- Session token 使用 HMAC-SHA256 签名，防止伪造和篡改
- Session 有过期时间（默认 1 小时）
- Cookie 设置 HttpOnly 标志，防止 XSS 窃取
- Session 存储使用 threading.Lock 保证线程安全
"""

import hashlib
import hmac
import base64
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class AuthResult:
    """
    认证检查的结果。

    字段说明:
        authenticated:  是否已成功认证
        username:       认证后的用户名（未认证时为 None）
        need_login:     是否需要展示登录页面（用于 session 认证）
        response_headers: 需要附加到 HTTP 响应中的头部
                         如 WWW-Authenticate (Basic Auth) 或 Set-Cookie (Session Auth)
    """
    authenticated: bool = False
    username: Optional[str] = None
    need_login: bool = False
    response_headers: dict = field(default_factory=dict)


# ============================================================================
# 认证器抽象基类
# ============================================================================

class Authenticator(ABC):
    """
    认证器抽象基类。

    所有认证方式（Basic Auth、Session Auth、将来可能的 JWT 等）
    都继承此类并实现 authenticate() 方法。
    """

    @abstractmethod
    def authenticate(self, request) -> AuthResult:
        """
        验证 HTTP 请求的认证状态。

        参数:
            request: HttpRequest 对象

        返回:
            AuthResult: 认证结果
        """
        ...

    @abstractmethod
    def verify_credentials(self, username: str, password: str) -> bool:
        """
        验证用户名和密码是否匹配。

        参数:
            username: 用户名
            password: 密码

        返回:
            bool: 凭据是否有效
        """
        ...


# ============================================================================
# HTTP Basic Authentication
# ============================================================================

class BasicAuthAuthenticator(Authenticator):
    """
    HTTP Basic Authentication (RFC 7617) 认证器。

    凭据验证:
    - 从 Authorization 请求头中提取 Base64 编码的凭据
    - 格式: Basic base64(username:password)
    - 与配置中存储的用户凭据比对

    使用示例:
        auth = BasicAuthAuthenticator({"admin": "admin123", "user": "user123"})
        result = auth.authenticate(request)
        if not result.authenticated:
            # result.response_headers 中包含 WWW-Authenticate 头部
    """

    def __init__(self, users: dict):
        """
        初始化 Basic Auth 认证器。

        参数:
            users: 用户名→密码的映射字典
        """
        self._users = users

    def authenticate(self, request) -> AuthResult:
        """
        验证 HTTP Basic Auth 凭据。

        从 Authorization 请求头中提取并验证凭据。
        如果未提供凭据或凭据无效，返回 401 响应头部信息。
        """
        auth_header = request.get_header("authorization", "")

        # 未提供 Authorization 头部
        if not auth_header:
            return AuthResult(
                authenticated=False,
                response_headers={
                    "WWW-Authenticate": 'Basic realm="SimpleWebServer 受限区域", charset="UTF-8"'
                },
            )

        # 验证认证类型为 Basic
        if not auth_header.startswith("Basic "):
            return AuthResult(
                authenticated=False,
                response_headers={
                    "WWW-Authenticate": 'Basic realm="SimpleWebServer 受限区域", charset="UTF-8"'
                },
            )

        # 解码 Base64 凭据
        try:
            encoded = auth_header[6:]  # 去掉 "Basic " 前缀
            decoded = base64.b64decode(encoded).decode("utf-8")
            if ":" not in decoded:
                return self._unauthorized()

            username, password = decoded.split(":", 1)
        except (base64.binascii.Error, UnicodeDecodeError, ValueError):
            return self._unauthorized()

        # 验证凭据
        if self.verify_credentials(username, password):
            return AuthResult(authenticated=True, username=username)

        return self._unauthorized()

    def verify_credentials(self, username: str, password: str) -> bool:
        """检查用户名和密码是否匹配"""
        return username in self._users and self._users[username] == password

    def _unauthorized(self) -> AuthResult:
        """构建未授权响应"""
        return AuthResult(
            authenticated=False,
            response_headers={
                "WWW-Authenticate": 'Basic realm="SimpleWebServer 受限区域", charset="UTF-8"'
            },
        )


# ============================================================================
# Session 表单登录认证
# ============================================================================

class SessionAuthAuthenticator(Authenticator):
    """
    基于 Session Cookie 的表单登录认证器。

    Session Token 格式（自包含、防篡改）:
        base64(username:expiry_timestamp:hmac_signature)

    这种自包含 token 设计有以下优势:
    - 无需在服务端存储 session 数据（stateless）
    - Token 自带过期时间，HMAС 签名防篡改
    - 减少服务端内存占用

    但对于主动登出/会话撤销场景，仍然维护一个黑名单集合。

    使用示例:
        auth = SessionAuthAuthenticator(
            users={"admin": "admin123"},
            secret_key="my-secret-key-change-me"
        )
        result = auth.authenticate(request)
        if result.need_login:
            # 重定向到登录页面
    """

    # Session 有效时长（秒）
    SESSION_DURATION = 3600  # 1 小时

    def __init__(self, users: dict, secret_key: str):
        """
        初始化 Session 认证器。

        参数:
            users: 用户名→密码的映射字典
            secret_key: HMAC 签名的密钥（生产环境应使用随机字符串）
        """
        self._users = users
        self._secret_key = secret_key.encode("utf-8") if isinstance(secret_key, str) else secret_key
        self._lock = threading.Lock()
        self._revoked_tokens: set[str] = set()  # 主动登出后失效的 token 集合

    # ------------------------------------------------------------------
    # 认证入口
    # ------------------------------------------------------------------

    def authenticate(self, request) -> AuthResult:
        """
        验证 Session 认证状态。

        检查步骤:
        1. 从 Cookie 中提取 session_token
        2. 验证 token 签名和过期时间
        3. 检查 token 是否已被撤销（登出）
        4. 返回认证结果
        """
        session_token = request.get_cookie("session_token", "")

        if not session_token:
            return AuthResult(authenticated=False, need_login=True)

        username = self.validate_session(session_token)
        if username:
            return AuthResult(authenticated=True, username=username)

        # Token 无效或已过期
        return AuthResult(authenticated=False, need_login=True)

    def verify_credentials(self, username: str, password: str) -> bool:
        """检查用户名和密码是否匹配"""
        return username in self._users and self._users[username] == password

    # ------------------------------------------------------------------
    # Session 管理方法
    # ------------------------------------------------------------------

    def create_session(self, username: str) -> str:
        """
        为用户创建新的 session token。

        生成的 token 格式:
            base64(username + ":" + expiry_timestamp + ":" + hmac_signature_hex)

        参数:
            username: 已通过验证的用户名

        返回:
            str: session token 字符串
        """
        expiry = int(time.time()) + self.SESSION_DURATION
        payload = f"{username}:{expiry}"

        # HMAC-SHA256 签名
        signature = hmac.new(
            self._secret_key,
            payload.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        token_data = f"{username}:{expiry}:{signature}"
        token = base64.urlsafe_b64encode(token_data.encode("utf-8")).decode("utf-8")
        return token

    def validate_session(self, token: str) -> Optional[str]:
        """
        验证 session token 的有效性。

        验证步骤:
        1. Base64 解码 token
        2. 解析 username, expiry, signature
        3. 验证 HMAC 签名
        4. 验证是否已过期
        5. 验证是否已被撤销

        参数:
            token: session token 字符串

        返回:
            str | None: 返回用户名（有效）或 None（无效）
        """
        try:
            # 解码 Base64
            token_data = base64.urlsafe_b64decode(token).decode("utf-8")
            parts = token_data.split(":", 2)
            if len(parts) != 3:
                return None

            username, expiry_str, provided_signature = parts
            expiry = int(expiry_str)

            # 重新计算签名
            payload = f"{username}:{expiry}"
            expected_signature = hmac.new(
                self._secret_key,
                payload.encode("utf-8"),
                hashlib.sha256,
            ).hexdigest()

            # 恒定时间比较签名（防止时序攻击）
            if not hmac.compare_digest(provided_signature, expected_signature):
                return None

            # 检查是否过期
            if time.time() > expiry:
                return None

            # 检查是否已被撤销（用户主动登出）
            with self._lock:
                if token in self._revoked_tokens:
                    return None

            return username

        except (base64.binascii.Error, UnicodeDecodeError, ValueError):
            return None

    def destroy_session(self, token: str) -> None:
        """
        销毁 session（用户登出时调用）。

        将 token 加入撤销集合，使其立即失效。
        即使 token 未过期，被撤销后也无法再使用。

        参数:
            token: 要销毁的 session token
        """
        with self._lock:
            self._revoked_tokens.add(token)

        # 定期清理过期的撤销记录（简单的内存管理）
        # 如果撤销集合过大，清空已过期 token
        if len(self._revoked_tokens) > 1000:
            self._cleanup_revoked()

    def _cleanup_revoked(self) -> None:
        """
        清理撤销集合中已过期的 token。

        由于 token 自带过期时间，过期的 token 即使仍在撤销集合中也无法使用。
        清理它们可以防止内存无限增长。
        """
        current_time = time.time()
        to_remove = set()
        for token in self._revoked_tokens:
            try:
                token_data = base64.urlsafe_b64decode(token).decode("utf-8")
                parts = token_data.split(":", 2)
                if len(parts) >= 2:
                    expiry = int(parts[1])
                    if current_time > expiry:
                        to_remove.add(token)
            except Exception:
                to_remove.add(token)  # 无效 token 也清理

        self._revoked_tokens -= to_remove
