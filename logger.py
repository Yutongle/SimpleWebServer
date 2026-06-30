"""
访问日志记录器 — 以 Common Log Format 记录 HTTP 请求日志。

Common Log Format (CLF) 格式:
    <host> - <user> [<timestamp>] "<method> <path> <version>" <status> <size>

示例:
    127.0.0.1 - admin [22/Jun/2026:15:30:45 +0800] "GET /index.html HTTP/1.1" 200 2048
    192.168.1.1 - - [22/Jun/2026:15:30:46 +0800] "POST /login HTTP/1.1" 302 0

本模块支持:
- 控制台输出（始终启用）
- 可选的文件日志输出
- 线程安全的日志写入
"""

import threading
from datetime import datetime, timezone, timedelta
from typing import Optional


class AccessLogger:
    """
    CLF 格式访问日志记录器。

    线程安全：使用 threading.Lock 保护日志文件写入操作。
    多个工作线程同时调用 log() 方法时不会产生竞争条件。

    使用示例:
        logger = AccessLogger({"enabled": True, "log_file": "access.log"})
        logger.log(request, 200, 1024, "127.0.0.1", "admin")
    """

    def __init__(self, config: dict):
        """
        初始化日志记录器。

        参数:
            config: 日志配置字典
                - enabled (bool): 是否启用日志 (默认 True)
                - log_file (str|None): 日志文件路径 (None 表示仅控制台)
                - format (str): 日志格式 (当前仅支持 "common")
        """
        self._enabled = config.get("enabled", True)
        self._log_file_path: Optional[str] = config.get("log_file", None)
        self._format = config.get("format", "common")
        self._lock = threading.Lock()  # 线程安全锁

        # 内存环形缓冲区：保留最近 N 行日志，供管理面板查询
        self._log_buffer: list[str] = []
        self._max_buffer_lines = config.get("buffer_lines", 500)

        # 初始化日志文件句柄
        self._file_handle = None
        if self._log_file_path:
            try:
                self._file_handle = open(self._log_file_path, "a", encoding="utf-8")
            except OSError as e:
                print(f"[警告] 无法打开日志文件 {self._log_file_path}: {e}")
                self._file_handle = None

    # ------------------------------------------------------------------
    # 公开方法
    # ------------------------------------------------------------------

    def log(
        self,
        request,           # HttpRequest
        status_code: int,
        response_size: int,
        client_ip: str,
        username: str = "-",
    ) -> None:
        """
        记录一条 HTTP 访问日志。

        参数:
            request: HttpRequest 对象
            status_code: HTTP 响应状态码
            response_size: 响应体字节数
            client_ip: 客户端 IP 地址
            username: 认证用户名（未认证时为 "-"）
        """
        if not self._enabled:
            return

        log_line = self._format_log(
            client_ip=client_ip,
            username=username,
            timestamp=self._get_timestamp(),
            method=request.method.value,
            path=request.path,
            query_string=request.query_string,
            http_version=request.http_version,
            status_code=status_code,
            response_size=response_size,
        )

        # 始终输出到控制台
        print(log_line)

        # 写入内存环形缓冲区
        self._log_buffer.append(log_line)
        if len(self._log_buffer) > self._max_buffer_lines:
            self._log_buffer = self._log_buffer[-self._max_buffer_lines:]

        # 同时写入日志文件
        if self._file_handle:
            with self._lock:
                try:
                    self._file_handle.write(log_line + "\n")
                    self._file_handle.flush()
                except OSError:
                    pass  # 日志写入失败不应影响请求处理

    def log_error(self, message: str) -> None:
        """
        记录服务器错误消息。

        用于记录非请求层面的错误（启动失败、配置错误、绑定失败等）。

        参数:
            message: 错误描述信息
        """
        timestamp = self._get_timestamp()
        log_line = f"[{timestamp}] [ERROR] {message}"
        print(log_line)

        if self._file_handle:
            with self._lock:
                try:
                    self._file_handle.write(log_line + "\n")
                    self._file_handle.flush()
                except OSError:
                    pass

    def log_info(self, message: str) -> None:
        """
        记录服务器信息消息。

        用于记录服务器启动、关闭等运行时事件。

        参数:
            message: 信息描述
        """
        timestamp = self._get_timestamp()
        log_line = f"[{timestamp}] [INFO] {message}"
        print(log_line)

        if self._file_handle:
            with self._lock:
                try:
                    self._file_handle.write(log_line + "\n")
                    self._file_handle.flush()
                except OSError:
                    pass

    def close(self) -> None:
        """关闭日志文件句柄，应在服务器关闭时调用"""
        if self._file_handle:
            try:
                self._file_handle.close()
            except OSError:
                pass
            self._file_handle = None

    def get_recent_logs(self, lines: int = 200) -> list[str]:
        """
        获取最近 N 行日志记录。

        用于管理面板的日志查看功能。
        线程安全：使用日志锁保护缓冲区读取。

        参数:
            lines: 需要获取的最近日志行数（默认 200）

        返回:
            list[str]: 最近 N 行日志（按时间升序排列）
        """
        with self._lock:
            return list(self._log_buffer[-lines:])

    # ------------------------------------------------------------------
    # 内部方法
    # ------------------------------------------------------------------

    def _format_log(
        self,
        client_ip: str,
        username: str,
        timestamp: str,
        method: str,
        path: str,
        query_string: str,
        http_version: str,
        status_code: int,
        response_size: int,
    ) -> str:
        """
        格式化为 Common Log Format 字符串。

        CLF 格式:
            <host> - <user> [<timestamp>] "<method> <path>[?<query>] <version>" <status> <size>
        """
        # 构建完整的请求目标（包含查询字符串）
        request_target = path
        if query_string:
            request_target += f"?{query_string}"

        # 响应大小为 0 时显示 "-" (常见于重定向响应)
        size_str = str(response_size) if response_size > 0 else "-"

        return (
            f'{client_ip} - {username} [{timestamp}] '
            f'"{method} {request_target} {http_version}" '
            f'{status_code} {size_str}'
        )

    def _get_timestamp(self) -> str:
        """
        获取当前时间的 CLF 格式时间戳。

        格式: dd/Mon/yyyy:HH:MM:SS +TZOFFSET
        例如: 22/Jun/2026:15:30:45 +0800

        使用本地时区以方便阅读。
        """
        now = datetime.now()
        # 计算 UTC 偏移
        utc_offset = now.astimezone().utcoffset()
        if utc_offset is None:
            offset_str = "+0000"
        else:
            total_seconds = int(utc_offset.total_seconds())
            sign = "+" if total_seconds >= 0 else "-"
            hours = abs(total_seconds) // 3600
            minutes = (abs(total_seconds) % 3600) // 60
            offset_str = f"{sign}{hours:02d}{minutes:02d}"

        return now.strftime(f"%d/%b/%Y:%H:%M:%S {offset_str}")
