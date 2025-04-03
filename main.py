#TO-DO-PLAN
# 1. Parse devices lists done!
# 2. Sort list by ip done!
# 2. Generate /etc/hosts with loopbacks
# 3. Drop all APKSH from devices lists
# 4. Connect to all vendors and get all interfaces
# 5. generate /etc/hosts with interfaces of devices
# 6. Rewrite code for multithreading
# 7. Get hosts from zabbix API

DEVICES_FILENAME = 'hosts.txt'

def read_devices_file_to_list_of_tuples(_filename: str) -> list:
    with open(_filename, "r", encoding="utf-8") as somefile:
        linesfromfile = somefile.read()
        linesfromfile = linesfromfile.replace("\n", "\n\n\n\n\n")
    lines = linesfromfile.split(sep="\n\n\n\n\n")
    devices = []
    for str in lines:
        strsplitted = str.split(sep="\t")
        devices.append((strsplitted[0], strsplitted[1], strsplitted[2], strsplitted[3]))
    return devices
def sort_devices_by_ip(_devices: list) -> list:
    return sorted(_devices, key=lambda device: tuple(map(int, device[1].split('.'))))

def generate_etc_hosts_for_loopbacks(_devices):




if __name__ == '__main__':
    devices = read_devices_file_to_list_of_tuples(DEVICES_FILENAME)
    sortedByIpDevices = sort_devices_by_ip(devices)



    print()

