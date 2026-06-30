#!/bin/bash
# ============================================================
# SimpleWebServer 手动测试脚本
# 使用 curl 验证所有核心功能
#
# 用法:
#   1. 先启动服务器: cd C:\Users\Administrator\SimpleWebServer && py server.py
#   2. 在此目录运行: bash tests/test_requests.sh
#   3. 或逐条执行以下命令
# ============================================================

BASE_URL="http://127.0.0.1:8080"
PASS=0
FAIL=0

# 颜色输出 (Git Bash / Linux / macOS)
GREEN="\033[32m"
RED="\033[31m"
YELLOW="\033[33m"
RESET="\033[0m"

pass() { echo -e "${GREEN}[PASS]${RESET} $1"; PASS=$((PASS + 1)); }
fail() { echo -e "${RED}[FAIL]${RESET} $1 (expected $2, got $3)"; FAIL=$((FAIL + 1)); }

echo "============================================================"
echo "  SimpleWebServer — 功能测试套件"
echo "  目标: $BASE_URL"
echo "============================================================"
echo ""

# ---- 测试 1: GET 首页 ----
echo "--- 测试 1: GET 首页 (200 OK) ---"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/")
if [ "$STATUS" = "200" ]; then pass "GET / → 200 OK"; else fail "GET /" "200" "$STATUS"; fi

# ---- 测试 2: GET 不存在的页面 ----
echo "--- 测试 2: GET 不存在的页面 (404) ---"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/nonexistent_page.html")
if [ "$STATUS" = "404" ]; then pass "GET /nonexistent_page.html → 404"; else fail "GET /nonexistent_page.html" "404" "$STATUS"; fi

# ---- 测试 3: 路径遍历攻击 ----
echo "--- 测试 3: 路径遍历攻击防御 (403) ---"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" --path-as-is "$BASE_URL/../../../etc/passwd")
if [ "$STATUS" = "403" ]; then
    pass "路径遍历: /../../../etc/passwd → 403"
else
    fail "路径遍历" "403" "$STATUS"
fi

# ---- 测试 4: URL 编码路径遍历 ----
echo "--- 测试 4: URL 编码路径遍历 (403) ---"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" --path-as-is "$BASE_URL/%2e%2e%2f%2e%2e%2f")
if [ "$STATUS" = "403" ]; then
    pass "URL 编码路径遍历 → 403"
else
    fail "URL 编码路径遍历" "403" "$STATUS"
fi

# ---- 测试 5: 未登录访问受保护资源 ----
echo "--- 测试 5: 未登录访问 /admin/ (302 重定向) ---"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -L --max-redirs 0 "$BASE_URL/admin/")
if [ "$STATUS" = "302" ]; then
    pass "未认证 GET /admin/ → 302 重定向"
else
    fail "未认证 GET /admin/" "302" "$STATUS"
fi

# ---- 测试 6: 登录流程 (正确凭据) ----
echo "--- 测试 6: 登录 (正确凭据) ---"
COOKIE_JAR=$(mktemp)
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/login" \
    -d "username=admin&password=admin123" \
    -c "$COOKIE_JAR")
if [ "$STATUS" = "302" ]; then
    pass "POST /login (正确凭据) → 302 重定向"
else
    fail "POST /login (正确凭据)" "302" "$STATUS"
fi

# ---- 测试 7: 登录后访问受保护资源 ----
echo "--- 测试 7: 使用 Session Cookie 访问 /admin/ (200) ---"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -b "$COOKIE_JAR" "$BASE_URL/admin/")
if [ "$STATUS" = "200" ]; then
    pass "已认证 GET /admin/ → 200 OK"
else
    fail "已认证 GET /admin/" "200" "$STATUS"
fi

# ---- 测试 8: 登录流程 (错误凭据) ----
echo "--- 测试 8: 登录 (错误凭据) ---"
LOCATION=$(curl -s -o /dev/null -w "%{redirect_url}" -X POST "$BASE_URL/login" \
    -d "username=admin&password=wrongpassword")
if echo "$LOCATION" | grep -q "error=1"; then
    pass "POST /login (错误凭据) → 重定向到 /login?error=1"
else
    fail "POST /login (错误凭据)" "/login?error=1" "$LOCATION"
fi

# ---- 测试 9: 注销登录 ----
echo "--- 测试 9: 注销 ---"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -b "$COOKIE_JAR" "$BASE_URL/logout")
if [ "$STATUS" = "302" ]; then pass "GET /logout → 302"; else fail "GET /logout" "302" "$STATUS"; fi

# ---- 测试 10: 注销后无法访问受保护资源 ----
echo "--- 测试 10: 注销后访问 /admin/ (302) ---"
# 使用同一个 cookie jar（logout 后会清除），重新测试
COOKIE_JAR2=$(mktemp)
# 先登录
curl -s -X POST "$BASE_URL/login" -d "username=admin&password=admin123" -c "$COOKIE_JAR2" > /dev/null
# 再登出
curl -s -b "$COOKIE_JAR2" "$BASE_URL/logout" > /dev/null
# 再尝试访问
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -b "$COOKIE_JAR2" "$BASE_URL/admin/")
if [ "$STATUS" = "302" ]; then
    pass "注销后 GET /admin/ → 302 重定向"
else
    fail "注销后 GET /admin/" "302" "$STATUS"
fi
rm -f "$COOKIE_JAR" "$COOKIE_JAR2"

# ---- 测试 11: POST 方法不允许 ----
echo "--- 测试 11: POST 到静态文件 (405) ---"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X POST "$BASE_URL/index.html" \
    -d "data=test")
if [ "$STATUS" = "405" ]; then
    pass "POST /index.html → 405 Method Not Allowed"
else
    fail "POST /index.html" "405" "$STATUS"
fi

# ---- 测试 12: 并发请求 ----
echo "--- 测试 12: 并发请求 (10 个并发) ---"
RESULTS=$(for i in 1 2 3 4 5 6 7 8 9 10; do
    curl -s -o /dev/null -w "%{http_code}\n" "$BASE_URL/" &
done; wait)
OK_COUNT=$(echo "$RESULTS" | grep -c "200")
if [ "$OK_COUNT" = "10" ]; then
    pass "并发 10 个 GET / → 全部 200"
else
    fail "并发 10 个 GET /" "全部 200" "${OK_COUNT}/10 成功"
fi

# ---- 测试 13: GET 登录页面 ----
echo "--- 测试 13: GET 登录页面 (200) ---"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/login")
if [ "$STATUS" = "200" ]; then pass "GET /login → 200"; else fail "GET /login" "200" "$STATUS"; fi

# ---- 测试 14: 不支持的 HTTP 方法 ----
echo "--- 测试 14: PUT 请求 (405) ---"
STATUS=$(curl -s -o /dev/null -w "%{http_code}" -X PUT "$BASE_URL/")
if [ "$STATUS" = "405" ]; then pass "PUT / → 405"; else fail "PUT /" "405" "$STATUS"; fi

# ---- 测试 15: 空请求体 ----
echo "--- 测试 15: 空 POST /login (302 error=1) ---"
LOCATION=$(curl -s -o /dev/null -w "%{redirect_url}" -X POST "$BASE_URL/login" \
    -d "")
if echo "$LOCATION" | grep -q "error=1"; then
    pass "POST /login (空) → /login?error=1"
else
    fail "POST /login (空)" "/login?error=1" "$LOCATION"
fi

# ---- 结果汇总 ----
echo ""
echo "============================================================"
echo "  测试结果: ${PASS} 通过, ${FAIL} 失败, $((PASS + FAIL)) 总计"
if [ "$FAIL" = "0" ]; then
    echo -e "  ${GREEN}全部通过! ✓${RESET}"
else
    echo -e "  ${RED}存在失败测试 ✗${RESET}"
fi
echo "============================================================"
