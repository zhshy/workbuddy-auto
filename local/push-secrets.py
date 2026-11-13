#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 local/tokens.json 里的凭证推到 GitHub 仓库的 Secrets。

前置：本机装有 gh CLI 且已登录（gh auth status 能通过）。
用法：python push-secrets.py <owner/repo>
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TOKENS = os.path.join(HERE, "tokens.json")


def main():
    if len(sys.argv) < 2:
        print("用法: python push-secrets.py <owner/repo>")
        print("例如: python push-secrets.py zhshy/workbuddy-auto")
        return 1
    repo = sys.argv[1]

    if not os.path.exists(TOKENS):
        print("✗ 找不到 tokens.json，请先运行 extract.bat")
        return 2

    with open(TOKENS, "r", encoding="utf-8") as f:
        d = json.load(f)

    pairs = [
        ("WB_TOKEN", d.get("access_token", "")),
        ("WB_UID", d.get("uid", "")),
        ("WB_REFRESH", d.get("refresh_token", "")),
        ("WB_DOMAIN", d.get("domain", "www.workbuddy.cn")),
    ]

    try:
        subprocess.run(["gh", "auth", "status"], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        print("✗ gh 未登录或未安装。先执行： gh auth login")
        return 3

    for name, value in pairs:
        if not value:
            print("· 跳过 %s（空值）" % name)
            continue
        r = subprocess.run(
            ["gh", "secret", "set", name, "-R", repo, "-b", value],
            capture_output=True, text=True,
        )
        if r.returncode == 0:
            print("✓ 已写入 %s（%d 字符）" % (name, len(value)))
        else:
            print("✗ %s 失败: %s" % (name, (r.stderr or r.stdout).strip()[:200]))
            return 4

    print("\n完成。到仓库 Actions 页手动 Run workflow 验证一次。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
