# SimpleWebServer 测试计划

## 测试环境

- **服务器**: SimpleWebServer/1.0 (Python 3.14.3)
- **默认地址**: http://127.0.0.1:8080
- **测试工具**: curl (命令行), 浏览器 (图形界面)

## 测试用例列表

### 1. 基础功能测试

| 编号 | 测试项 | 请求 | 预期结果 |
|------|--------|------|----------|
| T01 | 首页访问 | `GET /` | 200, 返回 index.html |
| T02 | 静态文件 | `GET /index.html` | 200, Content-Type: text/html |
| T03 | 根路径默认页面 | `GET /` | 200, 自动返回 index.html |
| T04 | 子目录默认页面 | `GET /admin/` | 200 或 302(未认证) |

### 2. 错误处理测试

| 编号 | 测试项 | 请求 | 预期结果 |
|------|--------|------|----------|
| T05 | 页面不存在 | `GET /nonexistent.html` | 404, 显示自定义 404 页面 |
| T06 | 不支持的 HTTP 方法 | `PUT /` | 405 Method Not Allowed |
| T07 | POST 到静态文件 | `POST /index.html` | 405 Method Not Allowed |
| T08 | 格式错误的请求 | `printf "INVALID\r\n\r\n" \| nc 127.0.0.1 8080` | 400 Bad Request |

### 3. 安全测试

| 编号 | 测试项 | 请求 | 预期结果 |
|------|--------|------|----------|
| T09 | 路径遍历 | `GET /../../../etc/passwd` | 403 Forbidden |
| T10 | URL 编码绕过 | `GET /%2e%2e%2f` | 403 Forbidden |
| T11 | 空字节注入 | `GET /%00index.html` | 400 Bad Request |
| T12 | 过大的请求行 | 发送 > 8192 字节的 URI | 414 URI Too Long |

### 4. 认证测试 — Session 登录

| 编号 | 测试项 | 步骤 | 预期结果 |
|------|--------|------|----------|
| T13 | 未认证访问受保护资源 | `GET /admin/` | 302 → /login |
| T14 | 获取登录页面 | `GET /login` | 200, 显示登录表单 |
| T15 | 正确凭据登录 | `POST /login` (admin/admin123) | 302, Set-Cookie: session_token |
| T16 | 已认证访问 | `GET /admin/` (带 Cookie) | 200, 显示管理页面 |
| T17 | 错误凭据登录 | `POST /login` (admin/wrong) | 302 → /login?error=1 |
| T18 | 注销 | `GET /logout` (带 Cookie) | 302, 清除 Cookie |
| T19 | 注销后访问 | `GET /admin/` (旧 Cookie) | 302 → /login |

### 5. 并发测试

| 编号 | 测试项 | 方法 | 预期结果 |
|------|--------|------|----------|
| T20 | 10 并发请求 | 10 个并发 `curl` 进程 | 全部返回 200 |
| T21 | 50 并发请求 | 50 个并发 `curl` 进程 | 全部返回 200 (无崩溃) |
| T22 | 慢速连接 | `sleep 60 \| nc 127.0.0.1 8080` | 30 秒后超时断开 |

### 6. 文件上传测试

| 编号 | 测试项 | 请求 | 预期结果 |
|------|--------|------|----------|
| T23 | 上传小文件 | `POST /upload -F "file=@test.txt"` | 200, 文件保存到 uploads/ |
| T24 | 空上传 | `POST /upload` (无文件) | 400, 错误提示 |

## 浏览器测试步骤

1. 打开浏览器，访问 `http://127.0.0.1:8080/`
   - 验证: 首页正确显示，CSS 样式正常
2. 点击导航链接
   - 验证: 页面切换正常
3. 访问 `http://127.0.0.1:8080/nonexistent`
   - 验证: 显示美观的 404 错误页面
4. 点击 "管理区域" 或直接访问 `http://127.0.0.1:8080/admin/`
   - 验证: 自动重定向到登录页面
5. 使用测试账户登录: admin / admin123
   - 验证: 登录成功后重定向到目标页面
6. 点击 "退出登录"
   - 验证: 重定向到首页，Cookie 被清除

## 自动化测试

运行 curl 测试脚本:
```bash
cd C:\Users\Administrator\SimpleWebServer
bash tests/test_requests.sh
```

运行并发测试:
```bash
for i in {1..50}; do
    curl -s -o /dev/null -w "%{http_code}\n" http://127.0.0.1:8080/ &
done
wait
# 预期: 50 行 "200"
```
