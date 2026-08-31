from __future__ import annotations

import random
import re
from dataclasses import dataclass


_AWG2_H_RANGES = (
    "100000-800000",
    "1000000-8000000",
    "10000000-80000000",
    "100000000-800000000",
)

# protocolConstants.h — defaultSpecialJunk1 for AWG 2.0
AWG2_DEFAULT_I1 = (
    "<r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>"
)

_AWG_MESSAGE_INITIATION = 56
_AWG_MESSAGE_RESPONSE = 32
_AWG_MESSAGE_COOKIE_REPLY = 32


def _rand_awg2_packet_sizes() -> tuple[int, int, int, int]:
    used: set[int] = set()
    s1 = random.randint(15, 150)
    used.add(s1)
    while True:
        s2 = random.randint(15, 150)
        if s2 in used or s1 + _AWG_MESSAGE_INITIATION == s2 + _AWG_MESSAGE_RESPONSE:
            continue
        used.add(s2)
        break
    while True:
        s3 = random.randint(0, 64)
        if s3 in used:
            continue
        if s1 + _AWG_MESSAGE_INITIATION == s3 + _AWG_MESSAGE_COOKIE_REPLY:
            continue
        if s2 + _AWG_MESSAGE_RESPONSE == s3 + _AWG_MESSAGE_COOKIE_REPLY:
            continue
        used.add(s3)
        break
    while True:
        s4 = random.randint(0, 20)
        if s4 not in used:
            break
    return s1, s2, s3, s4


def _rand_awg2_headers() -> tuple[str, str, str, str]:
    ranges: list[str] = []
    lower = 5
    upper = 2_147_483_647
    for _ in range(4):
        first = random.randint(lower, upper - 1)
        second = random.randint(first, upper)
        ranges.append(f"{first}-{second}")
        lower = second
    return ranges[0], ranges[1], ranges[2], ranges[3]


@dataclass(frozen=True)
class AwgObfuscationParams:
    jc: int = 5
    jmin: int = 54
    jmax: int = 173
    s1: int = 53
    s2: int = 75
    s3: int = 14
    s4: int = 12
    h1: str = _AWG2_H_RANGES[0]
    h2: str = _AWG2_H_RANGES[1]
    h3: str = _AWG2_H_RANGES[2]
    h4: str = _AWG2_H_RANGES[3]
    i1: str = AWG2_DEFAULT_I1
    i2: str = ""
    i3: str = ""
    i4: str = ""
    i5: str = ""
    random_trailers: bool = False
    disable_cookies: bool = False

    def conf_lines(self) -> str:
        lines = [
            f"Jc = {self.jc}",
            f"Jmin = {self.jmin}",
            f"Jmax = {self.jmax}",
            f"S1 = {self.s1}",
            f"S2 = {self.s2}",
        ]
        if self.s3 > 0 or self.s4 > 0:
            lines.append(f"S3 = {self.s3}")
            lines.append(f"S4 = {self.s4}")
        lines.extend([
            f"H1 = {self.h1}",
            f"H2 = {self.h2}",
            f"H3 = {self.h3}",
            f"H4 = {self.h4}",
        ])
        for idx, value in enumerate((self.i1, self.i2, self.i3, self.i4, self.i5), start=1):
            if value.strip():
                lines.append(f"I{idx} = {value}")
        if self.random_trailers:
            lines.append("RandomTrailers = 1")
        if self.disable_cookies:
            lines.append("DisableCookies = 1")
        return "\n".join(lines)

    def as_meta(self) -> dict[str, int | str]:
        meta: dict[str, int | str] = {
            "jc": self.jc,
            "jmin": self.jmin,
            "jmax": self.jmax,
            "s1": self.s1,
            "s2": self.s2,
            "s3": self.s3,
            "s4": self.s4,
            "h1": self.h1,
            "h2": self.h2,
            "h3": self.h3,
            "h4": self.h4,
            "awgVersion": "3.1" if self.random_trailers or self.disable_cookies else 2,
        }
        if self.i1.strip():
            meta["i1"] = self.i1
        if self.i2.strip():
            meta["i2"] = self.i2
        if self.i3.strip():
            meta["i3"] = self.i3
        if self.i4.strip():
            meta["i4"] = self.i4
        if self.i5.strip():
            meta["i5"] = self.i5
        if self.random_trailers:
            meta["randomTrailers"] = 1
        if self.disable_cookies:
            meta["disableCookies"] = 1
        return meta

    @classmethod
    def from_meta(cls, meta: dict[str, object]) -> AwgObfuscationParams:
        def _h(key: str, default: str) -> str:
            value = meta.get(key, default)
            return str(value)

        def _int(key: str, default: int) -> int:
            value = meta.get(key, default)
            return int(value)  # type: ignore[arg-type]

        def _opt_str(key: str) -> str:
            value = meta.get(key, "")
            return str(value) if value else ""

        return cls(
            jc=_int("jc", 5),
            jmin=_int("jmin", 54),
            jmax=_int("jmax", 173),
            s1=_int("s1", 53),
            s2=_int("s2", 75),
            s3=_int("s3", 14),
            s4=_int("s4", 12),
            h1=_h("h1", _AWG2_H_RANGES[0]),
            h2=_h("h2", _AWG2_H_RANGES[1]),
            h3=_h("h3", _AWG2_H_RANGES[2]),
            h4=_h("h4", _AWG2_H_RANGES[3]),
            i1=_opt_str("i1") or AWG2_DEFAULT_I1,
            i2=_opt_str("i2"),
            i3=_opt_str("i3"),
            i4=_opt_str("i4"),
            i5=_opt_str("i5"),
            random_trailers=bool(meta.get("randomTrailers")),
            disable_cookies=bool(meta.get("disableCookies")),
        )


def generate_awg1_0_params() -> AwgObfuscationParams:
    return AwgObfuscationParams(
        jc=random.randint(4, 6),
        jmin=10,
        jmax=50,
        s1=random.randint(15, 150),
        s2=random.randint(15, 150),
        s3=0,
        s4=0,
        h1=str(random.randint(100000000, 2000000000)),
        h2=str(random.randint(100000000, 2000000000)),
        h3=str(random.randint(100000000, 2000000000)),
        h4=str(random.randint(100000000, 2000000000)),
        i1="",
        i2="",
        i3="",
        i4="",
        i5="",
    )


def generate_awg1_5_params() -> AwgObfuscationParams:
    s1, s2, s3, s4 = _rand_awg2_packet_sizes()
    return AwgObfuscationParams(
        jc=random.randint(4, 6),
        jmin=10,
        jmax=50,
        s1=s1,
        s2=s2,
        s3=s3,
        s4=s4,
        h1=str(random.randint(100000000, 2000000000)),
        h2=str(random.randint(100000000, 2000000000)),
        h3=str(random.randint(100000000, 2000000000)),
        h4=str(random.randint(100000000, 2000000000)),
        i1="",
        i2="",
        i3="",
        i4="",
        i5="",
    )


def generate_awg2_params() -> AwgObfuscationParams:
    jc = random.randint(4, 6)
    jmin = 10
    jmax = 50
    s1, s2, s3, s4 = _rand_awg2_packet_sizes()
    h1, h2, h3, h4 = _rand_awg2_headers()
    return AwgObfuscationParams(
        jc=jc,
        jmin=jmin,
        jmax=jmax,
        s1=s1,
        s2=s2,
        s3=s3,
        s4=s4,
        h1=h1,
        h2=h2,
        h3=h3,
        h4=h4,
        i1=AWG2_DEFAULT_I1,
    )


def generate_awg3_1_params() -> AwgObfuscationParams:
    jc = random.randint(4, 6)
    jmin = 10
    jmax = 50
    s1, s2, s3, s4 = _rand_awg2_packet_sizes()
    return AwgObfuscationParams(
        jc=jc,
        jmin=jmin,
        jmax=jmax,
        s1=s1,
        s2=s2,
        s3=s3,
        s4=s4,
        h1=str(random.randint(100000000, 2000000000)),
        h2=str(random.randint(100000000, 2000000000)),
        h3=str(random.randint(100000000, 2000000000)),
        h4=str(random.randint(100000000, 2000000000)),
        i1=AWG2_DEFAULT_I1,
        random_trailers=True,
        disable_cookies=True,
    )


def generate_awg_params(version: str = "awg2.0") -> AwgObfuscationParams:
    v = (version or "awg2.0").lower().strip()
    if v in ("awg3.1", "3.1", "3"):
        return generate_awg3_1_params()
    elif v in ("awg1.0", "1.0", "1", "awg"):
        return generate_awg1_0_params()
    elif v in ("awg1.5", "1.5"):
        return generate_awg1_5_params()
    return generate_awg2_params()


DEFAULT_AWG_PARAMS = AwgObfuscationParams()


LEGACY_AWG_PARAMS = AwgObfuscationParams(
    jc=5,
    jmin=54,
    jmax=173,
    s1=53,
    s2=75,
    s3=14,
    s4=12,
    h1="1020325451",
    h2="3288052141",
    h3="1766607858",
    h4="2528465083",
    i1="",
    i2="",
    i3="",
    i4="",
    i5="",
)


def parse_awg_params_from_conf(conf: str) -> AwgObfuscationParams | None:
    kv: dict[str, str] = {}
    for line in conf.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if " = " not in line:
            continue
        key, value = line.split(" = ", 1)
        kv[key.strip()] = value.strip()

    required = ("Jc", "Jmin", "Jmax", "S1", "S2", "S3", "S4", "H1", "H2", "H3", "H4")
    if not all(kv.get(key) for key in required):
        return None

    return AwgObfuscationParams(
        jc=int(kv["Jc"]),
        jmin=int(kv["Jmin"]),
        jmax=int(kv["Jmax"]),
        s1=int(kv["S1"]),
        s2=int(kv["S2"]),
        s3=int(kv.get("S3", 0)),
        s4=int(kv.get("S4", 0)),
        h1=kv["H1"],
        h2=kv["H2"],
        h3=kv["H3"],
        h4=kv["H4"],
        i1=kv.get("I1", ""),
        i2=kv.get("I2", ""),
        i3=kv.get("I3", ""),
        i4=kv.get("I4", ""),
        i5=kv.get("I5", ""),
        random_trailers=kv.get("RandomTrailers") in ("1", "on", "true", "True"),
        disable_cookies=kv.get("DisableCookies") in ("1", "on", "true", "True"),
    )


def _parse_kv_conf(conf: str) -> dict[str, str]:
    kv: dict[str, str] = {}
    for line in conf.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("["):
            continue
        if " = " not in line:
            continue
        key, value = line.split(" = ", 1)
        kv[key.strip()] = value.strip()
    return kv


def _strip_awg_obfuscation(conf: str) -> str:
    drop = re.compile(
        r"^(Jc|Jmin|Jmax|S[1-4]|H[1-4]|I[1-5])\s*=",
        re.MULTILINE,
    )
    lines = [line for line in conf.splitlines() if not drop.match(line.strip())]
    return "\n".join(lines).strip() + "\n"


def ensure_awg2_obfuscation_in_conf(
    conf: str,
    params: AwgObfuscationParams,
) -> str:
    if parse_awg_params_from_conf(conf):
        base = _strip_awg_obfuscation(conf)
    else:
        base = conf.rstrip() + "\n"
    kv = _parse_kv_conf(base)
    interface_lines: list[str] = ["[Interface]"]
    for key in ("PrivateKey", "Address", "ListenPort", "DNS", "MTU"):
        if kv.get(key):
            interface_lines.append(f"{key} = {kv[key]}")
    interface_lines.append(params.conf_lines())
    for key in ("PostUp", "PostDown"):
        if kv.get(key):
            interface_lines.append(f"{key} = {kv[key]}")

    peer_lines: list[str] = ["", "[Peer]"]
    for key in ("PublicKey", "PresharedKey", "AllowedIPs", "Endpoint", "PersistentKeepalive"):
        if kv.get(key):
            peer_lines.append(f"{key} = {kv[key]}")

    return "\n".join(interface_lines + peer_lines).strip() + "\n"


def build_awg_server_conf(
    *,
    server_priv: str,
    client_pub: str,
    client_ip: str,
    listen_port: int,
    post_up: str,
    post_down: str,
    server_ip_cidr: str = "10.9.1.1/24",
    params: AwgObfuscationParams | None = None,
    preshared_key: str | None = None,
) -> str:
    awg = params or generate_awg2_params()
    psk_line = f"PresharedKey = {preshared_key}\n" if preshared_key else ""
    return f"""[Interface]
Address = {server_ip_cidr}
ListenPort = {listen_port}
PrivateKey = {server_priv}
{awg.conf_lines()}
PostUp = {post_up}
PostDown = {post_down}

[Peer]
PublicKey = {client_pub}
{psk_line}AllowedIPs = {client_ip}/32
"""


def build_awg_client_conf(
    *,
    client_priv: str,
    server_pub: str,
    server_host: str,
    listen_port: int,
    client_ip: str = "10.9.1.2/32",
    dns: str = "1.1.1.1, 8.8.8.8",
    params: AwgObfuscationParams | None = None,
    preshared_key: str | None = None,
) -> str:
    awg = params or generate_awg2_params()
    psk_line = f"PresharedKey = {preshared_key}\n" if preshared_key else ""
    return f"""[Interface]
PrivateKey = {client_priv}
Address = {client_ip}
DNS = {dns}
{awg.conf_lines()}

[Peer]
PublicKey = {server_pub}
{psk_line}Endpoint = {server_host}:{listen_port}
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
"""
