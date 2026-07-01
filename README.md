# 同济大学成绩自动查询

不打开浏览器，用统一身份认证账号密码自动登录 `1.tongji.edu.cn`，拉取全部学期成绩。

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置凭据（在同目录创建 .env）
cat > .env <<EOF
TONGJI_USERNAME=你的学号
TONGJI_PASSWORD=你的统一身份认证密码
EOF

# 3. 运行
python3 query_grades_auto.py
```

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

## 文件说明

| 文件 | 作用 |
|------|------|
| `query_grades_auto.py` | 全自动登录版主脚本（推荐） |
| `query_grades.py` | 早期纯 Cookie 版（需手动从浏览器取 cookie，保留作备用） |
| `接口分析.md` | 接口逆向分析文档 |
| `requirements.txt` | Python 依赖 |
| `.gitignore` | 排除 `.env`、cookie 等敏感文件 |
