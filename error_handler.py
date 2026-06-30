"""
自定义错误页面处理器 — 管理 HTTP 错误响应的 HTML 页面。

功能:
- 从文档根目录加载自定义错误页面（如 static/404.html）
- 缓存已加载的错误页面以提升性能
- 如果自定义页面不存在，生成内置的中文默认错误页面
- 支持的状态码: 400, 401, 403, 404, 405, 413, 414, 431, 500, 501, 503, 505

安全: 只读取 <document_root>/<status_code>.html，防止读取文档根目录外的文件。

使用示例:
    handler = ErrorHandler("./static")
    page = handler.get_error_page(404)  # 返回 bytes 类型的 HTML 页面
"""

import os
from http_response import HttpResponse


class ErrorHandler:
    """
    HTTP 错误页面管理器。

    缓存策略: 首次加载自定义错误页面后缓存到内存，后续请求直接使用缓存。
    这样可以避免每次返回错误时都读取磁盘文件，显著提升错误响应的速度。

    属性:
        _document_root: 文档根目录绝对路径
        _cache: 状态码 → 错误页面 HTML 字节的缓存字典
    """

    def __init__(self, document_root: str):
        """
        初始化错误页面处理器。

        参数:
            document_root: 文档根目录路径（相对或绝对）
        """
        self._document_root = os.path.abspath(document_root)
        self._cache: dict[int, bytes] = {}  # status_code → html_bytes

    def get_error_page(self, status_code: int) -> bytes:
        """
        获取指定状态码对应的错误页面 HTML。

        查找顺序:
        1. 内存缓存（最快）
        2. <document_root>/<status_code>.html 自定义页面
        3. 内置默认页面（包含中文描述）

        参数:
            status_code: HTTP 状态码 (如 404, 500)

        返回:
            bytes: UTF-8 编码的完整 HTML 页面
        """
        # 步骤 1: 检查缓存
        if status_code in self._cache:
            return self._cache[status_code]

        # 步骤 2: 尝试加载自定义错误页面
        custom_page = self._load_custom_page(status_code)
        if custom_page is not None:
            self._cache[status_code] = custom_page
            return custom_page

        # 步骤 3: 生成内置默认页面
        default_page = self._generate_default_page(status_code)
        self._cache[status_code] = default_page
        return default_page

    def clear_cache(self) -> None:
        """清空页面缓存（用于热更新自定义错误页面）"""
        self._cache.clear()

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _load_custom_page(self, status_code: int) -> bytes | None:
        """
        尝试从文档根目录加载自定义错误页面。

        自定义页面命名为 <status_code>.html，放在文档根目录下。
        例如: static/404.html, static/500.html

        安全: 只读取文档根目录下的 .html 文件，不追随符号链接。

        参数:
            status_code: 状态码

        返回:
            bytes | None: 页面内容，文件不存在或读取失败时返回 None
        """
        file_name = f"{status_code}.html"
        file_path = os.path.join(self._document_root, file_name)

        # 安全检查: 确保解析后的路径在文档根目录内
        try:
            real_root = os.path.realpath(self._document_root)
            real_path = os.path.realpath(file_path)
            if os.path.commonpath([real_root, real_path]) != real_root:
                return None  # 路径逃逸，拒绝读取
        except (ValueError, OSError):
            return None

        # 读取文件
        try:
            if os.path.isfile(file_path):
                with open(file_path, "rb") as f:
                    return f.read()
        except (OSError, PermissionError):
            pass

        return None

    def _generate_default_page(self, status_code: int) -> bytes:
        """
        生成内置的中文默认错误页面 HTML。

        页面设计:
        - 居中显示状态码和错误描述
        - 使用清晰的中文提示信息
        - 包含返回首页的链接
        - 显示服务器签名

        参数:
            status_code: HTTP 状态码

        返回:
            bytes: 完整的 HTML 页面字节
        """
        status_text = HttpResponse.STATUS_TEXTS.get(status_code, "Error")

        # 根据不同状态码提供中文错误描述和建议
        error_info = self._get_error_info(status_code)

        html = f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{status_code} {status_text} - SimpleWebServer</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: "Microsoft YaHei", "微软雅黑", Arial, sans-serif;
            background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%);
            min-height: 100vh;
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .error-container {{
            background: white;
            border-radius: 12px;
            box-shadow: 0 10px 40px rgba(0,0,0,0.1);
            padding: 60px 80px;
            text-align: center;
            max-width: 550px;
            width: 90%;
        }}
        .error-code {{
            font-size: 96px;
            font-weight: 700;
            color: #e74c3c;
            line-height: 1;
            margin-bottom: 10px;
        }}
        .error-code.code-4xx {{ color: #f39c12; }}
        .error-code.code-5xx {{ color: #e74c3c; }}
        .error-text {{
            font-size: 24px;
            color: #555;
            margin-bottom: 20px;
        }}
        .error-description {{
            font-size: 15px;
            color: #888;
            margin-bottom: 30px;
            line-height: 1.6;
        }}
        .error-suggestion {{
            font-size: 14px;
            color: #999;
            margin-bottom: 30px;
            padding: 15px;
            background: #f8f9fa;
            border-radius: 8px;
            border-left: 4px solid #3498db;
            text-align: left;
            line-height: 1.6;
        }}
        .home-link {{
            display: inline-block;
            background: #3498db;
            color: white;
            text-decoration: none;
            padding: 12px 30px;
            border-radius: 6px;
            font-size: 16px;
            transition: background 0.2s;
        }}
        .home-link:hover {{ background: #2980b9; }}
        .server-footer {{
            margin-top: 30px;
            font-size: 12px;
            color: #bbb;
        }}
    </style>
</head>
<body>
    <div class="error-container">
        <div class="error-code {'code-4xx' if 400 <= status_code < 500 else 'code-5xx'}">{status_code}</div>
        <div class="error-text">{status_text}</div>
        <div class="error-description">{error_info['description']}</div>
        <div class="error-suggestion">
            <strong>💡 建议：</strong><br>
            {error_info['suggestion']}
        </div>
        <a href="/" class="home-link">返回首页</a>
        <div class="server-footer">SimpleWebServer/1.0</div>
    </div>
</body>
</html>"""
        return html.encode("utf-8")

    def _get_error_info(self, status_code: int) -> dict:
        """
        获取各状态码的中文描述和建议信息。

        参数:
            status_code: HTTP 状态码

        返回:
            dict: {"description": "错误描述", "suggestion": "用户建议"}
        """
        info_map = {
            400: {
                "description": "服务器无法理解该请求。请求可能格式错误或包含无效字符。",
                "suggestion": "请检查请求的格式是否正确。如果您是通过浏览器访问此页面，请联系网站管理员。",
            },
            401: {
                "description": "访问该资源需要进行身份验证。您需要提供有效的用户名和密码。",
                "suggestion": "请使用正确的用户名和密码进行登录。如果您忘记了密码，请联系管理员重置。",
            },
            403: {
                "description": "服务器拒绝了您的访问请求。您没有权限查看该资源。",
                "suggestion": "请确认您有访问该资源的权限。检查 URL 是否正确，或尝试登录后重新访问。",
            },
            404: {
                "description": "您请求的页面或资源不存在。该资源可能已被移动、删除或从未存在。",
                "suggestion": "请检查输入的 URL 是否正确。您也可以尝试从首页开始浏览网站内容。",
            },
            405: {
                "description": "该 URL 不支持您所使用的请求方法。例如，某些页面只接受 GET 请求。",
                "suggestion": "请确认您的操作是否正确。大多数网页浏览只需使用 GET 请求。",
            },
            413: {
                "description": "您上传的文件或请求体超过了服务器允许的最大大小。",
                "suggestion": "请尝试上传较小的文件，或联系管理员增加上传大小限制。",
            },
            414: {
                "description": "请求的 URL 长度超过了服务器的处理限制。",
                "suggestion": "请缩短 URL 长度。如果是查询参数过多，请尝试减少查询条件。",
            },
            431: {
                "description": "请求头总大小超过了服务器的处理限制。",
                "suggestion": "请尝试清除浏览器的 Cookie 和缓存后重新访问。",
            },
            500: {
                "description": "服务器在处理请求时发生了内部错误。这通常是服务器端的问题。",
                "suggestion": "请稍后重试。如果问题持续出现，请联系网站管理员报告此问题。",
            },
            501: {
                "description": "服务器不支持该请求所要求的功能，该功能尚未实现。",
                "suggestion": "请确认您的请求类型是否被服务器支持。目前仅支持 GET 和 POST 请求。",
            },
            503: {
                "description": "服务器暂时无法处理该请求，可能由于临时过载或维护。",
                "suggestion": "请稍后重试。服务器可能正在处理大量请求或进行维护。",
            },
            505: {
                "description": "服务器不支持该请求使用的 HTTP 协议版本。",
                "suggestion": "请使用 HTTP/1.1 或 HTTP/1.0 协议版本进行请求。",
            },
        }

        return info_map.get(status_code, {
            "description": f"服务器返回了 HTTP {status_code} 错误。",
            "suggestion": "请稍后重试或联系网站管理员获取帮助。",
        })
