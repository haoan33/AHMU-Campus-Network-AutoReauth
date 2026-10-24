# -*- coding: utf-8 -*-
"""
安徽医科大学 (AHMU) 校园网运营商通道漂移监控与自愈守护程序
功能：
1. 直连探测公网出口运营商（中国电信/联通/移动/CERNET教育网），自动绕过梯子代理
2. 详细记录通道漂移日志（时间、变更前IP/归属 -> 变更后IP/归属、上个通道维持时长）至 carrier_switch.log
3. 一旦偏离中国电信，自动通过 eportal 801 真实 Portal 接口执行注销与电信重登
4. 本地持久化保存配置 (campus_config.json)
"""

import os
import sys
import time
import json
import urllib.request
import urllib.parse
from datetime import datetime

# Windows 命令行 UTF-8 输出防乱码
if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_FILE = os.path.join(SCRIPT_DIR, "campus_config.json")
LOG_FILE = os.path.join(SCRIPT_DIR, "carrier_switch.log")

DEFAULT_CONFIG = {
    "portal_ip": "10.0.2.2",
    "ep_port": "801",
    "account": "",
    "password": "",
    "user_ip": "",
    "user_mac": "",
    "check_interval_seconds": 20,
    "target_carrier_keywords": ["电信", "telecom", "ctnet", "chinatelecom"],
    "auto_reconnect_enabled": True
}

def log_message(text: str, print_to_console: bool = True):
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    formatted = f"[{timestamp}] {text}"
    if print_to_console:
        print(formatted)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(formatted + "\n")
    except Exception as e:
        print(f"写入日志失败: {e}")

def load_or_init_config():
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                user_cfg = json.load(f)
                config.update(user_cfg)
        except Exception as e:
            print(f"读取配置文件失败，使用默认值: {e}")

    # 检查密码是否存在
    while not config.get("password"):
        print("\n" + "=" * 55)
        print("【首次运行配置】未检测到校园网密码。")
        print(f"当前认证账号: {config.get('account')}")
        print("请输入您的校园网密码（输入后将永久保存在 campus_config.json 中）:")
        pwd = input("校园网密码: ").strip()
        if pwd:
            config["password"] = pwd
            try:
                with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                    json.dump(config, f, ensure_ascii=False, indent=2)
                print(">> 密码已成功永久保存到 campus_config.json！")
            except Exception as e:
                print(f">> 保存配置文件出错: {e}")
            break
        else:
            print("密码不能为空，请重新输入！")

    return config

def get_external_ip_and_isp():
    """直连国内权威 IP 归属地接口，绕过系统 HTTP 代理"""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    
    # 策略 1: cip.cc
    try:
        req = urllib.request.Request("http://cip.cc", headers={"User-Agent": "curl/7.68.0", "Accept": "*/*"})
        with opener.open(req, timeout=5) as resp:
            content = resp.read().decode("utf-8", errors="ignore")
            ip, isp, location = "", "", ""
            for line in content.splitlines():
                if line.startswith("IP"):
                    ip = line.split(":", 1)[-1].strip()
                elif line.startswith("网络") or line.startswith("运营商"):
                    isp = line.split(":", 1)[-1].strip()
                elif line.startswith("地址"):
                    location = line.split(":", 1)[-1].strip()
            if ip:
                full_desc = f"{isp} ({location})" if isp else location
                return ip, full_desc or "未知运营商"
    except Exception:
        pass

    # 策略 2: myip.ipip.net
    try:
        req = urllib.request.Request("http://myip.ipip.net", headers={"User-Agent": "curl/7.68.0"})
        with opener.open(req, timeout=5) as resp:
            content = resp.read().decode("utf-8", errors="ignore").strip()
            if "当前 IP" in content:
                parts = content.split("来自于：")
                ip = parts[0].replace("当前 IP：", "").strip()
                desc = parts[1].strip() if len(parts) > 1 else ""
                return ip, desc
    except Exception:
        pass

    return None, None

def get_carrier_category(isp_desc: str) -> str:
    """归一化运营商分类，消除测速源返回文本词序不一致导致的假漂移"""
    if not isp_desc:
        return "未知"
    d = isp_desc.lower()
    if any(k in d for k in ["电信", "telecom", "ctnet", "chinatelecom"]):
        return "中国电信"
    if any(k in d for k in ["联通", "unicom", "cnc"]):
        return "中国联通"
    if any(k in d for k in ["移动", "cmcc", "chinamobile"]):
        return "中国移动"
    if any(k in d for k in ["cernet", "教育网", "大学", "科技网"]):
        return "教育网CERNET"
    return "其他出口"

def is_target_carrier(isp_desc: str, target_keywords: list) -> bool:
    if not isp_desc:
        return False
    category = get_carrier_category(isp_desc)
    if category == "中国电信":
        return True
    lower_desc = isp_desc.lower()
    return any(k.lower() in lower_desc for k in target_keywords)

def do_verified_logout(portal_ip: str, ep_port: str, account: str, user_mac: str):
    """
    通过 DevTools 验证的真实注销流程：
    1. 调用 eportal mac/unbind 彻底解绑终端 MAC
    2. 调用 drcom/logout 全局注销
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    
    # 1. mac/unbind 接口
    try:
        unbind_url = (
            f"http://{portal_ip}:{ep_port}/eportal/portal/mac/unbind?"
            f"callback=drUnbind&user_account={urllib.parse.quote(account)}"
            f"&wlan_user_mac={user_mac}&wlan_user_ip=181613168&jsVersion=4.2"
        )
        req = urllib.request.Request(unbind_url, headers={"User-Agent": "Mozilla/5.0"})
        with opener.open(req, timeout=3) as resp:
            pass
    except Exception:
        pass

    # 2. drcom/logout
    try:
        req = urllib.request.Request(f"http://{portal_ip}/drcom/logout?callback=drLogout", headers={"User-Agent": "Mozilla/5.0"})
        with opener.open(req, timeout=3) as resp:
            pass
    except Exception:
        pass

def do_verified_login(portal_ip: str, ep_port: str, account: str, password: str, user_ip: str, user_mac: str):
    """
    通过 DevTools 验证的真实 801 Portal 电信认证接口：
    http://10.0.2.2:801/eportal/portal/login
    """
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    
    login_url = (
        f"http://{portal_ip}:{ep_port}/eportal/portal/login?"
        f"callback=drLogin&login_method=1"
        f"&user_account={urllib.parse.quote(account)}"
        f"&user_password={urllib.parse.quote(password)}"
        f"&wlan_user_ip={user_ip}&wlan_user_ipv6="
        f"&wlan_user_mac={user_mac}&wlan_ac_ip=&wlan_ac_name="
        f"&jsVersion=4.2"
    )
    
    try:
        req = urllib.request.Request(login_url, headers={"User-Agent": "Mozilla/5.0"})
        with opener.open(req, timeout=5) as resp:
            res_content = resp.read().decode("gbk", errors="ignore")
            if "result" in res_content:
                return True, res_content.strip()[:60]
            return True, "认证请求已送达"
    except Exception as e:
        return False, str(e)

def main():
    print("=" * 60)
    print("  安徽医科大学 校园网出口运营商监控与自愈守护程序  ")
    print("=" * 60)

    config = load_or_init_config()
    portal_ip = config.get("portal_ip", "10.0.2.2")
    ep_port = config.get("ep_port", "801")
    account = config.get("account", "")
    password = config.get("password")
    user_ip = config.get("user_ip", "10.211.50.112")
    user_mac = config.get("user_mac", "5C60BABB70A7")
    interval = config.get("check_interval_seconds", 20)
    target_keywords = config.get("target_carrier_keywords", ["电信", "telecom"])
    auto_reconnect = config.get("auto_reconnect_enabled", True)

    log_message("监控守护进程已启动...")
    log_message(f"监控参数: 目标通道=电信, 检测周期={interval}秒, 认证账号={account}")

    current_ip = None
    current_isp = None
    state_start_time = time.time()

    while True:
        try:
            ip, isp = get_external_ip_and_isp()
            
            if not ip:
                log_message("【网络告警】无法探测公网出口，可能已断网或正在重新拨号...")
                time.sleep(5)
                continue

            now = time.time()

            # 首次探测
            if current_ip is None:
                current_ip = ip
                current_isp = isp
                state_start_time = now
                is_ct = is_target_carrier(isp, target_keywords)
                status_str = "正常(电信)" if is_ct else "异常(非电信通道!)"
                log_message(f"初始出口状态: [{status_str}] IP={ip}, 归属={isp}")
                
                if not is_ct and auto_reconnect:
                    log_message(">> 检测到当前不是电信通道，正在执行注销并重登拉回电信...")
                    do_verified_logout(portal_ip, ep_port, account, user_mac)
                    time.sleep(3)
                    success, msg = do_verified_login(portal_ip, ep_port, account, password, user_ip, user_mac)
                    log_message(f">> 801 电信重拨请求已发送: {msg}")
                    time.sleep(4)
            
            # 检测是否发生真实通道漂移 (IP改变 或 运营商类别发生突变)
            elif (ip != current_ip) or (get_carrier_category(isp) != get_carrier_category(current_isp)):
                prev_category = get_carrier_category(current_isp)
                curr_category = get_carrier_category(isp)
                duration_min = round((now - state_start_time) / 60.0, 1)

                log_message("=" * 60)
                log_message(f"【出口漂移警报】检测到公网出口发生实质变更！")
                log_message(f"上一状态: IP={current_ip}, 运营商={prev_category} ({current_isp}) [持续约 {duration_min} 分钟]")
                log_message(f"当前状态: IP={ip}, 运营商={curr_category} ({isp})")
                log_message("=" * 60)

                current_ip = ip
                current_isp = isp
                state_start_time = now

                # 判断是否掉出电信
                if not is_target_carrier(isp, target_keywords):
                    log_message(">> 警告：出口已偏离中国电信！")
                    if auto_reconnect:
                        log_message(">> 正在执行注销并重新认证以强制拉回中国电信通道...")
                        do_verified_logout(portal_ip, ep_port, account, user_mac)
                        time.sleep(3)
                        success, msg = do_verified_login(portal_ip, ep_port, account, password, user_ip, user_mac)
                        log_message(f">> 801 电信重登结果: {msg}，等待生效中...")
                        time.sleep(5)
            else:
                # 状态正常维持 (即使测速源文本顺序有微小抖动，也平滑更新，不报警)
                current_isp = isp
                is_ct = is_target_carrier(isp, target_keywords)
                flag = "√" if is_ct else "!"
                stat_desc = "电信正常" if is_ct else "非电信通道(等待切回)"
                sys.stdout.write(f"\r[{datetime.now().strftime('%H:%M:%S')}] [{flag}] IP: {ip} | 运营商: {isp} ({stat_desc})   ")
                sys.stdout.flush()

        except KeyboardInterrupt:
            print("\n用户手动终止监控。")
            break
        except Exception as e:
            log_message(f"探测循环异常: {e}")

        time.sleep(interval)

if __name__ == "__main__":
    main()
