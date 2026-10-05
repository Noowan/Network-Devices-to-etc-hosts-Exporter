"""
Module for connecting to Cisco devices via SSH.

The module does not parse the output — it only connects to the device,
executes a set of show commands, and returns the raw text output.
Parsing the output will be handled by a separate module/function.

The login and password are retrieved from the SSH_USER and SSH_PASSWORD environment variables
(they are already imported into the system and are simply read via os.environ here).

Legacy hardware often supports only legacy SSH algorithms
(diffie-hellman-group1-sha1, ssh-rsa, 3des-cbc, etc.), which modern
paramiko disables by default for security reasons. Therefore, the legacy set
is explicitly enabled below via paramiko.Transport._preferred_* —
this is the only practical way to "negotiate" with old Cisco IOS
without dealing with ssh_config files.
"""

import os
import re
import socket
import time
import paramiko

import main

# Commands to be executed on the device.
# The list can be extended — the module just runs them one by one.
DEFAULT_COMMANDS = [
    "show ip interface brief",
]

# Legacy algorithms that need to be added to connect to old
# hardware. We add them to the beginning of paramiko's preferred algorithms list
# without removing anything — modern devices will continue to work as before.
_LEGACY_KEX = [
    "diffie-hellman-group1-sha1",
    "diffie-hellman-group14-sha1",
    "diffie-hellman-group-exchange-sha1",
]
_LEGACY_CIPHERS = [
    "3des-cbc",
    "aes128-cbc",
    "aes192-cbc",
    "aes256-cbc",
]
_LEGACY_KEYS = [
    "ssh-rsa",
    "ssh-dss",
]


def _patch_legacy_algorithms():
    """Adds legacy algorithms to the preferred list of paramiko.Transport."""
    for algo in _LEGACY_KEX:
        if algo not in paramiko.Transport._preferred_kex:
            paramiko.Transport._preferred_kex += (algo,)
    for algo in _LEGACY_CIPHERS:
        if algo not in paramiko.Transport._preferred_ciphers:
            paramiko.Transport._preferred_ciphers += (algo,)
    for algo in _LEGACY_KEYS:
        if algo not in paramiko.Transport._preferred_keys:
            paramiko.Transport._preferred_keys += (algo,)


_patch_legacy_algorithms()


class CiscoConnectionError(Exception):
    """Error connecting or executing commands on a Cisco device."""


def get_interfaces_and_ips(device, commands=None, timeout=15):
    """
    Connects to the device via SSH, executes show commands,
    and returns their raw output.

    :param device: hostname or IP address of the device.
    :param commands: list of commands, defaults to DEFAULT_COMMANDS.
    :param timeout: connection/read timeout in seconds.
    :return: dict {command: text output}
    """
    commands = commands or DEFAULT_COMMANDS

    user = os.environ.get("SSH_USER")
    password = os.environ.get("SSH_PASSWORD")
    if not user or not password:
        raise CiscoConnectionError(
            "SSH_USER / SSH_PASSWORD are not set in environment variables"
        )

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())

    try:
        client.connect(
            hostname=device.get('address'),
            username=user,
            password=password,
            timeout=timeout,
            look_for_keys=False,
            allow_agent=False,
        )
    except (paramiko.SSHException, socket.error) as exc:
        raise CiscoConnectionError(f"{device}: failed to connect: {exc}")

    try:
        shell = client.invoke_shell()
        shell.settimeout(timeout)

        # disable pagination, otherwise "show" commands
        # will wait for a spacebar press on "--More--"
        _send(shell, "terminal length 0")
        time.sleep(0.2)
        _read_until_prompt(shell, timeout)

        output = {}
        for cmd in commands:
            _send(shell, cmd)
            time.sleep(0.2)
            output[cmd] = _read_until_prompt(shell, timeout)

        return output


    except socket.timeout:
        raise CiscoConnectionError(f"{device}: timeout during command execution")
    finally:
        client.close()


def _send(shell, command):
    shell.send(command + "\n")


def _read_until_prompt(shell, timeout, idle_gap=10):
    """
    Reads output from an interactive SSH session until the device stops
    sending data (idle_gap seconds with no new data) or the timeout expires.

    A simple and reliable approach for Cisco IOS: a precise regex for the prompt
    (hostname#, hostname>) is redundant at this stage — parsing will be handled separately.
    """
    buffer = b""
    deadline = time.monotonic() + timeout
    last_data_at = time.monotonic()

    while time.monotonic() < deadline:
        if shell.recv_ready():
            buffer += shell.recv(65535)
            last_data_at = time.monotonic()
        else:
            if time.monotonic() - last_data_at > idle_gap:
                break
            time.sleep(0.2)

    return buffer.decode(errors="replace")

def parse_raw_output(raw_text: str, hostname: str, domain: str) -> list[str]:
    """
    Parses 'show ip interface brief' output (including cases with no line breaks
    between interfaces) and returns a list of strings like:
    '172.16.50.5 hostname1.gi0-0-0.example.ru'

    ALL interfaces with an assigned IP are considered, regardless of Status/Protocol
    (up, down, administratively down) — including VLAN interfaces (SVI) and Loopback.
    Interfaces with IP-Address == 'unassigned' are skipped.
    """

    # Interface type abbreviations -> as in the example (gi, te, lo, vlan, po...)
    short_names = {
        "GigabitEthernet": "gi",
        "TenGigabitEthernet": "te",
        "FastEthernet": "fa",
        "FortyGigabitEthernet": "fo",
        "HundredGigE": "hu",
        "Loopback": "lo",
        "Vlan": "vl",
        "Port-channel": "po",
        "Tunnel": "tun",
        "Multilink": "mu"
    }

    def to_short_name(ifname: str) -> str:
        for full, short in short_names.items():
            if ifname.startswith(full):
                rest = ifname[len(full):]          # e.g., "0/0/0" or "53"
                rest = rest.replace("/", "-")
                rest = rest.replace(".", "-")
                return f"{short}{rest}"
        # if the interface type is not in the dictionary, just normalize it as is
        ifname = ifname.replace(".", "-")
        return ifname.replace("/", "-").lower()

    # Pattern captures: Interface, IP-Address, OK?, Method, Status (1-3 words), Protocol
    pattern = re.compile(
        r"(\S+)\s+"
        r"(\d{1,3}(?:\.\d{1,3}){3}|unassigned)\s+"
        r"(YES|NO)\s+"
        r"(\S+)\s+"
        r"(administratively down|up|down)\s+"
        r"(up|down)"
    )

    results = []
    for m in pattern.finditer(raw_text.get("show ip interface brief")):
        ifname, ip = m.group(1), m.group(2)
        if ifname.lower() == "interface":   # skip table header
            continue
        if ip == "unassigned":              # ignore without IP
            continue
        if ifname.lower().find("loopback0") != -1:              # remove loopback as well
            continue
        if ifname.lower().find("lo0") != -1:              # remove loopback as well
            continue
        if ifname.lower().find("bdi") != -1:              # remove BDI as well
            continue
        if ifname.lower().find("nvi") != -1:              # remove NVI as well
            continue
        short_if = to_short_name(ifname)
        results.append(f"{ip} {hostname}.{short_if}.{domain}")

    return results
