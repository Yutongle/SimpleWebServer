"""
HTTP 响应构建器 — 将内部数据结构序列化为 HTTP/1.1 响应报文。

本模块负责:
- HTTP 响应数据结构 (HttpResponse)
- MIME 类型映射 (ContentType)
- 响应报文序列化 (to_bytes)
- 常见响应的构建工厂方法

HTTP 响应报文格式:
    HTTP/1.1 <status-code> <reason-phrase>\r\n
    <header-name>: <header-value>\r\n
    ...\r\n
    \r\n
    <message-body>
"""

import os
from datetime import datetime, timezone
from typing import Optional


# ============================================================================
# MIME 类型映射
# ============================================================================

class ContentType:
    """
    根据文件扩展名确定 MIME 类型。

    用于设置 HTTP 响应头中的 Content-Type 字段。
    浏览器根据 Content-Type 决定如何渲染/处理响应内容。

    使用示例:
        mime = ContentType.from_extension("/path/to/index.html")
        # 返回: "text/html; charset=utf-8"
    """

    # 文件扩展名 → MIME 类型映射表
    MIME_TYPES = {
        # 网页文本类型
        ".html": "text/html; charset=utf-8",
        ".htm": "text/html; charset=utf-8",
        ".css": "text/css; charset=utf-8",
        ".js": "application/javascript; charset=utf-8",
        ".mjs": "application/javascript; charset=utf-8",

        # 数据交换格式
        ".json": "application/json; charset=utf-8",
        ".xml": "application/xml; charset=utf-8",
        ".csv": "text/csv; charset=utf-8",

        # 纯文本
        ".txt": "text/plain; charset=utf-8",
        ".log": "text/plain; charset=utf-8",
        ".md": "text/markdown; charset=utf-8",

        # 图片类型
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".ico": "image/x-icon",
        ".webp": "image/webp",
        ".bmp": "image/bmp",

        # 音视频类型
        ".mp3": "audio/mpeg",
        ".wav": "audio/wav",
        ".ogg": "audio/ogg",
        ".mp4": "video/mp4",
        ".webm": "video/webm",

        # 文档类型
        ".pdf": "application/pdf",
        ".zip": "application/zip",
        ".gz": "application/gzip",
        ".tar": "application/x-tar",

        # 字体类型
        ".woff": "font/woff",
        ".woff2": "font/woff2",
        ".ttf": "font/ttf",
        ".otf": "font/otf",

        # 其他常见类型
        ".wasm": "application/wasm",
    }

    # 默认 MIME 类型（二进制流，浏览器会提示下载）
    DEFAULT_TYPE = "application/octet-stream"

    @classmethod
    def from_extension(cls, file_path: str) -> str:
        """
        根据文件路径的扩展名返回对应的 MIME 类型。

        参数:
            file_path: 文件路径（如 "/static/index.html"）

        返回:
            str: MIME 类型字符串
        """
        ext = os.path.splitext(file_path)[1].lower()
        return cls.MIME_TYPES.get(ext, cls.DEFAULT_TYPE)


# ============================================================================
# HTTP 响应数据结构
# ============================================================================

class HttpResponse:
    """
    表示一个完整的 HTTP 响应报文。

    包含状态行、响应头、响应体以及 Set-Cookie 指令。
    通过 to_bytes() 方法序列化为可发送的字节流。

    使用示例:
        response = HttpResponse(200, body=b"<h1>Hello</h1>")
        response.set_header("X-Custom", "value")
        response_bytes = response.to_bytes()
    """

    # HTTP 状态码 → 标准原因短语映射 (RFC 7231 + 扩展)
    STATUS_TEXTS = {
        200: "OK",
        201: "Created",
        204: "No Content",
        301: "Moved Permanently",
        302: "Found",
        303: "See Other",
        304: "Not Modified",
        307: "Temporary Redirect",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        405: "Method Not Allowed",
        408: "Request Timeout",
        413: "Content Too Large",
        414: "URI Too Long",
        429: "Too Many Requests",
        431: "Request Header Fields Too Large",
        500: "Internal Server Error",
        501: "Not Implemented",
        502: "Bad Gateway",
        503: "Service Unavailable",
        505: "HTTP Version Not Supported",
    }

    def __init__(
        self,
        status_code: int = 200,
        body: bytes = b"",
        content_type: str = "text/html; charset=utf-8",
    ):
        """
        创建一个 HTTP 响应对象。

        参数:
            status_code: HTTP 状态码 (默认 200)
            body: 响应体字节数据
            content_type: Content-Type 头部值
        """
        self.status_code = status_code
        self.status_text = self.STATUS_TEXTS.get(status_code, "Unknown")
        self.body = body
        self._headers: dict = {}
        self._cookies: list = []  # 每项为 (name, value, attributes_dict)

        # 设置默认头部
        self._headers["Content-Type"] = content_type
        self._headers["Server"] = "SimpleWebServer/1.0"
        self._headers["Connection"] = "close"

    # ------------------------------------------------------------------
    # 头部操作方法
    # ------------------------------------------------------------------

    def set_header(self, name: str, value: str):
        """设置或覆盖一个响应头"""
        self._headers[name] = value

    def set_cookie(
        self,
        name: str,
        value: str,
        path: str = "/",
        max_age: Optional[int] = None,
        http_only: bool = True,
        secure: bool = False,
        same_site: str = "Lax",
    ):
        """
        添加一个 Set-Cookie 指令。

        参数:
            name: Cookie 名称
            value: Cookie 值
            path: Cookie 有效路径 (默认 "/")
            max_age: 有效时间，秒 (None = 会话结束时过期)
            http_only: 设为 True 防止 JavaScript 读取 (XSS 防御)
            secure: 设为 True 仅通过 HTTPS 传输
            same_site: SameSite 属性 ("Strict", "Lax", "None")
        """
        attrs = {"Path": path, "SameSite": same_site}
        if max_age is not None:
            attrs["Max-Age"] = str(max_age)
        if http_only:
            attrs["HttpOnly"] = None  # 无值属性
        if secure:
            attrs["Secure"] = None
        self._cookies.append((name, value, attrs))

    def clear_cookie(self, name: str, path: str = "/"):
        """
        清除指定的 Cookie（通过设置过期时间为过去）。

        参数:
            name: 要清除的 Cookie 名称
            path: Cookie 路径
        """
        attrs = {"Path": path, "Max-Age": "0", "Expires": "Thu, 01 Jan 1970 00:00:00 GMT"}
        self._cookies.append((name, "", attrs))

    # ------------------------------------------------------------------
    # 序列化方法
    # ------------------------------------------------------------------

    def to_bytes(self) -> bytes:
        """
        将 HttpResponse 序列化为 HTTP/1.1 响应报文字节流。

        生成的格式:
            HTTP/1.1 200 OK\r\n
            Server: SimpleWebServer/1.0\r\n
            Date: Mon, 22 Jun 2026 12:00:00 GMT\r\n
            Content-Type: text/html; charset=utf-8\r\n
            Content-Length: 128\r\n
            Connection: close\r\n
            Set-Cookie: session=abc123; Path=/; HttpOnly; SameSite=Lax\r\n
            \r\n
            <body bytes>

        返回:
            bytes: 完整的 HTTP 响应报文，可直接通过 socket 发送
        """
        lines = []

        # 状态行
        lines.append(f"HTTP/1.1 {self.status_code} {self.status_text}")

        # 自动设置 Date 头（如果未手动设置）
        if "Date" not in self._headers:
            now = datetime.now(timezone.utc)
            self._headers["Date"] = now.strftime("%a, %d %b %Y %H:%M:%S GMT")

        # 自动设置 Content-Length（如果有响应体）
        if self.body and "Content-Length" not in self._headers:
            self._headers["Content-Length"] = str(len(self.body))
        elif not self.body and "Content-Length" not in self._headers:
            self._headers["Content-Length"] = "0"

        # 安全头部（如果未手动覆盖）
        if "X-Content-Type-Options" not in self._headers:
            self._headers["X-Content-Type-Options"] = "nosniff"

        # 输出所有响应头
        for name, value in self._headers.items():
            lines.append(f"{name}: {value}")

        # 输出 Set-Cookie 头部
        for name, value, attrs in self._cookies:
            cookie_parts = [f"{name}={value}"]
            for attr_name, attr_value in attrs.items():
                if attr_value is None:
                    cookie_parts.append(attr_name)  # 无值属性如 "HttpOnly"
                else:
                    cookie_parts.append(f"{attr_name}={attr_value}")
            lines.append(f"Set-Cookie: {'; '.join(cookie_parts)}")

        # 空行分隔头部和体
        lines.append("")
        header_bytes = "\r\n".join(lines).encode("utf-8") + b"\r\n"

        # 拼接头部和体
        if self.body:
            return header_bytes + self.body
        return header_bytes


# ============================================================================
# 响应构建器
# ============================================================================

class ResponseBuilder:
    """
    HTTP 响应构建器 — 将 DispatchResult 转换为 HttpResponse。

    这是响应生成管道的核心组件，根据不同场景构建合适的 HTTP 响应:
    - 静态文件响应 (200 OK)
    - 重定向响应 (301/302)
    - POST 处理响应
    - 错误响应 (从 ErrorHandler 获取自定义错误页)
    """

    def __init__(self, error_handler, config: dict):
        """
        参数:
            error_handler: ErrorHandler 实例，用于获取自定义错误页面
            config: 服务器配置字典
        """
        self._error_handler = error_handler
        self._config = config

    def build_file_response(self, file_path: str) -> HttpResponse:
        """
        构建静态文件响应 (200 OK)。

        参数:
            file_path: 文件系统上的绝对路径

        返回:
            HttpResponse: Content-Type 根据文件扩展名设置
        """
        try:
            with open(file_path, "rb") as f:
                content = f.read()

            content_type = ContentType.from_extension(file_path)
            return HttpResponse(200, body=content, content_type=content_type)

        except FileNotFoundError:
            return self.build_error(404)
        except PermissionError:
            return self.build_error(403)
        except Exception:
            return self.build_error(500)

    def build_redirect(self, location: str, status: int = 302) -> HttpResponse:
        """
        构建重定向响应。

        参数:
            location: 重定向目标 URL
            status: HTTP 状态码 (默认 302 Found)

        返回:
            HttpResponse: 带有 Location 头的重定向响应
        """
        # 构建最小重定向 HTML
        body = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>重定向</title></head>
<body><h1>{status} 重定向</h1><p>正在跳转到 <a href="{location}">{location}</a></p></body>
</html>""".encode("utf-8")

        response = HttpResponse(status, body=body)
        response.set_header("Location", location)
        return response

    def build_error(self, status_code: int, message: str = "") -> HttpResponse:
        """
        构建错误响应。

        优先使用自定义错误页面，如果不存在则使用 ErrorHandler 的内置模板。

        参数:
            status_code: HTTP 错误状态码
            message: 附加错误信息（可选，在日志中使用）

        返回:
            HttpResponse: 错误响应
        """
        error_body = self._error_handler.get_error_page(status_code)
        return HttpResponse(status_code, body=error_body)

    def build_post_response(self, result_type: str, extra: dict = None) -> HttpResponse:
        """
        构建 POST 请求的响应。

        根据处理结果构建不同的响应:
        - 'login_success': 登录成功，重定向
        - 'login_fail': 登录失败，重定向回登录页
        - 'upload_success': 上传成功
        - 'upload_fail': 上传失败

        参数:
            result_type: 结果类型
            extra: 附加数据字典

        返回:
            HttpResponse: 对应的响应对象
        """
        extra = extra or {}

        if result_type == "login_success":
            response = self.build_redirect(extra.get("redirect", "/"))
            response.set_cookie(
                "session_token",
                extra.get("session_token", ""),
                path="/",
                max_age=3600,
                http_only=True,
            )
            return response

        elif result_type == "login_fail":
            return self.build_redirect("/login?error=1")

        elif result_type == "logout":
            response = self.build_redirect("/")
            response.clear_cookie("session_token", path="/")
            return response

        elif result_type == "upload_success":
            body = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>上传成功</title>
<style>body{{font-family:Arial;max-width:600px;margin:50px auto;padding:20px;}}
.success{{color:#28a745;}}a{{color:#007bff;}}</style></head>
<body><h1 class="success">✓ 文件上传成功</h1>
<p>文件已保存: {extra.get('filename', 'unknown')}</p>
<p><a href="/">返回首页</a></p></body></html>""".encode("utf-8")
            return HttpResponse(200, body=body)

        elif result_type == "upload_fail":
            body = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head><meta charset="UTF-8"><title>上传失败</title>
<style>body{{font-family:Arial;max-width:600px;margin:50px auto;padding:20px;}}
.error{{color:#dc3545;}}a{{color:#007bff;}}</style></head>
<body><h1 class="error">✗ 文件上传失败</h1>
<p>{extra.get('error', '未知错误')}</p>
<p><a href="javascript:history.back()">返回</a></p></body></html>""".encode("utf-8")
            return HttpResponse(400, body=body)

        else:
            return self.build_error(500, "未知的 POST 处理结果类型")
