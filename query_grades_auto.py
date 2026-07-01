#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
同济大学成绩自动查询（全自动登录版）
======================================
不打开浏览器，用统一身份认证账号密码自动登录 1.tongji.edu.cn，拉取全部成绩。

依赖：
    pip install cryptography requests
    （cryptography 用于 RSA 加密密码，requests 处理 cookie/重定向）

凭据：
    在脚本同目录创建 .env 文件，内容：
        TONGJI_USERNAME=你的学号
        TONGJI_PASSWORD=你的统一身份认证密码
    或通过环境变量传入。.env 已在 .gitignore 中，不会被提交。

用法：
    python3 query_grades_auto.py              # 全部学期
    python3 query_grades_auto.py --term 20252 # 只看某学期（calName）
    python3 query_grades_auto.py --json       # 原始 JSON
    python3 query_grades_auto.py --save-cookie # 登录后把 cookie 存盘，下次可复用

登录原理：
    1. 访问 1.tongji.edu.cn/api/ssoservice/system/loginIn → 302 到 iam.tongji.edu.cn
       (OAuth2 authorize)
    2. 跟到 AuthnEngine 登录页，拿到 authnLcKey
    3. 用硬编码 RSA 公钥加密密码，POST j_username/j_password
    4. 登录成功返回 view=none，表单再 submit → IdP 302 带 code 回跳
    5. 1.tongji.edu.cn 用 code 换发 sessionid cookie → 即可查成绩
"""

import argparse
import base64
import hashlib
import json
import os
import re
import smtplib
import sys
import time
from email.mime.text import MIMEText
from typing import Optional
from urllib.parse import quote, urlparse, parse_qs

try:
    import requests
except ImportError:
    sys.exit("缺少依赖 requests，请运行：pip install requests")

try:
    from cryptography.hazmat.primitives.asymmetric import padding
    from cryptography.hazmat.primitives.serialization import load_der_public_key
except ImportError:
    sys.exit("缺少依赖 cryptography，请运行：pip install cryptography")


# ── 常量 ──────────────────────────────────────────────────────────────
BASE = "https://1.tongji.edu.cn"
SSO_ENTRY = f"{BASE}/api/ssoservice/system/loginIn"
IAM = "https://iam.tongji.edu.cn"
GRADES_URL = f"{BASE}/api/scoremanagementservice/scoreGrades/getMyGrades"
CLIENT_ID = "SYS20230001"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")

# 同济统一身份认证登录页硬编码的 RSA 公钥（来自 iam 的 crypt.js，固定不变）
# Base64 编码的 DER 格式 SubjectPublicKeyInfo
RSA_PUBKEY_B64 = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC9t16RqQWUE/J1IyOfoNHc4r/h"
    "6RPnXcWTJ4IbhQVUsEqMMm65F0hiytAgozXmVw68yPJywbpblDrx9zl1wdRcdHCo"
    "UvmPdr9/oCQtpQyVc7BXZIN6wJlD6MTeMeni+N0toNPxfXjiAawjNHGZZuT8wQpN"
    "EMwsVyJ/lonXaVdGZwIDAQAB"
)

COOKIE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           ".tongji_session_cookie.txt")


# ── 凭据加载 ───────────────────────────────────────────────────────────
def load_env(path=None):
    """简易 .env 解析（不依赖 python-dotenv）。"""
    if path is None:
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip().strip("'\""))


def get_credentials():
    user = os.environ.get("TONGJI_USERNAME")
    pwd = os.environ.get("TONGJI_PASSWORD")
    if not user or not pwd:
        sys.exit("未找到凭据。请在 .env 中设置 TONGJI_USERNAME / TONGJI_PASSWORD，"
                 "或通过环境变量传入。")
    return user, pwd


# ── RSA 加密（对齐前端 JSEncrypt：PKCS1v15）────────────────────────────
def rsa_encrypt(plaintext: str) -> str:
    der = base64.b64decode(RSA_PUBKEY_B64)
    pub = load_der_public_key(der)
    ct = pub.encrypt(plaintext.encode("utf-8"),
                     padding.PKCS1v15())
    # JSEncrypt 输出 base64（无换行）
    return base64.b64encode(ct).decode("ascii")


# ── 登录链路 ───────────────────────────────────────────────────────────
# 登录原理（已实测验证）：
#   1. GET 1.tongji SSO 入口 → 302 链 → iam.tongji.edu.cn 登录页 HTML
#      从 HTML 提取 authnLcKey 和 spAuthChainCode（spAuthChainCode 在内联 JS
#      `$(spCode).val('...')` 里，服务端每次动态生成，必须带上否则 loginFailed）
#   2. POST /idp/authcenter/ActionAuthChain（AJAX 验证账密，密码 RSA 加密）
#      → {"loginFailed":"false"} 表示账密正确
#   3. POST /idp/AuthnEngine（form_submit）→ 302 链回跳到
#      1.tongji.edu.cn/ssologin?token=&uid=&ts=
#   4. POST /api/sessionservice/session/login {uid,token,ts} → 拿到 sessionid
def login(student_id_for_hint="") -> requests.Session:
    """
    完成统一身份认证 OAuth 登录，返回已带 sessionid 的 requests.Session。
    失败抛出带提示的异常。
    """
    username, password = get_credentials()
    s = requests.Session()
    s.headers.update({"User-Agent": UA})

    # 1) 跟随 SSO 到 IdP 登录页
    r = s.get(SSO_ENTRY, allow_redirects=True, timeout=20)
    html = r.text
    if "系统登录" not in html and "j_password" not in html:
        raise RuntimeError("未拿到 IdP 登录页，可能已登录或 IdP 改版。")

    # 2) 提取 authnLcKey 与 spAuthChainCode
    m = re.search(r"name=['\"]authnLcKey['\"][^>]*value=['\"]([a-f0-9-]+)['\"]", html) \
        or re.search(r"authnLcKey=([a-f0-9-]+)", html)
    if not m:
        raise RuntimeError("登录页未找到 authnLcKey。")
    authn_lckey = m.group(1)

    # spAuthChainCode 藏在登录页内联 JS: $(spCode).val('xxxx')
    m_sp = re.search(r"\$\(spCode\)\.val\(['\"]([a-f0-9]+)['\"]\)", html) \
        or re.search(r"\.val\(['\"]([a-f0-9]{32})['\"]\)", html)
    sp_code = m_sp.group(1) if m_sp else ""
    if not sp_code:
        raise RuntimeError("登录页未找到 spAuthChainCode。")

    # 3) 构造登录表单。密码 RSA 加密后裸 base64 直接拼（对齐前端 jQuery
    #    serialize+replace 逻辑，不做二次 URL 编码）；j_checkcode 用占位符编码
    encrypted_pwd = rsa_encrypt(password)
    form_body = (
        f"j_username={quote(username, safe='')}"
        f"&j_password={encrypted_pwd}"
        f"&j_checkcode={quote('请输入验证码', safe='')}"
        f"&op=login"
        f"&spAuthChainCode={sp_code}"
        f"&authnLcKey={authn_lckey}"
    )
    ajax_headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "Referer": r.url,
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json, text/javascript, */*; q=0.01",
    }

    # 4) AJAX 验证账密
    r1 = s.post(f"{IAM}/idp/authcenter/ActionAuthChain?authnLcKey={authn_lckey}",
                data=form_body, headers=ajax_headers, timeout=20)
    try:
        result = r1.json()
    except Exception:
        raise RuntimeError(f"ActionAuthChain 返回非 JSON：{r1.text[:200]}")
    if str(result.get("loginFailed")).lower() == "true":
        tip = _parse_err_tip(r1.text)
        raise RuntimeError(f"账号或密码错误（loginFailed=true {tip}）。")
    # loginFailed == "false" → 账密正确，继续

    # 5) form_submit → 跟随 302 链到 1.tongji.edu.cn/ssologin?token=&uid=&ts=
    r2 = s.post(
        f"{IAM}/idp/AuthnEngine?"
        f"currentAuth=urn_oasis_names_tc_SAML_2.0_ac_classes_BAMUsernamePassword"
        f"&authnLcKey={authn_lckey}&entityId={CLIENT_ID}",
        data=form_body, allow_redirects=True, timeout=20)
    qs = parse_qs(urlparse(r2.url).query)
    token = qs.get("token", [None])[0]
    uid = qs.get("uid", [None])[0]
    ts = qs.get("ts", [None])[0]
    if not (token and uid and ts):
        raise RuntimeError(f"未从 ssologin 回跳 URL 取到 token/uid/ts：{r2.url}")

    # 6) 用 token 换 sessionid
    r3 = s.post(f"{BASE}/api/sessionservice/session/login",
                json={"uid": uid, "token": token, "ts": ts},
                headers={"Content-Type": "application/json",
                         "X-Requested-With": "XMLHttpRequest"}, timeout=20)
    try:
        data = r3.json().get("data", {}) or {}
    except Exception:
        raise RuntimeError(f"session/login 返回非 JSON：{r3.text[:200]}")
    if not data.get("sessionid"):
        raise RuntimeError(f"未拿到 sessionid：{r3.text[:200]}")
    # data.uid 即学号（如 "2351358"），挂到 session 上供 fetch_grades 复用
    s.student_id = data.get("uid") or ""
    return s


def _parse_err_tip(text: str):
    for pat in (r'"loginErrorHandlerErrorTip"\s*:\s*"([^"]+)"',
                r'"authenticationErrorTip"\s*:\s*"([^"]+)"',
                r'errorTip["\s:]*"([^"]+)"'):
        m = re.search(pat, text)
        if m:
            return m.group(1)
    return None


# ── 成绩查询 ───────────────────────────────────────────────────────────
def fetch_grades(session: requests.Session, student_id: str) -> dict:
    # student_id 优先级：显式传入 > login 返回的 uid（学号）> currentAuthId 反查
    if not student_id:
        student_id = getattr(session, "student_id", "") or ""
    if not student_id:
        student_id = resolve_student_id(session)
    url = f"{GRADES_URL}?studentId={student_id}&_t={int(time.time()*1000)}"
    r = session.get(url, headers={"Referer": f"{BASE}/oldStysteMyGrades"},
                    timeout=20)
    if r.status_code == 401:
        raise RuntimeError("sessionid 失效，请重新登录。")
    r.raise_for_status()
    return r.json()


def resolve_student_id(session: requests.Session) -> str:
    """通过 currentAuthId 接口从 key 字段反查学号。"""
    r = session.post(f"{BASE}/api/sessionservice/session/currentAuthId",
                     timeout=20)
    r.raise_for_status()
    d = r.json().get("data", {})
    key = d.get("key", "")  # 形如 <sessionid>_<studentId>
    sid = key.split("_", 1)[1] if "_" in key else ""
    if not sid:
        raise RuntimeError("无法自动获取学号，请用 --student-id 指定。")
    return sid


# ── Cookie 持久化（可选，省去每次登录）────────────────────────────────
def save_cookie(session: requests.Session):
    ck = "; ".join(f"{c.name}={c.value}" for c in session.cookies
                   if c.name in ("sessionid", "JSESSIONID", "language"))
    sid = getattr(session, "student_id", "") or ""
    with open(COOKIE_FILE, "w", encoding="utf-8") as f:
        f.write(f"student_id={sid}\n{ck}")
    print(f"[*] Cookie 已保存到 {COOKIE_FILE}（会话型，失效后重登录）", file=sys.stderr)


def load_cookie_session() -> Optional[requests.Session]:
    if not os.path.exists(COOKIE_FILE):
        return None
    with open(COOKIE_FILE, "r", encoding="utf-8") as f:
        content = f.read().strip()
    if not content:
        return None
    lines = content.splitlines()
    sid = ""
    cookie_lines = []
    for ln in lines:
        if ln.startswith("student_id="):
            sid = ln.split("=", 1)[1]
        else:
            cookie_lines.append(ln)
    ck = "; ".join(l.strip() for l in cookie_lines if l.strip())
    if not ck:
        return None
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Cookie": ck})
    s.student_id = sid
    return s


# ── 输出 ───────────────────────────────────────────────────────────────
def print_summary(data: dict, only_term: str, as_json: bool):
    if as_json:
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    if data.get("code") != 200:
        sys.exit(f"接口返回异常：code={data.get('code')} msg={data.get('msg')}")
    d = data["data"]
    print("=" * 64)
    print(f"  GPA 总绩点: {d.get('totalGradePoint')}   "
          f"已修学分: {d.get('actualCredit')}   "
          f"挂科: {d.get('failingCourseCount')} 门 / {d.get('failingCredits')} 学分")
    print("=" * 64)
    terms = d.get("term", [])
    if only_term:
        terms = [t for t in terms if t.get("calName") == only_term]
        if not terms:
            sys.exit(f"未找到学期 calName={only_term}，"
                     f"可用：{[t.get('calName') for t in d.get('term', [])]}")
    for t in terms:
        print(f"\n【{t.get('termName')}】  学期均绩 {t.get('averagePoint')}  "
              f"课程 {len(t.get('creditInfo', []))} 门")
        print("-" * 64)
        print(f"{'课程':<28} {'成绩':<14} {'学分':<6} {'性质':<6} {'类别'}")
        print("-" * 64)
        for c in t.get("creditInfo", []):
            name = (c.get("courseName") or "")[:26]
            gp = c.get("gradePoint")
            score = f"{c.get('score') or c.get('scoreName')}" + (f" (绩点 {gp})" if gp not in (None, "") else "")
            print(f"{name:<28} {score:<14} {c.get('credit'):<6} "
                  f"{c.get('publicCoursesName') or '-':<6} {c.get('courseLabName') or '-'}")
        print()


# ── 成绩变动检测 ───────────────────────────────────────────────────────
def grades_signature(data: dict) -> dict:
    """把每门课的 (课程名+成绩+绩点+学分) 做指纹，用于对比变动。"""
    sig = {}
    for t in data.get("data", {}).get("term", []):
        for c in t.get("creditInfo", []):
            key = f"{t.get('calName')}|{c.get('courseCode')}|{c.get('courseName')}"
            val = f"{c.get('score') or c.get('scoreName')}|{c.get('gradePoint')}|{c.get('credit')}"
            sig[key] = val
    return sig


SNAPSHOT_FILE = os.environ.get("TONGJI_SNAPSHOT_FILE",
                               os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                            ".tongji_grades_snapshot.json"))


def detect_changes(data: dict) -> tuple:
    """对比快照，返回 (新增课程列表, 是否有变动)。首次运行返回 (全部, True)。"""
    new_sig = grades_signature(data)
    old_sig = {}
    if os.path.exists(SNAPSHOT_FILE):
        try:
            with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
                old_sig = json.load(f).get("sig", {})
        except Exception:
            old_sig = {}
    # 课名 → 详情映射，用于通知正文
    course_map = {}
    for t in data.get("data", {}).get("term", []):
        for c in t.get("creditInfo", []):
            k = f"{t.get('calName')}|{c.get('courseCode')}|{c.get('courseName')}"
            course_map[k] = {
                "term": t.get("termName"),
                "name": c.get("courseName"),
                "score": c.get("score") or c.get("scoreName"),
                "gradePoint": c.get("gradePoint"),
                "credit": c.get("credit"),
            }
    added, changed = [], []
    for k, v in new_sig.items():
        if k not in old_sig:
            added.append(course_map[k])
        elif old_sig[k] != v:
            changed.append({**course_map[k], "old": old_sig[k]})
    # 保存新快照
    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump({"sig": new_sig, "ts": int(time.time())}, f, ensure_ascii=False)
    return added, changed, not bool(old_sig)


# ── 通知推送 ───────────────────────────────────────────────────────────
def build_message(added: list, changed: list, is_first: bool, data: dict) -> tuple:
    """构造通知标题和正文。返回 (subject, body)。"""
    d = data.get("data", {})
    gpa = d.get("totalGradePoint")
    if is_first:
        subject = f"[同济成绩] 首次监控已建立，GPA {gpa}"
        body_lines = [f"首次监控快照已建立，当前 GPA：{gpa}，"
                      f"已修学分 {d.get('actualCredit')}，挂科 {d.get('failingCourseCount')} 门。",
                      "", "当前共记录 %d 门课程。后续有新成绩会推送。" % len(grades_signature(data))]
        return subject, "\n".join(body_lines)
    n = len(added) + len(changed)
    subject = f"[同济成绩] 发现 {n} 门成绩变动"
    body_lines = []
    if added:
        body_lines.append(f"新增 {len(added)} 门：")
        for c in added:
            body_lines.append(f"  • {c['term']} {c['name']}：{c['score']}（绩点 {c['gradePoint']}，{c['credit']} 学分）")
    if changed:
        body_lines.append(f"\n成绩更新 {len(changed)} 门：")
        for c in changed:
            body_lines.append(f"  • {c['term']} {c['name']}：{c['score']}（绩点 {c['gradePoint']}）")
    body_lines.append(f"\n当前 GPA：{gpa}，已修学分 {d.get('actualCredit')}，挂科 {d.get('failingCourseCount')} 门。")
    return subject, "\n".join(body_lines)


def send_email(subject: str, body: str) -> None:
    """SMTP 发邮件。环境变量：
        TONGJI_SMTP_HOST / TONGJI_SMTP_PORT / TONGJI_SMTP_USER / TONGJI_SMTP_PASS
        TONGJI_MAIL_TO（收件人，逗号分隔）
    """
    host = os.environ.get("TONGJI_SMTP_HOST")
    if not host:
        print("[*] 未配置 TONGJI_SMTP_HOST，跳过邮件通知", file=sys.stderr)
        return
    user = os.environ.get("TONGJI_SMTP_USER", "")
    port = int(os.environ.get("TONGJI_SMTP_PORT", "465"))
    to_list = [x.strip() for x in os.environ.get("TONGJI_MAIL_TO", "").split(",") if x.strip()]
    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = user
    msg["To"] = ", ".join(to_list)
    with smtplib.SMTP_SSL(host, port, timeout=20) as smtp:
        smtp.login(user, os.environ.get("TONGJI_SMTP_PASS", ""))
        smtp.sendmail(user, to_list, msg.as_string())
    print(f"[*] 邮件已发送到 {len(to_list)} 个收件人", file=sys.stderr)


def send_serverchan(subject: str, body: str) -> None:
    """Server酱推送到微信。环境变量 TONGJI_SCKEY（SendKey）。"""
    sckey = os.environ.get("TONGJI_SCKEY")
    if not sckey:
        print("[*] 未配置 TONGJI_SCKEY，跳过 Server酱通知", file=sys.stderr)
        return
    import requests as _rq
    url = f"https://sctapi.ftqq.com/{sckey}.send"
    r = _rq.post(url, data={"text": subject, "desp": body}, timeout=20)
    if r.status_code == 200 and r.json().get("code") == 0:
        print("[*] Server酱已推送到微信", file=sys.stderr)
    else:
        print(f"[!] Server酱推送失败：{r.status_code} {r.text[:120]}", file=sys.stderr)


def notify(subject: str, body: str) -> None:
    send_email(subject, body)
    send_serverchan(subject, body)


# ── 主流程 ─────────────────────────────────────────────────────────────
def main():
    load_env()
    ap = argparse.ArgumentParser(description="同济大学成绩自动查询（全自动登录）")
    ap.add_argument("--student-id", default="", help="学号，留空则自动获取")
    ap.add_argument("--term", help="只看某学期 calName，如 20252")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    ap.add_argument("--save-cookie", action="store_true",
                    help="登录后把 session cookie 存盘，下次免登录复用")
    ap.add_argument("--no-reuse", action="store_true",
                    help="忽略已存 cookie，强制重新登录")
    ap.add_argument("--watch", action="store_true",
                    help="监控模式：对比上次成绩快照，有变动或首次才推送通知")
    args = ap.parse_args()

    data = run(args)
    if args.watch:
        added, changed, is_first = detect_changes(data)
        subject, body = build_message(added, changed, is_first, data)
        print(f"[*] 变动：新增 {len(added)} 门，更新 {len(changed)} 门，首次={is_first}",
              file=sys.stderr)
        # 首次只建快照不推送，避免首次运行误报；有真实变动才推
        if (added or changed) and not is_first:
            notify(subject, body)
        elif is_first:
            print("[*] 首次运行，已建立监控快照，本次不推送", file=sys.stderr)
        else:
            print("[*] 成绩无变动，不推送", file=sys.stderr)
    else:
        print_summary(data, args.term, args.json)


def run(args) -> dict:
    """执行登录 + 查成绩，返回成绩 JSON dict。供 main 和外部调用。"""
    # 优先复用存盘 cookie
    if not args.no_reuse:
        session = load_cookie_session()
        if session:
            try:
                data = fetch_grades(session, args.student_id)
                if data.get("code") == 200:
                    return data
            except Exception as e:
                print(f"[*] 存盘 cookie 失效（{e}），重新登录…", file=sys.stderr)

    # 全自动登录
    print("[*] 正在登录统一身份认证…", file=sys.stderr)
    session = login(args.student_id)
    print("[*] 登录成功", file=sys.stderr)
    if args.save_cookie:
        save_cookie(session)
    return fetch_grades(session, args.student_id)


if __name__ == "__main__":
    main()
