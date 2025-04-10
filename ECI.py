import os
import re
import sys
import telnetlib
import time
import paramiko
from paramiko.channel import Channel
from dotenv import load_dotenv

load_dotenv('credentials.env')
SSH_USER = os.getenv('SSH_USER')
SSH_PASSWORD = os.getenv('SSH_PASSWORD')

def get_interfaces_and_ips_with_telnet(_device:tuple) -> list:
    def to_bytes(line):
        return f"{line}\n".encode("utf-8")

    print(f"Telnet trying {_device[0]}, {_device[1]}")
    try:
        telnet = telnetlib.Telnet(_device[1])
        telnet.read_until(b"Username")
        telnet.write(to_bytes(SSH_USER))
        telnet.read_until(b"Password")
        telnet.write(to_bytes(SSH_PASSWORD))

        telnet.write(b"en\n")
        time.sleep(1)
        telnet.write(b"terminal length 0\n")
        time.sleep(1)
        telnet.write(b"sh ip int br\n")
        time.sleep(3)
    except Exception as e:
        print(f"{_device[0]}, {_device[1]}. Not connected. Reason {e}")
        return
    try:
        output = telnet.read_very_eager().decode("utf-8")
    except Exception as e:
        print(f"{_device[0]}, {_device[1]}. Not connected. Reason {e}")
        return

    try:
        telnet.close()
    except:
        exit(9)

    splittedOutput = output.split(sep='\r\n')

    #drop all before Interfaces
    searchPattern = 'sh ip int br'
    for i in range(len(splittedOutput)):
        if re.search(searchPattern,splittedOutput[i]):
            for j in range(0, i+1):
                splittedOutput.pop(0)
                j += 1
            break
    #drop last 2 strings with garbage
    splittedOutput.pop()

    #get only interfaces name and ips
    itemsToWorkWith = list()
    searchPatterns = ['Interface', 'inet']
    for i in range(0, len(splittedOutput)):
        for searchPattern in searchPatterns:
            if re.search(searchPattern, splittedOutput[i]):
                itemsToWorkWith.append(splittedOutput[i])

    #ip is absent? Delete interface from list
    itemsToWorkWith2 = []
    for i in range(0, len(itemsToWorkWith)):
        if re.search("inet", itemsToWorkWith[i]):
            itemsToWorkWith2.append(f'{itemsToWorkWith[i-1]} {itemsToWorkWith[i]}')

    #drop not sw interfaces
    itemsToWorkWith3 = []
    searchPatterns = ['sw']
    for i in range(0, len(itemsToWorkWith2)):
        for searchPattern in searchPatterns:
            if re.search(searchPattern, itemsToWorkWith2[i]):
                itemsToWorkWith3.append(itemsToWorkWith2[i])

    #make list of tuples with address, interface values
    interfacesAddressesList = []
    for line in itemsToWorkWith3:
        ifName = re.search("sw\d+", line).group(0)
        ifIpAddress = re.search("\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", line).group(0)
        if ifName == 'sw0':
            continue
        interfacesAddressesList.append((_device[0], ifName, ifIpAddress))
    return interfacesAddressesList


def connect_ssh(IP: str, DeviceName: str="NONAME") -> Channel:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    print(f"trying {DeviceName}, {IP}")
    try:
        client.connect(hostname=IP,
                      username=SSH_USER,
                      password=SSH_PASSWORD,
                      look_for_keys=False,
                      allow_agent=False,
                      timeout=10)
    except Exception as e:
        print(f"{DeviceName},{IP}. Not connected. Reason {e}")
        client.close()
        time.sleep(0.5)
        return
    try:
        shell = client.invoke_shell()
    except Exception as e:
        print(f"{DeviceName},{IP}. Can't invoke shell. Reason {e}")
        client.close()
    print(f"{DeviceName}, {IP}. Connected.")
    return shell


def get_interfaces_and_ips_with_SSH(_device):
    shell = connect_ssh(_device[1],_device[0])
    if shell == None:
        return
    try:
        print("Getting raw data about interfaces and ips....")
        time.sleep(5)
        shell.send("set cli screen-length 1000 \n")
        time.sleep(1)
        shell.send("show interfaces terse \n")
        time.sleep(5)
        output = shell.recv(102400).decode()
        time.sleep(10)
        shell.send("set cli screen-length 34'\n")
    except Exception as e:
        print(e)

    #split output to list of strings
    splittedOutput = output.split(sep='\r\n')

    #drop all before Interfaces
    searchPattern = 'Interface                Admin Link        Family Address'
    for i in range(len(splittedOutput)):
        if re.search(searchPattern,splittedOutput[i]):
            for j in range(0, i+1):
                splittedOutput.pop(0)
                j += 1
            break
    #drop last 1 string with garbage
    splittedOutput.pop()

    #get items from list which has ip addresses
    itemsToWorkWith = []
    for i in range(0, len(splittedOutput)):
        if re.search("\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", splittedOutput[i]):
            itemsToWorkWith.append(splittedOutput[i])

    #delete unused interfaces
    itemsToDelete = list()
    searchPatterns = ['Lower Down', 'lo0']
    for i in range(0, len(itemsToWorkWith)):
        for searchPattern in searchPatterns:
            if re.search(searchPattern, itemsToWorkWith[i]):
                itemsToDelete.append(itemsToWorkWith[i])
    for m in range(0, len(itemsToDelete)):
        try:
            itemsToWorkWith.remove(itemsToDelete[m])
        except:
            continue

    #make list of tuples with address, interface values
    interfacesAddressesList = []
    for line in itemsToWorkWith:
        #ifName = re.search("(GigabitEthernet\d+/\d+/\d+.\d+|GigabitEthernet\d+/\d+/\d+|Vlan\d+|GigabitEthernet\d+/\d+|Gi\d+/\d+/\d+.\d+)", line).group(0)
        ifName = re.search("(\w+-\d/\d.\d|\w+-\w+/\d+.\d)", line).group(0)
        ifIpAddress = re.search("\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", line).group(0)
        ifName = ifName.replace("K", "")
        ifName = ifName.replace("/", "-")
        ifName = ifName.replace(".", "-")
        interfacesAddressesList.append((_device[0], ifName, ifIpAddress))
    return interfacesAddressesList