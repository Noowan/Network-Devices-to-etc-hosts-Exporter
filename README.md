# Network Devices to /etc/hosts Exporter

A Python utility that collects interface and IP address information from network devices and maintains a generated section in `/etc/hosts`. The resulting host records can then be consumed by a DNS service or another local name-resolution mechanism.

The project is currently a working name rather than a final product name.

## How it works

The exporter performs the following workflow:

1. Connects to the Zabbix JSON-RPC API.
2. Retrieves enabled hosts and their interfaces.
3. Selects a management address for every host according to a priority order.
4. Reads the vendor and model from the first Zabbix tag.
5. Polls supported network devices concurrently over SSH or Telnet.
6. Parses the command output returned by each device.
7. Generates interface-to-name records.
8. Adds a loopback-style record for every device.
9. Replaces only the generated section of the target hosts file.
10. Creates backups and protects the update with file locking and atomic writes.

The program does not load `params.env` by itself. The file must be exported into the process environment, for example by a systemd unit or by using `set -a` in a shell.

## Supported devices

| Vendor | Model or platform | Transport | Command or data source |
|---|---|---|---|
| Cisco | Cisco IOS-compatible devices | SSH | `show ip interface brief` |
| Huawei | Huawei devices | SSH | `display ip int br` |
| Juniper | Juniper devices | SSH | `show interfaces terse` |
| ECI | AS9215 | Telnet | `show ip interface brief` |
| ECI | SR9604 | SSH | `show interfaces terse` |

The implementation also enables a number of legacy SSH algorithms in Paramiko so that older hardware can still be reached. This reduces the security level of connections to devices that require obsolete algorithms; use this only in a controlled management network and replace legacy algorithms whenever possible.

## Requirements

The project requires:

- Python 3.10 or newer, because the code uses structural pattern matching and modern type annotations.
- A Linux or other POSIX-like operating system with `fcntl` support.
- Network access to the Zabbix API.
- Network access to the managed devices over SSH and/or Telnet.
- Credentials with permission to run the required show or display commands.
- Permission to read and atomically replace the target hosts file.

Install the Python dependencies from `requirements.txt` in a virtual environment.

## Installation

Clone the repository and create a virtual environment:

```bash
git clone https://github.com/Noowan/Network-Devices-to-etc-hosts-Exporter.git /opt/network-device-hosts-exporter
cd /opt/network-device-hosts-exporter

python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
```

For production use, keep the virtual environment and project files in a location readable by the account that runs the service. Keep credentials outside version control.

## Configuration

Create a local configuration file from the example:

```bash
cp params_example.env params.env
chmod 600 params.env
```

Example configuration:

```dotenv
ZABBIX_URL=https://zabbix.example.net
ZABBIX_TOKEN=replace-with-a-zabbix-api-token
ZABBIX_AUTH_MODE=bearer

SSH_USER=network-reader
SSH_PASSWORD=replace-with-a-device-password

HOSTS_FILE=/etc/hosts
DOMAIN=example.local
MAX_WORKERS=30
MIN_LINE_RATIO=0.9
EXCLUDED_TAGS=tag1,tag2,tag3
```

### Configuration variables

| Variable | Default | Description |
|---|---:|---|
| `ZABBIX_URL` | — | Base URL of the Zabbix server. The client appends `/api_jsonrpc.php` unless the URL already ends with that path. |
| `ZABBIX_TOKEN` | — | Zabbix API token. Required. |
| `ZABBIX_AUTH_MODE` | `bearer` | Authentication mode: `bearer` or `jsonrpc`. |
| `ZABBIX_TIMEOUT` | `30` | Zabbix API request timeout in seconds. |
| `ZABBIX_CA_FILE` | — | Path to a custom CA certificate used to verify the Zabbix TLS certificate. |
| `SSH_USER` | — | Username used for SSH and Telnet device access. Required for device polling. |
| `SSH_PASSWORD` | — | Password used for SSH and Telnet device access. Required for device polling. |
| `HOSTS_FILE` | `/etc/hosts` | File to update. |
| `DOMAIN` | `example.local` | Domain suffix added to generated names. |
| `MAX_WORKERS` | `10` | Maximum number of devices polled concurrently. |
| `MIN_LINE_RATIO` | `0.9` | Minimum ratio of new file line count to the previous file line count. |
| `EXCLUDED_TAGS` | empty | Comma-separated Zabbix tag names. Hosts containing one of these tags are excluded. |

The code reads values from the environment with `os.getenv`. It does not parse dotenv files directly, so load the file before starting the program:

```bash
set -a
. ./params.env
set +a
. .venv/bin/activate
python main.py
```

## Zabbix data model

Only enabled Zabbix hosts are requested. The exporter uses the following host fields:

- `hostid`
- `host`
- `name`
- `status`
- `monitored_by`
- interfaces
- tags


### Management address selection

The management address is selected from the host interfaces in this order:

1. Main SNMP interface.
2. Any SNMP interface.
3. Main Zabbix agent interface.
4. Any Zabbix agent interface.
5. Any other main interface.
6. Any remaining interface.

For an interface configured to use an IP address, the IP value is used. Otherwise, the DNS value is used.

## Generated records

Interface records use this format:

```text
<ip-address> <device-name>.<short-interface-name>.<domain>
```

Example:

```text
192.0.2.10 edge-01.gi0-0.example.net
192.0.2.11 edge-01.te0-1.example.net
198.51.100.10 core-01.vl100.example.net
```

Interface names are normalized into short names where possible. Examples include:

| Original interface | Generated short name |
|---|---|
| `GigabitEthernet0/0` | `gi0-0` |
| `TenGigabitEthernet0/1` | `te0-1` |
| `FastEthernet0/1` | `fa0-1` |
| `Vlan100` or `Vlanif100` | `vl100` |
| `Port-channel1` | `po1` |
| `Loopback0` | `lo0` |

The parsers ignore interfaces without an assigned IP address. Loopback, management, internal, or other excluded interfaces are filtered according to the vendor-specific parser.

In addition to interface records, the exporter adds a device-level record using the selected management address:

```text
<management-address> <device-name>.lo0.<domain> <device-name>.<domain>
```

## Hosts-file update behavior

The generated section is identified by this marker:

```text
##########SCRIPT DISCOVERY##########
```

If the marker already exists, the exporter preserves everything before the marker and replaces everything after it. If the marker is missing, it is appended to the existing file.

The update is designed to be safe for a system file:

- A lock file prevents concurrent executions.
- A second lock is used during the hosts-file update.
- The new file is written to a temporary file first.
- The temporary file is flushed and synchronized with `fsync`.
- The replacement is performed with `os.replace`.
- Existing ownership and permissions are preserved when possible.
- Up to four rotating backups are maintained as `.bak.1` through `.bak.4`.
- The update is cancelled if the new file becomes smaller than the configured `MIN_LINE_RATIO`.

The lock file is created next to the output file. For `/etc/hosts`, the usual path is:

```text
/etc/hosts.lock
```

## Command-line usage

Display all available options:

```bash
python main.py --help
```

The main options are:

```text
--url URL                 Zabbix URL
--token TOKEN             Zabbix API token
--auth-mode MODE          bearer or jsonrpc
--timeout SECONDS         Zabbix API timeout
--ca-file PATH            Custom CA certificate
--insecure                Disable TLS certificate verification for Zabbix
--dry-run                 Declared option; see limitations below
--skip-invalid            Declared option; see limitations below
--output PATH             Hosts file to update
--workers NUMBER          Maximum number of concurrent device polls
--domain DOMAIN           Domain suffix for generated records
--min_line_ratio RATIO    Minimum new/old line-count ratio
--excluded-tags TAGS      Comma-separated tag names to exclude
```

For example:

```bash
set -a
. ./params.env
set +a
. .venv/bin/activate

python main.py \
  --output /etc/hosts \
  --domain example.net \
  --workers 20
```

The command requires the process to have permission to update the output file. Running against `/etc/hosts` normally requires root privileges or a service account with suitable permissions.

## systemd service

The following is an example service. Adjust paths, the service account, and permissions for the target environment:

```ini
[Unit]
Description=Export network device interfaces to hosts file
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
WorkingDirectory=/opt/network-device-hosts-exporter
EnvironmentFile=/opt/network-device-hosts-exporter/params.env
ExecStart=/opt/network-device-hosts-exporter/.venv/bin/python /opt/network-device-hosts-exporter/main.py
```

Save it as:

```text
/etc/systemd/system/network-device-hosts-exporter.service
```

A timer can run the exporter periodically:

```ini
[Unit]
Description=Periodic network device hosts export

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
```

Save it as:

```text
/etc/systemd/system/network-device-hosts-exporter.timer
```

Enable and start the timer:

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now network-device-hosts-exporter.timer
```

Check the latest execution:

```bash
sudo systemctl status network-device-hosts-exporter.service
sudo journalctl -u network-device-hosts-exporter.service
```

## Security considerations

Treat `ZABBIX_TOKEN` and `SSH_PASSWORD` as secrets. Store `params.env` with restrictive permissions, do not commit it to Git, and avoid printing the environment in service diagnostics.

The device modules currently use password authentication and automatically accept unknown SSH host keys with Paramiko's `AutoAddPolicy`. For production deployments, consider replacing this behavior with strict host-key verification.

Telnet sends credentials without transport encryption. Use it only where required by legacy hardware and isolate the management traffic. Prefer SSH whenever the device supports it.

The legacy SSH algorithm compatibility code enables algorithms such as `diffie-hellman-group1-sha1`, `ssh-dss`, and CBC ciphers. These algorithms are obsolete and should be removed when the device fleet no longer requires them.

## Troubleshooting

### Zabbix API errors

Verify the URL, token, authentication mode, TLS settings, and API permissions. If the Zabbix server uses an internal certificate authority, set `ZABBIX_CA_FILE` to the CA certificate path. Use `--insecure` only for controlled troubleshooting.

### No records are generated for a device

Check that the device is enabled in Zabbix, has a usable interface address, and has the expected vendor/model in the first tag. Confirm that the model string exactly matches the model handled by `main.py`.

### SSH authentication or algorithm errors

Verify `SSH_USER` and `SSH_PASSWORD`, network reachability, and the device's SSH configuration. Older devices may require the legacy algorithms enabled by the vendor modules. The service account must also be able to reach the device management network.

### Telnet prompt errors

The AS9215 connector expects a username prompt and a password prompt. If the device uses different prompt text, the regular expressions in `ECI.py` may need to be adjusted.

### Hosts file update is cancelled

Compare the new number of generated records with the old file and inspect `MIN_LINE_RATIO`. A sudden decrease is treated as a safety condition to prevent accidental destruction of the hosts file. Confirm the Zabbix response and device polling results before lowering the threshold.

### Another instance is already running

Check for an active service or manually started process. The exporter uses a lock file next to the output file. Remove a stale lock only after confirming that no exporter process is still running.

## Project files

| File | Purpose |
|---|---|
| `main.py` | Command-line entry point, Zabbix host processing, concurrent polling, and hosts-file updates. |
| `zabbix_api.py` | Small JSON-RPC client for Zabbix bearer and legacy JSON-RPC authentication. |
| `Cisco.py` | Cisco SSH connector and output parser. |
| `Huawei.py` | Huawei SSH connector and output parser. |
| `Juniper.py` | Juniper SSH connector and output parser. |
| `ECI.py` | ECI SSH/Telnet connectors and parsers for AS9215 and SR9604. |
| `params_example.env` | Example environment configuration. |
| `requirements.txt` | Python dependency versions. |

## Adding a new vendor or model

Add a vendor module with a connection function and a parser that returns records in the following format:

```python
[
    "192.0.2.10 device-01.gi0-0.example.net",
    "192.0.2.11 device-01.te0-1.example.net",
]
```

Then add the vendor and model dispatch logic to `get_interfaces_addresses()` in `main.py`. The parser should handle pagination, normalize interface names, ignore interfaces without addresses, and avoid returning duplicate or invalid records.

## Current limitations and implementation notes

The following behavior should be reviewed before calling the project production-ready:

- `--dry-run` is declared by the argument parser but is not currently used to prevent file modification.
- `--skip-invalid` is declared but is not currently used in the host conversion flow.
- Vendor and model selection uses only the first Zabbix tag.
- Device credentials are shared through `SSH_USER` and `SSH_PASSWORD`; per-device credentials are not supported.
- SSH host keys are automatically accepted.
- Telnet is unencrypted.
- The implementation relies on interactive shell output and idle-time detection rather than a prompt parser.
- Device command output formats can vary between firmware versions.
- The generated output is intended for IPv4 addresses; the parsers currently match IPv4 patterns.
