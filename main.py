# TO-DO-PLAN
# DONE 1. Parse devices lists
# DONE 2. Sort list by ip
# DONE 2. Generate /etc/hosts with loopbacks
# DONE 3. Drop all APKSH from devices lists
# DONE 4. Connect to all vendors and get all interfaces
# DONE 5. generate /etc/hosts with interfaces of devices
# DONE 6. Rewrite code for multithreading
# DONE 7. Get hosts from zabbix API
# 8. Auto change /etc/hosts on DNS
import os
import time
import requests
from dotenv import load_dotenv
import Huawei
import Juniper
import Cisco
import ECI
from threading import Thread

DEVICES_FILENAME = 'hosts.env'
MAXTHREADS = 1500
interfacesAndAddressesList = []

HEADERS={"Content-Type": 'application/json-rpc'}
load_dotenv('params.env')
API_TOKEN = os.getenv('API_TOKEN')
URL = os.getenv('URL')



def get_hosts_from_zabbix_api() -> list:

    def get_hostgroups():
        payload = {
            "jsonrpc": "2.0",
            "method": "hostgroup.get",
            "params": {
                "output": ["groupid", "name"],
            },
            "auth": API_TOKEN,
            "id": 1
        }

        response = requests.post(URL, json=payload, headers=HEADERS)
        response.raise_for_status()
        response = response.json()
        return response['result']

    def get_hosts(_groupids):
        payload = {
            "jsonrpc": "2.0",
            "method": "host.get",
            "params": {
                "output": ["hostid", "host", "name", "status", "monitored_by"],
                "groupids": _groupids,
                "selectInterfaces": ["ip"],
                "selectTags": ["tag", "value"]
            },
            "auth": API_TOKEN,
            "id": 1
        }

        response = requests.post(URL, json=payload, headers=HEADERS)
        response.raise_for_status()
        response = response.json()
        return response['result']


    hostgroups = get_hostgroups()
    hosts = []
    for hostgroup in hostgroups:
        items = get_hosts(hostgroup['groupid'])
        for item in items:
            if item["monitored_by"] == 1:
                continue
            if item["status"] == 1:
                continue
            hosts.append(item)
    return hosts
    print()

def convert_devices_from_api_to_list_of_tuples(_devices_from_api: list) -> list:
    devices_list = []
    for line in _devices_from_api:
        if len(line["tags"]) == 0:
            devices_list.append((line["host"], line["interfaces"][0]["ip"],
                                 "NO", "NO"))
        else:
            devices_list.append((line["host"], line["interfaces"][0]["ip"], line["tags"][0]["tag"], line["tags"][0]["value"]))
    return devices_list


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


def drop_wrong_devices_from_list(_devices: list) -> list:
    itemsToDelete = []
    for i in range(0, len(_devices)):
        if _devices[i][2] == "АПКШ":
            itemsToDelete.append(_devices[i])
        if _devices[i][2] == "Сторона":
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
        try:
            _rawInfoAboutInterfacesAndAddresses.remove(itemsToDelete[i])
        except ValueError:
            continue
    return _rawInfoAboutInterfacesAndAddresses


if __name__ == '__main__':
    #FOR TXT file
    #devices = read_devices_file_to_list_of_tuples(DEVICES_FILENAME)
    #--------------
    #FOR API REQUEST
    devices_raw = get_hosts_from_zabbix_api()
    devices = convert_devices_from_api_to_list_of_tuples(devices_raw)
    #------------
    sortedByIpDevices = sort_devices_by_ip(devices)
    #generate_etc_hosts_for_loopbacks(sortedByIpDevices)
    filteredDevices = drop_wrong_devices_from_list(sortedByIpDevices)
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
    clearedInterfacesAndAddressesList = deleteDuplicateLoopbacks(interfacesAndAddressesList, filteredDevices)

    #place all gathered data in file
    with open('hosts_all.txt', "w", encoding="utf-8") as somefile:
        for device in filteredDevices:
            somefile.writelines(
                f"{device[1]} {device[0]}.lo0.soptus.stn.transneft.ru {device[0]}.soptus.stn.transneft.ru\n")
        for item in clearedInterfacesAndAddressesList:
            somefile.writelines(f"{item[2]} {item[0]}.{item[1]}.soptus.stn.transneft.ru\n")
    print("hosts_all.txt generated")
