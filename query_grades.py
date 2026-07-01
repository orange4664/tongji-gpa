#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
同济大学成绩自动查询脚本
========================
不打开浏览器，直接用 Cookie 调用后端接口拉取全部成绩。

依赖：Python 3.7+，仅用标准库（requests 如有更好，没有则用 urllib）。

用法：
    1. 把登录后的 Cookie 写入 ~/.tongji_grades_cookie.txt，内容形如：
       language=cn; JSESSIONID=xxxx; sessionid=yyyy
       （三段用 "; " 分隔，顺序无关。JSESSIONID / sessionid 必填）
    2. python3 query_grades.py            # 打印汇总 + 全部课程
       python3 query_grades.py --term 20252   # 只看某学期（calName）
       python3 query_grades.py --json     # 原始 JSON 输出，便于管道处理
       python3 query_grades.py --cookie "language=cn; JSESSIONID=..; sessionid=.."  # 临时指定

Cookie 获取：浏览器登录 1.tongji.edu.cn 后，F12 → Application → Cookies
→ 复制 JSESSIONID 与 sessionid 两个值。Cookie 是会话型，失效就重取。
"""

import argparse
import json
import os
import sys
import time
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

STUDENT_ID = "2351358"  # 学号，已知固定，按需改
BASE = "https://1.tongji.edu.cn"
GRADES_URL = f"{BASE}/api/scoremanagementservice/scoreGrades/getMyGrades"
COOKIE_FILE = os.path.expanduser("~/.tongji_grades_cookie.txt")
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
      "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36")


def load_cookie(arg_cookie: str) -> str:
    if arg_cookie:
        return arg_cookie.strip()
    if os.path.exists(COOKIE_FILE):
        with open(COOKIE_FILE, "r", encoding="utf-8") as f:
            return f.read().strip()
    sys.exit(f"未找到 Cookie。请写入 {COOKIE_FILE} 或用 --cookie 传入。")


def fetch_grades(cookie: str, student_id: str) -> dict:
    url = f"{GRADES_URL}?studentId={student_id}&_t={int(time.time()*1000)}"
    req = Request(url, headers={
        "User-Agent": UA,
        "Cookie": cookie,
        "Referer": f"{BASE}/oldStysteMyGrades",
        "Accept": "application/json, text/plain, */*",
    })
    try:
        with urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            # 跟随跳转：urllib 默认会跟 302；若跳到登录页，body 不是 JSON
            if not body.lstrip().startswith("{"):
                sys.exit("Cookie 已失效（被重定向到登录页），请重新获取。")
            return json.loads(body)
    except HTTPError as e:
        sys.exit(f"HTTP {e.code}：Cookie 可能失效或网络异常。")
    except URLError as e:
        sys.exit(f"网络错误：{e.reason}")


def fmt_score(s: str, gp) -> str:
    """成绩展示：分数或等级 + 绩点。"""
    gp = "" if gp in (None, "") else f" (绩点 {gp})"
    return f"{s}{gp}"


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
            score = fmt_score(c.get("score") or c.get("scoreName"), c.get("gradePoint"))
            credit = c.get("credit")
            nature = c.get("publicCoursesName") or "-"
            lab = c.get("courseLabName") or "-"
            print(f"{name:<28} {score:<14} {credit:<6} {nature:<6} {lab}")
        print()


def main():
    ap = argparse.ArgumentParser(description="同济大学成绩自动查询")
    ap.add_argument("--cookie", help="直接传入 Cookie 字符串")
    ap.add_argument("--student-id", default=STUDENT_ID, help="学号")
    ap.add_argument("--term", help="只看某学期 calName，如 20252")
    ap.add_argument("--json", action="store_true", help="输出原始 JSON")
    args = ap.parse_args()

    cookie = load_cookie(args.cookie)
    data = fetch_grades(cookie, args.student_id)
    print_summary(data, args.term, args.json)


if __name__ == "__main__":
    main()
