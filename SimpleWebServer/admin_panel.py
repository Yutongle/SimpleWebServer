"""
管理面板模块 — 服务端渲染动态管理页面。

本模块负责:
- 根据服务器运行时数据动态生成 HTML 管理页面
- 提供仪表盘（自动刷新）、日志查看、文件管理（含上传）等交互功能
- 遵循项目 CGI-less / 纯 HTTP 交互的设计原则
- 仪表盘使用 meta refresh 实现每1秒自动刷新

页面结构:
    管理面板
    ├── 仪表盘 (/admin/)     — 服务器实时统计（每1秒自动刷新）
    ├── 日志查看 (/admin/logs) — 最近 N 行 CLF 日志
    └── 文件管理 (/admin/files) — 上传文件列表 + 快捷上传表单

使用示例:
    stats = ServerStats()
    panel = AdminPanel(stats, config, logger)
    html = panel.render_dashboard("admin")
"""

import os
import time as time_module
from datetime import datetime
from typing import Optional


# ============================================================================
# 服务器统计信息
# ============================================================================

class ServerStats:
    """
    线程安全的服务器运行时统计信息。

    记录服务器启动以来的关键指标:
    - 请求总量
    - 运行时长（通过启动时间戳计算）
    - 统计锁保护并发读写安全

    使用示例:
        stats = ServerStats()
        stats.increment_request()
        print(stats.get_uptime_seconds())
    """

    def __init__(self):
        """初始化统计数据，记录服务器启动时刻"""
        self.start_time = time_module.time()
        self.request_count: int = 0
        self._lock = __import__("threading").Lock()

    def increment_request(self) -> None:
        """递增请求计数器（线程安全）"""
        with self._lock:
            self.request_count += 1

    def get_uptime_seconds(self) -> float:
        """获取服务器已运行秒数"""
        return time_module.time() - self.start_time

    def get_stats(self) -> dict:
        """
        获取所有统计数据的快照。

        返回:
            dict: 包含 start_time, uptime_seconds, request_count
        """
        return {
            "start_time": self.start_time,
            "uptime_seconds": self.get_uptime_seconds(),
            "request_count": self.request_count,
        }


# ============================================================================
# 管理面板渲染器
# ============================================================================

class AdminPanel:
    """
    管理面板页面渲染器 — 动态生成管理后台 HTML。

    采用服务端渲染（SSR）方式，将服务器运行时数据嵌入 HTML 模板。
    每个页面由 _build_base_page() 提供统一的布局框架（头部栏 + 导航栏），
    各子页面通过 render_* 方法填充专属内容区域。

    仪表盘使用 meta refresh 自动刷新，无需 JavaScript。

    线程安全: 所有方法通过参数获取外部状态，方法内无共享可变状态。
    """

    def __init__(
        self,
        stats: ServerStats,
        config: dict,
        logger,  # AccessLogger 实例
    ):
        """
        初始化管理面板渲染器。

        参数:
            stats: 服务器统计信息实例
            config: 完整服务器配置字典
            logger: 访问日志记录器实例
        """
        self._stats = stats
        self._config = config
        self._logger = logger

        server_config = config.get("server", {})
        self._upload_dir = os.path.abspath(
            server_config.get("upload_dir", "./uploads")
        )
        self._document_root = os.path.abspath(
            server_config.get("document_root", "./static")
        )
        self._host = server_config.get("host", "127.0.0.1")
        self._port = server_config.get("port", 8080)
        self._max_workers = server_config.get("max_workers", 10)

    # ------------------------------------------------------------------
    # 公开渲染方法
    # ------------------------------------------------------------------

    def render_dashboard(self, username: str) -> str:
        """
        渲染管理面板仪表盘页面。

        显示内容:
        - 服务器运行状态（运行中 / 已停止）
        - 请求总数、运行时长、请求速率
        - 服务器配置摘要（监听地址、线程数、认证状态）
        - 功能导航快捷入口

        仪表盘每 1 秒自动刷新以反映最新统计数据（不含仪表盘自身刷新请求）。

        参数:
            username: 当前登录用户名

        返回:
            str: 完整的 HTML 页面字符串
        """
        s = self._stats.get_stats()
        uptime_str = self._format_uptime(s["uptime_seconds"])
        req_per_sec = (
            s["request_count"] / s["uptime_seconds"]
            if s["uptime_seconds"] > 0
            else 0
        )

        auth_config = self._config.get("authentication", {})
        auth_status = "已启用 (Session)" if auth_config.get("enabled", False) else "已禁用"
        users_list = ", ".join(auth_config.get("users", {}).keys()) or "无"

        body = f"""
        <div class="refresh-hint">🔄 每 1 秒自动刷新 — 请求计数为实时数据（不含仪表盘自身刷新）</div>

        <!-- 状态卡片行 -->
        <div class="stats-row">
            <div class="stat-card">
                <div class="stat-value">{s['request_count']}</div>
                <div class="stat-label">请求总数</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{uptime_str}</div>
                <div class="stat-label">运行时长</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{req_per_sec:.1f} req/s</div>
                <div class="stat-label">请求速率</div>
            </div>
        </div>

        <!-- 配置信息卡片 -->
        <div class="card">
            <h2>📊 服务器配置</h2>
            <ul>
                <li><strong>监听地址:</strong> http://{self._host}:{self._port}</li>
                <li><strong>文档根目录:</strong> {self._document_root}</li>
                <li><strong>上传目录:</strong> {self._upload_dir}</li>
                <li><strong>工作线程数:</strong> {self._max_workers}</li>
                <li><strong>认证状态:</strong> {auth_status}</li>
                <li><strong>注册用户:</strong> {users_list}</li>
            </ul>
        </div>

        <!-- 安全信息卡片 -->
        <div class="card">
            <h2>🔒 安全特性</h2>
            <ul>
                <li>路径遍历防护: 三层防御 (字符串检测 + URL解码 + realpath验证)</li>
                <li>会话安全: HMAC-SHA256 签名 · HttpOnly Cookie · 恒定时间比较</li>
                <li>请求限制: 最大 50MB 请求体 · 30s Socket 超时</li>
                <li>安全头部: X-Content-Type-Options: nosniff</li>
            </ul>
        </div>

        <!-- 操作入口 -->
        <div class="action-links">
            <a href="/admin/logs" class="action-btn">📋 查看访问日志</a>
            <a href="/admin/files" class="action-btn">📁 管理上传文件</a>
            <a href="/" class="action-btn action-btn-secondary">🏠 返回首页</a>
        </div>
        """

        # 仪表盘需要自动刷新（1 秒更新一次）
        return self._build_base_page("仪表盘", username, body, auto_refresh=1)

    def render_logs(self, username: str, lines: int = 200) -> str:
        """
        渲染日志查看页面。

        从日志环形缓冲区读取最近 N 行并以等宽字体展示。
        支持通过查询参数选择显示行数。

        参数:
            username: 当前登录用户名
            lines: 要显示的行数（默认 200）

        返回:
            str: 完整的 HTML 页面字符串
        """
        recent_logs = self._logger.get_recent_logs(lines)

        if recent_logs:
            # 反转日志顺序（最新的在最上面，便于阅读）
            log_text = "\n".join(reversed(recent_logs))
        else:
            log_text = "（暂无日志记录）\n\n提示: 请在服务器终端查看实时日志输出。\n日志缓冲区仅在配置文件设置 log_file 或最近访问后才有数据。"

        # 行数选择器
        line_options = "".join(
            f'<a href="/admin/logs?lines={n}" class="line-option{" active-option" if n == lines else ""}">{n}行</a>'
            for n in [50, 100, 200, 500]
        )

        body = f"""
        <div class="log-toolbar">
            <span>显示行数:</span>
            {line_options}
            <a href="/admin/logs" class="btn-refresh">🔄 刷新</a>
        </div>

        <div class="log-viewer">
            <pre>{log_text}</pre>
        </div>

        <div class="action-links" style="margin-top: 20px;">
            <a href="/admin/" class="action-btn">◀ 返回仪表盘</a>
        </div>
        """

        return self._build_base_page("访问日志", username, body)

    def render_files(self, username: str) -> str:
        """
        渲染上传文件管理页面。

        功能:
        - 列出 upload_dir 中所有文件及其大小、修改时间
        - 提供文件删除功能（POST 表单）
        - 内置文件上传表单（multipart/form-data）

        参数:
            username: 当前登录用户名

        返回:
            str: 完整的 HTML 页面字符串
        """
        files = self._list_uploaded_files()

        # ---- 文件上传表单（始终显示，放在顶部） ----
        upload_form = f"""
        <div class="card">
            <h2>📤 上传新文件</h2>
            <form method="POST" action="/upload" enctype="multipart/form-data">
                <div class="upload-row">
                    <input type="file" name="file" class="upload-file-input" required>
                    <button type="submit" class="upload-btn">📤 上传</button>
                </div>
                <div class="upload-hint">
                    支持任意文件类型，最大 {self._config.get('server', {}).get('max_upload_size_mb', 10)} MB
                </div>
            </form>
        </div>
        """

        # ---- 文件列表 ----
        if files:
            total_size = sum(f["size"] for f in files)
            total_size_str = self._format_size(total_size)

            rows = ""
            for f_info in files:
                rows += f"""
                <tr>
                    <td class="file-name">{f_info['name']}</td>
                    <td>{self._format_size(f_info['size'])}</td>
                    <td>{f_info['modified']}</td>
                    <td>
                        <div style="display:flex;gap:6px;">
                            <a href="/admin/files/view?name={f_info['name']}"
                               class="btn-view">👁 查看</a>
                            <a href="/admin/files/run?name={f_info['name']}"
                               class="btn-run">▶ 运行</a>
                            <form method="POST" action="/admin/files/delete"
                                  style="display:inline;">
                                <input type="hidden" name="filename" value="{f_info['name']}">
                                <button type="submit" class="btn-delete"
                                        onclick="return confirm('确定要删除 {f_info['name']} 吗？');">
                                    🗑 删除
                                </button>
                            </form>
                        </div>
                    </td>
                </tr>"""

            file_table = f"""
            <div class="card">
                <h2>📁 文件列表</h2>
                <div class="file-summary">
                    共 <strong>{len(files)}</strong> 个文件，
                    总大小 <strong>{total_size_str}</strong>
                </div>
                <table class="file-table">
                    <thead>
                        <tr>
                            <th>文件名</th>
                            <th>大小</th>
                            <th>修改时间</th>
                            <th>操作</th>
                        </tr>
                    </thead>
                    <tbody>
                        {rows}
                    </tbody>
                </table>
            </div>
            """
        else:
            file_table = """
            <div class="card">
                <h2>📁 文件列表</h2>
                <div class="empty-state">
                    <div class="empty-icon">📭</div>
                    <p>上传目录为空</p>
                    <p style="font-size:13px; color:#888;">
                        使用上方的上传表单上传第一个文件吧！
                    </p>
                </div>
            </div>
            """

        body = f"""
        {upload_form}
        {file_table}

        <div class="action-links" style="margin-top: 25px;">
            <a href="/admin/" class="action-btn">◀ 返回仪表盘</a>
        </div>
        """

        return self._build_base_page("文件管理", username, body)

    # ------------------------------------------------------------------
    # 内部辅助方法
    # ------------------------------------------------------------------

    def _build_base_page(self, title: str, username: str, body_content: str, auto_refresh: int = 0) -> str:
        """
        构建统一的管理面板 HTML 框架。

        提供:
        - 深色主题样式（与现有 admin/index.html 风格一致）
        - 顶部导航栏（当前用户 + 退出链接）
        - 标签式导航（仪表盘 | 日志 | 文件管理）
        - 可选自动刷新（auto_refresh > 0 时生效）

        参数:
            title: 页面标题后缀
            username: 当前登录用户名
            body_content: 主体 HTML 内容
            auto_refresh: 自动刷新间隔秒数（0 = 不自动刷新）

        返回:
            str: 完整的 HTML 页面
        """
        # 确定当前激活的导航标签
        nav_items = [
            ("/admin/", "📊 仪表盘", title == "仪表盘"),
            ("/admin/logs", "📋 日志", title == "访问日志"),
            ("/admin/files", "📁 文件管理", title == "文件管理"),
        ]

        nav_html = ""
        for url, label, active in nav_items:
            active_class = ' class="active"' if active else ""
            nav_html += f'<a href="{url}"{active_class}>{label}</a>\n'

        # 自动刷新 meta 标签（仪表盘用）
        refresh_meta = ""
        if auto_refresh > 0:
            refresh_meta = f'<meta http-equiv="refresh" content="{auto_refresh}">\n'

        return f"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    {refresh_meta}    <title>{title} — SimpleWebServer 管理面板</title>
    <style>
        * {{ margin: 0; padding: 0; box-sizing: border-box; }}
        body {{
            font-family: "Microsoft YaHei", "微软雅黑", Arial, sans-serif;
            background: linear-gradient(135deg, #1a1a2e 0%, #16213e 100%);
            min-height: 100vh;
            color: #e0e0e0;
        }}
        /* 顶部栏 */
        .admin-header {{
            background: rgba(0,0,0,0.3);
            padding: 15px 40px;
            display: flex;
            align-items: center;
            justify-content: space-between;
            border-bottom: 1px solid rgba(255,255,255,0.1);
        }}
        .admin-header h1 {{
            font-size: 20px;
            color: #f39c12;
        }}
        .admin-header .user-info {{
            font-size: 14px;
            color: #aaa;
        }}
        .admin-header .user-info a {{
            color: #e74c3c;
            text-decoration: none;
            margin-left: 12px;
        }}
        .admin-header .user-info a:hover {{ text-decoration: underline; }}
        /* 标签导航 */
        .admin-nav {{
            background: rgba(0,0,0,0.2);
            padding: 0 40px;
            display: flex;
            border-bottom: 1px solid rgba(255,255,255,0.08);
        }}
        .admin-nav a {{
            display: inline-block;
            padding: 12px 20px;
            color: #aaa;
            text-decoration: none;
            font-size: 14px;
            border-bottom: 2px solid transparent;
            transition: all 0.2s;
        }}
        .admin-nav a:hover {{ color: #fff; background: rgba(255,255,255,0.05); }}
        .admin-nav a.active {{
            color: #f39c12;
            border-bottom-color: #f39c12;
        }}
        /* 主内容区 */
        .admin-content {{
            max-width: 960px;
            margin: 30px auto;
            padding: 0 20px 40px;
        }}
        /* 自动刷新提示 */
        .refresh-hint {{
            text-align: center;
            color: #666;
            font-size: 12px;
            margin-bottom: 15px;
        }}
        /* 统计卡片行 */
        .stats-row {{
            display: flex;
            gap: 20px;
            margin-bottom: 25px;
            flex-wrap: wrap;
        }}
        .stat-card {{
            flex: 1;
            min-width: 180px;
            background: linear-gradient(135deg, #667eea, #764ba2);
            border-radius: 12px;
            padding: 25px 20px;
            text-align: center;
        }}
        .stat-card:nth-child(2) {{ background: linear-gradient(135deg, #f093fb, #f5576c); }}
        .stat-card:nth-child(3) {{ background: linear-gradient(135deg, #4facfe, #00f2fe); }}
        .stat-value {{
            font-size: 28px;
            font-weight: bold;
            color: #fff;
            margin-bottom: 6px;
        }}
        .stat-label {{
            font-size: 13px;
            color: rgba(255,255,255,0.8);
        }}
        /* 卡片 */
        .card {{
            background: rgba(255,255,255,0.05);
            border-radius: 12px;
            padding: 25px;
            margin-bottom: 25px;
            border: 1px solid rgba(255,255,255,0.08);
        }}
        .card h2 {{
            font-size: 17px;
            color: #f39c12;
            margin-bottom: 15px;
        }}
        .card ul {{
            list-style: none;
            padding-left: 0;
        }}
        .card ul li {{
            color: #bbb;
            line-height: 2;
            font-size: 14px;
        }}
        .card ul li strong {{ color: #ddd; }}
        /* 操作按钮 */
        .action-links {{
            display: flex;
            gap: 12px;
            flex-wrap: wrap;
        }}
        .action-btn {{
            display: inline-block;
            padding: 10px 22px;
            background: #667eea;
            color: #fff;
            text-decoration: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 500;
            transition: background 0.2s;
        }}
        .action-btn:hover {{ background: #5a6fd6; }}
        .action-btn-secondary {{ background: rgba(255,255,255,0.1); }}
        .action-btn-secondary:hover {{ background: rgba(255,255,255,0.2); }}
        /* 日志查看器 */
        .log-toolbar {{
            display: flex;
            align-items: center;
            gap: 10px;
            margin-bottom: 15px;
            font-size: 13px;
            color: #aaa;
            flex-wrap: wrap;
        }}
        .line-option {{
            padding: 4px 10px;
            background: rgba(255,255,255,0.08);
            color: #aaa;
            text-decoration: none;
            border-radius: 4px;
            font-size: 12px;
        }}
        .line-option:hover {{ background: rgba(255,255,255,0.15); color: #fff; }}
        .active-option {{
            background: #667eea;
            color: #fff;
        }}
        .active-option:hover {{ background: #5a6fd6; }}
        .btn-refresh {{
            padding: 4px 12px;
            background: rgba(255,255,255,0.1);
            color: #aaa;
            text-decoration: none;
            border-radius: 4px;
            font-size: 12px;
        }}
        .btn-refresh:hover {{ background: rgba(255,255,255,0.2); color: #fff; }}
        .log-viewer {{
            background: #0d1117;
            border-radius: 8px;
            padding: 20px;
            overflow-x: auto;
            max-height: 600px;
            overflow-y: auto;
        }}
        .log-viewer pre {{
            margin: 0;
            font-family: "Consolas", "Courier New", monospace;
            font-size: 13px;
            color: #c9d1d9;
            line-height: 1.6;
            white-space: pre;
        }}
        /* 文件上传 */
        .upload-row {{
            display: flex;
            gap: 12px;
            align-items: center;
            flex-wrap: wrap;
        }}
        .upload-file-input {{
            flex: 1;
            min-width: 250px;
            padding: 10px 12px;
            background: rgba(0,0,0,0.3);
            border: 1px solid rgba(255,255,255,0.15);
            border-radius: 6px;
            color: #ddd;
            font-size: 14px;
            font-family: inherit;
        }}
        .upload-file-input::file-selector-button {{
            padding: 7px 16px;
            background: #555;
            color: #fff;
            border: none;
            border-radius: 4px;
            cursor: pointer;
            font-family: inherit;
        }}
        .upload-file-input::file-selector-button:hover {{
            background: #777;
        }}
        .upload-btn {{
            padding: 10px 28px;
            background: #27ae60;
            color: #fff;
            border: none;
            border-radius: 6px;
            font-size: 14px;
            font-weight: 500;
            cursor: pointer;
            font-family: inherit;
            transition: background 0.2s;
        }}
        .upload-btn:hover {{ background: #219a52; }}
        .upload-hint {{
            margin-top: 8px;
            font-size: 12px;
            color: #888;
        }}
        /* 文件表格 */
        .file-summary {{
            color: #aaa;
            font-size: 14px;
            margin-bottom: 15px;
        }}
        .file-table {{
            width: 100%;
            border-collapse: collapse;
            background: rgba(255,255,255,0.03);
            border-radius: 8px;
            overflow: hidden;
        }}
        .file-table th {{
            text-align: left;
            padding: 12px 15px;
            background: rgba(255,255,255,0.08);
            color: #f39c12;
            font-size: 13px;
            font-weight: 500;
        }}
        .file-table td {{
            padding: 10px 15px;
            border-bottom: 1px solid rgba(255,255,255,0.05);
            font-size: 14px;
            color: #bbb;
        }}
        .file-table .file-name {{
            color: #e0e0e0;
            font-weight: 500;
        }}
        .btn-delete {{
            padding: 4px 12px;
            background: rgba(231,76,60,0.2);
            color: #e74c3c;
            border: 1px solid rgba(231,76,60,0.3);
            border-radius: 4px;
            font-size: 12px;
            cursor: pointer;
            font-family: inherit;
        }}
        .btn-delete:hover {{
            background: rgba(231,76,60,0.4);
        }}
        .btn-view {{
            padding: 4px 12px;
            background: rgba(52,152,219,0.2);
            color: #3498db;
            border: 1px solid rgba(52,152,219,0.3);
            border-radius: 4px;
            font-size: 12px;
            text-decoration: none;
            display: inline-block;
        }}
        .btn-view:hover {{
            background: rgba(52,152,219,0.4);
        }}
        .btn-run {{
            padding: 4px 12px;
            background: rgba(230,126,34,0.2);
            color: #e67e22;
            border: 1px solid rgba(230,126,34,0.3);
            border-radius: 4px;
            font-size: 12px;
            text-decoration: none;
            display: inline-block;
        }}
        .btn-run:hover {{
            background: rgba(230,126,34,0.4);
        }}
        .run-output {{
            background: #0d1117;
            border-radius: 8px;
            padding: 20px;
            overflow-x: auto;
            max-height: 500px;
            overflow-y: auto;
            border-left: 4px solid #27ae60;
        }}
        .run-output pre {{
            margin: 0;
            font-family: "Consolas", "Courier New", monospace;
            font-size: 14px;
            color: #c9d1d9;
            line-height: 1.6;
            white-space: pre-wrap;
            word-wrap: break-word;
        }}
        .run-exit-ok {{
            padding: 8px 16px;
            background: rgba(39,174,96,0.15);
            color: #27ae60;
            border-radius: 6px;
            font-size: 14px;
            margin-bottom: 12px;
        }}
        .run-exit-error {{
            padding: 8px 16px;
            background: rgba(231,76,60,0.15);
            color: #e74c3c;
            border-radius: 6px;
            font-size: 14px;
            margin-bottom: 12px;
        }}
        .run-exit-timeout {{
            padding: 8px 16px;
            background: rgba(241,196,15,0.15);
            color: #f1c40f;
            border-radius: 6px;
            font-size: 14px;
            margin-bottom: 12px;
        }}
        /* 空状态 */
        .empty-state {{
            text-align: center;
            padding: 60px 20px;
        }}
        .empty-state .empty-icon {{
            font-size: 48px;
            margin-bottom: 15px;
        }}
        .empty-state p {{
            color: #aaa;
            font-size: 15px;
            line-height: 1.8;
        }}
    </style>
</head>
<body>
    <div class="admin-header">
        <h1>⚙️ SimpleWebServer 管理面板</h1>
        <div class="user-info">
            👤 {username} | <a href="/logout">退出登录</a>
        </div>
    </div>
    <div class="admin-nav">
        {nav_html}
    </div>
    <div class="admin-content">
        {body_content}
    </div>
</body>
</html>"""

    def render_file_view(self, filename: str, content: str, size_str: str) -> str:
        """
        渲染文件内容查看页面。

        参数:
            filename: 文件名
            content: 文件内容字符串
            size_str: 格式化后的文件大小

        返回:
            str: 完整的 HTML 页面字符串
        """
        # HTML 实体转义
        safe_content = content.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

        body = f"""
        <div class="card">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:15px;">
                <h2 style="margin:0;">📄 {filename}</h2>
                <span style="color:#888;font-size:13px;">大小: {size_str}</span>
            </div>
            <div class="log-viewer" style="max-height:70vh;">
                <pre>{safe_content}</pre>
            </div>
        </div>

        <div class="action-links" style="margin-top:20px;">
            <a href="/admin/files" class="action-btn">◀ 返回文件管理</a>
            <a href="/admin/files/view?name={filename}&raw=1" class="action-btn action-btn-secondary">📥 下载文件</a>
        </div>
        """

        return self._build_base_page(f"查看: {filename}", "管理员", body)

    def render_file_run(self, filename: str, stdout: str, stderr: str, exit_msg: str) -> str:
        """
        渲染文件运行结果页面。

        参数:
            filename: 文件名
            stdout: 标准输出内容
            stderr: 标准错误输出内容
            exit_msg: 退出状态信息 HTML

        返回:
            str: 完整的 HTML 页面字符串
        """
        # HTML 实体转义
        safe_stdout = stdout.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") if stdout else ""
        safe_stderr = stderr.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;") if stderr else ""

        stderr_section = ""
        if safe_stderr.strip():
            stderr_section = f"""
            <div class="card" style="margin-top:15px;">
                <h2>⚠ 错误输出 (stderr)</h2>
                <div class="run-output" style="border-left-color:#e74c3c;">
                    <pre>{safe_stderr}</pre>
                </div>
            </div>
            """

        body = f"""
        <div class="card">
            <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:15px;">
                <h2 style="margin:0;">▶ 运行结果: {filename}</h2>
            </div>
            {exit_msg}
            <div class="run-output">
                <pre>{safe_stdout if safe_stdout.strip() else '(无输出)'}</pre>
            </div>
        </div>
        {stderr_section}

        <div class="action-links" style="margin-top:20px;">
            <a href="/admin/files" class="action-btn">◀ 返回文件管理</a>
            <a href="/admin/files/run?name={filename}" class="action-btn" style="background:#e67e22;">🔄 重新运行</a>
        </div>
        """

        return self._build_base_page(f"运行: {filename}", "管理员", body)

    def _list_uploaded_files(self) -> list:
        """
        扫描上传目录，返回文件信息列表。

        按修改时间倒序排列（最新的在前）。

        返回:
            list[dict]: 每项包含 name, size, modified 字段
        """
        files = []
        try:
            for entry in os.scandir(self._upload_dir):
                if entry.is_file():
                    stat = entry.stat()
                    mtime = datetime.fromtimestamp(stat.st_mtime).strftime(
                        "%Y-%m-%d %H:%M:%S"
                    )
                    files.append({
                        "name": entry.name,
                        "size": stat.st_size,
                        "modified": mtime,
                    })
        except OSError:
            pass

        # 按修改时间倒序
        files.sort(key=lambda f: f["modified"], reverse=True)
        return files

    @staticmethod
    def _format_uptime(seconds: float) -> str:
        """
        将秒数格式化为可读的运行时长。

        例如:
            3661 → "1小时2分钟"
            75   → "1分钟15秒"
            30   → "30秒"

        参数:
            seconds: 运行秒数

        返回:
            str: 中文运行时长描述
        """
        seconds = int(seconds)
        days = seconds // 86400
        hours = (seconds % 86400) // 3600
        minutes = (seconds % 3600) // 60
        secs = seconds % 60

        parts = []
        if days > 0:
            parts.append(f"{days}天")
        if hours > 0:
            parts.append(f"{hours}小时")
        if minutes > 0:
            parts.append(f"{minutes}分钟")
        if secs > 0 or not parts:
            parts.append(f"{secs}秒")

        return "".join(parts)

    @staticmethod
    def _format_size(size_bytes: int) -> str:
        """
        格式化文件大小为可读字符串。

        例如:
            1024       → "1.0 KB"
            1048576    → "1.0 MB"

        参数:
            size_bytes: 文件字节数

        返回:
            str: 格式化后的大小
        """
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        elif size_bytes < 1024 * 1024 * 1024:
            return f"{size_bytes / (1024 * 1024):.1f} MB"
        else:
            return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"
