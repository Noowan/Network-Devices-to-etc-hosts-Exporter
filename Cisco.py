"""
Модуль подключения к Cisco-устройствам по SSH.

Точка входа: Cisco.get_interfaces_and_ips(device)

Модуль не занимается парсингом вывода — только подключается к устройству,
выполняет набор show-команд и возвращает "сырой" текстовый вывод.
Разбор (парсинг) вывода будет отдельным модулем/функцией.

Логин и пароль берутся из переменных окружения SSH_USER и SSH_PASSWORD
(они уже импортированы в систему, здесь просто читаются через os.environ).

Старое железо часто поддерживает только legacy-алгоритмы SSH
(diffie-hellman-group1-sha1, ssh-rsa, 3des-cbc и т.п.), которые современный
paramiko по умолчанию отключил из соображений безопасности. Поэтому ниже
явно включается legacy-набор через paramiko.Transport._preferred_* —
это единственный практичный способ "договориться" со старым Cisco IOS
без танцев с конфигами ssh_config.
"""

import os
import re
import socket
import time
import paramiko

import main

# Команды, которые нужно выполнить на устройстве.
# Список можно расширять — модуль просто прогоняет их по очереди.
DEFAULT_COMMANDS = [
    "show ip interface brief",
]

# Legacy-алгоритмы, которые нужно добавить, чтобы подключаться к старому
# железу. Добавляем их в начало списка предпочитаемых алгоритмов paramiko,
# ничего не убирая — современные устройства продолжат работать как раньше.
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
    """Добавляет legacy-алгоритмы в список предпочитаемых у paramiko.Transport."""
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
    """Ошибка подключения или выполнения команд на устройстве Cisco."""


def get_interfaces_and_ips(device, commands=None, timeout=15):
    """
    Подключается к устройству по SSH, выполняет show-команды
    и возвращает их сырой вывод.

    :param device: hostname или IP-адрес устройства.
    :param commands: список команд, по умолчанию DEFAULT_COMMANDS.
    :param timeout: таймаут подключения/чтения, сек.
    :return: dict {команда: текстовый вывод}
    """
    commands = commands or DEFAULT_COMMANDS

    user = os.environ.get("SSH_USER")
    password = os.environ.get("SSH_PASSWORD")
    if not user or not password:
        raise CiscoConnectionError(
            "SSH_USER / SSH_PASSWORD не заданы в переменных окружения"
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
        raise CiscoConnectionError(f"{device}: не удалось подключиться: {exc}")

    try:
        shell = client.invoke_shell()
        shell.settimeout(timeout)

        # отключаем постраничный вывод, иначе "show" команды
        # будут ждать нажатия пробела на "--More--"
        _send(shell, "terminal length 0")
        _read_until_prompt(shell, timeout)

        output = {}
        for cmd in commands:
            _send(shell, cmd)
            output[cmd] = _read_until_prompt(shell, timeout)

        return output


    except socket.timeout:
        raise CiscoConnectionError(f"{device}: таймаут при выполнении команд")
    finally:
        client.close()


def _send(shell, command):
    shell.send(command + "\n")


def _read_until_prompt(shell, timeout, idle_gap=0.5):
    """
    Читает вывод из интерактивной SSH-сессии, пока устройство не перестанет
    что-то присылать (idle_gap секунд без новых данных) или не истечёт timeout.

    Простой и надёжный подход для Cisco IOS: точный regex под приглашение
    (hostname#, hostname>) избыточен на этом этапе — парсингом займёмся отдельно.
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

def parse_raw_output(raw_text: str, hostname: str, domain: str) -> list[str]:
    """
    Разбирает вывод 'show ip interface brief' (в т.ч. без переносов строк
    между интерфейсами) и возвращает список строк вида:
    '172.16.50.5 SO-SGP-PKU0-SW-TP-1.gi0-0-0.soptus.stn.transneft.ru'

    Учитываются ВСЕ интерфейсы с назначенным IP, независимо от Status/Protocol
    (up, down, administratively down) — включая VLAN-интерфейсы (SVI) и Loopback.
    Интерфейсы с IP-Address == 'unassigned' пропускаются.
    """

    # Сокращения для типов интерфейсов -> как в примере (gi, te, lo, vlan, po...)
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
                rest = ifname[len(full):]          # например "0/0/0" или "53"
                rest = rest.replace("/", "-")
                rest = rest.replace(".", "-")
                return f"{short}{rest}"
        # если тип интерфейса не в словаре — просто нормализуем как есть
        ifname = ifname.replace(".", "-")
        return ifname.replace("/", "-").lower()

    # Паттерн ловит: Interface, IP-Address, OK?, Method, Status(1-3 слова), Protocol
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
        if ifname.lower() == "interface":   # пропускаем заголовок таблицы
            continue
        if ip == "unassigned":              # без IP не учитываем
            continue
        if ifname.lower().find("loopback0") != -1:              # loopback тоже убираем
            continue
        if ifname.lower().find("lo0") != -1:              # loopback тоже убираем
            continue
        if ifname.lower().find("bdi") != -1:              # loopback тоже убираем
            continue
        if ifname.lower().find("nvi") != -1:              # loopback тоже убираем
            continue
        short_if = to_short_name(ifname)
        results.append(f"{ip} {hostname}.{short_if}.{domain}")

    return results