"""Transactional database reservations plus host resource discovery for standalone VPNs."""
import ipaddress
import json
import re

from fastapi import HTTPException

DEFAULT_PORTS = {'wg': 51820, 'awg': 55424, 'xray': 443, 'openvpn': 1194}


def allocate(conn, server_id, protocol, runner):
    ports = set()
    networks = []
    for row in conn.execute('SELECT * FROM configs').fetchall():
        meta = json.loads(row['meta_json'] or '{}')
        hops = meta.get('hops', []) if meta.get('chain') else []
        protocols = [h['protocol'] for h in hops if h.get('serverId') == server_id]
        if row['server_id'] == server_id and not hops:
            protocols.append(row['protocol'])
        for proto in protocols:
            if proto not in DEFAULT_PORTS:
                continue
            ports.add(int(meta.get('listenPort') or DEFAULT_PORTS[proto]))
            subnet = meta.get('subnet')
            if not subnet and 'client_config' in row.keys():
                address = re.search(r'^Address\s*=\s*(10\.\d+\.\d+\.\d+)', row['client_config'] or '', re.M)
                if address:
                    subnet = str(ipaddress.ip_network(address[1] + '/24', strict=False))
            subnet = subnet or {'wg': '10.66.66.0/24', 'awg': '10.9.1.0/24', 'openvpn': '10.8.0.0/24'}.get(proto)
            if subnet:
                networks.append(ipaddress.ip_network(subnet, strict=False))
    for cmd in ('ss -H -lntu 2>/dev/null', 'wg show all listen-port 2>/dev/null', 'awg show all listen-port 2>/dev/null'):
        _, out, _ = runner.run(cmd, timeout=30)
        for line in out.splitlines():
            fields = line.split()
            if cmd.startswith('ss'):
                if len(fields) >= 5:
                    match = re.search(r':(\d+)$', fields[4])
                    if match:
                        ports.add(int(match[1]))
            elif fields and fields[-1].isdigit():
                ports.add(int(fields[-1]))
    _, out, _ = runner.run('ip addr show 2>/dev/null', timeout=30)
    for cidr in re.findall(r'inet\s+(\d+\.\d+\.\d+\.\d+/\d+)', out):
        networks.append(ipaddress.ip_network(cidr, strict=False))
    base = DEFAULT_PORTS[protocol]
    end = 449 if protocol == 'xray' else 65536
    port = next((p for p in range(base, end) if p not in ports), None)
    if port is None:
        raise HTTPException(409, f'Нет свободных портов для {protocol} в пуле {base}–{end - 1}')
    result = {'listenPort': port}
    prefix, start = {'wg': ('10.66', 66), 'awg': ('10.9', 1), 'openvpn': ('10.8', 0)}.get(protocol, (None, 0))
    if prefix:
        subnet = next((f'{prefix}.{i}.0/24' for i in range(start, 255)
                       if not any(ipaddress.ip_network(f'{prefix}.{i}.0/24').overlaps(n) for n in networks)), None)
        if subnet is None:
            raise HTTPException(409, f'Нет свободных подсетей для {protocol}')
        result['subnet'] = subnet
    return result
