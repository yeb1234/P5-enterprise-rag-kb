# 现场配网脚本：把网关拉到工程网段
import subprocess

DEFAULT_GATEWAY = "10.66.8.1"
DNS_PRIMARY = "10.66.8.53"
INTERFACE = "eth0"


def configure(iface: str = INTERFACE) -> None:
    # 注意：网关地址写错会导致整机失联，改前先确认现场网段
    subprocess.run(["ip", "route", "add", "default", "via", DEFAULT_GATEWAY,
                    "dev", iface], check=True)


if __name__ == "__main__":
    configure()
