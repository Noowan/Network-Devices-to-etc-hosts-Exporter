"""
Module for connecting to ECI-Telecom devices via SSH or Telnet.

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
import telnetlib
import paramiko

import main

# Commands to be executed on the device.
# The list can be extended — the module just runs them one by one.
DEFAULT_COMMANDS = [
    "show interfaces terse",
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


class ECIConnectionError(Exception):
    """Error connecting or executing commands on a Cisco device."""


def ssh_get_interfaces_and_ips(device, commands=None, timeout=15):
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
        raise ECIConnectionError(
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
        raise ECIConnectionError(f"{device}: failed to connect: {exc}")

    try:
        shell = client.invoke_shell()
        shell.settimeout(timeout)

        # disable pagination, otherwise "show" commands
        # will wait for a spacebar press on "--More--"
        _send(shell, "set cli screen-length 1000")
        time.sleep(0.3)
        _read_until_prompt(shell, timeout)
        output = {}
        for cmd in commands:
            _send(shell, cmd)
            time.sleep(0.3)
            output[cmd] = _read_until_prompt(shell, timeout)

        _send(shell, "set cli screen-length 34")
        time.sleep(0.3)
        _read_until_prompt(shell, timeout)

        return output

    except socket.timeout:
        raise ECIConnectionError(f"{device}: timeout during command execution")
    finally:
        client.close()

def _send(shell, command):
    shell.send(command + "\n")


def _read_until_prompt(shell, timeout, idle_gap=1.5):
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
            time.sleep(0.1)

    return buffer.decode(errors="replace")


def telnet_get_interfaces_and_ips(
    device,
    commands=None,
    timeout=15,
    idle_gap=1.5,
):
    """
    Connects to the device via Telnet, executes commands, and returns
    their raw text output.

    Credentials are taken from environment variables:
        SSH_USER
        SSH_PASSWORD

    Expected device structure:
        {
            "address": "192.168.1.1",
            "port": 23,              # optional
        }

    :param device: dictionary with device parameters.
    :param commands: list of commands. Defaults to:
                     ["show ip interface brief"].
    :param timeout: total connection and data waiting timeout, sec.
    :param idle_gap: how long to wait for new data after the last chunk, sec.
    :return: dict {command: raw text output}
    """

    if commands is None:
        commands = ["show ip interface brief"]

    user = os.environ.get("SSH_USER")
    password = os.environ.get("SSH_PASSWORD")

    if not user or not password:
        raise ECIConnectionError(
            "SSH_USER / SSH_PASSWORD are not set in environment variables"
        )

    if not isinstance(device, dict):
        raise ECIConnectionError(
            f"Invalid device description: expected dict, got "
            f"{type(device).__name__}"
        )

    address = device.get("address")
    port = device.get("telnet_port", device.get("port", 23))

    if not address:
        raise ECIConnectionError(
            f"{address}: invalid Telnet port: {port}"
        )

    try:
        port = int(port)
    except (TypeError, ValueError) as exc:
        raise ECIConnectionError(
            f"{address}: invalid Telnet port: {port}"
        ) from exc

    def to_bytes(line):
        return f"{line}\n".encode("utf-8")

    def send_command(connection, command):
        connection.write(to_bytes(command))

    def read_until_idle(connection):
        """
        Reads data until the device stops sending it
        within idle_gap seconds or until the timeout expires.
        """
        buffer = bytearray()
        deadline = time.monotonic() + timeout
        last_data_at = time.monotonic()

        while time.monotonic() < deadline:
            try:
                chunk = connection.read_very_eager()
            except EOFError:
                break

            if chunk:
                buffer.extend(chunk)
                last_data_at = time.monotonic()
            else:
                if time.monotonic() - last_data_at >= idle_gap:
                    break

                time.sleep(0.1)

        return buffer.decode("utf-8", errors="replace")

    def wait_for_prompt(connection, patterns, prompt_name):
        """
        Waits for one of the specified Telnet prompts.
        """
        compiled_patterns = [
            re.compile(pattern, re.IGNORECASE)
            for pattern in patterns
        ]

        index, match, received = connection.expect(
            compiled_patterns,
            timeout=timeout,
        )

        if index == -1:
            text = received.decode("utf-8", errors="replace")
            raise ECIConnectionError(
                f"{address}: {prompt_name} prompt was not received. "
                f"Device response: {text!r}"
            )

        return received.decode("utf-8", errors="replace")

    telnet = None

    try:
        telnet = telnetlib.Telnet(
            host=address,
            port=port,
            timeout=timeout,
        )

        wait_for_prompt(
            telnet,
            patterns=[
                rb"username\s*:?\s*$",
                rb"login\s*:?\s*$",
                rb"user\s*:?\s*$",
            ],
            prompt_name="username",
        )
        send_command(telnet, user)

        wait_for_prompt(
            telnet,
            patterns=[
                rb"password\s*:?\s*$",
            ],
            prompt_name="password",
        )
        send_command(telnet, password)

        login_output = read_until_idle(telnet)

        if re.search(
            r"(login\s+invalid|authentication\s+failed|"
            r"access\s+denied|incorrect\s+password)",
            login_output,
            re.IGNORECASE,
        ):
            raise ECIConnectionError(
                f"{address}: device rejected the username or password"
            )

        # Switch to privileged mode.
        send_command(telnet, "enable")
        enable_output = read_until_idle(telnet)

        # Some devices prompt for the password again after the enable command.
        if re.search(
            r"password\s*:?\s*$",
            enable_output,
            re.IGNORECASE,
        ):
            send_command(telnet, password)
            enable_output += read_until_idle(telnet)

            if re.search(
                r"(authentication\s+failed|access\s+denied|"
                r"incorrect\s+password)",
                enable_output,
                re.IGNORECASE,
            ):
                raise ECIConnectionError(
                    f"{address}: failed to switch to privileged mode"
                )

        # Disable pagination.
        send_command(telnet, "terminal length 0")
        read_until_idle(telnet)

        output = {}

        for command in commands:
            send_command(telnet, command)
            output[command] = read_until_idle(telnet)

        return output

    except ECIConnectionError:
        raise

    except (EOFError, OSError, socket.timeout) as exc:
        raise ECIConnectionError(
            f"{address}: Telnet connection or command execution error: {exc}"
        ) from exc

    except Exception as exc:
        raise ECIConnectionError(
            f"{address}: unexpected error when operating via Telnet: {exc}"
        ) from exc

    finally:
        if telnet is not None:
            try:
                telnet.close()
            except Exception:
                pass


def parse_raw_output_9604(raw_text: str, hostname: str, domain: str) -> list[str]:
    """
    Parses 'show ip interface brief' output (including cases with no line breaks
    between interfaces) and returns a list of strings like:
    '172.16.50.5 hostname1.gi0-0-0.example.ru'

    ALL interfaces with an assigned IP are considered, regardless of Status/Protocol
    (up, down, administratively down) — including VLAN interfaces (SVI) and Loopback.
    Interfaces with IP-Address == 'unassigned' are skipped.
    """

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
    }

    def to_short_name(ifname: str) -> str:
        for full, short in short_names.items():
            if ifname.startswith(full):
                rest = ifname[len(full):]
                rest = rest.replace("/", "-")
                return f"{short}{rest}"
        ifname = ifname.replace(".","-")
        return ifname.replace("/", "-").lower()

    pattern = re.compile(
        r"^(?:\x1b|[^a-zA-Z0-9])*\[K"         # Consumes ESC sequences like \x1b[K or [K
        r"(\S+)\s+"                           # Group 1: Interface name
        r"(?:Up|Down)\s+"                     # Admin status
        r"(?:Up|Down|Lower\s+Down)\s+"        # Link status (considering the space in Lower Down)
        r"inet\s+"                            # inet family
        r"(\d{1,3}(?:\.\d{1,3}){3}\/\d{1,2})",# Group 2: IP address with mask
        re.MULTILINE | re.IGNORECASE
    )

    results = []
    for m in pattern.finditer(raw_text.get("show interfaces terse")):
        ifname, ip = m.group(1), m.group(2)

        # Filter out loopback (important: in Juniper they are named 'lo0', not 'loopback')
        if "lo0" in ifname.lower():
            continue
        if "lo0.0" in ifname.lower():
            continue
        short_if = to_short_name(ifname)
        ip_clean = ip.split('/')[0]
        results.append(f"{ip_clean} {hostname}.{short_if}.{domain}")

    return results


def parse_raw_output_9215(raw_text: str, hostname: str, domain: str) -> list[str]:

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
    }

    def to_short_name(ifname: str) -> str:
        for full, short in short_names.items():
            if ifname.startswith(full):
                rest = ifname[len(full):]
                rest = rest.replace("/", "-")
                return f"{short}{rest}"
        return ifname.replace("/", "-").lower()

    pattern = re.compile(
        r"^Interface\s+(?P<name>\S+)\s*\n"
        r"(?:.*\n)*?"
        r"(?:\s+Flags\s*:\s*<(?P<status>[^>]+)>|.*(?P<inactive>Inactive))"
        r"(?:\s*\n\s+inet\s+(?P<ip>\d{1,3}(?:\.\d{1,3}){3}/\d+))?",
        re.MULTILINE
    )

    results = []
    for m in pattern.finditer(raw_text.get("show ip interface brief")):
        ifname, netaddr = m.group(1), m.group(4)

        if "lo" in ifname.lower():
            continue
        if "outband" in ifname.lower():
            continue
        if netaddr is None:
            continue
        if "unassigned" in netaddr.lower():
            continue
        short_if = to_short_name(ifname)
        ip_clean = netaddr.split('/')[0]
        results.append(f"{ip_clean} {hostname}.{short_if}.{domain}")

    return results