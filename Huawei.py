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
        print(f"{DeviceName}, {IP}. Connected.")
        return shell
    except Exception as e:
        print(f"{DeviceName},{IP}. Can't invoke shell. Reason {e}")
        client.close()


def get_interfaces_and_ips(_device):
    shell = connect_ssh(_device[1],_device[0])
    if shell == None:
        return
    try:
        print("Getting raw data about interfaces and ips....")
        shell.send("screen-length 0 temporary\n")
        time.sleep(1)
        shell.send("display ip int br\n")
        time.sleep(5)
        output = shell.recv(102400).decode()
    except Exception as e:
        print(e)

    #split output to list of strings
    splittedOutput = output.split(sep='\r\n')

    #drop all before Interfaces
    searchPattern = 'Interface\s+IP Address/Mask'
    for i in range(len(splittedOutput)):
        if re.search(searchPattern,splittedOutput[i]):
            for j in range(0, i+1):
                splittedOutput.pop(0)
                j += 1
            break
    #drop last 1 string with garbage
    splittedOutput.pop()
    if re.search("", splittedOutput[-1]):
        splittedOutput.pop()

    #drop interfaces with operstatus down and adminstatus down and not interesting interfaces
    itemsToDelete = list()
    searchPatterns = ['\*down', 'Cellular', 'NULL', 'MEth', 'unassigned', 'LoopBack']
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
        ifName = re.search("(GigabitEthernet\d+/\d+/\d+.\d+|GigabitEthernet\d+/\d+/\d+|Vlanif\d+|Eth-Trunk\d|Tunnel\d/\d/\d)", line).group(0)
        ifIpAddress = re.search("\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}", line).group(0)
        ifName = ifName.replace("GigabitEthernet", "gi")
        ifName = ifName.replace("Vlanif", "vl")
        ifName = ifName.replace("/", "-")
        ifName = ifName.replace(".", "-")
        interfacesAddressesList.append((_device[0], ifName, ifIpAddress))
    return interfacesAddressesList