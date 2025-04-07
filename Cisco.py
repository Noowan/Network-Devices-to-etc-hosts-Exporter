import os
import re
import time
import paramiko
from paramiko.channel import Channel
from dotenv import load_dotenv

load_dotenv('credentials.env')
SSH_USER = os.getenv('SSH_USER')
SSH_PASSWORD = os.getenv('SSH_PASSWORD')

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


def get_interfaces_and_ips(_device):
    shell = connect_ssh(_device[1],_device[0])
    if shell == None:
        return
    try:
        print("Getting raw data about interfaces and ips....")
        shell.send("terminal length 0\n")
        time.sleep(1)
        shell.send("show ip int br\n")
        time.sleep(5)
        output = shell.recv(102400).decode()
    except Exception as e:
        print(e)

    #split output to list of strings
    splittedOutput = output.split(sep='\r\n')

    #drop all before Interfaces
    searchPattern = 'Interface              IP-Address      OK\? Method Status                Protocol'
    for i in range(len(splittedOutput)):
        if re.search(searchPattern,splittedOutput[i]):
            for j in range(0, i+1):
                splittedOutput.pop(0)
                j += 1
            break
    #drop last 2 strings with garbage
    splittedOutput.pop()

    #drop interfaces with operstatus down and adminstatus down and not interesting interfaces
    itemsToDelete = list()
    searchPatterns = ['administratively down', 'unassigned', 'Loopback', 'Se0/', 'Et1/', 'BDI', 'Ethernet-Internal']
    for i in range(0, len(splittedOutput)):
        for searchPattern in searchPatterns:
            if re.search(searchPattern, splittedOutput[i]):
                itemsToDelete.append(splittedOutput[i])
    for m in range(0, len(itemsToDelete)):
        try:
            splittedOutput.remove(itemsToDelete[m])
        except:
            continue

    #make list of tuples with address, interface values
    interfacesAddressesList = []
    for line in splittedOutput:
        ifName = re.search("(GigabitEthernet\d+/\d+/\d+.\d+|GigabitEthernet\d+/\d+/\d+|Vlan\d+|GigabitEthernet\d+/\d+|Gi\d+/\d+/\d+.\d+)", line).group(0)
        ifIpAddress = re.search("\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", line).group(0)
        ifName = ifName.replace("GigabitEthernet", "gi")
        ifName = ifName.replace("Vlan", "vl")
        interfacesAddressesList.append((_device[0], ifName, ifIpAddress))
    return interfacesAddressesList