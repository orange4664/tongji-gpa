# 同济大学成绩自动查询

不打开浏览器，用统一身份认证账号密码自动登录 `1.tongji.edu.cn`，拉取全部学期成绩。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 直接运行（首次会交互式引导输入学号密码并保存）
python3 query_grades_auto.py
```

首次运行时，脚本会提示输入学号和密码（密码输入时不显示），自动保存到 `.env`（已被 `.gitignore` 忽略，不会提交）。之后再次运行会询问：

```
已检测到已保存的凭据：学号 2351358
是否使用已保存的凭据？(Y 使用旧的 / n 重新输入) [Y]:
```

- 直接回车 → 用已保存的凭据
- 输入 `n` → 重新输入并更新

> 交互式配置只在**不带任何参数**运行时触发。带参数（如 `--term`、`--watch`）或在 CI 环境运行时，照旧从环境变量 / `.env` 读取，不交互。

## 用法

```bash
python3 query_grades_auto.py              # 全部学期
python3 query_grades_auto.py --term 20252 # 只看某学期（calName，如 20252）
python3 query_grades_auto.py --json       # 原始 JSON，便于管道/二次处理
python3 query_grades_auto.py --save-cookie # 登录后把 session cookie 存盘，下次免登录复用
python3 query_grades_auto.py --student-id 2351358  # 显式指定学号（留空则自动获取）
python3 query_grades_auto.py --no-reuse   # 忽略已存 cookie，强制重新登录
```

凭据也可通过环境变量传入，不必用 `.env`：

```bash
TONGJI_USERNAME=学号 TONGJI_PASSWORD=密码 python3 query_grades_auto.py
```

## 安全提示

- `.env` 已在 `.gitignore` 中，**不会**被提交。请勿把含密码的 `.env` 上传到任何公开仓库。
- `.tongji_session_cookie.txt` 存有登录后的 session cookie，同样在 `.gitignore` 中。
- 脚本不向任何第三方服务发送数据，仅与 `1.tongji.edu.cn` 和 `iam.tongji.edu.cn` 通信。
- session cookie 是会话型，浏览器关闭/退出登录后失效；失效时脚本会自动重新登录。

## 登录原理

同济统一身份认证（`iam.tongji.edu.cn`）走 OAuth2 授权码流程，密码用 RSA 加密。脚本复刻了浏览器登录的全部步骤：

1. 访问 `1.tongji.edu.cn` SSO 入口 → 跳到 IdP 登录页，提取 `authnLcKey` 与 `spAuthChainCode`
2. RSA 加密密码，POST `/idp/authcenter/ActionAuthChain` 验证账密
3. POST `/idp/AuthnEngine` 触发 OAuth 回跳，拿到 `token` / `uid` / `ts`
4. POST `1.tongji.edu.cn/api/sessionservice/session/login` 用 token 换 `sessionid`
5. 带 sessionid 调 `scoremanagementservice/scoreGrades/getMyGrades` 拉取成绩

## 已知限制

- **验证码**：正常登录无需验证码。若账号触发风控（多次失败等），IdP 会要求验证码，脚本暂不支持，请改用浏览器登录一次解除风控。
- **二级认证**（短信/OTP 等）：暂不支持。
- **首次登录改密**：若系统要求修改密码，请先在浏览器登录处理一次。

## 成绩变动监控 + 推送通知

脚本支持 `--watch` 监控模式：每次运行对比上次成绩快照，发现**新增课程**或**成绩更新**时推送通知。支持邮件和微信（Server酱），可只配一种。

### 本地定时监控（推荐，凭据不出本机）

```bash
# 1. 配置凭据（账号密码）
cat > .env <<EOF
TONGJI_USERNAME=你的学号
TONGJI_PASSWORD=你的统一身份认证密码
EOF

# 2. 配置通知（按需二选一或都配）
# 邮件（以 QQ 邮箱为例，需在 QQ 邮箱设置开启 SMTP 并获取授权码）
echo 'TONGJI_SMTP_HOST=smtp.qq.com' >> .env
echo 'TONGJI_SMTP_PORT=465' >> .env
echo 'TONGJI_SMTP_USER=你的@qq.com' >> .env
echo 'TONGJI_SMTP_PASS=QQ邮箱授权码' >> .env
echo 'TONGJI_MAIL_TO=收件人@xx.com' >> .env

# Server酱微信（访问 https://sct.ftqq.com 注册拿 SendKey）
echo 'TONGJI_SCKEY=你的SendKey' >> .env

# 3. 每 5 小时跑一次（macOS/Linux crontab）
#   crontab -e 后加入（每 5 小时的第 0 分）：
#   0 */5 * * * cd /path/to/tongji-gpa && python3 query_grades_auto.py --watch >> watch.log 2>&1
```

### GitHub Actions 定时监控（开源用户推荐）

仓库已内置 `.github/workflows/grades-watch.yml`，每 5 小时自动跑一次。Fork 或使用本仓库后，在 **Settings → Secrets and variables → Actions** 添加以下 Secrets（按需配置）：

| Secret | 必填 | 说明 |
|--------|------|------|
| `TONGJI_USERNAME` | ✅ | 学号 |
| `TONGJI_PASSWORD` | ✅ | 统一身份认证密码 |
| `TONGJI_SMTP_HOST` | 邮件 | SMTP 主机，如 `smtp.qq.com` |
| `TONGJI_SMTP_PORT` | 邮件 | 端口，SSL 默认 `465` |
| `TONGJI_SMTP_USER` | 邮件 | 发件邮箱 |
| `TONGJI_SMTP_PASS` | 邮件 | 邮箱授权码（非登录密码） |
| `TONGJI_MAIL_TO` | 邮件 | 收件邮箱，多个用逗号分隔 |
| `TONGJI_SCKEY` | 微信 | Server酱 SendKey（sct.ftqq.com） |

配置后在 **Actions** 页面可手动触发 `成绩监控` workflow 测试。首次运行只建立快照不推送，之后有成绩变动才会通知。

> **注意**：GitHub Actions 的 `schedule` 在公开仓库高峰期可能有十几分钟延迟，甚至被降频。若需严格定时，建议用本地 crontab。
> 成绩快照通过 `actions/cache` 持久化，不会暴露到公开仓库。

## 文件说明

| 文件 | 作用 |
|------|------|
| `query_grades_auto.py` | 全自动登录版主脚本（推荐） |
| `query_grades.py` | 早期纯 Cookie 版（需手动从浏览器取 cookie，保留作备用） |
| `接口分析.md` | 接口逆向分析文档 |
| `requirements.txt` | Python 依赖 |
| `.gitignore` | 排除 `.env`、cookie 等敏感文件 |
