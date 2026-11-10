<div align="center">

![Hoplyra banner](assets/banner.png)

</div>

> **Self-hosted VPN dashboard.** Your VPS, your rules — no cloud provider or middlemen.

Hoplyra is not just a panel for a single VPN. It is a **multi-hop chain builder**: you compose a route from different protocols and servers, and the system links the hops, configures routing, and delivers **one client config** — at the chain entry.

---

## 🧅 AmneziaWG 3.1 — Default and Stealth

Hoplyra speaks the full **AmneziaWG 3.1** protocol with two one-click modes:

- **Default** — plain AWG 3.1 with no obfuscation, for networks where WireGuard is not filtered
- **Stealth** — automatic obfuscation profile: junk packets (`Jc/Jmin/Jmax`), packet-size shifts (`S1–S4`), magic headers (`H1–H4`), and a CPS mimicry chain (`I1–I5`)

Stealth ships **QUIC / TLS / DNS / SIP / STUN** mimicry profiles out of the box. Advanced 3.1 options are available when you need full control: **`HeaderProtectionKey`**, **`ContentPaddingAddition`**, **`RandomTrailers`**, **`DisableCookies`**.

---

## 🔗 Chains

A typical VPN is one tunnel to one server. Hoplyra lets you **combine multiple protocols into a single route** across your VPS nodes:

- **Mix protocols** — AWG, WireGuard, OpenVPN, Xray, and Tor in one chain, each hop on its own server
- **One config for the client** — connect only to the entry; middle hops and exit are configured for you
- **Tor in the chain** — as a relay hop or as an exit with a changing IP
- **Smart deploy** — background deployment with hop-by-hop progress: gateway, iptables, containers, and inter-server links without manual SSH

<div align="center">

![Example chain AWG → Tor → Xray](assets/chain.png)

</div>

**Common patterns:** `AWG → Tor → Xray`, **WireGuard → Xray → Tor**, **OpenVPN → Tor → AmneziaWG** — and many other combinations.

Background deployments use **multi-process deploy leases** with heartbeats and crash recovery to coordinate workers and recover interrupted jobs.

Multiple configurations can run **side by side on the same server** — each one gets its own **container, interface, port, and subnet**, so protocols never override each other.

The web UI uses the **Ink Amber** dark theme, bento-style cards, and refreshed gold icons.

---

## 🔐 Panel login

The dashboard is protected by a **local administrator account** (not a cloud signup):

- After `make install`, sign in at **https://YOUR_SERVER_IP:8787** (or **https://127.0.0.1:8787**) with **`admin` / `admin`**
- **Change the password** in **Settings** on first use
- Sessions use **HTTP-only cookies** (Secure flag on HTTPS); SSH passwords for VPS are stored **encrypted** in the local SQLite database
- **HTTPS** is enabled automatically — certificates are generated on `make install` (`scripts/generate-ssl.sh`), or use an Nginx/Caddy reverse proxy

---

## 📊 Status

The **Status** tab shows live health of your control host and every added VPS:

- **CPU, memory, disk, load average, uptime**, and network throughput
- Per-server **latency** and container list where available
- **Live polling** (~1s) when the API can reach servers over SSH

Use it to spot overloaded nodes, offline hosts, and whether metrics collection is working before you deploy chains.

<div align="center">

![Status monitoring](assets/status.png)

</div>

---

## 🧦 SOCKS5 proxy

The **Proxy** tab exposes an optional **SOCKS5** server for every active VPN or chain:

- **Purpose** — browser or app proxy through the same path as your VPN/chain (entry → … → exit)
- **When** — config status is **active**; enable/disable **without redeploy**
- **Where in UI** — **Proxy** tab: filter by all routes, chains, or single VPN; collapse/expand cards

<div align="center">

![SOCKS5 proxy flow](assets/proxy.png)

</div>

---

## 🛡 Protocols

| | ID | Protocol | Port |
|:-:|---|----------|------|
| <img src="assets/protocols/badges/wg.png" width="44" alt="WireGuard"> | `wg` | **WireGuard** | 51820/udp |
| <img src="assets/protocols/badges/awg.png" width="44" alt="AmneziaWG"> | `awg` | **AmneziaWG 3.1** | 55424/udp |
| <img src="assets/protocols/badges/openvpn.png" width="44" alt="OpenVPN"> | `openvpn` | **OpenVPN** | 1194/udp or tcp |
| <img src="assets/protocols/badges/xray.png" width="44" alt="Xray"> | `xray` | **Xray VLESS + TLS / REALITY** | 443/tcp |
| <img src="assets/protocols/badges/tor.png" width="44" alt="Tor"> | `tor` | **Tor (SOCKS)** | 9050/tcp |

---

## ⚙️ Installation

Hoplyra is a **server application** with a web UI. Deploy the backend on a Linux server — it serves the dashboard on one port.

**Dependencies** (Debian / Ubuntu example):

```bash
sudo apt install make python3-venv podman podman-compose
```

**Install:**

```bash
git clone https://github.com/Linchevatel/Hoplyra.git
cd Hoplyra
make install
```

Open **https://YOUR_SERVER_IP:8787** (locally: **https://127.0.0.1:8787**) and sign in with **`admin` / `admin`**.

**Container images:**

| Image | Purpose |
|-------|---------|
| `hoplyra-gateway:1` | Xray + Tor + iptables (AWG/WG/OpenVPN chains) |
| `hoplyra-tor:1` | Tor sidecar |
| `hoplyra-xray:1` | Xray relay/exit |
| `hoplyra-wg:1` | WireGuard in a container |
| `hoplyra-openvpn:4` | OpenVPN + PKI |
| `hoplyra-socks:1` | 3proxy sidecar for standalone SOCKS5 |

---

## 💛 Support the project

| | Network | Address |
|:-:|---------|---------|
| <img src="assets/crypto/btc.png" width="44" alt="BTC"> | **Bitcoin** | `bc1q0u4pwuqxg7kt5y4p84lc8zzcawhzr3auzw005z` |
| <img src="assets/crypto/eth.png" width="44" alt="ETH"> | **Ethereum** | `0xD3002c0967a8D28FDF67c2Df8488006e965B9a6A` |
| <img src="assets/crypto/usdt.png" width="44" alt="USDT"> | **TRON (TRC-20)** | `TYUkmupkzCGzkio77Db7PKEDu4JN8JD1eb` |
| <img src="assets/crypto/trx.png" width="44" alt="TRX"> | **TRON** | `TX7yRGo5xT2Mj5NBVhu7bdv58jmStHFuGZ` |

---

## 📄 License

MIT
