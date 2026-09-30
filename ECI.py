"""
Модуль подключения к Huawei-устройствам по SSH.

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
import telnetlib
import paramiko

import main

# Команды, которые нужно выполнить на устройстве.
# Список можно расширять — модуль просто прогоняет их по очереди.
DEFAULT_COMMANDS = [
    "show interfaces terse",
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


def ssh_get_interfaces_and_ips(device, commands=None, timeout=15):
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
        raise CiscoConnectionError(f"{device}: таймаут при выполнении команд")
    finally:
        client.close()

def _send(shell, command):
    shell.send(command + "\n")


def _read_until_prompt(shell, timeout, idle_gap=1.5):
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


def telnet_get_interfaces_and_ips(
    device,
    commands=None,
    timeout=15,
    idle_gap=1.5,
):
    """
    Подключается к устройству по Telnet, выполняет команды и возвращает
    их сырой текстовый вывод.

    Учётные данные берутся из переменных окружения:
        SSH_USER
        SSH_PASSWORD

    Ожидаемая структура device:
        {
            "address": "192.168.1.1",
            "port": 23,              # необязательно
        }

    :param device: словарь с параметрами устройства.
    :param commands: список команд. По умолчанию:
                     ["show ip interface brief"].
    :param timeout: общий таймаут подключения и ожидания данных, сек.
    :param idle_gap: сколько ждать новых данных после последней порции, сек.
    :return: dict {команда: сырой текстовый вывод}
    """

    if commands is None:
        commands = ["show ip interface brief"]

    user = os.environ.get("SSH_USER")
    password = os.environ.get("SSH_PASSWORD")

    if not user or not password:
        raise CiscoConnectionError(
            "SSH_USER / SSH_PASSWORD не заданы в переменных окружения"
        )

    if not isinstance(device, dict):
        raise CiscoConnectionError(
            f"Некорректное описание устройства: ожидался dict, получен "
            f"{type(device).__name__}"
        )

    address = device.get("address")
    port = device.get("telnet_port", device.get("port", 23))

    if not address:
        raise CiscoConnectionError(
            f"{device}: не указан адрес устройства в поле 'address'"
        )

    try:
        port = int(port)
    except (TypeError, ValueError) as exc:
        raise CiscoConnectionError(
            f"{address}: некорректный Telnet-порт: {port}"
        ) from exc

    def to_bytes(line):
        return f"{line}\n".encode("utf-8")

    def send_command(connection, command):
        connection.write(to_bytes(command))

    def read_until_idle(connection):
        """
        Читает данные, пока устройство не перестанет их присылать
        в течение idle_gap секунд либо пока не закончится timeout.
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
        Ожидает один из указанных Telnet-промптов.
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
            raise CiscoConnectionError(
                f"{address}: не получен запрос {prompt_name}. "
                f"Ответ устройства: {text!r}"
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
            prompt_name="имени пользователя",
        )
        send_command(telnet, user)

        wait_for_prompt(
            telnet,
            patterns=[
                rb"password\s*:?\s*$",
            ],
            prompt_name="пароля",
        )
        send_command(telnet, password)

        login_output = read_until_idle(telnet)

        if re.search(
            r"(login\s+invalid|authentication\s+failed|"
            r"access\s+denied|incorrect\s+password)",
            login_output,
            re.IGNORECASE,
        ):
            raise CiscoConnectionError(
                f"{address}: устройство отклонило имя пользователя или пароль"
            )

        # Переход в привилегированный режим.
        send_command(telnet, "enable")
        enable_output = read_until_idle(telnet)

        # Некоторые устройства после команды enable повторно запрашивают пароль.
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
                raise CiscoConnectionError(
                    f"{address}: не удалось перейти в привилегированный режим"
                )

        # Отключаем постраничный вывод.
        send_command(telnet, "terminal length 0")
        read_until_idle(telnet)

        output = {}

        for command in commands:
            send_command(telnet, command)
            output[command] = read_until_idle(telnet)

        return output

    except CiscoConnectionError:
        raise

    except (EOFError, OSError, socket.timeout) as exc:
        raise CiscoConnectionError(
            f"{address}: ошибка Telnet-подключения или выполнения команд: {exc}"
        ) from exc

    except Exception as exc:
        raise CiscoConnectionError(
            f"{address}: непредвиденная ошибка при работе через Telnet: {exc}"
        ) from exc

    finally:
        if telnet is not None:
            try:
                telnet.close()
            except Exception:
                pass


def parse_raw_output_9604(raw_text: str, hostname: str, domain: str) -> list[str]:
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
    }

    def to_short_name(ifname: str) -> str:
        for full, short in short_names.items():
            if ifname.startswith(full):
                rest = ifname[len(full):]          # например "0/0/0" или "53"
                rest = rest.replace("/", "-")
                return f"{short}{rest}"
        # если тип интерфейса не в словаре — просто нормализуем как есть
        ifname = ifname.replace(".","-")
        return ifname.replace("/", "-").lower()

    pattern = re.compile(
        r"^(?:\x1b|[^a-zA-Z0-9])*\[K"         # Сжирает ESC-последовательности вроде \x1b[K или  [K
        r"(\S+)\s+"                           # Группа 1: Имя интерфейса
        r"(?:Up|Down)\s+"                     # Админ-статус
        r"(?:Up|Down|Lower\s+Down)\s+"        # Линк-статус (учитываем пробел в Lower Down)
        r"inet\s+"                            # Семейство inet
        r"(\d{1,3}(?:\.\d{1,3}){3}\/\d{1,2})",# Группа 2: IP-адрес с маской
        re.MULTILINE | re.IGNORECASE
    )

    results = []
    for m in pattern.finditer(raw_text.get("show interfaces terse")):
        ifname, ip = m.group(1), m.group(2)

        # Фильтруем loopback (важно: в Juniper они называются 'lo0', а не 'loopback')
        if "lo0" in ifname.lower():
            continue
        if "lo0.0" in ifname.lower():
            continue
        short_if = to_short_name(ifname)
        ip_clean = ip.split('/')[0]
        results.append(f"{ip_clean} {hostname}.{short_if}.{domain}")

    return results


def parse_raw_output_9215(raw_text: str, hostname: str, domain: str) -> list[str]:
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
    }

    def to_short_name(ifname: str) -> str:
        for full, short in short_names.items():
            if ifname.startswith(full):
                rest = ifname[len(full):]          # например "0/0/0" или "53"
                rest = rest.replace("/", "-")
                return f"{short}{rest}"
        # если тип интерфейса не в словаре — просто нормализуем как есть
        return ifname.replace("/", "-").lower()

    # Паттерн ищет блок "Interface название" и собирает его параметры до следующего блока
    pattern = re.compile(
        r"^Interface\s+(?P<name>\S+)\s*\n"  # Имя интерфейса
        r"(?:.*\n)*?"  # Пропуск строк (например, Description)
        r"(?:\s+Flags\s*:\s*<(?P<status>[^>]+)>|.*(?P<inactive>Inactive))"  # Статус: флаги или Inactive
        r"(?:\s*\n\s+inet\s+(?P<ip>\d{1,3}(?:\.\d{1,3}){3}/\d+))?",  # IP-адрес с маской (если есть)
        re.MULTILINE
    )

    results = []
    for m in pattern.finditer(raw_text.get("show ip interface brief")):
        ifname, netaddr = m.group(1), m.group(4)

        # Фильтруем loopback (важно: в Juniper они называются 'lo0', а не 'loopback')
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