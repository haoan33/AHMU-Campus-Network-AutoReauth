# -*- coding: utf-8 -*-
"""
安徽医科大学 (AHMU) 校园网自动重登工具 (全校通用版)
支持功能：
1. 首次打开自动提示录入并保存学号、密码及运营商通道（默认中国电信 @telecom，支持联通/移动/校园网）
2. 启动 3 秒倒计时自动执行重登，期间按任意键或点击“修改账号/密码”即可随时更改已保存的账号密码
3. 自动探测任意电脑的校园网内网 IP、十进制整型 IP 与真实网卡 MAC（彻底告别硬编码个人网卡信息）
4. 智能 UTF-8/GBK 双编码解码，彻底杜绝网关返回汉字乱码
5. 精准错误原因诊断（区分：密码错误、账号不存在、运营商选错/未绑定、欠费停机、多终端超限、首次登录需改密等）并弹窗引导处理
6. 支持一键开启/关闭 Windows 开机自启动
"""

import os
import re
import sys
import json
import time
import uuid
import socket
import struct
import threading
import subprocess
import urllib.request
import urllib.parse
from datetime import datetime
import tkinter as tk
from tkinter import ttk, messagebox

if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

# 兼容 PyInstaller 单文件 .exe 与普通 .py 脚本的目录定位
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(os.path.abspath(sys.executable))
    APP_EXEC_PATH = os.path.abspath(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    APP_EXEC_PATH = os.path.abspath(__file__)

CONFIG_FILE = os.path.join(BASE_DIR, "campus_config.json")

PORTAL_IP_DEFAULT = "10.0.2.2"
EP_PORT_DEFAULT = "801"
JS_VERSION = "4.2"

# 运营商后缀映射表
CARRIER_OPTIONS = [
    ("中国电信 (@telecom)", "@telecom", ["电信", "telecom", "ctnet", "chinatelecom"]),
    ("中国联通 (@unicom)", "@unicom", ["联通", "unicom", "cnc"]),
    ("中国移动 (@cmcc)", "@cmcc", ["移动", "cmcc", "chinamobile"]),
    ("校园网/教育网 (无后缀)", "", ["教育网", "cernet", "大学"]),
]

# 安医大 Dr.COM 网关底层错误码映射字典 (提取自网关 a43.js 与 loadErrorPrompt 接口)
DRCOM_ERROR_MAP = {
    "userid error1": ("账号不存在", "该学号/账号不存在，请检查学号是否输入正确！", True),
    "userid error2": ("密码错误", "校园网密码输入错误，请核对后重新输入密码！", True),
    "userid error3": ("密码错误", "校园网密码输入错误，请核对后重新输入密码！", True),
    "ldap auth error": ("账号或密码错误", "您输入的账号或密码不对（注：校园网已统一使用信息门户密码，新生默认密码为 Ahykd_身份证后六位）！", True),
    "auth error": ("账号或密码验证失败", "用户认证失败，请检查学号、密码及所选运营商通道是否正确！", True),
    "bind userid error": ("运营商账号未绑定或错误", "未绑定该运营商宽带或运营商账号错误！请检查【运营商出口通道】是否选对（如移动/联通/电信，未办营业厅宽带请选校园网）。", True),
    "bind isp userid error": ("运营商账号绑定错误", "运营商宽带账号绑定校验失败，请确认您办理的运营商通道（电信/联通/移动）是否选择正确！", True),
    "Mac, IP, NASip, PORT err(6)!": ("首次登录需改密或区域限制", "本账号只能在指定 VLAN 使用，或新账号首次登录须先在网页端修改初始密码！", True),
    "limit users err": ("多终端登录超限", "该账号同时在线设备数已达上限，请先在其他设备上注销下线！", False),
    "Port limit err": ("端口/终端数受限", "该账号登录终端数量超限，请先解绑其他设备！", False),
    "clientip online": ("终端IP已在线", "当前电脑 IP 已经处于在线状态。", False),
    "error0": ("登录方式受限", "当前 IP 不允许通过 Web 方式登录。", False),
    "error1": ("登录方式受限", "当前账号不允许通过 Web 方式登录。", False),
}


def decode_portal_bytes(raw: bytes) -> str:
    """
    智能解码网关返回的字节流，彻底解决 801 Portal (UTF-8) 与 80 Dr.COM (GBK) 混用导致的汉字乱码
    """
    if not raw:
        return ""
    text = ""
    # 1. 优先尝试标准 UTF-8 解码（801 端口 eportal 所有接口均为 UTF-8）
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        # 2. 若包含非 UTF-8 字节，则使用 GBK/CP936 解码（80 端口老接口为 GBK）
        text = raw.decode("gbk", errors="replace")

    # 3. 检查是否存在服务端已产生的单层 UTF8-as-GBK 乱码（例如 "璇ョ..."、"璐﹀..."、"瀵嗙..."）并自动还原
    mojibake_markers = ("璇", "璐", "瀵", "璁", "宸", "缁", "鐢", "鐠", "閸", "閹", "缂", "閻")
    if any(m in text for m in mojibake_markers):
        def _recover_match(match):
            val = match.group(1)
            if any(m in val for m in mojibake_markers):
                for _ in range(2):
                    try:
                        rec = val.encode("gbk", errors="ignore").decode("utf-8", errors="ignore")
                        if rec and rec != val:
                            val = rec
                        else:
                            break
                    except Exception:
                        break
            return f'"msg":"{val}"'

        text = re.sub(r'"msg"\s*:\s*"([^"]+)"', _recover_match, text)

    return text.strip()


def parse_jsonp(text: str) -> dict:
    """从 drLogin({...}) 等 JSONP 响应文本中提取字典"""
    if not text:
        return {}
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return {}
    try:
        return json.loads(m.group(0))
    except Exception:
        return {}


def query_portal_error_prompt(portal_ip: str, ep_port: str, user_ip: str, user_mac: str, error_code: str = "") -> str:
    """
    调用安医大网关 801 端口的 err_code / loadErrorPrompt 接口查询详细中文错误原因
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # 1. 若有具体 error_code，优先查 loadErrorPrompt
    if error_code and error_code not in ("AC认证失败", "no errcode"):
        try:
            url = (
                f"http://{portal_ip}:{ep_port}/eportal/portal/err_code/loadErrorPrompt?"
                f"callback=drErr&error_code={urllib.parse.quote(error_code)}"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(req, timeout=2.5) as resp:
                data = parse_jsonp(decode_portal_bytes(resp.read()))
                zh = (data.get("error_prompt_zh") or "").strip()
                if zh and zh != error_code and zh != "AC认证失败":
                    return zh
        except Exception:
            pass

    # 2. 按 IP + MAC 查询网关最近一次失败错误码
    if user_ip:
        try:
            url = (
                f"http://{portal_ip}:{ep_port}/eportal/portal/err_code?"
                f"callback=drErr&ip={urllib.parse.quote(user_ip)}&mac={urllib.parse.quote(user_mac)}"
            )
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(req, timeout=2.5) as resp:
                data = parse_jsonp(decode_portal_bytes(resp.read()))
                zh = (data.get("error_prompt_zh") or "").strip()
                if zh and zh not in ("AC认证失败", "no errcode"):
                    return zh
        except Exception:
            pass

    return ""


def diagnose_login_failure(portal_json: dict, drcom_json: dict, portal_ip: str, ep_port: str, user_ip: str, user_mac: str, account: str):
    """
    综合分析 801 Portal 与 /drcom/login 返回的错误结构，输出：
    (错误简短分类, 详细可读中文说明, 是否属于账号/密码/运营商配置错误需立即停止重试并改密)
    """
    p_msg = str(portal_json.get("msg") or "").strip()
    p_ret = portal_json.get("ret_code")
    d_msg = drcom_json.get("msg")
    d_msga = str(drcom_json.get("msga") or "").strip()

    # 若 801 返回笼统的 AC认证失败，去网关错误码接口或 drcom msga 挖掘真实原因
    detailed_prompt = ""
    if d_msga and d_msga in DRCOM_ERROR_MAP:
        title, desc, is_cfg_err = DRCOM_ERROR_MAP[d_msga]
        return title, desc, is_cfg_err

    if p_msg in DRCOM_ERROR_MAP:
        title, desc, is_cfg_err = DRCOM_ERROR_MAP[p_msg]
        return title, desc, is_cfg_err

    if not p_msg or p_msg == "AC认证失败" or (d_msga and d_msga != "clientip online"):
        lookup_key = d_msga if (d_msga and d_msga != "clientip online") else p_msg
        detailed_prompt = query_portal_error_prompt(portal_ip, ep_port, user_ip, user_mac, lookup_key)

    combined = f"{p_msg} | {detailed_prompt} | {d_msga}".strip(" |")

    # 1. 密码错误 / 账号或密码不对
    if any(k in combined for k in ["密码错误", "密码不对", "密码不正确", "Password Error", "userid error2", "userid error3", "ldap auth error"]):
        return (
            "密码错误",
            f"网关返回：【{detailed_prompt or p_msg or '账号或密码错误'}】\n"
            f"请检查账号 [{account}] 的密码是否正确。\n"
            f"（提示：校园网统一使用安医大信息门户密码；入校新生默认密码一般为 Ahykd_身份证后六位）",
            True
        )

    # 2. 账号不存在
    if any(k in combined for k in ["账号不存在", "用户不存在", "Account does not exist", "userid error1", "未开户"]):
        return (
            "账号不存在",
            f"网关返回：【{detailed_prompt or p_msg or '账号不存在'}】\n"
            f"请检查学号 [{account}] 是否填写有误，或所选运营商后缀是否未开通！",
            True
        )

    # 3. 运营商通道未绑定 / 运营商账号密码错误
    if any(k in combined for k in ["运营商", "绑定", "bind", "isp", "ISP"]):
        return (
            "运营商通道不匹配/未绑定",
            f"网关返回：【{detailed_prompt or p_msg or '运营商绑定校验失败'}】\n"
            f"当前选择的认证账号为 [{account}]。\n"
            f"可能原因：您未办理该运营商校园宽带，或选错了电信/联通/移动通道（若仅使用学校免费基础网，请切换为『校园网/教育网(无后缀)』）。",
            True
        )

    # 4. 欠费 / 停机 / 流量时长超支
    if any(k in combined for k in ["欠费", "停机", "超支", "暂停", "过期", "余额不足"]) or d_msg in (4, "4", 5, "5"):
        reason = "本账号已暂停使用或停机" if d_msg in (5, "5") else "账号费用超支、欠费或时长流量超限"
        return (
            "账号欠费或停机",
            f"网关返回：【{detailed_prompt or p_msg or reason}】\n"
            f"您的校园网或运营商宽带账户可能已欠费停机，请核查话费/宽带状态。",
            True
        )

    # 5. 账号已在其他终端登录 / 数量超限
    if any(k in combined for k in ["其他终端", "正在使用中", "终端数", "上限", "limit"]) or d_msg in (2, "2"):
        return (
            "多设备登录受限",
            f"网关返回：【{detailed_prompt or p_msg or '该账号正在其他终端使用中'}】\n"
            f"您的账号已在其他电脑/手机登录且达到设备数上限，请先在其他设备下线。",
            False
        )

    # 6. 首次登录强制改密 / VLAN 绑定错误
    if any(k in combined for k in ["VLAN", "首次登录", "修改密码", "err(6)", "error bind vid"]):
        return (
            "需修改初始密码或VLAN受限",
            f"网关返回：【{detailed_prompt or p_msg or '首次登录须修改初始密码或VLAN受限'}】\n"
            f"若是新生首次使用，请先用浏览器打开 10.0.2.2 修改初始密码后再使用本工具。",
            True
        )

    # 7. 根据 /drcom/login 的 msg=1 且 msga 为空判断（标准 Dr.COM 密码不对返回）
    if d_msg in (0, 1, "0", "1") and d_msga == "" and p_ret in (1, "1"):
        return (
            "账号或密码不对",
            f"网关拒绝了 [{account}] 的登录请求（错误码 ret_code=1）。\n"
            f"常见原因：① 校园网密码输入错误；② 运营商通道（电信/联通/移动/校园网）选择与实际办理不一致。",
            True
        )

    # 8. 终端 IP 已经在线
    if p_ret in (2, "2") or "已经在线" in combined or d_msga == "clientip online":
        return (
            "本机IP已经在线",
            f"网关提示当前电脑 IP ({user_ip}) 已经处于在线状态（旧连接尚未完全断开）。",
            False
        )

    # 9. 其他未知网关错误
    readable = detailed_prompt or p_msg or d_msga or f"网关错误码 ret_code={p_ret}, msg={d_msg}"
    return (
        "网关认证未通过",
        f"网关返回提示：【{readable}】\n请检查账号密码及运营商通道配置是否正确，或稍后重试。",
        False
    )


def load_config() -> dict:
    """加载本地配置文件，若不存在则返回空默认结构"""
    cfg = {
        "portal_ip": PORTAL_IP_DEFAULT,
        "ep_port": EP_PORT_DEFAULT,
        "account": "",
        "password": "",
        "check_interval_seconds": 20,
        "target_carrier_keywords": ["电信", "telecom", "ctnet", "chinatelecom"],
        "auto_reconnect_enabled": True,
    }
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    cfg.update(data)
        except Exception:
            pass
    return cfg


def save_config(cfg: dict) -> bool:
    """持久化保存配置到 campus_config.json"""
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        return True
    except Exception:
        return False


def split_account(full_account: str):
    """将完整账号（如 2026xxxxxx@telecom）拆分为 (纯学号, 后缀)"""
    full_account = (full_account or "").strip()
    if "@" in full_account:
        parts = full_account.split("@", 1)
        return parts[0].strip(), "@" + parts[1].strip()
    return full_account, "@telecom" if not full_account else ""


def ip_to_int(ip_str: str) -> int:
    """将点分十进制 IPv4 转为网关解绑所需的 32 位无符号整数"""
    try:
        return struct.unpack("!I", socket.inet_aton(ip_str))[0]
    except Exception:
        return 0


def detect_network_identity(portal_ip: str = PORTAL_IP_DEFAULT, ep_port: str = EP_PORT_DEFAULT):
    """
    自动获取当前电脑的校园网内网 IP、网卡 MAC 地址及当前在线账号。
    """
    user_ip = ""
    user_mac = ""
    online_uid = ""

    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    # 1. 尝试从 Dr.COM 网关状态接口直接读取
    try:
        req = urllib.request.Request(
            f"http://{portal_ip}/drcom/chkstatus?callback=dr1",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with opener.open(req, timeout=2.5) as resp:
            raw = decode_portal_bytes(resp.read())
            m_ip = re.search(r'"v46ip"\s*:\s*"([^"]+)"', raw) or re.search(r'"v4ip"\s*:\s*"([^"]+)"', raw)
            if m_ip and m_ip.group(1) and m_ip.group(1) != "0.0.0.0":
                user_ip = m_ip.group(1).strip()
            m_mac = re.search(r'"olmac"\s*:\s*"([^"]+)"', raw)
            if m_mac and m_mac.group(1) and m_mac.group(1) != "000000000000":
                user_mac = m_mac.group(1).replace("-", "").replace(":", "").strip().upper()
            m_uid = re.search(r'"uid"\s*:\s*"([^"]+)"', raw)
            if m_uid and m_uid.group(1):
                online_uid = m_uid.group(1).strip()
    except Exception:
        pass

    # 2. 本地路由表探测内网 IP
    if not user_ip:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.settimeout(1.5)
            s.connect((portal_ip, int(ep_port)))
            user_ip = s.getsockname()[0]
            s.close()
        except Exception:
            try:
                user_ip = socket.gethostbyname(socket.gethostname())
            except Exception:
                user_ip = ""

    # 3. 通过 ipconfig /all 匹配对应 IP 所在网卡的 MAC 地址
    if not user_mac and user_ip:
        try:
            flags = subprocess.CREATE_NO_WINDOW if sys.platform.startswith("win") else 0
            out = subprocess.check_output("ipconfig /all", shell=True, creationflags=flags).decode("gbk", errors="ignore")
            for block in re.split(r"\r?\n(?=\S)", out):
                if user_ip in block:
                    m = re.search(r"([0-9A-Fa-f]{2}(?:-[0-9A-Fa-f]{2}){5})", block)
                    if m:
                        user_mac = m.group(1).replace("-", "").upper()
                        break
        except Exception:
            pass

    # 4. 兜底 MAC 获取
    if not user_mac:
        try:
            node = uuid.getnode()
            user_mac = f"{node:012X}"
        except Exception:
            user_mac = "000000000000"

    return user_ip, user_mac, online_uid


def get_startup_bat_path() -> str:
    """获取当前 Windows 用户开机启动文件夹中的快捷批处理路径"""
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return ""
    startup_dir = os.path.join(appdata, r"Microsoft\Windows\Start Menu\Programs\Startup")
    return os.path.join(startup_dir, "AHMU_Campus_AutoReauth.bat")


def is_startup_enabled() -> bool:
    path = get_startup_bat_path()
    return bool(path and os.path.exists(path))


def set_startup_enabled(enable: bool) -> tuple[bool, str]:
    """开启或关闭 Windows 开机自动启动（遵循 CP936/ANSI 编码与 cd /d 规范）"""
    bat_path = get_startup_bat_path()
    if not bat_path:
        return False, "无法定位系统 Startup 启动目录"
    try:
        if enable:
            if getattr(sys, 'frozen', False):
                cmd_line = f'start "" "{APP_EXEC_PATH}"'
            else:
                py_exe = sys.executable
                pyw_exe = py_exe.replace("python.exe", "pythonw.exe")
                runner = pyw_exe if os.path.exists(pyw_exe) else py_exe
                cmd_line = f'start "" "{runner}" "{APP_EXEC_PATH}"'

            content = (
                "@echo off\r\n"
                f'cd /d "{BASE_DIR}"\r\n'
                f"{cmd_line}\r\n"
                "exit\r\n"
            )
            with open(bat_path, "w", encoding="cp936", errors="ignore") as f:
                f.write(content)
            return True, "已成功设置开机自动运行！"
        else:
            if os.path.exists(bat_path):
                os.remove(bat_path)
            return True, "已取消开机自动运行。"
    except Exception as e:
        return False, f"操作失败: {e}"


# ==================== 核心网络认证组件 ====================

def wait_for_network(portal_ip: str, ep_port: str, log_fn, max_wait_seconds: int = 20) -> bool:
    """开机后等待网卡连通、能访问网关"""
    log_fn("正在等待网络链路就绪...")
    start_time = time.time()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    while time.time() - start_time < max_wait_seconds:
        try:
            req = urllib.request.Request(
                f"http://{portal_ip}:{ep_port}/",
                headers={"User-Agent": "curl/7.0"}
            )
            with opener.open(req, timeout=2) as resp:
                log_fn("网络链路已就绪，成功探测到校园网认证网关。")
                return True
        except Exception:
            time.sleep(1.2)

    log_fn("等待网关探测超时，尝试直接执行认证流程...")
    return False


def get_current_isp():
    """探测当前公网 IP 与所属运营商（直连绕过系统代理）"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    # 源 1: cip.cc
    try:
        req = urllib.request.Request("http://cip.cc", headers={"User-Agent": "curl/7.68.0"})
        with opener.open(req, timeout=4) as resp:
            lines = resp.read().decode('utf-8', errors='ignore').splitlines()
            ip, isp = "", ""
            for l in lines:
                if l.startswith("IP"):
                    ip = l.split(":", 1)[-1].strip()
                elif l.startswith("数据二") or l.startswith("运营商") or l.startswith("网络"):
                    isp = l.split(":", 1)[-1].strip()
            if ip:
                return ip, isp or "未知运营商"
    except Exception:
        pass

    # 源 2: myip.ipip.net
    try:
        req = urllib.request.Request("http://myip.ipip.net", headers={"User-Agent": "curl/7.68.0"})
        with opener.open(req, timeout=4) as resp:
            content = resp.read().decode("utf-8", errors="ignore").strip()
            if "当前 IP" in content:
                parts = content.split("来自于：")
                ip = parts[0].replace("当前 IP：", "").strip()
                desc = parts[1].strip() if len(parts) > 1 else ""
                return ip, desc
    except Exception:
        pass

    return "未知", "探测超时/无外网"


def do_logout(portal_ip: str, ep_port: str, account: str, user_ip: str, user_mac: str, online_uid: str, log_fn):
    """
    全方位注销并解绑旧连接：
    1. 801 Portal mac/unbind（同时解绑完整账号与无后缀纯学号，兼容 isp_unbind_suffix=1）
    2. 801 Portal 标准 /eportal/portal/logout 接口
    3. 80 端口 /drcom/logout 全局注销接口
    """
    log_fn("步骤 1/3: 正在清除当前旧连接（注销下线）...")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    ip_int = ip_to_int(user_ip) if user_ip else 0

    accounts_to_unbind = []
    for candidate in (online_uid, account):
        if candidate:
            if candidate not in accounts_to_unbind:
                accounts_to_unbind.append(candidate)
            pure_id, _ = split_account(candidate)
            if pure_id and pure_id not in accounts_to_unbind:
                accounts_to_unbind.append(pure_id)

    # 1. 801 portal mac/unbind
    for acc in accounts_to_unbind:
        try:
            unbind_url = (
                f"http://{portal_ip}:{ep_port}/eportal/portal/mac/unbind?"
                f"callback=drLogout1&user_account={urllib.parse.quote(acc)}"
                f"&wlan_user_mac={user_mac}&wlan_user_ip={ip_int}&jsVersion={JS_VERSION}"
            )
            req = urllib.request.Request(unbind_url, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(req, timeout=3) as resp:
                res_text = decode_portal_bytes(resp.read())
                res_obj = parse_jsonp(res_text)
                msg_str = res_obj.get("msg") or res_text[:60]
                log_fn(f" -> 解绑 [{acc}]: {msg_str}")
        except Exception as e:
            log_fn(f" -> 解绑请求略过: {e}")

    # 2. 801 portal 标准 logout 接口 (关键：释放非本账号的残留在线状态)
    try:
        portal_logout_url = (
            f"http://{portal_ip}:{ep_port}/eportal/portal/logout?"
            f"callback=drLogout0&login_method=1&user_account=drcom&user_password=123"
            f"&ac_logout=0&register_mode=1&wlan_user_ip={user_ip}&wlan_user_ipv6="
            f"&wlan_vlan_id=0&wlan_user_mac={user_mac}&wlan_ac_ip=&wlan_ac_name="
            f"&jsVersion={JS_VERSION}"
        )
        req = urllib.request.Request(portal_logout_url, headers={"User-Agent": "Mozilla/5.0"})
        with opener.open(req, timeout=3) as resp:
            res_text = decode_portal_bytes(resp.read())
            res_obj = parse_jsonp(res_text)
            msg_str = res_obj.get("msg") or res_text[:60]
            log_fn(f" -> 801 Portal 注销: {msg_str}")
    except Exception:
        pass

    # 3. drcom 全局 logout
    try:
        req = urllib.request.Request(
            f"http://{portal_ip}/drcom/logout?callback=drLogout2",
            headers={"User-Agent": "Mozilla/5.0"}
        )
        with opener.open(req, timeout=3) as resp:
            res_text = decode_portal_bytes(resp.read())
            res_obj = parse_jsonp(res_text)
            log_fn(f" -> 网关全局注销: result={res_obj.get('result', 'ok')}")
    except Exception as e:
        log_fn(f" -> 网关注销略过: {e}")


def do_login(portal_ip: str, ep_port: str, account: str, password: str, user_ip: str, user_mac: str, log_fn, max_retries: int = 3):
    """
    向网关发起认证请求（801 Portal + /drcom/login 双通道保障），
    返回 (success: bool, err_title: str, err_detail: str, is_config_error: bool)
    """
    log_fn(f"步骤 2/3: 正在向网关发起认证 ({account})...")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    login_url = (
        f"http://{portal_ip}:{ep_port}/eportal/portal/login?"
        f"callback=drLogin&login_method=1"
        f"&user_account={urllib.parse.quote(account)}"
        f"&user_password={urllib.parse.quote(password)}"
        f"&wlan_user_ip={user_ip}&wlan_user_ipv6="
        f"&wlan_user_mac={user_mac}&wlan_ac_ip=&wlan_ac_name="
        f"&jsVersion={JS_VERSION}"
    )

    dr_url = (
        f"http://{portal_ip}/drcom/login?callback=drLogin2&DDDDD={urllib.parse.quote(account)}"
        f"&upass={urllib.parse.quote(password)}&0MKKey=123456&R1=&R2=0&R3=&R6=0&para=00"
        f"&v6ip=&terminal_type=1&lang=zh-cn"
    )

    last_err_title = "认证失败"
    last_err_detail = "未能完成网关认证，请检查网络连接或账号配置。"
    last_is_cfg_err = False

    for attempt in range(1, max_retries + 1):
        if attempt > 1:
            log_fn(f"正在进行第 {attempt}/{max_retries} 次重试登录...")

        portal_json = {}
        drcom_json = {}
        success = False

        # 1. 尝试 801 Portal 接口
        try:
            req = urllib.request.Request(login_url, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(req, timeout=6) as resp:
                res_text = decode_portal_bytes(resp.read())
                portal_json = parse_jsonp(res_text)
                p_res = portal_json.get("result")
                p_msg = portal_json.get("msg", "")
                p_ret = portal_json.get("ret_code", "")
                log_fn(f" -> 801 Portal 响应: result={p_res}, msg=\"{p_msg}\", ret_code={p_ret}")
                if p_res in (1, "1", "ok") or "成功" in str(p_msg):
                    success = True
        except Exception as e:
            log_fn(f" -> 801 Portal 请求异常: {e}")

        # 2. 若 801 未直接成功，调用 /drcom/login 辅助认证并获取底层 msga 错误码
        if not success:
            try:
                req = urllib.request.Request(dr_url, headers={"User-Agent": "Mozilla/5.0"})
                with opener.open(req, timeout=6) as resp:
                    res_text = decode_portal_bytes(resp.read())
                    drcom_json = parse_jsonp(res_text)
                    d_res = drcom_json.get("result")
                    d_msg = drcom_json.get("msg", "")
                    d_msga = drcom_json.get("msga", "")
                    log_fn(f" -> /drcom/login 响应: result={d_res}, msg={d_msg}, msga=\"{d_msga}\"")
                    if d_res in (1, "1", "ok"):
                        success = True
            except Exception as e:
                log_fn(f" -> /drcom/login 请求异常: {e}")

        if success:
            log_fn("✅ 认证请求已被网关接受，正在建立目标通道...")
            return True, "", "", False

        # 诊断具体失败原因
        err_title, err_detail, is_cfg_err = diagnose_login_failure(
            portal_json, drcom_json, portal_ip, ep_port, user_ip, user_mac, account
        )
        last_err_title = err_title
        last_err_detail = err_detail
        last_is_cfg_err = is_cfg_err

        log_fn(f"⚠️ 失败原因诊断: 【{err_title}】 {err_detail.splitlines()[0]}")

        # 如果是明确的密码错误、账号不存在、运营商选错或欠费，不再做无意义重试，立即中断并提示用户！
        if is_cfg_err:
            log_fn("🛑 检测到账号/密码/运营商配置问题，已停止自动重试，请修改配置后重试！")
            return False, last_err_title, last_err_detail, True

        # 如果提示“本机IP已经在线”，说明刚刚的注销网关还没释放完，补发一次注销并等待 2.5 秒再试
        if err_title == "本机IP已经在线" and attempt < max_retries:
            log_fn(" -> 检测到旧会话尚未彻底释放，正在补发注销指令并等待 2.5 秒...")
            do_logout(portal_ip, ep_port, account, user_ip, user_mac, "", log_fn)
            time.sleep(2.5)
            continue

        if attempt < max_retries:
            time.sleep(2.0)

    return False, last_err_title, last_err_detail, last_is_cfg_err


# ==================== 图形界面 (Tkinter GUI) ====================

class CampusReauthApp:
    def __init__(self, root: tk.Tk, force_config: bool = False):
        self.root = root
        self.root.title("安徽医科大学 - 校园网自动重登工具 (通用版)")
        self.root.geometry("580x490")
        self.root.minsize(540, 450)
        self.root.configure(bg="#F5F7FA")

        # 窗口居中
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = (sw - 580) // 2
        y = (sh - 490) // 2
        self.root.geometry(f"580x490+{x}+{y}")

        self.config = load_config()
        self.countdown_sec = 3
        self.countdown_job = None
        self.auto_close_job = None
        self.is_running_auth = False
        self.user_interacted = False

        self._build_ui()

        # 绑定任意按键中断倒计时（方便启动 3 秒内按回车/空格直接改密）
        self.root.bind("<Key>", self._on_any_key_pressed)

        # 判断是否首次使用（无账号或无密码）或指定打开配置
        if force_config or not self.config.get("account") or not self.config.get("password"):
            self._append_log("👋 欢迎使用！检测到尚未配置校园网账号密码，请先完成首次保存。")
            self._set_banner("首次使用：请先设置并保存您的校园网账号与密码", mode="info")
            self.root.after(150, lambda: self.open_config_dialog(first_time=True))
        else:
            self._refresh_account_banner()
            self._start_boot_countdown()

    def _build_ui(self):
        # 顶部标题栏区域
        header = tk.Frame(self.root, bg="#1E40AF", padx=18, pady=12)
        header.pack(fill=tk.X)

        title_lbl = tk.Label(
            header,
            text="安徽医科大学 · 校园网自动重登认证助手",
            font=("Microsoft YaHei UI", 13, "bold"),
            fg="#FFFFFF",
            bg="#1E40AF"
        )
        title_lbl.pack(anchor="w")

        self.sub_lbl = tk.Label(
            header,
            text="当前账号: 未设置  |  本机 IP/MAC 自动识别",
            font=("Microsoft YaHei UI", 9),
            fg="#DBEAFE",
            bg="#1E40AF"
        )
        self.sub_lbl.pack(anchor="w", pady=(3, 0))

        # 倒计时 / 状态横幅
        self.banner_frame = tk.Frame(
            self.root, bg="#EFF6FF", highlightbackground="#BFDBFE", highlightthickness=1, padx=14, pady=9
        )
        self.banner_frame.pack(fill=tk.X, padx=14, pady=(12, 6))

        self.status_var = tk.StringVar(value="准备就绪")
        self.status_lbl = tk.Label(
            self.banner_frame,
            textvariable=self.status_var,
            font=("Microsoft YaHei UI", 9, "bold"),
            fg="#1D4ED8",
            bg="#EFF6FF",
            justify=tk.LEFT,
            wraplength=520
        )
        self.status_lbl.pack(side=tk.LEFT, fill=tk.X)

        # 操作按钮栏
        btn_frame = tk.Frame(self.root, bg="#F5F7FA", padx=14, pady=4)
        btn_frame.pack(fill=tk.X)

        self.reauth_btn = tk.Button(
            btn_frame,
            text="🚀 立即重登校园网",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg="#2563EB",
            fg="#FFFFFF",
            activebackground="#1D4ED8",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            padx=14,
            pady=6,
            cursor="hand2",
            command=self.trigger_reauth_manual
        )
        self.reauth_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.config_btn = tk.Button(
            btn_frame,
            text="🔑 修改账号 / 密码",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg="#F59E0B",
            fg="#FFFFFF",
            activebackground="#D97706",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            padx=14,
            pady=6,
            cursor="hand2",
            command=lambda: self.open_config_dialog(first_time=False)
        )
        self.config_btn.pack(side=tk.LEFT, padx=(0, 8))

        self.startup_var = tk.BooleanVar(value=is_startup_enabled())
        self.startup_chk = tk.Checkbutton(
            btn_frame,
            text="开机自动重登",
            variable=self.startup_var,
            font=("Microsoft YaHei UI", 9),
            bg="#F5F7FA",
            activebackground="#F5F7FA",
            cursor="hand2",
            command=self._toggle_startup
        )
        self.startup_chk.pack(side=tk.RIGHT)

        # 日志文本框区域（使用 Microsoft YaHei UI 保证中文清晰不乱码）
        log_container = tk.Frame(self.root, bg="#F5F7FA", padx=14, pady=6)
        log_container.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            log_container,
            font=("Microsoft YaHei UI", 9),
            bg="#111827",
            fg="#E5E7EB",
            insertbackground="#FFFFFF",
            relief=tk.FLAT,
            padx=10,
            pady=8,
            state=tk.DISABLED,
            wrap=tk.WORD
        )
        scrollbar = ttk.Scrollbar(log_container, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 底部提示栏
        footer = tk.Label(
            self.root,
            text="提示：启动倒计时期间按【回车/空格/任意键】或点击【修改账号/密码】可随时更改已保存的密码",
            font=("Microsoft YaHei UI", 8),
            fg="#6B7280",
            bg="#F5F7FA",
            pady=4
        )
        footer.pack(side=tk.BOTTOM, fill=tk.X)

    def _set_banner(self, text: str, mode: str = "info"):
        """更新顶部状态横幅颜色与文本：info(蓝) / success(绿) / error(红) / warn(黄)"""
        palette = {
            "info": ("#EFF6FF", "#BFDBFE", "#1D4ED8"),
            "success": ("#ECFDF5", "#A7F3D0", "#047857"),
            "error": ("#FEF2F2", "#FECACA", "#B91C1C"),
            "warn": ("#FFFBEB", "#FDE68A", "#B45309"),
        }
        bg, border, fg = palette.get(mode, palette["info"])
        self.banner_frame.configure(bg=bg, highlightbackground=border)
        self.status_lbl.configure(bg=bg, fg=fg)
        self.status_var.set(text)

    def _refresh_account_banner(self):
        acc = self.config.get("account", "未设置")
        _, suffix = split_account(acc)
        carrier_name = "校园网"
        for label, sfx, _ in CARRIER_OPTIONS:
            if sfx == suffix:
                carrier_name = label.split(" ")[0]
                break
        self.sub_lbl.config(text=f"当前保存账号: {acc} ({carrier_name})  |  密码已保存至本地")

    def _append_log(self, msg: str):
        now = datetime.now().strftime("%H:%M:%S")
        line = f"[{now}] {msg}\n"
        print(line.strip(), flush=True)

        def _update():
            self.log_text.configure(state=tk.NORMAL)
            self.log_text.insert(tk.END, line)
            self.log_text.see(tk.END)
            self.log_text.configure(state=tk.DISABLED)

        self.root.after(0, _update)

    def _cancel_timers(self):
        if self.countdown_job is not None:
            try:
                self.root.after_cancel(self.countdown_job)
            except Exception:
                pass
            self.countdown_job = None
        if self.auto_close_job is not None:
            try:
                self.root.after_cancel(self.auto_close_job)
            except Exception:
                pass
            self.auto_close_job = None

    def _on_any_key_pressed(self, event):
        """启动倒计时阶段按任意键中断倒计时并弹出修改密码窗口"""
        if self.countdown_job is not None and not self.is_running_auth:
            self.user_interacted = True
            self._cancel_timers()
            self._append_log("⏸️ 已响应按键中断自动倒计时，正在打开账号密码修改窗口...")
            self._set_banner("已暂停自动重登 - 正在修改账号密码", mode="warn")
            self.open_config_dialog(first_time=False)
        elif self.auto_close_job is not None:
            self.user_interacted = True
            self._cancel_timers()
            self._set_banner("已取消自动关闭窗口，您可随时查看日志或修改配置", mode="info")

    def _start_boot_countdown(self):
        self._cancel_timers()
        self.countdown_sec = 3
        self._tick_countdown()

    def _tick_countdown(self):
        if self.countdown_sec > 0:
            self._set_banner(
                f"⏳ {self.countdown_sec} 秒后自动重登... (按任意键或点击【修改账号/密码】可更改配置)",
                mode="info"
            )
            self.countdown_sec -= 1
            self.countdown_job = self.root.after(1000, self._tick_countdown)
        else:
            self.countdown_job = None
            self.start_reauth_flow()

    def _toggle_startup(self):
        self.user_interacted = True
        self._cancel_timers()
        enable = self.startup_var.get()
        ok, msg = set_startup_enabled(enable)
        self._append_log(f"⚙️ 开机自启设置: {msg}")
        if not ok:
            self.startup_var.set(not enable)
            messagebox.showwarning("设置提示", msg, parent=self.root)

    def trigger_reauth_manual(self):
        self.user_interacted = True
        self._cancel_timers()
        if not self.config.get("account") or not self.config.get("password"):
            self.open_config_dialog(first_time=True)
            return
        self.start_reauth_flow()

    def open_config_dialog(self, first_time: bool = False):
        """弹出保存/修改账号密码对话框"""
        self.user_interacted = True
        self._cancel_timers()
        if not first_time:
            self._set_banner("正在修改已保存的账号密码配置...", mode="warn")

        dlg = tk.Toplevel(self.root)
        dlg.title("首次运行 - 绑定校园网账号密码" if first_time else "修改校园网账号与密码")
        dlg.geometry("430x360")
        dlg.resizable(False, False)
        dlg.configure(bg="#FFFFFF")
        dlg.transient(self.root)
        dlg.grab_set()

        # 居中于主窗口
        self.root.update_idletasks()
        rx = self.root.winfo_rootx() + (self.root.winfo_width() - 430) // 2
        ry = self.root.winfo_rooty() + (self.root.winfo_height() - 360) // 2
        dlg.geometry(f"430x360+{max(0, rx)}+{max(0, ry)}")

        title_text = "👋 首次使用：请保存校园网账号密码" if first_time else "🔑 更改已保存的账号与密码"
        tk.Label(
            dlg, text=title_text, font=("Microsoft YaHei UI", 11, "bold"), bg="#FFFFFF", fg="#1E3A8A"
        ).pack(anchor="w", padx=22, pady=(16, 4))

        tk.Label(
            dlg,
            text="保存后下次打开将自动登录；2026年4月起校园网统一使用信息门户密码。",
            font=("Microsoft YaHei UI", 8),
            bg="#FFFFFF",
            fg="#6B7280"
        ).pack(anchor="w", padx=22, pady=(0, 12))

        form = tk.Frame(dlg, bg="#FFFFFF", padx=22)
        form.pack(fill=tk.X)

        cur_student_id, cur_suffix = split_account(self.config.get("account", ""))
        cur_pwd = self.config.get("password", "")

        # 1. 学号/账号输入
        tk.Label(form, text="校园网学号 / 账号:", font=("Microsoft YaHei UI", 9, "bold"), bg="#FFFFFF", fg="#374151").grid(
            row=0, column=0, sticky="w", pady=6
        )
        acc_var = tk.StringVar(value=cur_student_id)
        acc_entry = ttk.Entry(form, textvariable=acc_var, font=("Consolas", 10), width=26)
        acc_entry.grid(row=0, column=1, sticky="ew", padx=(10, 0), pady=6)

        # 2. 运营商通道选择
        tk.Label(form, text="运营商出口通道:", font=("Microsoft YaHei UI", 9, "bold"), bg="#FFFFFF", fg="#374151").grid(
            row=1, column=0, sticky="w", pady=6
        )
        carrier_labels = [item[0] for item in CARRIER_OPTIONS]
        selected_idx = 0
        for idx, (_, sfx, _) in enumerate(CARRIER_OPTIONS):
            if sfx == cur_suffix:
                selected_idx = idx
                break
        carrier_var = tk.StringVar(value=carrier_labels[selected_idx])
        carrier_combo = ttk.Combobox(
            form, textvariable=carrier_var, values=carrier_labels, state="readonly", font=("Microsoft YaHei UI", 9), width=24
        )
        carrier_combo.grid(row=1, column=1, sticky="ew", padx=(10, 0), pady=6)

        # 3. 密码输入 + 显示密码勾选
        tk.Label(form, text="校园网登录密码:", font=("Microsoft YaHei UI", 9, "bold"), bg="#FFFFFF", fg="#374151").grid(
            row=2, column=0, sticky="w", pady=6
        )
        pwd_frame = tk.Frame(form, bg="#FFFFFF")
        pwd_frame.grid(row=2, column=1, sticky="ew", padx=(10, 0), pady=6)

        pwd_var = tk.StringVar(value=cur_pwd)
        pwd_entry = ttk.Entry(pwd_frame, textvariable=pwd_var, font=("Consolas", 10), show="●", width=19)
        pwd_entry.pack(side=tk.LEFT, fill=tk.X, expand=True)

        show_pwd_var = tk.BooleanVar(value=False)

        def _toggle_pwd():
            pwd_entry.config(show="" if show_pwd_var.get() else "●")

        tk.Checkbutton(
            pwd_frame, text="显示", variable=show_pwd_var, command=_toggle_pwd, bg="#FFFFFF", font=("Microsoft YaHei UI", 8)
        ).pack(side=tk.RIGHT, padx=(4, 0))

        form.columnconfigure(1, weight=1)

        # 预览完整认证账号
        preview_var = tk.StringVar()

        def _update_preview(*_):
            raw_id = acc_var.get().strip()
            if "@" in raw_id:
                preview_var.set(f"最终发送认证账号: {raw_id}")
                return
            sel_label = carrier_var.get()
            sfx = "@telecom"
            for label, s, _ in CARRIER_OPTIONS:
                if label == sel_label:
                    sfx = s
                    break
            preview_var.set(f"最终发送认证账号: {raw_id}{sfx}" if raw_id else "请输入您的学号")

        acc_var.trace_add("write", _update_preview)
        carrier_var.trace_add("write", _update_preview)
        _update_preview()

        tk.Label(dlg, textvariable=preview_var, font=("Microsoft YaHei UI", 8), bg="#FFFFFF", fg="#2563EB").pack(
            anchor="w", padx=22, pady=(6, 12)
        )

        # 底部按钮区
        btn_bar = tk.Frame(dlg, bg="#FFFFFF", padx=22)
        btn_bar.pack(fill=tk.X, pady=(6, 10))

        def _save_and_login():
            raw_id = acc_var.get().strip()
            raw_pwd = pwd_var.get().strip()
            if not raw_id:
                messagebox.showwarning("提示", "学号/账号不能为空！", parent=dlg)
                acc_entry.focus_set()
                return
            if not raw_pwd:
                messagebox.showwarning("提示", "校园网密码不能为空！", parent=dlg)
                pwd_entry.focus_set()
                return

            sel_label = carrier_var.get()
            chosen_sfx = "@telecom"
            chosen_keywords = ["电信", "telecom", "ctnet", "chinatelecom"]
            for label, sfx, kw in CARRIER_OPTIONS:
                if label == sel_label:
                    chosen_sfx = sfx
                    chosen_keywords = kw
                    break

            full_acc = raw_id if "@" in raw_id else f"{raw_id}{chosen_sfx}"
            self.config["account"] = full_acc
            self.config["password"] = raw_pwd
            self.config["target_carrier_keywords"] = chosen_keywords

            if save_config(self.config):
                self._refresh_account_banner()
                self._append_log(f"✅ 账号配置已保存: {full_acc}")
                dlg.destroy()
                self.start_reauth_flow()
            else:
                messagebox.showerror("保存失败", "无法写入配置文件 campus_config.json，请检查目录权限。", parent=dlg)

        def _clear_saved():
            if messagebox.askyesno("清除确认", "确定要清除本地已保存的个人账号和密码吗？\n（适合将此软件分享给其他同学前执行）", parent=dlg):
                self.config["account"] = ""
                self.config["password"] = ""
                save_config(self.config)
                acc_var.set("")
                pwd_var.set("")
                self._refresh_account_banner()
                self._append_log("🧹 已清除本地保存的个人账号与密码配置。")

        save_btn = tk.Button(
            btn_bar,
            text="💾 保存并立即重登",
            font=("Microsoft YaHei UI", 9, "bold"),
            bg="#2563EB",
            fg="#FFFFFF",
            activebackground="#1D4ED8",
            activeforeground="#FFFFFF",
            relief=tk.FLAT,
            padx=14,
            pady=6,
            cursor="hand2",
            command=_save_and_login
        )
        save_btn.pack(side=tk.LEFT)

        clear_btn = tk.Button(
            btn_bar,
            text="🧹 清除记录",
            font=("Microsoft YaHei UI", 9),
            bg="#FEE2E2",
            fg="#B91C1C",
            activebackground="#FECACA",
            relief=tk.FLAT,
            padx=10,
            pady=6,
            cursor="hand2",
            command=_clear_saved
        )
        clear_btn.pack(side=tk.LEFT, padx=(8, 0))

        if not first_time:
            cancel_btn = tk.Button(
                btn_bar,
                text="取消",
                font=("Microsoft YaHei UI", 9),
                bg="#E5E7EB",
                fg="#374151",
                relief=tk.FLAT,
                padx=12,
                pady=6,
                cursor="hand2",
                command=dlg.destroy
            )
            cancel_btn.pack(side=tk.RIGHT)

        dlg.bind("<Return>", lambda e: _save_and_login())
        acc_entry.focus_set()

    def start_reauth_flow(self):
        if self.is_running_auth:
            return
        self.is_running_auth = True
        self.reauth_btn.config(state=tk.DISABLED, text="⏳ 正在重登中...")
        self._set_banner("🔄 正在执行校园网注销与重认证流程...", mode="info")

        t = threading.Thread(target=self._worker_reauth, daemon=True)
        t.start()

    def _worker_reauth(self):
        try:
            portal_ip = self.config.get("portal_ip", PORTAL_IP_DEFAULT)
            ep_port = str(self.config.get("ep_port", EP_PORT_DEFAULT))
            account = self.config.get("account", "")
            password = self.config.get("password", "")
            target_keywords = self.config.get("target_carrier_keywords", ["电信", "telecom"])

            self._append_log("=" * 52)
            self._append_log(f"开始执行自动重登流程 | 认证账号: {account}")

            wait_for_network(portal_ip, ep_port, self._append_log, max_wait_seconds=15)

            # 自动探测本机校园网 IP 与网卡 MAC
            user_ip, user_mac, online_uid = detect_network_identity(portal_ip, ep_port)
            self._append_log(f"自动识别本机内网 IP: {user_ip or '未知'} | 网卡 MAC: {user_mac}")
            if online_uid:
                self._append_log(f"检测到网关当前在线会话账号: {online_uid}")

            ip_before, isp_before = get_current_isp()
            self._append_log(f"重登前公网出口: {ip_before} ({isp_before})")

            do_logout(portal_ip, ep_port, account, user_ip, user_mac, online_uid, self._append_log)
            self._append_log("等待 3 秒让网关充分释放旧会话...")
            time.sleep(3)

            # 注销后再次确认本机 IP
            new_ip, new_mac, _ = detect_network_identity(portal_ip, ep_port)
            if new_ip:
                user_ip = new_ip
            if new_mac and new_mac != "000000000000":
                user_mac = new_mac

            login_ok, err_title, err_detail, is_cfg_err = do_login(
                portal_ip, ep_port, account, password, user_ip, user_mac, self._append_log, max_retries=3
            )

            if not login_ok:
                self._append_log("=" * 52)
                self._append_log(f"❌ 登录失败 [{err_title}]:")
                for line in err_detail.splitlines():
                    self._append_log(f"   {line}")
                self._append_log("=" * 52)
                self.root.after(
                    0,
                    lambda: self._on_reauth_failed(err_title, err_detail, is_cfg_err)
                )
                return

            self._append_log("步骤 3/3: 正在验证公网出口切换状态...")
            time.sleep(3.5)

            ip_after, isp_after = get_current_isp()
            self._append_log("=" * 52)
            self._append_log(f"重登后公网出口: {ip_after} ({isp_after})")

            matched = any(k.lower() in isp_after.lower() for k in target_keywords) or (ip_after != "未知" and login_ok)
            if matched and ip_after != "未知":
                self._append_log(f"🎉 认证成功！当前出口已连通: {ip_after} ({isp_after})")
                self._append_log("=" * 52)
                self.root.after(0, lambda: self._on_reauth_success(f"✅ 重登成功！当前出口: {ip_after} ({isp_after})"))
            else:
                self._append_log("⚠️ 网关已接受请求，但外网出口校验未达预期，请稍后重试或检查状态。")
                self._append_log("=" * 52)
                self.root.after(
                    0,
                    lambda: self._on_reauth_failed(
                        "外网出口未就绪",
                        f"网关认证请求已发送，但当前公网探测结果为: {ip_after} ({isp_after})。\n请确认账号套餐状态或点击【立即重登】再试一次。",
                        False
                    )
                )

        except Exception as e:
            self._append_log(f"❌ 执行过程发生异常: {e}")
            self.root.after(0, lambda: self._on_reauth_failed("程序执行异常", str(e), False))

    def _on_reauth_success(self, banner_msg: str):
        self.is_running_auth = False
        self.reauth_btn.config(state=tk.NORMAL, text="🚀 立即重登校园网")
        self._set_banner(banner_msg, mode="success")

        # 如果是开机静默自动触发且中途用户没有手动干预，成功后 4 秒自动关闭窗口
        if not self.user_interacted:
            self._start_auto_close_countdown(4)

    def _on_reauth_failed(self, err_title: str, err_detail: str, is_cfg_err: bool):
        self.is_running_auth = False
        self.reauth_btn.config(state=tk.NORMAL, text="🚀 立即重登校园网")
        first_line = err_detail.splitlines()[0] if err_detail else err_title
        self._set_banner(f"❌ 登录失败【{err_title}】：{first_line}", mode="error")

        # 弹出清晰的错误提示框，若属于账号/密码/运营商配置问题，直接询问是否打开修改密码窗口
        if is_cfg_err:
            ask_msg = (
                f"失败原因：【{err_title}】\n\n"
                f"{err_detail}\n\n"
                f"是否立即打开【修改账号/密码】窗口重新设置？"
            )
            if messagebox.askyesno(f"校园网登录失败 - {err_title}", ask_msg, icon="error", parent=self.root):
                self.open_config_dialog(first_time=False)
        else:
            messagebox.showwarning(
                f"校园网登录未成功 - {err_title}",
                f"失败原因：【{err_title}】\n\n{err_detail}",
                parent=self.root
            )

    def _start_auto_close_countdown(self, sec: int):
        if self.user_interacted:
            return
        if sec > 0:
            self._set_banner(f"✅ 重登成功！窗口将在 {sec} 秒后自动关闭 (按任意键保持窗口打开)", mode="success")
            self.auto_close_job = self.root.after(1000, lambda: self._start_auto_close_countdown(sec - 1))
        else:
            self.root.destroy()


def main():
    force_cfg = any(arg in sys.argv[1:] for arg in ("--config", "-c", "--reset"))
    root = tk.Tk()
    app = CampusReauthApp(root, force_config=force_cfg)
    root.mainloop()


if __name__ == "__main__":
    main()
