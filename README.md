# AHMU-Campus-Network-AutoReauth
安徽医科大学 (AHMU) 校园网自动重登与运营商通道自愈守护工具

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Platform](https://img.shields.io/badge/Platform-Windows-0078D6.svg)](https://github.com/haoan33/AHMU-Campus-Network-AutoReauth)
[![Release](https://img.shields.io/badge/Release-v1.0.0-success.svg)](https://github.com/haoan33/AHMU-Campus-Network-AutoReauth/releases)

专为**安徽医科大学全校师生**设计的开机自动重登与运营商出口通道守护工具。支持开机自动注销旧连接、自动识别本机内网 IP 与 MAC 地址、重登目标运营商通道（电信/联通/移动/校园网）、防止运营商通道漂移，并提供图形化设置界面与精准错误原因诊断。

---

## 🌟 核心特性

- 🚀 **免安装单文件运行**：提供无需配置 Python 环境的单文件可执行程序（`.exe`），全校同学双击即可直接使用。
- 🔑 **首次引导与快捷改密**：
  - 首次运行自动弹出账号密码配置窗口，引导填写学号、密码与运营商通道并保存至本地。
  - 日常开机启动时提供 **3 秒倒计时**，期间按任意键（回车/空格等）或点击界面按钮即可随时更改已保存的账号密码。
- 🔍 **全校通用动态 IP / MAC 识别**：
  - 自动从校园网网关状态接口与本地路由网卡获取当前电脑的真实内网 IP、十进制整型 IP 与网卡物理 MAC 地址，无需手动配置设备信息。
- 🌐 **多运营商通道全面支持**：
  - 预设 **中国电信 (`@telecom`)**、**中国联通 (`@unicom`)**、**中国移动 (`@cmcc`)** 与 **校园网/教育网（无后缀）**，仅需输入纯学号即可自动补全对应后缀。
- 🛡️ **底层错误精准诊断与中文提示**：
  - 深度结合学校网关前端错误码字典与 `/drcom/login` 底层协议，彻底杜绝网关返回信息汉字乱码。
  - 明确分类并弹窗提示：**密码错误**、**学号不存在**、**运营商通道未绑定/选错**、**欠费停机**、**多终端登录达上限**、**新生首次登录须改密**等具体原因；遇到账号密码错误立即停止重试并引导修改。
- 🔄 **强力会话注销与解绑**：
  - 结合 801 Portal `mac/unbind`（双重账号解绑）、标准 `/eportal/portal/logout` 与 80 端口 `/drcom/logout` 全局注销，确保更换账号或重新拨号时彻底释放旧连接。
- ⏰ **一键 Windows 开机自启动**：
  - 界面内置「开机自动重登」开关，勾选即可自动添加至 Windows 启动项，开机静默重登成功后 4 秒自动关闭窗口，日常使用完全无感。

---

## 📥 下载与使用

### 方式一：下载预编译单文件版（推荐）

1. 前往 [Releases 页面](https://github.com/haoan33/AHMU-Campus-Network-AutoReauth/releases) 下载最新的 `AHMU校园网自动重登.exe`。
2. 双击运行程序：
   - **首次运行**：会弹出配置窗口，输入学号、密码并选择运营商通道（默认中国电信），点击「保存并立即重登」。
   - **日常使用**：打开后自动倒计时 3 秒并执行重登；如需改密，在倒计时结束前按任意键即可进入配置面板。
   - **开机自启**：在主界面右上角勾选「开机自动重登」即可。

### 方式二：Python 源码运行

如已安装 Python 3.10+ 环境：

```bash
# 克隆仓库
git clone https://github.com/haoan33/AHMU-Campus-Network-AutoReauth.git
cd AHMU-Campus-Network-AutoReauth

# 运行主程序
python auto_reauth_boot.py

# 或运行出口运营商持续守护监控
python campus_carrier_monitor.py
```

如需自行打包为单文件 `.exe`：

```bash
pip install pyinstaller
python -m PyInstaller --noconfirm --onefile --windowed --name "AHMU校园网自动重登" auto_reauth_boot.py
```

---

## 🛠️ 文件结构说明

| 文件名 | 说明 |
| :--- | :--- |
| `auto_reauth_boot.py` | 主程序源码（Tkinter GUI、动态识别、重登与开机启动逻辑） |
| `campus_carrier_monitor.py` | 运营商通道漂移监控与自愈守护进程（适用于挂机/下载场景） |
| `campus_config.example.json` | 配置文件结构参考模板 |
| `启动校园网电信重登.bat` | 快速启动批处理脚本（ANSI/CP936 编码） |
| `运行校园网监控.bat` | 后台监控启动脚本 |
| `test_reauth.py` | 协议调试与链路连通性测试脚本 |

---

## 🔒 隐私与安全说明

- 本工具为**纯本地运行软件**，所有认证请求仅直连安医大校园网网关（`10.0.2.2`）。
- 您的校园网学号与密码仅持久化保存在当前运行目录下的本地文件 `campus_config.json` 中，**绝不会向任何第三方云端或外网服务器上传您的凭据**。
- 若需将软件分享给其他同学使用，请**直接单独发送 `AHMU校园网自动重登.exe`**，或在设置界面中点击「🧹 清除记录」后再行分享。

---

## 📄 开源许可证

本项目基于 [MIT License](LICENSE) 开源发布。
欢迎各位同学提 Issue 或 PR 改进！
