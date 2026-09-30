#!/usr/bin/env python3

import argparse
import fcntl
import stat
import os
import shutil
import sys
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import requests
from zabbix_api import ZabbixAPI
import ipaddress
import Cisco
import Huawei
import Juniper
import ECI


EXCLUDED_TAGS = 'АПКШ', 'Сторона','ШБД', 'Ubuntu','HP','ШРД','Debian'
DISCOVERY_MARKER = "##########SCRIPT DISCOVERY##########"

def stderr(message):
    print(message, file=sys.stderr)


class ExportError(Exception):
    pass


def fetch_hosts(api):
    return api.call(
        "host.get",
        {
            "output": [
                "hostid",
                "host",
                "name",
                "status",
                "monitored_by",
            ],
            "selectInterfaces": [
                "interfaceid",
                "type",
                "main",
                "useip",
                "ip",
                "dns",
                "port",
            ],
            "selectTags": [
                "tag",
                "value",
            ],
            "filter": {
                "status": "0",
            },
            "sortfield": "host",
            "sortorder": "ASC",
        },
    )


def select_address(host):
    def interface_priority(interface):
        interface_type = str(interface.get("type", ""))
        is_main = str(interface.get("main", "0")) == "1"

        # Типы интерфейсов Zabbix:
        # 1 — Agent
        # 2 — SNMP
        # 3 — IPMI
        # 4 — JMX
        if interface_type == "2" and is_main:
            return 0
        if interface_type == "2":
            return 1
        if interface_type == "1" and is_main:
            return 2
        if interface_type == "1":
            return 3
        if is_main:
            return 4
        return 5

    def interface_address(interface):
        use_ip = str(interface.get("useip", "1")) == "1"

        if use_ip:
            return str(interface.get("ip", "")).strip()

        return str(interface.get("dns", "")).strip()


    tags = host.get("tags", [])

    interfaces = sorted(
        host.get("interfaces", []),
        key=interface_priority,
    )

    for interface in interfaces:
        address = interface_address(interface)
        if address:
            return address

    return None


def select_vendor_and_model(tags):
    for item in tags:
        return item.get('tag',None), item.get('value',None)


def convert_hosts(hosts):
    result = []
    errors = []

    for host in hosts:
        technical_name = str(host.get("host", "")).strip()
        tags = host.get("tags", [])

        if any(tag.get("tag") in EXCLUDED_TAGS for tag in tags):
            continue

        try:
            address = select_address(host)
            vendor, model = select_vendor_and_model(host.get("tags"))

            result.append(
                {
                    "name": technical_name,
                    "address": address,
                    "vendor": vendor,
                    "model": model
                }
            )

        except ExportError:
            raise

    result.sort(key=lambda item: item["name"].lower())

    names = set()
    duplicates = set()

    for item in result:
        if item["name"] in names:
            duplicates.add(item["name"])
        names.add(item["name"])

    if duplicates:
        raise ExportError(
            "Duplicate hostnames detected: "
            + ", ".join(sorted(duplicates))
        )

    for error in errors:
        stderr(f"WARNING: {error}")

    return result


def parse_arguments():
    parser = argparse.ArgumentParser(
        description=(
            "Выгрузка активных хостов из Zabbix API "
            "и их последующий опрос для формирования /etc/hosts файла"
        )
    )
    parser.add_argument(
        "--url",
        default=os.getenv("ZABBIX_URL"),
        help="URL Zabbix, например https://zabbix.example.local",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("ZABBIX_TOKEN"),
        help="API-токен Zabbix; безопаснее передавать через ZABBIX_TOKEN",
    )
    parser.add_argument(
        "--auth-mode",
        choices=("bearer", "jsonrpc"),
        default=os.getenv("ZABBIX_AUTH_MODE", "bearer"),
        help="Способ передачи API-токена",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.getenv("ZABBIX_TIMEOUT", "30")),
    )
    parser.add_argument(
        "--ca-file",
        default=os.getenv("ZABBIX_CA_FILE"),
        help="Путь к внутреннему CA-сертификату",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Не проверять TLS-сертификат Zabbix",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Вывести результат, но не изменять /etc/hosts",
    )
    parser.add_argument(
        "--skip-invalid",
        action="store_true",
        help="Пропускать некорректные хосты вместо остановки",
    )
    parser.add_argument(
        "--output",
        default=os.getenv(
            "HOSTS_FILE",
            "/etc/hosts",
        ),
        help="Путь к /etc/hosts на Docker-хосте",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.getenv("MAX_WORKERS", "10")),
        help="Максимальное количество одновременно опрашиваемых устройств",
    )
    parser.add_argument(
        "--domain",
        default=os.getenv(
            "DOMAIN",
            "example.local",
        ),
        help="домен добавляемый к имени железки",
    )
    parser.add_argument(
        "--min_line_ratio",
        type=float,
        default=os.getenv(
            "MIN_LINE_RATIO",
            "0.9",
        ),
        help="Разница между кол-вом строк до и после обновления файла",
    )

    return parser.parse_args()


def sort_devices_by_ip(_devices: list) -> list:
    return sorted(_devices, key=lambda device: ipaddress.ip_address(device['address']))


def get_interfaces_addresses(_device):
    args = parse_arguments()
    match _device.get("vendor"):
        case "ECI":
            match _device.get("model"):
                case "AS9215":
                    data = ECI.parse_raw_output_9215(ECI.telnet_get_interfaces_and_ips(_device), _device.get("name"), args.domain)
                    return data
                case "SR9604":
                    data = ECI.parse_raw_output_9604(ECI.ssh_get_interfaces_and_ips(_device), _device.get("name"), args.domain)
                    return data
                case _:
                    print(f'{_device} - UNKNOWN ECI DEVICE')
        case "Cisco":
            data = Cisco.parse_raw_output(Cisco.get_interfaces_and_ips(_device), _device.get("name"), args.domain)
            return data
        case "Huawei":
            data = Huawei.parse_raw_output(Huawei.get_interfaces_and_ips(_device), _device.get("name"), args.domain)
            return data
        case "Juniper":
            data = Juniper.parse_raw_output(Juniper.get_interfaces_and_ips(_device), _device.get("name"), args.domain)
            return data
        case _:
            print(f'{_device} - UNKNOWN DEVICE')

def poll_device(device):
    """
    Опрос одного устройства.

    Функция выполняется в рабочем потоке и не изменяет
    общие структуры данных.
    """
    result = get_interfaces_addresses(device)
    return result or []

def poll_devices(devices, max_workers):
    interfaces_and_addresses = []

    if max_workers < 1:
        raise ExportError("--workers должен быть больше нуля")

    # Нет смысла создавать больше потоков, чем устройств.
    workers = min(max_workers, len(devices)) if devices else 1

    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="device-poll",
    ) as executor:
        future_to_device = {
            executor.submit(poll_device, device): device
            for device in devices
        }

        for future in as_completed(future_to_device):
            device = future_to_device[future]
            device_name = device.get("name", "<unknown>")
            address = device.get("address", "<unknown>")

            try:
                result = future.result()
            except Exception as exc:
                stderr(
                    f"WARNING: ошибка опроса "
                    f"{device_name} ({address}): {exc}"
                )
                continue

            interfaces_and_addresses.extend(result)

            print(
                f"{device_name} ({address}): "
                f"получено записей — {len(result)}"
            )

    return interfaces_and_addresses


def fsync_directory(directory: Path) -> None:
    """
    Синхронизация каталога после os.replace().
    """
    directory_fd = os.open(
        directory,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )

    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)

def atomic_copy(source: Path, destination: Path) -> None:
    """
    Атомарное копирование файла.
    """
    temporary_name = None

    try:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.tmp.",
            dir=str(destination.parent),
        )

        os.close(fd)

        shutil.copy2(source, temporary_name)

        with open(temporary_name, "rb") as temporary_file:
            os.fsync(temporary_file.fileno())

        os.replace(temporary_name, destination)
        fsync_directory(destination.parent)

        temporary_name = None

    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

def rotate_backups(output: Path) -> None:
    """
    Ротация резервных копий:

        hosts.bak.3 -> hosts.bak.4
        hosts.bak.2 -> hosts.bak.3
        hosts.bak.1 -> hosts.bak.2
        hosts        -> hosts.bak.1
    """

    for backup_number in range(4, 1, -1):
        old_backup = output.with_name(
            f"{output.name}.bak.{backup_number - 1}"
        )

        new_backup = output.with_name(
            f"{output.name}.bak.{backup_number}"
        )

        if old_backup.exists():
            os.replace(old_backup, new_backup)

    if output.exists():
        backup_1 = output.with_name(
            f"{output.name}.bak.1"
        )

        atomic_copy(output, backup_1)

def atomic_write(
    output: Path,
    content: str,
) -> None:
    """
    Атомарная запись содержимого файла.
    """
    temporary_name = None
    old_stat = output.stat() if output.exists() else None

    try:
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{output.name}.tmp.",
            dir=str(output.parent),
        )

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
            newline="",
        ) as temporary_file:
            temporary_file.write(content)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())

        if old_stat is not None:
            os.chmod(
                temporary_name,
                stat.S_IMODE(old_stat.st_mode),
            )

            try:
                os.chown(
                    temporary_name,
                    old_stat.st_uid,
                    old_stat.st_gid,
                )
            except PermissionError:
                pass
        else:
            os.chmod(temporary_name, 0o644)

        os.replace(temporary_name, output)
        fsync_directory(output.parent)

        temporary_name = None

    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

def update_hosts_file(
    records: list[str],
    output: Path,
    lock_path: Path,
    min_line_ratio: float = 0.5,
) -> bool:
    """
    Обновляет секцию SCRIPT DISCOVERY в hosts-файле.

    records:
        Подготовленный список строк без необходимости проверки,
        например:

        [
            "192.168.0.1 host1.example.ru",
            "192.168.0.2 host2.example.ru",
        ]

    output:
        Например: Path("/etc/hosts")

    lock_path:
        Например: Path("/etc/host.lock")

    min_line_ratio:
        Минимально допустимое отношение нового количества строк
        к старому.

        0.5 означает:
        нельзя заменить файл, если новый файл меньше половины
        старого по количеству строк.

    Возвращает:

        True  — файл обновлён;
        False — содержимое уже было таким же.
    """

    if not 0 < min_line_ratio <= 1:
        raise ValueError(
            "min_line_ratio должен быть в диапазоне от 0 до 1"
        )

    output = Path(output)
    lock_path = Path(lock_path)

    output.parent.mkdir(parents=True, exist_ok=True)
    lock_path.parent.mkdir(parents=True, exist_ok=True)

    lock_file = lock_path.open("a+", encoding="ascii")

    try:
        try:
            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as exc:
            raise RuntimeError(
                f"Другая копия программы уже запущена: {lock_path}"
            ) from exc

        lock_file.seek(0)
        lock_file.truncate()
        lock_file.write(f"{os.getpid()}\n")
        lock_file.flush()
        os.fsync(lock_file.fileno())

        if output.exists():
            old_content = output.read_text(
                encoding="utf-8",
            )
        else:
            old_content = ""

        old_line_count = len(old_content.splitlines())

        marker_position = old_content.find(DISCOVERY_MARKER)

        if marker_position >= 0:
            file_prefix = old_content[
                :marker_position + len(DISCOVERY_MARKER)
            ]
        else:
            if old_content and not old_content.endswith("\n"):
                old_content += "\n"

            file_prefix = (
                old_content
                + DISCOVERY_MARKER
            )

        records_content = "\n".join(records)

        new_content = (
            file_prefix.rstrip("\n")
            + "\n"
            + records_content.rstrip("\n")
            + "\n"
        )

        new_line_count = len(new_content.splitlines())

        if (
            old_line_count > 0
            and new_line_count < old_line_count * min_line_ratio
        ):
            raise RuntimeError(
                "Обновление отменено: количество строк уменьшилось "
                f"с {old_line_count} до {new_line_count}"
            )

        if new_content == old_content:
            return False

        if output.exists():
            rotate_backups(output)

        atomic_write(
            output=output,
            content=new_content,
        )

        return True

    finally:
        try:
            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_UN,
            )
        finally:
            lock_file.close()




def main():
    args = parse_arguments()

    if not args.url:
        raise ExportError(
            "Не задан URL Zabbix. Укажите --url или ZABBIX_URL"
        )

    if not args.token:
        raise ExportError(
            "Не задан API-токен. Укажите переменную ZABBIX_TOKEN"
        )

    if args.insecure:
        verify = False
        requests.packages.urllib3.disable_warnings(
            requests.packages.urllib3.exceptions.InsecureRequestWarning
        )
    elif args.ca_file:
        verify = args.ca_file
    else:
        verify = True

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    lock_path = Path(str(output) + ".lock")

    with open(lock_path, "w", encoding="utf-8") as lock_file:
        try:
            fcntl.flock(
                lock_file.fileno(),
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as exc:
            raise ExportError(
                "Другой экземпляр уже работает"
            ) from exc

        api = ZabbixAPI(
            url=args.url,
            token=args.token,
            auth_mode=args.auth_mode,
            verify=verify,
            timeout=args.timeout,
        )

    hosts = fetch_hosts(api)

    devices = convert_hosts(hosts)

    sorted_devices = sort_devices_by_ip(devices)

    interfaces_and_addresses = poll_devices(sorted_devices[:10],max_workers=args.workers)

    #add loopbacks to list
    for device in reversed(sorted_devices):
        interfaces_and_addresses.insert(0, f"{device.get("address")} {device.get("name")}.lo0.{args.domain} {device.get("name")}.{args.domain}")

    #write file
    try:
        changed = update_hosts_file(
            records=interfaces_and_addresses,
            output=output,
            lock_path=lock_path,
            min_line_ratio=args.min_line_ratio,
        )

        if changed:
            print(f"Файл {output} обновлён")
        else:
            print(f"Файл {output} уже актуален")

    except RuntimeError as error:
        print(f"Ошибка обновления hosts: {error}")
        raise SystemExit(1)

    return 0


if __name__ == "__main__":
    interfacesAndAddressesList = []
    try:
        sys.exit(main())
    except ExportError as exc:
        stderr(f"ERROR: {exc}")
        sys.exit(1)
    except KeyboardInterrupt:
        stderr("ERROR: выполнение прервано")
        sys.exit(130)