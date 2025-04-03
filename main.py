#TO-DO-PLAN
# 1. Parse devices lists
# 2. Sort list by ip
# 2. Generate /etc/hosts with loopbacks
# 3. Drop all APKSH from devices lists
# 4. Connect to all vendors and get all interfaces
# 5. generate /etc/hosts with interfaces of devices
# 6. Rewrite code for multithreading
# 7. Get hosts from zabbix API

DEVICES_FILENAME = 'hosts.txt'

def read_devices_to_list(filename: str) -> list:
    with open(filename, "r", encoding="utf-8") as somefile:
        linesfromfile = somefile.read()
        linesfromfile = linesfromfile.replace("\n", "\n\n\n\n\n")
    devices = linesfromfile.split(sep="\n\n\n\n\n")
    return devices

if __name__ == '__main__':
    devices = read_devices_to_list(DEVICES_FILENAME)

