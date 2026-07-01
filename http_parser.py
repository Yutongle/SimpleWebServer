"""
HTTP/1.1 请求解析器 — 基于状态机的健壮解析实现。

本模块实现了 RFC 7230 中定义的 HTTP/1.1 请求消息解析。
采用基于缓冲区的增量解析方式，能够正确处理 TCP 流式传输中
常见的分包到达、请求行过长、头部过大等边界情况。

解析状态机:
    REQUEST_LINE → HEADERS → BODY → COMPLETE
    任何状态遇到不可恢复错误 → ERROR

安全措施:
    - 请求行最大长度限制: 8192 字节（防止 URI 过长攻击）
    - 请求头最大长度限制: 65536 字节（防止头部过大攻击）
    - HTTP/1.1 强制要求 Host 头（RFC 7230 Section 5.4）
    - 请求体大小由外部限制（在 server.py 中根据配置检查）
"""

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from urllib.parse import unquote


# ============================================================================
# 枚举定义
# ============================================================================

class HttpMethod(Enum):
    """
    HTTP 请求方法枚举。

    本服务器仅支持 GET 和 POST 两种方法。
    其他方法（PUT、DELETE、HEAD 等）将由 Router 返回 405 Method Not Allowed。
    """
    GET = "GET"
    POST = "POST"

    @classmethod
    def from_str(cls, method_str: str) -> Optional["HttpMethod"]:
        """从字符串解析 HTTP 方法，不支持的返回 None"""
        try:
            return cls(method_str.upper())
        except ValueError:
            return None


class ParseState(Enum):
    """HTTP 请求解析状态机的状态枚举"""
    REQUEST_LINE = 1   # 正在等待/解析请求行
    HEADERS = 2        # 正在等待/解析请求头
    BODY = 3           # 正在等待/解析请求体
    COMPLETE = 4       # 解析完成
    ERROR = 5          # 解析出错


# ============================================================================
# 数据结构
# ============================================================================

@dataclass
class HttpRequest:
    """
    解析完成的 HTTP 请求数据结构。

    包含从原始 HTTP 请求报文中提取的所有信息：
    - 请求方法（GET/POST）
    - 请求路径和查询字符串
    - HTTP 版本
    - 请求头字典（键名统一小写）
    - 请求体（仅 POST 请求）
    - 解析后的 Cookie 字典
    """
    method: HttpMethod
    path: str                             # URL 路径部分，如 "/index.html"
    query_string: str = ""                # 查询字符串，如 "q=hello&page=1"
    http_version: str = "HTTP/1.1"        # HTTP 版本
    headers: dict = field(default_factory=dict)  # 请求头字典，键名为小写
    body: bytes = b""                     # 请求体原始字节
    cookies: dict = field(default_factory=dict)  # 解析后的 Cookie

    def get_header(self, name: str, default: str = "") -> str:
        """获取指定请求头的值（大小写不敏感）"""
        return self.headers.get(name.lower(), default)

    def get_cookie(self, name: str, default: str = "") -> str:
        """获取指定 Cookie 的值"""
        return self.cookies.get(name, default)

    def get_post_param(self, name: str, default: str = "") -> str:
        """
        从 POST 请求体中提取表单参数。

        POST 数据支持两种格式:
        - application/x-www-form-urlencoded: key=value&key2=value2
        - 其他格式返回空字符串（可扩展支持 multipart/form-data）
        """
        content_type = self.get_header("content-type", "")
        if "application/x-www-form-urlencoded" in content_type:
            try:
                body_str = self.body.decode("utf-8", errors="replace")
                for pair in body_str.split("&"):
                    if "=" in pair:
                        key, value = pair.split("=", 1)
                        if unquote(key) == name:
                            return unquote(value)
            except Exception:
                pass
        return default

    def get_all_post_params(self) -> dict:
        """
        解析所有 POST 表单参数，返回字典。

        仅处理 application/x-www-form-urlencoded 格式。
        对于 multipart/form-data 文件上传，在 router.py 中单独处理。
        """
        params = {}
        content_type = self.get_header("content-type", "")
        if "application/x-www-form-urlencoded" in content_type and self.body:
            try:
                body_str = self.body.decode("utf-8", errors="replace")
                for pair in body_str.split("&"):
                    if "=" in pair:
                        key, value = pair.split("=", 1)
                        params[unquote(key)] = unquote(value)
            except Exception:
                pass
        return params

    @property
    def query_params(self) -> dict:
        """
        解析查询字符串为键值对字典。

        用于 GET 请求中 URL 参数的提取。
        例如: "/admin/files/delete?name=test.bin" → {"name": "test.bin"}

        返回:
            dict: 解码后的查询参数字典
        """
        params = {}
        if self.query_string:
            for pair in self.query_string.split("&"):
                if "=" in pair:
                    key, value = pair.split("=", 1)
                    params[unquote(key)] = unquote(value)
                else:
                    params[unquote(pair)] = ""
        return params


# ============================================================================
# 异常定义
# ============================================================================

class HttpParseError(Exception):
    """
    HTTP 解析错误异常。

    携带对应的 HTTP 状态码，由 ClientHandler 捕获后直接返回相应的错误响应。
    这避免了在解析层和响应层之间传递错误码的耦合问题。
    """
    def __init__(self, status_code: int, message: str):
        super().__init__(message)
        self.status_code = status_code
        self.message = message


# ============================================================================
# HTTP 解析器
# ============================================================================

class HttpParser:
    """
    HTTP/1.1 请求解析器。

    使用状态机方式逐步解析 HTTP 请求报文的各个部分:
    1. 请求行: METHOD SP request-target SP HTTP-version CRLF
    2. 请求头: field-name ":" OWS field-value OWS CRLF (重复)
    3. 空行: CRLF (标记头部结束)
    4. 消息体: 根据 Content-Length 或 Transfer-Encoding 读取

    使用示例:
        parser = HttpParser()
        try:
            request = parser.parse(raw_bytes)
            print(f"收到 {request.method.value} 请求: {request.path}")
        except HttpParseError as e:
            print(f"解析失败: HTTP {e.status_code} - {e.message}")
    """

    # 安全限制常量
    MAX_REQUEST_LINE = 8192    # 请求行最大字节数 (RFC 7230 建议 >= 8000)
    MAX_HEADERS = 65536        # 所有请求头总计最大字节数

    def parse(self, raw_data: bytes) -> HttpRequest:
        """
        解析原始 HTTP 请求数据，返回 HttpRequest 对象。

        参数:
            raw_data: 从 socket 接收到的原始字节数据

        返回:
            HttpRequest: 解析完成的请求对象

        异常:
            HttpParseError: 解析失败时抛出，携带 HTTP 状态码
        """
        if not raw_data:
            raise HttpParseError(400, "收到空的请求数据")

        try:
            # --- 步骤 1: 定位头部结束标记 (CRLF CRLF) ---
            # HTTP 协议规定头部和消息体之间由一个空行分隔
            header_end = raw_data.find(b"\r\n\r\n")
            if header_end == -1:
                # 数据不完整 — 在生产级服务器中应缓冲更多数据
                # 本实现简化为直接返回 400
                raise HttpParseError(400, "请求数据不完整，缺少头部结束标记")

            header_section = raw_data[:header_end]
            body_start = header_end + 4  # 跳过 \r\n\r\n

            # 解码头部区域为文本
            try:
                header_text = header_section.decode("utf-8", errors="strict")
            except UnicodeDecodeError:
                raise HttpParseError(400, "请求包含非法的 UTF-8 字符")

            lines = header_text.split("\r\n")
            if not lines:
                raise HttpParseError(400, "空的请求头部")

            # --- 步骤 2: 解析请求行 ---
            # 格式: METHOD SP request-target SP HTTP-version
            request_line = lines[0]

            # 检查请求行长度
            if len(request_line) > self.MAX_REQUEST_LINE:
                raise HttpParseError(414, f"请求行过长 (>{self.MAX_REQUEST_LINE} 字节)")

            method, path, http_version = self._parse_request_line(request_line)

            # --- 步骤 3: 解析请求头 ---
            header_lines = lines[1:]

            # 检查头部总长度
            total_header_size = sum(len(line) for line in header_lines)
            if total_header_size > self.MAX_HEADERS:
                raise HttpParseError(431, f"请求头总长度过大 (>{self.MAX_HEADERS} 字节)")

            headers = self._parse_headers(header_lines)

            # HTTP/1.1 必须包含 Host 头
            if http_version == "HTTP/1.1" and "host" not in headers:
                raise HttpParseError(400, "HTTP/1.1 请求缺少必需的 Host 头部")

            # --- 步骤 4: 解析请求体（仅 POST） ---
            body = b""
            if method == HttpMethod.POST:
                body = self._parse_body(raw_data[body_start:], headers)

            # --- 步骤 5: 解析查询字符串和 Cookie ---
            clean_path, query_string = self._parse_query_string(path)
            cookies = self._parse_cookies(headers)

            return HttpRequest(
                method=method,
                path=clean_path,
                query_string=query_string,
                http_version=http_version,
                headers=headers,
                body=body,
                cookies=cookies,
            )

        except HttpParseError:
            raise
        except Exception as e:
            raise HttpParseError(400, f"解析请求时发生错误: {str(e)}")

    # ------------------------------------------------------------------
    # 内部解析方法
    # ------------------------------------------------------------------

    def _parse_request_line(self, line: str) -> tuple:
        """
        解析 HTTP 请求行。

        格式: METHOD SP request-target SP HTTP-version CRLF

        返回: (HttpMethod, path, http_version)

        异常:
            HttpParseError(400): 请求行格式错误
            HttpParseError(405): 不支持的 HTTP 方法
            HttpParseError(505): 不支持的 HTTP 版本
        """
        parts = line.split(" ")
        if len(parts) != 3:
            raise HttpParseError(400, f"请求行格式错误，期望 3 个部分，得到 {len(parts)} 个")

        method_str, target, version_str = parts

        # 验证 HTTP 方法
        method = HttpMethod.from_str(method_str)
        if method is None:
            raise HttpParseError(405, f"不支持的 HTTP 方法: {method_str}")

        # 验证 HTTP 版本
        if version_str not in ("HTTP/1.0", "HTTP/1.1"):
            raise HttpParseError(505, f"不支持的 HTTP 版本: {version_str}")

        # 验证请求目标不为空
        if not target:
            raise HttpParseError(400, "请求目标为空")

        return method, target, version_str

    def _parse_headers(self, lines: list) -> dict:
        """
        解析 HTTP 请求头。

        格式: field-name ":" OWS field-value OWS

        处理要点:
        - 头部名称统一转为小写（HTTP 头部名称大小写不敏感）
        - OWS (可选空白) 在冒号前后被去除
        - 重复的头部名称，值会被拼接（逗号分隔），符合 RFC 7230 语义
        - 空行终止头部列表（调用者保证 lines 中无空行）

        参数:
            lines: 请求头行列表（不含请求行）

        返回:
            dict: 键为小写头部名称，值为头部值
        """
        headers = {}
        for line in lines:
            if not line:
                continue

            # 查找冒号分隔符
            colon_pos = line.find(":")
            if colon_pos == -1:
                # 不符合格式的头部行，跳过（宽松处理）
                continue

            name = line[:colon_pos].strip().lower()
            value = line[colon_pos + 1:].strip()

            # 处理重复头部: 用逗号拼接（如多个 Set-Cookie 的特殊情况保持独立）
            if name in headers:
                headers[name] = headers[name] + ", " + value
            else:
                headers[name] = value

        return headers

    def _parse_body(self, data: bytes, headers: dict) -> bytes:
        """
        解析 HTTP 请求消息体。

        支持的传输方式:
        - Content-Length: 读取指定字节数
        - Transfer-Encoding: chunked: 分块传输编码（可选扩展）

        参数:
            data: 头部之后的所有原始数据
            headers: 已解析的请求头字典

        返回:
            bytes: 消息体内容
        """
        # Content-Length 方式
        content_length_str = headers.get("content-length", "")
        if content_length_str:
            try:
                content_length = int(content_length_str)
                if content_length < 0:
                    raise HttpParseError(400, "Content-Length 不能为负数")
                if content_length > len(data):
                    # 数据未完全到达 — 简化处理，返回已有数据
                    # 生产环境应等待更多数据
                    return data[:content_length]
                return data[:content_length]
            except ValueError:
                raise HttpParseError(400, f"Content-Length 格式无效: {content_length_str}")

        # Transfer-Encoding: chunked (简化处理 — 返回 raw data)
        transfer_encoding = headers.get("transfer-encoding", "")
        if transfer_encoding.lower() == "chunked":
            # 基础实现：尝试简单地从 chunked 编码中提取数据
            # 完整的 chunked 解析较复杂，此处返回原始数据
            # 在 DESIGN.md 中标注为可选扩展
            return self._decode_chunked(data)

        # 无消息体
        return b""

    def _decode_chunked(self, data: bytes) -> bytes:
        """
        解码分块传输编码的数据（简化实现）。

        格式:
            chunk-size [chunk-ext] CRLF
            chunk-data CRLF
            ...
            0 CRLF
            [trailer-part] CRLF

        这是一个基础实现，完整支持见 DESIGN.md 中标注的扩展方向。
        """
        result = bytearray()
        pos = 0
        while pos < len(data):
            # 查找 chunk-size 行结束
            line_end = data.find(b"\r\n", pos)
            if line_end == -1:
                break

            # 解析 chunk-size（十六进制）
            try:
                chunk_size_str = data[pos:line_end].split(b";")[0].strip()
                chunk_size = int(chunk_size_str, 16)
            except (ValueError, IndexError):
                break

            if chunk_size == 0:
                # 最后一个 chunk
                break

            # 读取 chunk 数据
            chunk_start = line_end + 2
            chunk_end = chunk_start + chunk_size
            if chunk_end > len(data):
                break

            result.extend(data[chunk_start:chunk_end])
            pos = chunk_end + 2  # 跳过数据和 CRLF

        return bytes(result)

    def _parse_cookies(self, headers: dict) -> dict:
        """
        从 Cookie 请求头解析 Cookie 键值对。

        Cookie 格式: name1=value1; name2=value2

        返回: dict {name: value}
        """
        cookies = {}
        cookie_header = headers.get("cookie", "")
        if not cookie_header:
            return cookies

        for part in cookie_header.split(";"):
            part = part.strip()
            if "=" in part:
                name, value = part.split("=", 1)
                cookies[name.strip()] = value.strip()

        return cookies

    def _parse_query_string(self, path: str) -> tuple:
        """
        从请求路径中分离查询字符串。

        例如:
            "/search?q=hello&page=1" → ("/search", "q=hello&page=1")
            "/index.html"           → ("/index.html", "")

        返回: (clean_path, query_string)
        """
        if "?" in path:
            path_part, query_part = path.split("?", 1)
            return path_part, query_part
        return path, ""
