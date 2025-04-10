# TO-DO-PLAN
# DONE 1. Parse devices lists
# DONE 2. Sort list by ip
# DONE 2. Generate /etc/hosts with loopbacks
# DONE 3. Drop all APKSH from devices lists
# DONE 4. Connect to all vendors and get all interfaces
# 5. generate /etc/hosts with interfaces of devices
# 6. Rewrite code for multithreading
# 7. Get hosts from zabbix API
# 8. Auto change /etc/hosts on DNS
import time

import Huawei
import Juniper
import Cisco
import ECI
from threading import Thread

DEVICES_FILENAME = 'hosts.txt'
MAXTHREADS = 500
interfacesAndAddressesList = []


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
    itemsToDelete = []
    for i in range(0, len(_devices)):
        if _devices[i][2] == "АПКШ":
            itemsToDelete.append(_devices[i])
    for m in range(0, len(itemsToDelete)):
        try:
            _devices.remove(itemsToDelete[m])
        except:
            continue
    return _devices

def get_interfaces_addresses(_device):
    match _device[2]:
        case "ECI":
            match _device[3]:
                case "AS9215":
                    return ECI.get_interfaces_and_ips_with_telnet(_device)
                case "SR9604":
                    return ECI.get_interfaces_and_ips_with_SSH(_device)
                case _:
                    print(f'{_device} - UNKNOWN ECI DEVICE')
        case "Cisco":
            return Cisco.get_interfaces_and_ips(_device)
        case "Huawei":
            return Huawei.get_interfaces_and_ips(_device)
        case "Juniper":
            return Juniper.get_interfaces_and_ips(_device)
        case _:
            print(f'{_device} - UNKNOWN DEVICE')

def main_func(_device):
    result = get_interfaces_addresses(_device)
    if result:
        interfacesAndAddressesList.extend(result)

def deleteDuplicateLoopbacks(_rawInfoAboutInterfacesAndAddresses, _deviceslist):
    itemsToDelete = []
    for tuple in _rawInfoAboutInterfacesAndAddresses:
        for i in range(len(_deviceslist)):
            if tuple[2] == _deviceslist[i][1]:
                print(f'dup {tuple} == {_deviceslist[i]}. i = {i}')
                itemsToDelete.append(tuple)
    for i in range(len(itemsToDelete)):
        _rawInfoAboutInterfacesAndAddresses.remove(itemsToDelete[i])
    return _rawInfoAboutInterfacesAndAddresses


if __name__ == '__main__':
    devices = read_devices_file_to_list_of_tuples(DEVICES_FILENAME)
    sortedByIpDevices = sort_devices_by_ip(devices)
    #generate_etc_hosts_for_loopbacks(sortedByIpDevices)
    filteredDevices = drop_apksh_from_list(sortedByIpDevices)
    interfacesAndAddressesList = []

    #for device in filteredDevices:
    #    main_func(device)
    threads = []
    tries = int(len(filteredDevices) / MAXTHREADS)
    leastTries = len(filteredDevices) % MAXTHREADS
    START = 0
    FINISH = MAXTHREADS

    i = 1
    while i <= tries:
        for j in range(START, FINISH):
            threads.append(
                Thread(target=main_func, args=(filteredDevices[j], ), name=f"{filteredDevices[j]}:Thread"))
            print(f"Thread {j} created")
        for thread in threads:
            # print(f"start {thread.name}")
            thread.start()
            time.sleep(0.1)
        for thread in threads:
            thread.join()
        threads.clear()
        START = FINISH
        i = i + 1
        FINISH = FINISH + MAXTHREADS
    i = 1
    if tries == 0:
        j = -1
    while i <= leastTries:
        threads.append(
            Thread(target=main_func, args=(filteredDevices[i+j], ), name=f"{filteredDevices[i+j]}:Thread"))
        print(f"Thread {i + j} created")
        i = i + 1
    for thread in threads:
        # print(f"start {thread.name}")
        thread.start()
    for thread in threads:
        thread.join()
    threads.clear()

    #search duplicates in loopbacks list
    clearedInterfacesAndAddressesList = []
    clearedInterfacesAndAddressesList = deleteDuplicateLoopbacks(interfacesAndAddressesList, filteredDevices)

    #place all gathered data in file
    with open('hosts_all.txt', "w", encoding="utf-8") as somefile:
        for device in filteredDevices:
            somefile.writelines(
                f"{device[1]} {device[0]}.lo0.soptus.stn.transneft.ru {device[0]}.soptus.stn.transneft.ru\n")
        for item in clearedInterfacesAndAddressesList:
            somefile.writelines(f"{item[2]} {item[0]}.{item[1]}.soptus.stn.transneft.ru\n")
    print("hosts_all.txt generated")
