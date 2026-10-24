# -*- coding: utf-8 -*-
"""
安徽医科大学 (AHMU) 校园网自动注销与重登测试脚本 (基于 Chrome DevTools 提取的真实前端对象逻辑)
"""

import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime

if sys.platform.startswith('win'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
        sys.stderr.reconfigure(encoding='utf-8')
    except Exception:
        pass

PORTAL_IP = "10.0.2.2"
EP_PORT = "801"
ACCOUNTS = ["YOUR_STUDENT_ID@telecom"]   # 示例账号格式
PASSWORD = "YOUR_PASSWORD"
USER_IP = "10.x.x.x"
USER_MAC = "000000000000"         # 网卡 MAC

JS_VERSION = "4.2"

def get_current_isp():
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request("http://cip.cc", headers={"User-Agent": "curl/7.68.0"})
        with opener.open(req, timeout=4) as resp:
            lines = resp.read().decode('utf-8', errors='ignore').splitlines()
            ip, isp = "", ""
            for l in lines:
                if l.startswith("IP"):
                    ip = l.split(":", 1)[-1].strip()
                elif l.startswith("数据二") or l.startswith("运营商"):
                    isp = l.split(":", 1)[-1].strip()
            return ip, isp
    except Exception:
        return "未知", "探测超时/已断网"

def try_logout():
    print("\n[步骤 1] 正在请求网关注销下线...")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    for acc in ACCOUNTS:
        try:
            unbind_url = (
                f"http://{PORTAL_IP}:{EP_PORT}/eportal/portal/mac/unbind?"
                f"callback=drLogout1&user_account={urllib.parse.quote(acc)}"
                f"&wlan_user_mac={USER_MAC}&wlan_user_ip=181613168&jsVersion={JS_VERSION}"
            )
            req = urllib.request.Request(unbind_url, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(req, timeout=3) as resp:
                res_text = resp.read().decode('gbk', errors='ignore')
                print(f" -> 解绑注销 ({acc}): {res_text.strip()[:70]}")
        except Exception as e:
            print(f" -> 解绑注销出错: {e}")

    try:
        req = urllib.request.Request(f"http://{PORTAL_IP}/drcom/logout?callback=drLogout2", headers={"User-Agent": "Mozilla/5.0"})
        with opener.open(req, timeout=3) as resp:
            res_text = resp.read().decode('gbk', errors='ignore')
            print(f" -> 全局注销 (drcom/logout): {res_text.strip()[:70]}")
    except Exception as e:
        print(f" -> 全局注销出错: {e}")

def try_login():
    print(f"\n[步骤 2] 正在向网关发起电信通道重新登录 (优先测试 @dx)...")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    for acc in ACCOUNTS:
        print(f" >> 尝试账号格式: {acc} ...")
        # 1. 801 Portal 接口
        login_url = (
            f"http://{PORTAL_IP}:{EP_PORT}/eportal/portal/login?"
            f"callback=drLogin&login_method=1"
            f"&user_account={urllib.parse.quote(acc)}"
            f"&user_password={urllib.parse.quote(PASSWORD)}"
            f"&wlan_user_ip={USER_IP}&wlan_user_ipv6="
            f"&wlan_user_mac={USER_MAC}&wlan_ac_ip=&wlan_ac_name="
            f"&jsVersion={JS_VERSION}"
        )
        try:
            req = urllib.request.Request(login_url, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(req, timeout=4) as resp:
                res = resp.read().decode('gbk', errors='ignore')
                print(f"    801 Portal 响应: {res.strip()[:80]}")
        except Exception as e:
            print(f"    801 请求异常: {e}")

        # 2. drcom/login 接口 (备用)
        dr_url = (
            f"http://{PORTAL_IP}/drcom/login?callback=drLogin2&DDDDD={urllib.parse.quote(acc)}"
            f"&upass={urllib.parse.quote(PASSWORD)}&0MKKey=123456&R1=&R2=0&R3=&R6=0&para=00"
            f"&v6ip=&terminal_type=1&lang=zh-cn"
        )
        try:
            req = urllib.request.Request(dr_url, headers={"User-Agent": "Mozilla/5.0"})
            with opener.open(req, timeout=4) as resp:
                res = resp.read().decode('gbk', errors='ignore')
                print(f"    /drcom/login 响应: {res.strip()[:80]}")
        except Exception as e:
            print(f"    /drcom/login 异常: {e}")

def main():
    print("=" * 60)
    print("   安徽医科大学 校园网【电信通道自动注销与重登】测试程序   ")
    print("=" * 60)

    ip_before, isp_before = get_current_isp()
    print(f"【当前公网出口】IP: {ip_before} | 运营商: {isp_before}")

    input("\n请确认：按【回车键 (Enter)】开始执行电信重登测试（如需取消请按 Ctrl+C）...")

    try_logout()
    print("\n等待 3 秒释放旧网络会话...")
    time.sleep(3)

    try_login()

    print("\n[步骤 3] 等待 6 秒检测出口切换结果...")
    for i in range(6, 0, -1):
        print(f"倒计时 {i} 秒...", end="\r")
        time.sleep(1)
    print(" " * 20)

    ip_after, isp_after = get_current_isp()
    print("=" * 60)
    print("【测试结果对比】")
    print(f" 重登前出口: IP={ip_before} | 归属={isp_before}")
    print(f" 重登后出口: IP={ip_after} | 归属={isp_after}")
    if "电信" in isp_after:
        print(" >> 成功！已正确拉回到【中国电信】通道！")
    else:
        print(" >> 当前状态: 未变为电信，请查看上方接口返回的响应信息。")
    print("=" * 60)

if __name__ == "__main__":
    main()
