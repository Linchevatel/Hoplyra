
from __future__ import annotations

import base64
import json
import re
import struct
import zlib
from typing import Any


AWG2_CONTAINER = "amnezia-awg2"
# Amnezia maps both amnezia-awg and amnezia-awg2 to protocol key "awg" (scriptsRegistry / ContainerConfig::fromJson).
AWG_PROTOCOL_KEY = "awg"


def _qt_compress(raw: bytes) -> bytes:
    compressed = zlib.compress(raw)
    return struct.pack(">I", len(raw)) + compressed


def encode_amnezia_vpn_uri(package: dict[str, Any]) -> str:
    raw = json.dumps(package, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    payload = _qt_compress(raw)
    token = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
    return f"vpn://{token}"


def _parse_kv_conf(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if " = " not in line:
            continue
        key, value = line.split(" = ", 1)
        out[key.strip()] = value.strip()
    return out


def build_amnezia_awg_package(
    client_conf: str,
    *,
    host: str,
    port: int,
    description: str,
) -> dict[str, Any]:
    kv = _parse_kv_conf(client_conf)
    dns_match = re.search(
        r"DNS\s*=\s*(\d{1,3}(?:\.\d{1,3}){3})(?:\s*,\s*(\d{1,3}(?:\.\d{1,3}){3}))?",
        client_conf,
    )
    dns1 = dns_match.group(1) if dns_match else "1.1.1.1"
    dns2 = dns_match.group(2) if (dns_match and dns_match.group(2)) else "1.0.0.1"

    is_v3 = bool(kv.get("HeaderProtectionKey") or kv.get("ContentPaddingAddition"))
    container_name = "amnezia-awg"
    proto_ver = "2"

    allowed = kv.get("AllowedIPs", "0.0.0.0/0")
    allowed_list = [part.strip() for part in allowed.split(",") if part.strip()]
    client_priv = kv.get("PrivateKey", "")
    client_ip_val = kv.get("Address", "")
    server_pub = kv.get("PublicKey", "")
    last_config: dict[str, Any] = {
        "config": client_conf,
        "hostName": host,
        "port": port,
        "client_priv_key": client_priv,
        "clientPrivKey": client_priv,
        "client_ip": client_ip_val,
        "clientIp": client_ip_val,
        "server_pub_key": server_pub,
        "serverPubKey": server_pub,
        "allowed_ips": allowed_list,
        "allowedIps": allowed_list,
    }
    int_keys = {"Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "jc", "jmin", "jmax", "s1", "s2", "s3", "s4"}
    camel_map = {
        "HeaderProtectionKey": "headerProtectionKey",
        "ContentPaddingAddition": "contentPaddingAddition",
        "RekeyAfterTime": "rekeyAfterTime",
        "RekeyTimeout": "rekeyTimeout",
        "RejectAfterTime": "rejectAfterTime",
        "KeepaliveTimeout": "keepaliveTimeout",
        "MaxHandshakeAttempts": "maxHandshakeAttempts",
        "RandomTrailers": "randomTrailers",
        "DisableCookies": "disableCookies",
    }
    for key in (
        "Jc",
        "Jmin",
        "Jmax",
        "S1",
        "S2",
        "S3",
        "S4",
        "H1",
        "H2",
        "H3",
        "H4",
        "I1",
        "I2",
        "I3",
        "I4",
        "I5",
        "HeaderProtectionKey",
        "ContentPaddingAddition",
        "RekeyAfterTime",
        "RekeyTimeout",
        "RejectAfterTime",
        "KeepaliveTimeout",
        "MaxHandshakeAttempts",
        "RandomTrailers",
        "DisableCookies",
    ):
        if kv.get(key):
            val = kv[key]
            typed_val = int(val) if (key in int_keys and str(val).isdigit()) else str(val)
            last_config[key] = typed_val
            last_config[key.lower()] = typed_val
            if key in camel_map:
                last_config[camel_map[key]] = typed_val
    if kv.get("PresharedKey"):
        last_config["psk_key"] = kv["PresharedKey"]
        last_config["pskKey"] = kv["PresharedKey"]

    awg_container = {
        "last_config": json.dumps(last_config, ensure_ascii=False),
        "isThirdPartyConfig": False,
        "port": str(port),
        "transport_proto": "udp",
        "protocol_version": proto_ver,
    }

    return {
        "containers": [
            {
                "container": container_name,
                AWG_PROTOCOL_KEY: awg_container,
            }
        ],
        "defaultContainer": container_name,
        "description": description,
        "dns1": dns1,
        "dns2": dns2,
        "hostName": host,
    }


def build_amnezia_awg_vpn_uri(
    client_conf: str,
    *,
    host: str,
    port: int,
    description: str,
) -> str:
    package = build_amnezia_awg_package(
        client_conf,
        host=host,
        port=port,
        description=description,
    )
    return encode_amnezia_vpn_uri(package)
