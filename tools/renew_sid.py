#!/usr/bin/env python3
"""
360 扫地机 sid 自动续期器（配合 hass-vacuum-360 集成使用）。

智能续期流程：
  1. 读 HA 当前用的 qid/sid，验证是否仍有效
  2. 仍有效 → 直接退出（不重启 HA，不触发风控）
  3. 已失效 → 调 get_sid.py 走 chromium 自动登录拿新凭据
  4. 把新 qid/sid 写入 HA 的 core.config_entries
  5. 重启 homeassistant 容器让新 sid 生效
  6. 探活：确认 coordinator 又能拿到设备

设计原则：
  - 幂等：跑多少次结果一样
  - 安全：旧 sid 仍有效时跳过登录（避免频繁触发 360 风控）
  - 可观测：每步打日志，失败有明确退出码
  - 不破坏：config_entries 改之前先备份

环境变量配置：
  VACUUM_360_HA_CONFIG     HA 配置目录（默认 /opt/homeassistant/config）
  VACUUM_360_HA_CONTAINER  HA 容器名（默认 homeassistant）
  VACUUM_360_DEVICE_SN     设备 SN（可选，用于验证目标设备在线）
  VACUUM_360_ACCOUNT       360 账号（get_sid.py 用）
  VACUUM_360_PASSWORD      360 密码（get_sid.py 用）

用法：
    python3 renew_sid.py              # 智能续期
    python3 renew_sid.py --force      # 强制重新登录
    python3 renew_sid.py --check-only # 只验证当前 sid
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
GET_SID = SCRIPT_DIR / "get_sid.py"
CRED_FILE = SCRIPT_DIR / "credentials.json"
LOG_FILE = SCRIPT_DIR / "renew_sid.log"

HA_CONFIG = Path(os.environ.get("VACUUM_360_HA_CONFIG", "/opt/homeassistant/config"))
CONFIG_ENTRIES = HA_CONFIG / ".storage" / "core.config_entries"
HA_CONTAINER = os.environ.get("VACUUM_360_HA_CONTAINER", "homeassistant")

DEVICE_SN = os.environ.get("VACUUM_360_DEVICE_SN", "")
DOMAIN = "vacuum_360"


def log(msg: str) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, "a") as f:
        f.write(line + "\n")


# ----------------------------------------------------------------------------
# 步骤 0: 读 HA 当前 sid
# ----------------------------------------------------------------------------
def read_ha_credentials() -> tuple[str, str] | None:
    try:
        with open(CONFIG_ENTRIES) as f:
            ce = json.load(f)
    except Exception as e:
        log(f"❌ 读 config_entries 失败: {e}")
        return None
    for entry in ce["data"]["entries"]:
        if entry.get("domain") == DOMAIN:
            return entry["data"].get("qid", ""), entry["data"].get("sid", "")
    return None


# ----------------------------------------------------------------------------
# 步骤 1: 验证 sid
# ----------------------------------------------------------------------------
def verify_sid(qid: str, sid: str) -> bool:
    cookie = f"q=u=&t=1;t=&v=2.0&a=1;qid={qid};sid={sid}"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": cookie,
        "User-Agent": "QihooSuperApp_NoPods/11.1.0 (iPhone; iOS 14.8; Scale/3.00)",
    }
    form = urllib.parse.urlencode({
        "countryId": "DE", "devType": "3", "from": "mpc_ios", "lang": "de_DE",
        "taskid": "verify-" + str(int(time.time())),
    }).encode()
    req = urllib.request.Request(
        "https://q.smart.360.cn/common/dev/GetList", data=form, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read())
    except Exception as e:
        log(f"  验证请求异常: {e}")
        return False
    if result.get("errno") != 0:
        log(f"  ❌ sid 无效: errno={result.get('errno')} {result.get('errmsg')}")
        return False
    if DEVICE_SN:
        dev_list = (result.get("data") or {}).get("list", [])
        found = any(d.get("sn") == DEVICE_SN for d in dev_list)
        log(f"  ✅ sid 有效，目标设备{'已找到' if found else '未找到'}")
        return found
    log("  ✅ sid 有效")
    return True


# ----------------------------------------------------------------------------
# 步骤 2: 跑 get_sid.py
# ----------------------------------------------------------------------------
def fetch_new_credentials() -> dict | None:
    if not GET_SID.exists():
        log(f"❌ 找不到 {GET_SID}")
        return None
    log("→ 启动 get_sid.py（chromium 自动登录 + 验证码破解，预计 30-90 秒）...")
    start = time.time()
    env = os.environ.copy()  # 保留 VACUUM_360_ACCOUNT/PASSWORD 等传给子进程
    try:
        proc = subprocess.run(
            ["python3", "-u", str(GET_SID)],
            capture_output=True, text=True, timeout=300, env=env,
        )
    except subprocess.TimeoutExpired:
        log("❌ get_sid.py 超时（5分钟）")
        return None
    log(f"  get_sid.py 完成，耗时 {time.time() - start:.1f}s（退出码 {proc.returncode}）")
    if proc.returncode != 0:
        log(f"  stderr: {proc.stderr[-400:] if proc.stderr else '(空)'}")
        return None
    try:
        creds = json.loads(CRED_FILE.read_text())
    except Exception as e:
        log(f"❌ 读 {CRED_FILE} 失败: {e}")
        return None
    if not creds.get("qid") or not creds.get("sid"):
        log("❌ 凭据文件缺 qid 或 sid")
        return None
    return creds


# ----------------------------------------------------------------------------
# 步骤 3: 写 HA config_entries
# ----------------------------------------------------------------------------
def update_ha_config(qid: str, sid: str) -> bool:
    backup = CONFIG_ENTRIES.with_name(CONFIG_ENTRIES.stem + ".bak")
    try:
        shutil.copy2(CONFIG_ENTRIES, backup)
    except Exception as e:
        log(f"❌ 备份失败: {e}")
        return False
    try:
        with open(CONFIG_ENTRIES) as f:
            ce = json.load(f)
    except Exception as e:
        log(f"❌ 读 config_entries 失败: {e}")
        return False
    updated = False
    for entry in ce["data"]["entries"]:
        if entry.get("domain") == DOMAIN:
            entry["data"]["qid"] = qid
            entry["data"]["sid"] = sid
            entry["modified_at"] = datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%S.000000+00:00")
            updated = True
            break
    if not updated:
        log("❌ 没找到 vacuum_360 entry，无法更新")
        return False
    with open(CONFIG_ENTRIES, "w") as f:
        json.dump(ce, f, ensure_ascii=False, indent=2)
    log(f"  ✅ 已写入新 sid ({sid[:6]}...{sid[-4:]})，备份: {backup.name}")
    return True


# ----------------------------------------------------------------------------
# 步骤 4: 重启 HA
# ----------------------------------------------------------------------------
def restart_ha() -> bool:
    log(f"→ 重启 {HA_CONTAINER} 容器...")
    r = subprocess.run(["docker", "restart", HA_CONTAINER],
                       capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        log(f"❌ docker restart 失败: {r.stderr}")
        return False
    for i in range(40):
        time.sleep(3)
        check = subprocess.run(
            ["docker", "exec", HA_CONTAINER, "curl", "-s", "-o", "/dev/null",
             "-w", "%{http_code}", "http://localhost:8123/api/"],
            capture_output=True, text=True, timeout=10,
        )
        if check.stdout.strip() in ("200", "401"):
            log(f"  ✅ HA 就绪（第 {i + 1} 次检查）")
            return True
    log("❌ HA 2 分钟内未就绪")
    return False


# ----------------------------------------------------------------------------
# 步骤 5: 探活
# ----------------------------------------------------------------------------
def post_check() -> bool:
    log("→ 等待 20 秒让 coordinator 完成首次轮询...")
    time.sleep(20)
    r = subprocess.run(
        ["docker", "logs", HA_CONTAINER, "--since", "90s"],
        capture_output=True, text=True, timeout=15,
    )
    out = re.sub(r"\x1b\[[0-9;]*m", "", (r.stdout or "") + (r.stderr or ""))
    if "Error while setting up" in out or "SetupError" in out:
        log("  ❌ 探活失败：HA 报 setup 错误")
        return False
    if "Found" in out and "360 device" in out:
        log("  ✅ 探活成功：coordinator 发现设备")
        return True
    log("  ⚠️  探活未确认（日志关键字未匹配，可能仍正常）")
    return False


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description="360 扫地机 sid 自动续期")
    parser.add_argument("--force", action="store_true",
                        help="强制重新登录，即使当前 sid 还有效")
    parser.add_argument("--check-only", action="store_true",
                        help="只验证当前 sid，不续期")
    args = parser.parse_args()

    log("=" * 60)
    log("360 扫地机 sid 续期器启动")
    log("=" * 60)

    current = read_ha_credentials()
    if current:
        qid, sid = current
        log(f"当前 HA 凭据: qid={qid} sid={sid[:6]}...{sid[-4:]}")
    else:
        log("⚠️  HA 无凭据，必须重新登录")
        args.force = True

    if current and not args.force:
        log("→ 验证当前 sid...")
        if verify_sid(*current):
            log("✅ 当前 sid 仍有效，无需续期。退出。")
            return 0
        log("→ 当前 sid 已失效，需要重新登录")

    if args.check_only:
        ok = bool(current and verify_sid(*current))
        return 0 if ok else 1

    creds = fetch_new_credentials()
    if not creds:
        log("❌ 续期失败：未能获取新凭据")
        return 2

    new_qid, new_sid = creds["qid"], creds["sid"]
    log(f"→ 新凭据: qid={new_qid} sid={new_sid[:6]}...{new_sid[-4:]}")

    log("→ 验证新 sid...")
    if not verify_sid(new_qid, new_sid):
        log("❌ 新 sid 也无效，登录链路可能被风控。放弃更新。")
        return 3

    if not update_ha_config(new_qid, new_sid):
        return 4
    if not restart_ha():
        return 5
    post_check()

    log("=" * 60)
    log("✅ sid 续期完成")
    log("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
