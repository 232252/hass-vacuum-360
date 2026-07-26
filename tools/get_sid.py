#!/usr/bin/env python3
"""
360 账号自动登录工具 — 通过 chromium 自动破解滑动验证码，获取 qid + sid。

本工具是 hass-vacuum-360 集成的辅助脚本，用于在 sid 过期后重新获取会话凭据。
登录流程：
  1. chromium 加载 i.360.cn 登录页
  2. 自动填表单（从环境变量读取账号密码）
  3. 检测滑动验证码，下载背景图+滑块图
  4. OpenCV (Canny 边缘 + matchTemplate) 计算缺口位置
  5. CDP 模拟人类式拖动滑块（带抖动和缓动）
  6. 等待登录完成，捕获 qid/sid cookies
  7. 验证 sid 有效性（调 q.smart.360.cn GetList）
  8. 保存凭据到 JSON 文件

凭据来源（优先级从高到低）：
  1. 环境变量 VACUUM_360_ACCOUNT / VACUUM_360_PASSWORD
  2. 同目录 .env 文件
  3. argparse 参数 --account / --password

用法：
    cp .env.example .env && 填入账号密码
    python3 get_sid.py
    python3 get_sid.py --account 13800138000 --password 'your_pass'
    VACUUM_360_ACCOUNT=13800138000 VACUUM_360_PASSWORD='xx' python3 get_sid.py
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import random
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import cv2
import numpy as np
import websockets

# ----------------------------------------------------------------------------
# 配置（除账号密码外都是非敏感的协议常量）
# ----------------------------------------------------------------------------
CHROME = os.environ.get("VACUUM_360_CHROME", "/usr/bin/chromium")
USER_DATA_DIR = Path(os.environ.get("VACUUM_360_USER_DATA", "/tmp/vacuum360_chrome"))
DEBUG_PORT = int(os.environ.get("VACUUM_360_DEBUG_PORT", "9234"))
OUTPUT_FILE = Path(os.environ.get("VACUUM_360_OUTPUT", "credentials.json"))
MAX_SLIDE_RETRIES = int(os.environ.get("VACUUM_360_MAX_RETRIES", "5"))

LOGIN_URL = "https://i.360.cn/login?destUrl=https%3A%2F%2Fi.360.cn%2F"
VERIFY_URL = "https://q.smart.360.cn/common/dev/GetList"


def load_credentials() -> tuple[str, str]:
    """从环境变量 / .env / argparse 读取账号密码。绝不硬编码。"""
    account = os.environ.get("VACUUM_360_ACCOUNT")
    password = os.environ.get("VACUUM_360_PASSWORD")

    # 尝试加载 .env（避免引入 python-dotenv 依赖，手工解析）
    if not account or not password:
        env_file = Path(__file__).parent / ".env"
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                line = line.strip()
                if line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip("'\"")
                if k == "VACUUM_360_ACCOUNT" and not account:
                    account = v
                elif k == "VACUUM_360_PASSWORD" and not password:
                    password = v

    # argparse 兜底
    parser = argparse.ArgumentParser(description="360 账号自动登录获取 sid")
    parser.add_argument("--account", help="360 账号（手机号/邮箱）")
    parser.add_argument("--password", help="360 账号密码")
    args = parser.parse_args()
    account = args.account or account
    password = args.password or password

    if not account or not password:
        print("❌ 缺少账号或密码。请通过环境变量 VACUUM_360_ACCOUNT / VACUUM_360_PASSWORD、")
        print("   .env 文件、或 --account / --password 参数提供。")
        sys.exit(2)
    return account, password


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ----------------------------------------------------------------------------
# chromium + CDP
# ----------------------------------------------------------------------------
def start_chromium() -> subprocess.Popen:
    USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    proc = subprocess.Popen(
        [CHROME, "--headless=new", "--no-sandbox", "--disable-gpu",
         "--disable-dev-shm-usage", f"--remote-debugging-port={DEBUG_PORT}",
         f"--user-data-dir={USER_DATA_DIR}", "--window-size=1280,900", LOGIN_URL],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return proc


def get_ws_url() -> str:
    for _ in range(40):
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{DEBUG_PORT}/json", timeout=2) as r:
                for p in json.loads(r.read()):
                    if p.get("type") == "page":
                        return p["webSocketDebuggerUrl"]
        except Exception:
            pass
        time.sleep(0.5)
    raise RuntimeError("CDP 连接超时（chromium 未启动？）")


# ----------------------------------------------------------------------------
# 验证码识别（OpenCV）
# ----------------------------------------------------------------------------
def download_image(url: str) -> np.ndarray:
    req = urllib.request.Request(url, headers={"Referer": "https://i.360.cn/"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        data = resp.read()
    import tempfile
    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
        f.write(data)
        path = f.name
    img = cv2.imread(path, cv2.IMREAD_UNCHANGED)
    os.unlink(path)
    return img


def find_gap_distance(bg_url: str, slider_url: str) -> tuple[float, float]:
    """Canny 边缘 + matchTemplate 计算缺口位置。返回 (滑动距离, 置信度)。"""
    bg = download_image(bg_url)
    slider_full = download_image(slider_url)
    if bg is None or slider_full is None:
        raise RuntimeError("验证码图片下载失败")

    # 处理滑块图的 alpha 通道，裁出实际滑块区域
    if len(slider_full.shape) == 3 and slider_full.shape[2] == 4:
        alpha = slider_full[:, :, 3]
        nonzero = np.where(alpha > 10)
        if len(nonzero[0]) > 0:
            y_min, y_max = nonzero[0].min(), nonzero[0].max()
            x_min, x_max = nonzero[1].min(), nonzero[1].max()
            slider_rgb = slider_full[y_min:y_max + 1, x_min:x_max + 1, :3]
        else:
            slider_rgb = slider_full[:, :, :3]
            x_min = 0
    else:
        slider_rgb = slider_full
        x_min = 0

    bg_gray = cv2.cvtColor(bg[:, :, :3], cv2.COLOR_BGR2GRAY)
    slider_gray = cv2.cvtColor(slider_rgb, cv2.COLOR_BGR2GRAY)
    bg_edges = cv2.Canny(bg_gray, 100, 200)
    slider_edges = cv2.Canny(slider_gray, 100, 200)

    res = cv2.matchTemplate(bg_edges, slider_edges, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(res)
    gap_x = max_loc[0]

    # 背景图自然宽度 → 显示宽度（328px）的缩放
    bg_w_natural = bg.shape[1]
    bg_w_display = 328
    scale = bg_w_display / bg_w_natural
    return gap_x * scale, float(max_val)


async def human_like_drag(send, start_x: float, start_y: float, distance: float) -> None:
    """CDP 模拟人类拖动：按下 → 缓动移动（带抖动）→ 释放。"""
    await send("Input.dispatchMouseEvent", {
        "type": "mousePressed", "x": start_x, "y": start_y,
        "button": "left", "clickCount": 1,
    })
    await asyncio.sleep(0.1)
    steps = 25
    for i in range(1, steps + 1):
        progress = i / steps
        eased = 1 - (1 - progress) ** 3  # ease-out cubic
        offset = distance * eased
        await send("Input.dispatchMouseEvent", {
            "type": "mouseMoved",
            "x": start_x + offset + random.uniform(-1, 1),
            "y": start_y + random.uniform(-2, 2),
            "button": "left",
        })
        await asyncio.sleep(0.005 + 0.02 * progress)
    # 精确到缺口
    await send("Input.dispatchMouseEvent", {
        "type": "mouseMoved", "x": start_x + distance, "y": start_y, "button": "left",
    })
    await asyncio.sleep(0.15)
    await send("Input.dispatchMouseEvent", {
        "type": "mouseReleased", "x": start_x + distance, "y": start_y,
        "button": "left", "clickCount": 1,
    })


# ----------------------------------------------------------------------------
# 验证 sid
# ----------------------------------------------------------------------------
def verify_credentials(qid: str, sid: str) -> bool:
    cookie = f"q=u=&t=1;t=&v=2.0&a=1;qid={qid};sid={sid}"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Cookie": cookie,
        "User-Agent": "QihooSuperApp_NoPods/11.1.0 (iPhone; iOS 14.8; Scale/3.00)",
    }
    form = urllib.parse.urlencode({
        "countryId": "DE", "devType": "3", "from": "mpc_ios", "lang": "de_DE",
    }).encode()
    req = urllib.request.Request(VERIFY_URL, data=form, method="POST")
    for k, v in headers.items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return json.loads(resp.read()).get("errno") == 0
    except Exception:
        return False


# ----------------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------------
async def main() -> int:
    account, password = load_credentials()
    log(f"账号: {account[:3]}***{account[-2:] if len(account) > 5 else ''}")

    proc = start_chromium()
    log(f"chromium PID={proc.pid}")
    try:
        async with websockets.connect(get_ws_url(), max_size=50 * 1024 * 1024) as ws:
            mid = [0]

            async def send(method: str, params: dict | None = None) -> dict:
                mid[0] += 1
                await ws.send(json.dumps({"id": mid[0], "method": method, "params": params or {}}))
                while True:
                    r = json.loads(await ws.recv())
                    if r.get("id") == mid[0]:
                        return r

            await send("Page.enable")
            await send("Network.enable")
            await asyncio.sleep(5)
            log("页面就绪，提交登录表单...")

            await send("Runtime.evaluate", {
                "expression": """(function(){
                    document.querySelector('input[name="userName"]').value=%s;
                    document.querySelector('input[name="userName"]').dispatchEvent(new Event('input',{bubbles:true}));
                    document.querySelector('input[name="password"]').value=%s;
                    document.querySelector('input[name="password"]').dispatchEvent(new Event('input',{bubbles:true}));
                    var a=document.querySelector('input[name="is_agree"]');
                    if(!a.checked){a.checked=true;a.dispatchEvent(new Event('change',{bubbles:true}));}
                    document.querySelector('input[type="submit"]').click();
                })()""" % (json.dumps(account), json.dumps(password)),
            })
            log("已提交，等待验证码...")
            await asyncio.sleep(8)

            for attempt in range(1, MAX_SLIDE_RETRIES + 1):
                log(f"\n--- 滑动尝试 {attempt}/{MAX_SLIDE_RETRIES} ---")
                r = await send("Runtime.evaluate", {
                    "expression": """(function(){
                        var bg=null,slider=null;
                        document.querySelectorAll('img').forEach(function(img){
                            var src=img.src||'';
                            if(src.indexOf('bgpic')!==-1) bg={src:src,x:img.getBoundingClientRect().x,y:img.getBoundingClientRect().y,w:img.offsetWidth};
                            if(img.classList&&img.classList.contains('slide-block')) slider={src:src,x:img.getBoundingClientRect().x+img.offsetWidth/2,y:img.getBoundingClientRect().y+img.offsetHeight/2};
                        });
                        if(!bg||!slider) return JSON.stringify({error:'no captcha',url:location.href});
                        return JSON.stringify({bg:bg,slider:slider,url:location.href});
                    })()""",
                    "returnByValue": True,
                })
                captcha = json.loads(r.get("result", {}).get("result", {}).get("value", "{}"))

                if captcha.get("error"):
                    if "login" not in captcha.get("url", ""):
                        log(f"🎉 已登录成功！URL: {captcha['url'][:60]}")
                        break
                    await asyncio.sleep(3)
                    continue

                try:
                    distance, confidence = find_gap_distance(captcha["bg"]["src"], captcha["slider"]["src"])
                    log(f"缺口距离: {distance:.1f}px (置信度 {confidence:.3f})")
                except Exception as e:
                    log(f"识别失败: {e}")
                    continue

                if confidence < 0.1:
                    await send("Runtime.evaluate", {
                        "expression": "var r=document.querySelector('.captcha-refresh');if(r)r.click();",
                    })
                    await asyncio.sleep(3)
                    continue

                await human_like_drag(send, captcha["slider"]["x"], captcha["slider"]["y"], distance)
                await asyncio.sleep(4)

                r2 = await send("Runtime.evaluate", {
                    "expression": """(function(){
                        var r={url:location.href};
                        var c=document.querySelector('.quc-slide-con .quc-body');
                        r.captchaVisible=c&&c.offsetParent!==null;
                        return JSON.stringify(r);
                    })()""",
                    "returnByValue": True,
                })
                result = json.loads(r2.get("result", {}).get("result", {}).get("value", "{}"))
                if not result.get("captchaVisible", True) or "login" not in result.get("url", "login"):
                    log("🎉 验证码通过！")
                    await asyncio.sleep(3)
                    break

            # 读 cookies
            r3 = await send("Network.getCookies", {
                "urls": ["https://i.360.cn", "https://360.cn", "https://q.smart.360.cn"],
            })
            cookies = r3.get("result", {}).get("cookies", [])
            creds = {c["name"]: c["value"] for c in cookies
                     if c["name"].lower() in ("qid", "sid", "q", "t")}

            qid = creds.get("qid", "")
            sid = creds.get("sid", "")
            if not qid or not sid:
                log("❌ 未能捕获 qid/sid（登录可能失败或被风控）")
                return 3

            # 验证
            if verify_credentials(qid, sid):
                log(f"✅ sid 有效（qid={qid[:4]}*** sid={sid[:6]}...）")
            else:
                log("⚠️  sid 验证失败，但凭据已保存（可能需要重试）")

            output = {
                "qid": qid,
                "sid": sid,
                "q_cookie": creds.get("q", ""),
                "t_cookie": creds.get("t", ""),
                "captured_at": time.time(),
            }
            OUTPUT_FILE.write_text(json.dumps(output, indent=2, ensure_ascii=False))
            os.chmod(OUTPUT_FILE, 0o600)
            log(f"✅ 凭据已保存到 {OUTPUT_FILE}（权限 0600）")
            return 0

    except Exception:
        import traceback
        traceback.print_exc()
        return 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
