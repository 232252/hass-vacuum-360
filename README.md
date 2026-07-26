# hass-vacuum-360

[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![HA Custom Integration](https://img.shields.io/badge/HA-custom%20integration-41BDF5.svg)](https://developers.home-assistant.io/docs/creating_integration_file/)
[![hacs](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz)

**360 扫地机器人 Home Assistant 自定义集成** — 通过逆向 360 智能管家云端协议，让 360 扫地机接入 Home Assistant，支持远程控制、定时清扫、状态查询。

A Home Assistant custom integration for 360 robot vacuums. Reverse-engineers the 360 Smart Home cloud protocol (q.smart.360.cn) so you can control your 360 vacuum from Home Assistant.

---

> ## ⚠️ 免责声明 / Disclaimer
>
> **本项目仅供学习与个人使用，请遵守 360 用户协议与《网络安全法》《数据安全法》《个人信息保护法》。**
>
> - 本项目与 360 公司（Qihoo 360）**无任何关联**，未获其授权或背书。
> - 使用本项目登录 360 账户所产生的一切后果（包括但不限于账户封禁、数据丢失、法律责任）由使用者自行承担。
> - 请勿将本项目用于商业用途、批量登录、撞库或任何违反 360 服务条款的行为。
> - 本项目作者不对因使用本项目导致的任何损失负责。
>
> *This project is for educational and personal use only. It is not affiliated with, endorsed by, or sponsored by Qihoo 360. Use at your own risk. The authors are not liable for any account suspension, data loss, or legal consequences.*

---

## ✨ 功能特性 / Features

- 🤖 **云端控制**：开始清扫、暂停、回充、切换吸力档位
- 🔋 **状态查询**：清扫状态、电量、设备在线状态
- 🕒 **配合 HA 自动化**：可设置定时清扫、离家清扫、传感器联动等
- 🔄 **sid 自动续期**：配套脚本自动破解滑动验证码，sid 过期时无人值守刷新
- 🌐 **不依赖官方 App**：完全通过 HA 控制，无需 360 智能管家 App

### 支持的设备 / Supported Devices

经社区验证可用的型号（基于 360 云端 `q.smart.360.cn` 协议）：
- 360 S 系列（S6 / S7 / S8 / S9 / S10）
- 360 X 系列（X75 / X90 / X95 / X100 / X95Pro）
- 华为智选 360 扫地机（如华为智选 X90V2）

> 💡 只要设备绑定在 360 智能管家 App 里、能在 App 里控制，本集成就能控制它。

---

## 📦 安装 / Installation

### 方式一：HACS（推荐 / Recommended）

[![Open in HACS](https://img.shields.io/badge/Add%20to-HACS-41BDF5.svg)](#)

1. 在 HACS → Integrations → 右上角 ⋯ → Custom repositories
2. 粘贴本仓库地址，Category 选 **Integration**
3. 搜索 "360 Robot Vacuum" → Install
4. 重启 Home Assistant

### 方式二：手动安装 / Manual

```bash
# 1. 下载 custom_components/vacuum_360 目录
# 2. 复制到你的 HA 配置目录：
cp -r custom_components/vacuum_360 /path/to/ha-config/custom_components/

# 3. 重启 Home Assistant
```

### 添加集成 / Add Integration

设置 → 设备与服务 → 添加集成 → 搜索 **"360 Robot Vacuum"**

需要提供两个值（见下一节如何获取）：
- **qid**：360 账号 ID（数字）
- **sid**：会话 ID（32 位十六进制字符串）

---

## 🔑 获取 sid / Getting Your sid

sid 是 360 云端的会话凭据。有三种获取方式，按推荐顺序：

### 方式 A：用本仓库的自动登录工具（推荐 / Recommended）

本仓库 `tools/` 目录提供 `get_sid.py`，自动通过 chromium 破解滑动验证码登录，无需手动操作：

```bash
cd tools/
cp .env.example .env
# 编辑 .env，填入你的 360 账号密码
pip3 install -r requirements.txt

# 确保 chromium 已安装（Debian/Ubuntu：sudo apt install chromium）
python3 get_sid.py
# → 输出 credentials.json，内含 qid 和 sid
```

### 方式 B：抓包 360 App（手动 / Manual capture）

1. 手机安装 360 智能管家 App，登录
2. 用抓包工具（iOS 用 [Stream](https://apps.apple.com/app/stream-network-debug-tool/id1359496646)，Android 用 HttpCanary）
3. 打开 App，找到请求 `q.smart.360.cn/common/dev/GetList`
4. 从请求头 `Cookie:` 里取出 `qid=` 和 `sid=` 的值

### 方式 C：浏览器登录（可能不含 sid）

1. 浏览器访问 `https://i.360.cn/login` 登录
2. F12 → Application → Cookies → 找 `qid`（但不一定有 `sid`，需测试）

---

## 🔄 sid 自动续期 / Automatic sid Renewal

sid 会过期（几天到几周不定）。本仓库提供 `tools/renew_sid.py` 实现无人值守续期：

```bash
cd tools/
python3 renew_sid.py              # 智能续期：sid 还有效就跳过
python3 renew_sid.py --force      # 强制重新登录
python3 renew_sid.py --check-only # 只检查 sid 状态
```

配合 cron 每天自动续期（在 HA 所在机器上）：

```bash
# crontab -e
# 每天凌晨 4 点智能续期（不影响白天使用）
0 4 * * *  cd /path/to/hass-vacuum-360/tools && python3 renew_sid.py >> renew_sid.log 2>&1
```

---

## ⚙️ 配置说明 / Configuration

集成通过 ConfigFlow（UI）配置，无需编辑 YAML。

| 字段 | 说明 | 示例 |
|---|---|---|
| qid | 360 账号 ID | `1234567890` |
| sid | 会话 ID（32位十六进制） | `abcdef0123456789abcdef0123456789` |

### 支持的服务 / Supported Services

| 服务 | 说明 |
|---|---|
| `vacuum.start` | 开始清扫（或从暂停恢复） |
| `vacuum.pause` | 暂停 |
| `vacuum.return_to_base` | 回充 |
| `vacuum.set_fan_speed` | 切换吸力（Auto / Quiet / Strong） |

---

## 🛠️ 故障排查 / Troubleshooting

### 实体显示 unavailable

通常是 sid 过期。运行 `python3 tools/renew_sid.py --check-only` 检查，过期了跑 `--force` 续期。

### 开启 debug 日志

在 `configuration.yaml` 添加：

```yaml
logger:
  logs:
    custom_components.vacuum_360: debug
```

重启 HA 后查看日志，会显示每次云端调用的 errno。

### 续期失败（验证码识别不通过）

滑动验证码识别依赖 OpenCV 模板匹配，置信度低于 0.1 会自动刷新重试。若连续失败：
1. 检查 chromium 是否正常启动（`chromium --version`）
2. 确认网络能访问 `i.360.cn` 和 `q.smart.360.cn`
3. 360 可能临时升级了验证码机制，过段时间再试

---

## 📂 项目结构 / Project Structure

```
hass-vacuum-360/
├── custom_components/vacuum_360/    # HA 集成本体
│   ├── __init__.py                  # 入口：setup coordinator + forward platforms
│   ├── api.py                       # 360 云端 API 客户端
│   ├── config_flow.py               # ConfigFlow 配置流
│   ├── const.py                     # 协议常量 + 状态映射
│   ├── coordinator.py               # DataUpdateCoordinator 轮询
│   ├── exceptions.py                # 异常体系
│   ├── manifest.json                # HA 集成清单
│   ├── sensor.py                    # 电量传感器
│   ├── strings.json                 # UI 文案
│   ├── translations/                # 中英文翻译
│   └── vacuum.py                    # 扫地机实体
├── tools/                           # 辅助工具
│   ├── get_sid.py                   # 自动登录获取 sid
│   ├── renew_sid.py                 # sid 自动续期器
│   ├── requirements.txt             # 工具依赖
│   └── .env.example                 # 凭据配置模板
├── hacs.json                        # HACS 元数据
├── LICENSE                          # Apache-2.0
└── README.md
```

---

## 🤝 致谢 / Acknowledgements

- [ioBroker.botslab360](https://github.com/iobroker-community-adapters/ioBroker.botslab360) — 协议结构参考
- 360 智能管家 App 的逆向分析社区

## 📜 License

[Apache-2.0](LICENSE)

## ⚠️ 重要提醒

- 本集成通过 360 **云端** 控制设备，需要设备保持联网。
- sid 是敏感凭据，请勿在公开场合（issue、截图、日志）泄露。
- 如果 360 修改协议，本集成可能失效，作者会尽力跟进适配但不作保证。
