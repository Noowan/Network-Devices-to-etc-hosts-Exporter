# TO-DO-PLAN
# DONE 1. Parse devices lists
# DONE 2. Sort list by ip
# DONE 2. Generate /etc/hosts with loopbacks
# DONE 3. Drop all APKSH from devices lists
# 4. Connect to all vendors and get all interfaces
# 5. generate /etc/hosts with interfaces of devices
# 6. Rewrite code for multithreading
# 7. Get hosts from zabbix API
# 8. Auto change /etc/hosts on DNS
import Huawei
import Juniper
import Cisco
import ECI

DEVICES_FILENAME = 'hosts.txt'

def read_devices_file_to_list_of_tuples(_filename: str) -> list:
    with open(_filename, "r", encoding="utf-8") as somefile:
        linesfromfile = somefile.read()
        linesfromfile = linesfromfile.replace("\n", "\n\n\n\n\n")
        linesfromfile = linesfromfile.replace(" ", "-")
    lines = linesfromfile.split(sep="\n\n\n\n\n")
    devices_list = []
    for line in lines:
        line_splitted = line.split(sep="\t")
        devices_list.append((line_splitted[0], line_splitted[1], line_splitted[2], line_splitted[3]))
    return devices_list


def sort_devices_by_ip(_devices: list) -> list:
    return sorted(_devices, key=lambda device: tuple(map(int, device[1].split('.'))))


def generate_etc_hosts_for_loopbacks(_devices):
    with open('hosts_loopbacks.txt', "w", encoding="utf-8") as somefile:
        for device in _devices:
            somefile.writelines(
                f"{device[1]} {device[0]}.lo0.soptus.stn.transneft.ru {device[0]}.soptus.stn.transneft.ru\n")
    print("/etc/hosts with loopbacks generated")


def drop_apksh_from_list(_devices: list) -> list:
    for device in _devices:
        if device[2] == "АПКШ":
            # print(device)
            _devices.remove(device)
    return _devices

def get_interfaces_addresses(_device):
    match _device[2]:
        case "ECI":
            pass
        case "Cisco":
            pass
        case "Huawei":
            result = Huawei.get_interfaces_and_ips(_device)
            return result
        case "Juniper":
            pass
        case _:
            print(f'{_device} - UNKNOWN DEVICE')

if __name__ == '__main__':
    devices = read_devices_file_to_list_of_tuples(DEVICES_FILENAME)
    sortedByIpDevices = sort_devices_by_ip(devices)
    generate_etc_hosts_for_loopbacks(sortedByIpDevices)
    filteredDevices = drop_apksh_from_list(sortedByIpDevices)
    interfacesAndAddressesList = []
    for device in filteredDevices:
        result = get_interfaces_addresses(device)
        if result:
            interfacesAndAddressesList.extend(result)
    print()
