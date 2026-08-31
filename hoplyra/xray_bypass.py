from __future__ import annotations

import base64
import json
import secrets
import uuid
from typing import Any

from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

REALITY_DEST = "swdist.apple.com:443"
REALITY_SERVER_NAMES = ["swdist.apple.com", "updates.cdn-apple.com", "gateway.icloud.com"]
REALITY_SERVER_NAME = "swdist.apple.com"
REALITY_FINGERPRINT = "qq"

RU_DIRECT_DOMAINS: tuple[str, ...] = (
    "domain:max.ru",
    "domain:2gis.ru",
    "domain:ads.x5.ru",
    "domain:2gis.com",
    "domain:aif.ru",
    "domain:aeroflot.ru",
    "domain:alfabank.ru",
    "domain:avito.ru",
    "domain:beeline.ru",
    "domain:burgerkingrus.ru",
    "domain:dellin.ru",
    "domain:drive2.ru",
    "domain:dzen.ru",
    "domain:flypobeda.ru",
    "domain:forbes.ru",
    "domain:gazeta.ru",
    "domain:gazprombank.ru",
    "domain:gismeteo.ru",
    "domain:gosuslugi.ru",
    "domain:hh.ru",
    "domain:kontur.ru",
    "domain:kontur.host",
    "domain:kp.ru",
    "domain:kuper.ru",
    "domain:lenta.ru",
    "domain:mail.ru",
    "domain:megamarket.ru",
    "domain:megamarket.tech",
    "domain:megafon.ru",
    "domain:moex.com",
    "domain:motivtelecom.ru",
    "domain:ozon.ru",
    "domain:pervye.ru",
    "domain:psbank.ru",
    "domain:rambler.ru",
    "domain:rambler-co.ru",
    "domain:rbc.ru",
    "domain:reg.ru",
    "domain:reviews.2gis.com",
    "domain:rg.ru",
    "domain:ria.ru",
    "domain:rosseti.ru",
    "domain:ruwiki.ru",
    "domain:rustore.ru",
    "domain:rutube.ru",
    "domain:rzd.ru",
    "domain:sirena-travel.ru",
    "domain:sravni.ru",
    "domain:t-j.ru",
    "domain:t2.ru",
    "domain:tank-online.com",
    "domain:taximaxim.ru",
    "domain:tbank-online.com",
    "domain:tildaapi.com",
    "domain:tns-counter.ru",
    "domain:trvl.yandex.net",
    "domain:tutu.ru",
    "domain:vk.com",
    "domain:vk.ru",
    "domain:vkvideo.ru",
    "domain:vtb.ru",
    "domain:x5.ru",
    "domain:ya.ru",
    "domain:yandex.ru",
    "domain:yandex.net",
    "domain:yandex.com",
    "domain:yastatic.net",
    "domain:yandexcloud.net",
    "full:go.yandex",
    "full:ru.ruwiki.ru",
    "domain:xn--90acagbhgpca7c8c7f.xn--p1ai",
    "domain:xn--80ajghhoc2aj1c8b.xn--p1ai",
    "domain:xn--90aivcdt6dxbc.xn--p1ai",
    "domain:xn--b1aew.xn--p1ai",
    "domain:api.oneme.ru",
    "domain:fd.oneme.ru",
    "domain:i.oneme.ru",
    "domain:miniapps.max.ru",
    "domain:sdk-api.apptracer.ru",
    "domain:st.max.ru",
    "domain:tracker-api.vk-analytics.ru",
    "domain:wildberries.ru",
    "domain:wink.ru",
    "domain:rt.ru",
    "domain:rostelecom.ru",
    "domain:ngenix.net",
    "domain:wb.ru",
    "domain:wbbasket.ru",
    "domain:wbstatic.net",
    "domain:nalog.gov.ru",
    "domain:samokat.ru",
    "domain:sberid.ru",
    "domain:sberbank.ru",
    "domain:sbermarket.ru",
    "domain:x5id.ru",
    "domain:5ka.ru",
    "domain:perekrestok.ru",
    "domain:chizhik.club",
    "domain:banki.ru",
    "domain:mos.ru",
    "domain:esia.gosuslugi.ru",
    "domain:ok.ru",
    "domain:mycdn.me",
    "domain:cdnvideo.ru",
    "domain:plvideo.ru",
)


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def generate_reality_keypair() -> tuple[str, str]:
    private = x25519.X25519PrivateKey.generate()
    public = private.public_key()
    priv_raw = private.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    pub_raw = public.public_bytes(Encoding.Raw, PublicFormat.Raw)
    return _b64url(priv_raw), _b64url(pub_raw)


def generate_short_id() -> str:
    return secrets.token_hex(8)


def _client_sniffing() -> dict[str, Any]:
    return {
        "enabled": True,
        "routeOnly": False,
        "destOverride": ["http", "tls", "quic"],
    }


def _client_routing_rules(server_host: str | None = None) -> list[dict[str, Any]]:
    rules: list[dict[str, Any]] = [
        {"type": "field", "port": 53, "outboundTag": "dns-out"},
        {"type": "field", "domain": ["domain:2ip.ru"], "outboundTag": "proxy"},
        {"type": "field", "protocol": ["bittorrent"], "outboundTag": "direct"},
    ]
    if server_host:
        rules.append({"type": "field", "ip": [server_host], "outboundTag": "direct"})
    rules.append({"type": "field", "domain": list(RU_DIRECT_DOMAINS), "outboundTag": "direct"})
    return rules


def build_reality_vless_inbound(
    vless_uuid: str,
    private_key: str,
    short_id: str,
    *,
    port: int = 443,
    server_names: list[str] | None = None,
) -> dict[str, Any]:
    names = server_names or list(REALITY_SERVER_NAMES)
    return {
        "tag": "vless-in",
        "port": port,
        "listen": "0.0.0.0",
        "protocol": "vless",
        "settings": {
            "clients": [{"id": vless_uuid, "flow": "xtls-rprx-vision"}],
            "decryption": "none",
        },
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "show": False,
                "dest": REALITY_DEST,
                "xver": 0,
                "serverNames": names,
                "privateKey": private_key,
                "shortIds": [short_id],
            },
        },
        "sniffing": {
            "enabled": True,
            "destOverride": ["http", "tls", "quic"],
        },
    }


def build_bypass_client_config(
    server_host: str,
    vless_uuid: str,
    public_key: str,
    short_id: str,
    *,
    port: int = 443,
    remark: str = "Hoplyra",
    server_name: str = REALITY_SERVER_NAME,
) -> dict[str, Any]:
    return {
        "dns": {
            "servers": [
                "https://1.1.1.1/dns-query",
                "https://8.8.8.8/dns-query",
                "1.1.1.1"
            ],
            "queryStrategy": "UseIPv4",
        },
        "routing": {
            "rules": _client_routing_rules(server_host),
            "domainMatcher": "hybrid",
            "domainStrategy": "IPIfNonMatch",
        },
        "inbounds": [
            {
                "tag": "socks",
                "port": 10808,
                "listen": "127.0.0.1",
                "protocol": "socks",
                "settings": {"udp": True, "auth": "noauth"},
                "sniffing": _client_sniffing(),
            },
            {
                "tag": "http",
                "port": 10809,
                "listen": "127.0.0.1",
                "protocol": "http",
                "settings": {"allowTransparent": False},
                "sniffing": _client_sniffing(),
            },
        ],
        "outbounds": [
            {
                "tag": "proxy",
                "protocol": "vless",
                "settings": {
                    "vnext": [
                        {
                            "address": server_host,
                            "port": port,
                            "users": [
                                {
                                    "id": vless_uuid,
                                    "encryption": "none",
                                    "flow": "xtls-rprx-vision",
                                }
                            ],
                        }
                    ]
                },
                "streamSettings": {
                    "network": "tcp",
                    "tcpSettings": {},
                    "security": "reality",
                    "realitySettings": {
                        "serverName": server_name,
                        "publicKey": public_key,
                        "shortId": short_id,
                        "fingerprint": REALITY_FINGERPRINT,
                    },
                },
            },
            {"tag": "dns-out", "protocol": "dns"},
            {"tag": "direct", "protocol": "freedom"},
            {"tag": "block", "protocol": "blackhole"},
        ],
        "remarks": remark,
    }


def build_vless_reality_uri(
    vless_uuid: str,
    server_host: str,
    public_key: str,
    short_id: str,
    *,
    port: int = 443,
    remark: str = "Hoplyra",
    server_name: str = REALITY_SERVER_NAME,
) -> str:
    tag = remark.replace(" ", "%20")
    return (
        f"vless://{vless_uuid}@{server_host}:{port}"
        f"?encryption=none&flow=xtls-rprx-vision&security=reality"
        f"&sni={server_name}&fp={REALITY_FINGERPRINT}&pbk={public_key}&sid={short_id}&type=tcp"
        f"#{tag}"
    )


def new_vless_uuid() -> str:
    return str(uuid.uuid4())


def build_bypass_server_config(
    vless_uuid: str,
    private_key: str,
    short_id: str,
    *,
    port: int = 443,
) -> dict[str, Any]:
    return {
        "log": {"loglevel": "warning"},
        "dns": {
            "servers": ["1.1.1.1", "8.8.8.8"],
            "queryStrategy": "UseIPv4",
        },
        "inbounds": [build_reality_vless_inbound(vless_uuid, private_key, short_id, port=port)],
        "outbounds": [
            {
                "protocol": "freedom",
                "tag": "direct",
                "settings": {
                    "domainStrategy": "UseIPv4",
                },
                "streamSettings": {
                    "sockopt": {
                        "tcpKeepAliveInterval": 15,
                    }
                },
            }
        ],
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [{"type": "field", "network": "tcp,udp", "outboundTag": "direct"}],
        },
    }


def rebuild_bypass_bundle_from_secrets(
    server_host: str,
    config_id: str,
    *,
    vless_uuid: str,
    private_key: str,
    public_key: str,
    short_id: str,
    port: int = 443,
    name: str | None = None,
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    remark = name or f"Hoplyra-{config_id[:8]}"
    server_cfg = build_bypass_server_config(vless_uuid, private_key, short_id, port=port)
    client_cfg = build_bypass_client_config(
        server_host,
        vless_uuid,
        public_key,
        short_id,
        port=port,
        remark=remark,
    )
    vless_uri = build_vless_reality_uri(
        vless_uuid,
        server_host,
        public_key,
        short_id,
        port=port,
        remark=remark,
    )
    client_text = f"{json.dumps(client_cfg, indent=2, ensure_ascii=False)}\n\n{vless_uri}\n"
    meta = bypass_meta_from_keys(
        vless_uuid=vless_uuid,
        public_key=public_key,
        short_id=short_id,
        port=port,
        vless_uri=vless_uri,
    )
    return server_cfg, client_text, meta


def public_key_from_private(private_key: str) -> str:
    priv = x25519.X25519PrivateKey.from_private_bytes(_b64url_decode(private_key))
    pub_raw = priv.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return _b64url(pub_raw)


def _b64url_decode(value: str) -> bytes:
    pad = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + pad)


def extract_server_reality_secrets(server_cfg: dict[str, Any]) -> tuple[str, str, str]:
    inbound = find_reality_vless_inbound(server_cfg)
    vless_uuid = inbound["settings"]["clients"][0]["id"]
    reality = inbound["streamSettings"]["realitySettings"]
    private_key = reality["privateKey"]
    short_id = reality["shortIds"][0]
    return vless_uuid, private_key, short_id


def find_reality_vless_inbound(server_cfg: dict[str, Any]) -> dict[str, Any]:
    for inbound in server_cfg.get("inbounds", []):
        if inbound.get("protocol") != "vless":
            continue
        stream = inbound.get("streamSettings") or {}
        if stream.get("security") == "reality":
            return inbound
    raise KeyError("no VLESS REALITY inbound in server config")


def patch_reality_inbound(inbound: dict[str, Any]) -> dict[str, Any]:
    stream = inbound.setdefault("streamSettings", {})
    stream["network"] = "tcp"
    stream["security"] = "reality"
    reality = stream.setdefault("realitySettings", {})
    reality["show"] = False
    reality["dest"] = REALITY_DEST
    reality["xver"] = 0
    reality["serverNames"] = list(REALITY_SERVER_NAMES)
    inbound["sniffing"] = {
        "enabled": True,
        "destOverride": ["http", "tls", "quic"],
    }
    clients = inbound.setdefault("settings", {}).setdefault("clients", [])
    if clients and "flow" not in clients[0]:
        clients[0]["flow"] = "xtls-rprx-vision"
    return inbound


def bypass_meta_from_keys(
    *,
    vless_uuid: str,
    public_key: str,
    short_id: str,
    port: int = 443,
    vless_uri: str | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {
        "vlessUuid": vless_uuid,
        "listenPort": port,
        "xrayBypass": True,
        "realityPublicKey": public_key,
        "realityShortId": short_id,
        "realityServerName": REALITY_SERVER_NAME,
        "realityDest": REALITY_DEST,
        "realityFingerprint": REALITY_FINGERPRINT,
        "xrayRuHardening": True,
    }
    if vless_uri:
        meta["vlessUri"] = vless_uri
    return meta


def format_bypass_client_bundle(
    server_host: str,
    config_id: str,
    *,
    port: int = 443,
    name: str | None = None,
) -> tuple[dict[str, Any], str, dict[str, Any], dict[str, Any]]:
    vless_uuid = new_vless_uuid()
    private_key, public_key = generate_reality_keypair()
    short_id = generate_short_id()
    server_cfg, client_text, meta = rebuild_bypass_bundle_from_secrets(
        server_host,
        config_id,
        vless_uuid=vless_uuid,
        private_key=private_key,
        public_key=public_key,
        short_id=short_id,
        port=port,
        name=name,
    )
    secrets = {"private_key": private_key, "short_id": short_id, "vless_uuid": vless_uuid}
    return server_cfg, client_text, meta, secrets
