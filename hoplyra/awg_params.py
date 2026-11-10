from __future__ import annotations

import random
import re
import secrets
from dataclasses import dataclass
from typing import Any

from hoplyra.hosts import endpoint_host

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

# WireGuard unpadded message sizes
_WG_MSG_INITIATION = 148
_WG_MSG_RESPONSE = 92
_WG_MSG_COOKIE_REPLY = 64

# Handshake message size differences (collision offsets)
# 148 + S1 == 92 + S2 => S2 == S1 + 56
_INIT_TO_RESPONSE = _WG_MSG_INITIATION - _WG_MSG_RESPONSE  # 56
# 148 + S1 == 64 + S3 => S3 == S1 + 84
_INIT_TO_COOKIE = _WG_MSG_INITIATION - _WG_MSG_COOKIE_REPLY  # 84
# 92 + S2 == 64 + S3 => S3 == S2 + 28
_RESPONSE_TO_COOKIE = _WG_MSG_RESPONSE - _WG_MSG_COOKIE_REPLY  # 28

# Padding limits
S1_MAX = 1132
S2_MAX = 1188
S3_MAX = 1132
S4_MAX = 32
MIN_S_WITH_HEADER_PROTECTION = 12

JC_MIN = 0
JC_MAX = 128

SUPPORTED_CPS_PROFILES = ("quic", "tls", "dns", "sip", "stun")

CPS_TAG_RE = re.compile(
    r"^((<(b 0x[0-9a-fA-F]*|t|c|r \d+|rc \d+|rd \d+|d|ds|dz)>)+)$"
)


class AwgValidationError(ValueError):
    """Raised when AmneziaWG obfuscation parameters violate protocol constraints."""
    pass


def parse_h_range(val: str | int) -> tuple[int, int]:
    """Parse integer or range string 'lo-hi' into (lo, hi)."""
    s = str(val).strip()
    if "-" in s:
        parts = s.split("-", 1)
        return int(parts[0].strip()), int(parts[1].strip())
    n = int(s)
    return n, n


def parse_range_spec(val: str | int) -> tuple[int, int]:
    """Parse integer or range string 'lo-hi' into (lo, hi)."""
    s = str(val).strip()
    if "-" in s:
        parts = s.split("-", 1)
        return int(parts[0].strip()), int(parts[1].strip())
    n = int(s)
    return n, n


def generate_cps_profile(profile: str = "quic") -> tuple[str, str, str, str, str]:
    """
    Generate CPS I1-I5 signatures mimicking a chosen protocol.
    Supported: quic, tls, dns, sip, stun.
    """
    p = (profile or "quic").lower().strip()
    if p in ("quic", "quic_initial", "http3"):
        # QUIC RFC 9000 Initial and short/long header bursts
        dcid = secrets.token_hex(8)
        i1 = f"<b 0xc00000000108{dcid}><rc {random.randint(10, 20)}><t><r {random.randint(32, 64)}>"
        i2 = f"<b 0xd00000000108{secrets.token_hex(8)}><rc {random.randint(8, 16)}><r {random.randint(32, 64)}>"
        i3 = f"<b 0xe00000000108{secrets.token_hex(8)}><t><r {random.randint(24, 48)}>"
        i4 = f"<b 0xf00000000108{secrets.token_hex(8)}><rc {random.randint(12, 24)}><r {random.randint(32, 64)}>"
        i5 = f"<b 0xc00000000108{secrets.token_hex(8)}><r {random.randint(48, 96)}>"
        return i1, i2, i3, i4, i5

    if p in ("tls", "tls13", "https"):
        # TLS 1.3 ClientHello record (RFC 8446)
        rand_bytes = secrets.token_hex(16)
        i1 = f"<b 0x16030300{random.randint(150, 240):02x}010000{random.randint(100, 200):02x}0303{rand_bytes}><rc {random.randint(16, 32)}><t><r {random.randint(32, 64)}>"
        i2 = f"<b 0x16030300{random.randint(60, 120):02x}020000{random.randint(40, 90):02x}0303{secrets.token_hex(16)}><r {random.randint(24, 48)}>"
        i3 = f"<b 0x140303000101><r {random.randint(16, 32)}><t>"
        i4 = f"<b 0x17030300{random.randint(40, 100):02x}><r {random.randint(32, 64)}>"
        i5 = f"<b 0x17030300{random.randint(40, 100):02x}><r {random.randint(48, 80)}>"
        return i1, i2, i3, i4, i5

    if p in ("dns", "dns_query"):
        # DNS Query (RFC 1035) with query flags and label encoding
        i1 = f"<b 0x{secrets.token_hex(2)}010000010000000000000669636c6f756403636f6d0000010001><rc 8><rd 4><r 32>"
        i2 = f"<b 0x{secrets.token_hex(2)}0100000100000000000007676f6f676c6503636f6d0000010001><rc 6><rd 4><r 24>"
        i3 = f"<b 0x{secrets.token_hex(2)}010000010000000000000a636c6f7564666c61726503636f6d00001c0001><r 28>"
        i4 = f"<b 0x{secrets.token_hex(2)}01000001000000000000096170706c652d646e73036e65740000010001><rc 8><r 24>"
        i5 = f"<b 0x{secrets.token_hex(2)}01000001000000000000086d6963726f736f667403636f6d0000010001><r 32>"
        return i1, i2, i3, i4, i5

    if p in ("sip", "voip"):
        # SIP REGISTER / OPTIONS (RFC 3261)
        i1 = f"<b 0x4f5054494f4e53207369703a><rc {random.randint(10, 18)}><rd {random.randint(4, 8)}><t><r {random.randint(24, 48)}>"
        i2 = f"<b 0x5245474953544552207369703a><rc {random.randint(10, 16)}><rd 4><t><r {random.randint(20, 40)}>"
        i3 = f"<b 0x494e56495445207369703a><rc {random.randint(8, 14)}><rd 4><t><r {random.randint(24, 48)}>"
        i4 = f"<b 0x41434b207369703a><rc {random.randint(10, 16)}><rd 4><r {random.randint(16, 32)}>"
        i5 = f"<b 0x425945207369703a><rc {random.randint(8, 12)}><rd 4><r {random.randint(16, 32)}>"
        return i1, i2, i3, i4, i5

    if p in ("stun", "turn"):
        # STUN Binding Request / Allocate (RFC 8489, 8656) with Magic Cookie 0x2112a442
        i1 = f"<b 0x000100002112a442{secrets.token_hex(12)}><r {random.randint(24, 48)}>"
        i2 = f"<b 0x000300002112a442{secrets.token_hex(12)}><r {random.randint(28, 56)}>"
        i3 = f"<b 0x000100082112a442{secrets.token_hex(12)}00060004{secrets.token_hex(4)}><r {random.randint(20, 40)}>"
        i4 = f"<b 0x000100002112a442{secrets.token_hex(12)}><t><r {random.randint(24, 40)}>"
        i5 = f"<b 0x000300082112a442{secrets.token_hex(12)}00060004{secrets.token_hex(4)}><r {random.randint(24, 48)}>"
        return i1, i2, i3, i4, i5

    # Fallback to QUIC mimicry
    return generate_cps_profile("quic")


def _rand_awg2_packet_sizes(with_hpk: bool = False) -> tuple[int, int, int, int]:
    min_s = MIN_S_WITH_HEADER_PROTECTION if with_hpk else 15
    while True:
        s1 = random.randint(min_s, 150)
        s2 = random.randint(min_s, 150)
        if s1 == s2 or s1 + _INIT_TO_RESPONSE == s2:
            continue
        min_s3 = MIN_S_WITH_HEADER_PROTECTION if with_hpk else 0
        s3 = random.randint(min_s3, 64)
        if s3 == s1 + _INIT_TO_COOKIE or s3 == s2 + _RESPONSE_TO_COOKIE:
            continue
        min_s4 = MIN_S_WITH_HEADER_PROTECTION if with_hpk else 0
        max_s4 = S4_MAX if with_hpk else 20
        s4 = random.randint(min_s4, max_s4)
        return s1, s2, s3, s4


_HEADER_ZONES = (
    (10_000, 450_000_000),
    (500_000_000, 950_000_000),
    (1_000_000_000, 1_450_000_000),
    (1_500_000_000, 2_000_000_000),
)


def _rand_awg2_headers() -> tuple[str, str, str, str]:
    ranges: list[str] = []
    for z_min, z_max in _HEADER_ZONES:
        lo = random.randint(z_min, z_max - 50_000)
        hi = lo + random.randint(1_000, 40_000)
        ranges.append(f"{lo}-{hi}")
    return ranges[0], ranges[1], ranges[2], ranges[3]


@dataclass(frozen=True)
class AwgObfuscationParams:
    obfuscation_mode: str = "auto"
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
    header_protection_key: str = ""
    content_padding_addition: str = ""

    def conf_lines(self) -> str:
        if self.obfuscation_mode == "none":
            return ""
        lines = [
            f"Jc = {self.jc}",
            f"Jmin = {self.jmin}",
            f"Jmax = {self.jmax}",
            f"S1 = {self.s1}",
            f"S2 = {self.s2}",
        ]
        if self.s3 > 0 or self.s4 > 0 or self.header_protection_key.strip():
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
                lines.append(f"I{idx} = {value.strip()}")
        if self.header_protection_key.strip():
            lines.append(f"HeaderProtectionKey = {self.header_protection_key.strip()}")
        if self.content_padding_addition.strip():
            lines.append(f"ContentPaddingAddition = {self.content_padding_addition.strip()}")
        if self.random_trailers:
            lines.append("RandomTrailers = 1")
        if self.disable_cookies:
            lines.append("DisableCookies = 1")
        return "\n".join(lines)

    def as_meta(self) -> dict[str, Any]:
        if self.obfuscation_mode == "none":
            return {"obfuscationMode": "none"}
        is_v31 = bool(
            self.random_trailers
            or self.disable_cookies
            or self.header_protection_key.strip()
            or self.content_padding_addition.strip()
        )
        meta: dict[str, Any] = {
            "obfuscationMode": self.obfuscation_mode,
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
            "awgVersion": "3.1" if is_v31 else 2,
        }
        # Empty CPS values are explicit overrides, not missing defaults.
        for idx, value in enumerate((self.i1, self.i2, self.i3, self.i4, self.i5), start=1):
            meta[f"i{idx}"] = value.strip()
        if self.header_protection_key.strip():
            meta["headerProtectionKey"] = self.header_protection_key.strip()
        if self.content_padding_addition.strip():
            meta["contentPaddingAddition"] = self.content_padding_addition.strip()
        if self.random_trailers:
            meta["randomTrailers"] = 1
        if self.disable_cookies:
            meta["disableCookies"] = 1
        return meta

    @classmethod
    def from_meta(cls, meta: dict[str, object]) -> AwgObfuscationParams:
        def _h(key: str, default: str) -> str:
            value = meta.get(key, meta.get(key.upper(), default))
            return str(value)

        def _int(key: str, default: int) -> int:
            value = meta.get(key, meta.get(key.capitalize(), default))
            if value is None or value == "":
                return default
            return int(value)  # type: ignore[arg-type]

        def _opt_str(key: str) -> str:
            value = meta.get(key, "")
            return str(value).strip() if value else ""

        rt = meta.get("randomTrailers", meta.get("random_trailers", meta.get("RandomTrailers", False)))
        dc = meta.get("disableCookies", meta.get("disable_cookies", meta.get("DisableCookies", False)))

        hpk = (
            _opt_str("headerProtectionKey")
            or _opt_str("header_protection_key")
            or _opt_str("HeaderProtectionKey")
        )
        cpa = (
            _opt_str("contentPaddingAddition")
            or _opt_str("content_padding_addition")
            or _opt_str("ContentPaddingAddition")
        )

        return cls(
            obfuscation_mode=str(meta.get("obfuscationMode", "auto")),
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
            i1=(_opt_str("i1") if "i1" in meta else _opt_str("I1") if "I1" in meta else AWG2_DEFAULT_I1),
            i2=_opt_str("i2") or _opt_str("I2"),
            i3=_opt_str("i3") or _opt_str("I3"),
            i4=_opt_str("i4") or _opt_str("I4"),
            i5=_opt_str("i5") or _opt_str("I5"),
            random_trailers=bool(rt) and str(rt).lower() not in ("0", "false", "off"),
            disable_cookies=bool(dc) and str(dc).lower() not in ("0", "false", "off"),
            header_protection_key=hpk,
            content_padding_addition=cpa,
        )


def validate_awg_params(
    params: AwgObfuscationParams | dict[str, Any],
    awg_version: str | None = None,
) -> list[str]:
    """
    Pure validation function for AmneziaWG obfuscation parameters.
    Checks:
      - Jc in 0..128, Jmin >= 0, Jmax >= 0, Jmin < Jmax.
      - S1 (0..1132), S2 (0..1188), S3 (0..1132), S4 (0..32).
      - S1..S4 >= 12 when HeaderProtectionKey is present.
      - Size collision checks (S1+56 == S2, S1+84 == S3, S2+28 == S3).
      - HeaderProtectionKey format (32 bytes hex / 64 hex chars).
      - H1-H4 uniqueness (no overlapping ranges) and separation from standard WireGuard 1-4.
      - CPS I1-I5 tag syntax.
      - ContentPaddingAddition syntax/range.
      - Version exclusions (v3.1 features rejected if version is not 3.1; S3/S4/I rejected on v1.0).
    Returns a list of human-readable error strings (empty if valid).
    """
    if (isinstance(params, AwgObfuscationParams) and params.obfuscation_mode == "none") or (isinstance(params, dict) and params.get("obfuscationMode") == "none"):
        return []
    errors: list[str] = []

    def _get_val(key_camel: str, key_snake: str, key_pascal: str, default: Any = None) -> Any:
        if isinstance(params, AwgObfuscationParams):
            return getattr(params, key_snake, default)
        if isinstance(params, dict):
            if key_camel in params:
                return params[key_camel]
            if key_snake in params:
                return params[key_snake]
            if key_pascal in params:
                return params[key_pascal]
        return default

    def _to_int(val: Any, default: int) -> int:
        if val is None or val == "":
            return default
        try:
            return int(val)
        except (ValueError, TypeError):
            errors.append("Ожидалось целое число")
            return default

    jc = _to_int(_get_val("jc", "jc", "Jc", 5), 5)
    jmin = _to_int(_get_val("jmin", "jmin", "Jmin", 54), 54)
    jmax = _to_int(_get_val("jmax", "jmax", "Jmax", 173), 173)
    s1 = _to_int(_get_val("s1", "s1", "S1", 53), 53)
    s2 = _to_int(_get_val("s2", "s2", "S2", 75), 75)
    s3 = _to_int(_get_val("s3", "s3", "S3", 14), 14)
    s4 = _to_int(_get_val("s4", "s4", "S4", 12), 12)

    h1 = str(_get_val("h1", "h1", "H1", "") or "").strip()
    h2 = str(_get_val("h2", "h2", "H2", "") or "").strip()
    h3 = str(_get_val("h3", "h3", "H3", "") or "").strip()
    h4 = str(_get_val("h4", "h4", "H4", "") or "").strip()

    i1 = str(_get_val("i1", "i1", "I1", "") or "").strip()
    i2 = str(_get_val("i2", "i2", "I2", "") or "").strip()
    i3 = str(_get_val("i3", "i3", "I3", "") or "").strip()
    i4 = str(_get_val("i4", "i4", "I4", "") or "").strip()
    i5 = str(_get_val("i5", "i5", "I5", "") or "").strip()

    rt_raw = _get_val("randomTrailers", "random_trailers", "RandomTrailers", False)
    dc_raw = _get_val("disableCookies", "disable_cookies", "DisableCookies", False)
    random_trailers = bool(rt_raw) and str(rt_raw).lower() not in ("0", "false", "off")
    disable_cookies = bool(dc_raw) and str(dc_raw).lower() not in ("0", "false", "off")

    hpk = str(_get_val("headerProtectionKey", "header_protection_key", "HeaderProtectionKey", "") or "").strip()
    cpa = str(_get_val("contentPaddingAddition", "content_padding_addition", "ContentPaddingAddition", "") or "").strip()

    # 1. Version compatibility and mutual exclusions
    ver_str = str(awg_version or "").lower().strip()
    if ver_str:
        is_v31 = ver_str in ("awg3.1", "3.1", "3")
        is_v20 = ver_str in ("awg2.0", "2.0", "2")
        is_v15 = ver_str in ("awg1.5", "1.5")
        is_v10 = ver_str in ("awg1.0", "1.0", "1", "awg")

        if not is_v31:
            if random_trailers:
                errors.append(f"RandomTrailers поддерживается только в AmneziaWG 3.1 (версия: {awg_version})")
            if disable_cookies:
                errors.append(f"DisableCookies поддерживается только в AmneziaWG 3.1 (версия: {awg_version})")
            if hpk:
                errors.append(f"HeaderProtectionKey поддерживается только в AmneziaWG 3.1 (версия: {awg_version})")
            if cpa:
                errors.append(f"ContentPaddingAddition поддерживается только в AmneziaWG 3.1 (версия: {awg_version})")

        if is_v10:
            if s3 > 0 or s4 > 0:
                errors.append("S3 и S4 не поддерживаются в AmneziaWG 1.0")
            if any((i1, i2, i3, i4, i5)):
                errors.append("CPS (I1-I5) не поддерживаются в AmneziaWG 1.0")
        elif is_v15:
            if s3 > 0 or s4 > 0:
                errors.append("S3 и S4 не поддерживаются в AmneziaWG 1.5")

    # 2. HeaderProtectionKey validation and Nonce S floor
    has_hpk = bool(hpk)
    if has_hpk:
        is_hex_64 = len(hpk) == 64 and all(c in "0123456789abcdefABCDEF" for c in hpk)
        is_b64_44 = len(hpk) == 44 and bool(re.match(r"^[A-Za-z0-9+/]{43}=$", hpk))
        if not (is_hex_64 or is_b64_44):
            errors.append(
                f"HeaderProtectionKey должен быть 32-байтным hex-ключом (64 hex-символа), неверный формат"
            )
        if s1 < MIN_S_WITH_HEADER_PROTECTION:
            errors.append(
                f"При включённом HeaderProtectionKey значение S1 должно быть >= {MIN_S_WITH_HEADER_PROTECTION} (получено: {s1})"
            )
        if s2 < MIN_S_WITH_HEADER_PROTECTION:
            errors.append(
                f"При включённом HeaderProtectionKey значение S2 должно быть >= {MIN_S_WITH_HEADER_PROTECTION} (получено: {s2})"
            )
        if s3 < MIN_S_WITH_HEADER_PROTECTION:
            errors.append(
                f"При включённом HeaderProtectionKey значение S3 должно быть >= {MIN_S_WITH_HEADER_PROTECTION} (получено: {s3})"
            )
        if s4 < MIN_S_WITH_HEADER_PROTECTION:
            errors.append(
                f"При включённом HeaderProtectionKey значение S4 должно быть >= {MIN_S_WITH_HEADER_PROTECTION} (получено: {s4})"
            )

    # 3. S bounds
    if s1 < 0 or s1 > S1_MAX:
        errors.append(f"S1 ({s1}) вне диапазона 0..{S1_MAX}")
    if s2 < 0 or s2 > S2_MAX:
        errors.append(f"S2 ({s2}) вне диапазона 0..{S2_MAX}")
    if s3 < 0 or s3 > S3_MAX:
        errors.append(f"S3 ({s3}) вне диапазона 0..{S3_MAX}")
    if s4 < 0 or s4 > S4_MAX:
        errors.append(f"S4 ({s4}) вне диапазона 0..{S4_MAX}")

    # 4. Message size collision checks
    if s1 + _INIT_TO_RESPONSE == s2:
        errors.append(
            f"Коллизия размеров пакетов: S1 + {_INIT_TO_RESPONSE} == S2 ({s1} + {_INIT_TO_RESPONSE} = {s2})"
        )
    if s3 > 0 and s1 + _INIT_TO_COOKIE == s3:
        errors.append(
            f"Коллизия размеров пакетов: S1 + {_INIT_TO_COOKIE} == S3 ({s1} + {_INIT_TO_COOKIE} = {s3})"
        )
    if s3 > 0 and s2 + _RESPONSE_TO_COOKIE == s3:
        errors.append(
            f"Коллизия размеров пакетов: S2 + {_RESPONSE_TO_COOKIE} == S3 ({s2} + {_RESPONSE_TO_COOKIE} = {s3})"
        )

    # 5. Jc / Jmin / Jmax
    if jc < JC_MIN or jc > JC_MAX:
        errors.append(f"Jc ({jc}) вне допустимого диапазона {JC_MIN}..{JC_MAX}")
    if jmin < 0:
        errors.append(f"Jmin ({jmin}) не может быть отрицательным")
    if jmax < 0:
        errors.append(f"Jmax ({jmax}) не может быть отрицательным")
    if jmin >= jmax:
        errors.append(f"Jmin ({jmin}) должен быть строго меньше Jmax ({jmax})")

    # 6. H1..H4 range bounds, uniqueness and distinction from standard WireGuard 1-4
    h_fields = [("H1", h1), ("H2", h2), ("H3", h3), ("H4", h4)]
    parsed_headers: list[tuple[str, int, int]] = []
    for h_name, h_val in h_fields:
        if not h_val:
            errors.append(f"Заголовок {h_name} не задан")
            continue
        try:
            lo, hi = parse_h_range(h_val)
            if lo < 0 or hi > 4_294_967_295:
                errors.append(f"{h_name} ({h_val}) выходит за границы 32-битного диапазона 0..4294967295")
            elif lo > hi:
                errors.append(f"{h_name} ({h_val}) некорректен: нижняя граница {lo} больше верхней {hi}")
            else:
                parsed_headers.append((h_name, lo, hi))
                # If HeaderProtectionKey is NOT set, message types 1-4 on the wire collide with WireGuard
                if not has_hpk and not (hi < 1 or lo > 4):
                    errors.append(f"Заголовок {h_name} ({h_val}) перекрывается со стандартными типами WireGuard 1-4")
        except Exception as exc:
            errors.append(f"Некорректный заголовок {h_name} ({h_val}): {exc}")

    # Check pairwise non-overlap among H1-H4
    for i in range(len(parsed_headers)):
        for j in range(i + 1, len(parsed_headers)):
            n1, lo1, hi1 = parsed_headers[i]
            n2, lo2, hi2 = parsed_headers[j]
            # Overlap: not (hi1 < lo2 or hi2 < lo1)
            if not (hi1 < lo2 or hi2 < lo1):
                errors.append(f"Заголовки {n1} ({h_fields[i][1]}) и {n2} ({h_fields[j][1]}) перекрываются")

    # 7. ContentPaddingAddition range check
    if cpa:
        try:
            c_lo, c_hi = parse_range_spec(cpa)
            if c_lo < 0 or c_hi < c_lo:
                errors.append(f"Некорректный диапазон ContentPaddingAddition: {cpa}")
        except Exception as exc:
            errors.append(f"Некорректный формат ContentPaddingAddition ({cpa}): {exc}")

    # 8. CPS I1..I5 tag syntax
    for idx, i_val in enumerate((i1, i2, i3, i4, i5), start=1):
        if i_val:
            if not CPS_TAG_RE.match(i_val):
                errors.append(f"Неверный синтаксис CPS в I{idx}: {i_val}")

    return errors


def check_awg_params_valid(
    params: AwgObfuscationParams | dict[str, Any],
    awg_version: str | None = None,
) -> None:
    """Raise AwgValidationError if parameters violate compatibility rules."""
    errors = validate_awg_params(params, awg_version=awg_version)
    if errors:
        raise AwgValidationError("; ".join(errors))


def generate_awg1_0_params() -> AwgObfuscationParams:
    while True:
        s1 = random.randint(15, 150)
        s2 = random.randint(15, 150)
        if s1 != s2 and s1 + _INIT_TO_RESPONSE != s2:
            break
    h1, h2, h3, h4 = _rand_awg2_headers()
    return AwgObfuscationParams(
        jc=random.randint(4, 6),
        jmin=10,
        jmax=50,
        s1=s1,
        s2=s2,
        s3=0,
        s4=0,
        h1=h1,
        h2=h2,
        h3=h3,
        h4=h4,
        i1="",
        i2="",
        i3="",
        i4="",
        i5="",
    )


def generate_awg1_5_params() -> AwgObfuscationParams:
    while True:
        s1 = random.randint(15, 150)
        s2 = random.randint(15, 150)
        if s1 != s2 and s1 + _INIT_TO_RESPONSE != s2:
            break
    h1, h2, h3, h4 = _rand_awg2_headers()
    return AwgObfuscationParams(
        jc=random.randint(4, 6),
        jmin=10,
        jmax=50,
        s1=s1,
        s2=s2,
        s3=0,
        s4=0,
        h1=h1,
        h2=h2,
        h3=h3,
        h4=h4,
        i1="",
        i2="",
        i3="",
        i4="",
        i5="",
    )


def generate_awg2_params(profile: str = "quic") -> AwgObfuscationParams:
    jc = random.randint(4, 6)
    jmin = 10
    jmax = 50
    s1, s2, s3, s4 = _rand_awg2_packet_sizes(with_hpk=False)
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


def generate_awg3_1_params(
    *,
    profile: str = "quic",
    header_protection: bool = True,
    content_padding: bool = True,
    random_trailers: bool = True,
    disable_cookies: bool = True,
) -> AwgObfuscationParams:
    jc = random.randint(4, 6)
    jmin = 10
    jmax = 50
    s1, s2, s3, s4 = _rand_awg2_packet_sizes(with_hpk=header_protection)
    h1, h2, h3, h4 = _rand_awg2_headers()
    i1, i2, i3, i4, i5 = generate_cps_profile(profile)
    hpk = secrets.token_hex(32) if header_protection else ""
    cpa = "16-64" if content_padding else ""

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
        i1=i1,
        i2=i2,
        i3=i3,
        i4=i4,
        i5=i5,
        random_trailers=random_trailers,
        disable_cookies=disable_cookies,
        header_protection_key=hpk,
        content_padding_addition=cpa,
    )


def generate_auto_awg_params(
    version: str = "awg3.1",
    overrides: dict[str, Any] | None = None,
) -> AwgObfuscationParams:
    """
    Generate a complete, guaranteed-valid obfuscation profile for AmneziaWG.
    - Default behavior requires choosing ONLY the AWG version.
    - For v3.1: full parameter set (HeaderProtectionKey, ContentPaddingAddition,
      RandomTrailers, DisableCookies per rules, safe Jc/Jmin/Jmax, safe S1-S4 >= 12,
      unique non-overlapping H1-H4, and a random CPS mimicry profile from
      QUIC/TLS/DNS/SIP/STUN).
    - For v2.0, v1.5, v1.0: only the parameters supported by each version.
    - If `overrides` dict is provided, manual values take precedence (advanced mode)
      and are strictly validated against compatibility rules.
    """
    v = (version or "awg3.1").lower().strip()
    is_v31 = v in ("awg3.1", "3.1", "3")
    is_v10 = v in ("awg1.0", "1.0", "1", "awg")
    is_v15 = v in ("awg1.5", "1.5")

    overrides = dict(overrides or {})
    mode = overrides.pop("obfuscationMode", "auto")
    if mode == "none":
        return AwgObfuscationParams(obfuscation_mode="none")
    if mode == "custom":
        candidate = AwgObfuscationParams.from_meta({**overrides, "obfuscationMode": "custom"})
        check_awg_params_valid(candidate, awg_version=version)
        return candidate
    # Canonicalize aliases before merging so explicit values beat generated defaults.
    aliases = {
        "header_protection_key": "headerProtectionKey",
        "content_padding_addition": "contentPaddingAddition",
        "random_trailers": "randomTrailers", "disable_cookies": "disableCookies",
    }
    for key in list(overrides):
        canonical = aliases.get(key, key[0].lower() + key[1:] if key else key)
        if canonical != key:
            overrides[canonical] = overrides.pop(key)
    profile_choice = overrides.pop("profile", None) or random.choice(SUPPORTED_CPS_PROFILES)
    if profile_choice not in SUPPORTED_CPS_PROFILES:
        raise AwgValidationError("Неизвестный CPS профиль")

    if is_v31:
        # For auto v3.1 profile: enable random trailers, header protection, content padding,
        # disableCookies=False by default (safer under NAT load per Architect rules).
        base = generate_awg3_1_params(
            profile=profile_choice,
            header_protection=True,
            content_padding=True,
            random_trailers=True,
            disable_cookies=False,
        )
    elif is_v10:
        base = generate_awg1_0_params()
    elif is_v15:
        base = generate_awg1_5_params()
    else:
        base = generate_awg2_params(profile=profile_choice)

    if not overrides:
        return base

    # Apply manual overrides on top of base auto-profile
    merged = base.as_meta()
    for k, val in overrides.items():
        if val is not None:
            merged[k] = val

    try:
        candidate = AwgObfuscationParams.from_meta(merged)
    except (ValueError, TypeError) as exc:
        raise AwgValidationError("Некорректный тип параметра AWG") from exc
    check_awg_params_valid(candidate, awg_version=v)
    return candidate


def generate_awg_params(
    version: str = "awg2.0",
    profile: str = "quic",
) -> AwgObfuscationParams:
    v = (version or "awg2.0").lower().strip()
    if v in ("awg3.1", "3.1", "3"):
        return generate_awg3_1_params(profile=profile)
    elif v in ("awg1.0", "1.0", "1", "awg"):
        return generate_awg1_0_params()
    elif v in ("awg1.5", "1.5"):
        return generate_awg1_5_params()
    return generate_awg2_params(profile=profile)


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

    required = ("Jc", "Jmin", "Jmax", "S1", "S2", "H1", "H2", "H3", "H4")
    if not all(kv.get(key) for key in required):
        return None

    hpk = kv.get("HeaderProtectionKey", "")
    cpa = kv.get("ContentPaddingAddition", "")
    rt = kv.get("RandomTrailers") in ("1", "on", "true", "True")
    dc = kv.get("DisableCookies") in ("1", "on", "true", "True")

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
        random_trailers=rt,
        disable_cookies=dc,
        header_protection_key=hpk,
        content_padding_addition=cpa,
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
        r"^(Jc|Jmin|Jmax|S[1-4]|H[1-4]|I[1-5]|HeaderProtectionKey|ContentPaddingAddition|RandomTrailers|DisableCookies)\s*=",
        re.IGNORECASE | re.MULTILINE,
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
{psk_line}Endpoint = {endpoint_host(server_host)}:{listen_port}
AllowedIPs = 0.0.0.0/0, ::/0
PersistentKeepalive = 25
"""
