FROM docker.io/library/golang:1.25.12-alpine AS builder-go

ARG AWG_GO_REPO="https://github.com/amnezia-vpn/amneziawg-go.git"
# Pinned release commit for deterministic, reproducible builds
ARG AWG_GO_COMMIT="08fe3d33bbb116be09a6183d02609311b8b0c9e8"

RUN apk add --no-cache git build-base && \
    git clone "${AWG_GO_REPO}" /src && \
    cd /src && \
    git checkout "${AWG_GO_COMMIT}" && \
    go build -ldflags '-s -w' -v -o /usr/local/bin/amneziawg-go .

FROM docker.io/library/alpine:3.20 AS builder-tools

ARG AWG_TOOLS_REPO="https://github.com/amnezia-vpn/amneziawg-tools.git"
# Pinned release commit for deterministic, reproducible builds
ARG AWG_TOOLS_COMMIT="ee0f0a9aa34ff0a0da4b3433b9512781cfe02843"

RUN apk add --no-cache git build-base linux-headers bash && \
    git clone "${AWG_TOOLS_REPO}" /amneziawg-tools && \
    cd /amneziawg-tools && \
    git checkout "${AWG_TOOLS_COMMIT}" && \
    cd src && make

FROM docker.io/library/alpine:3.20

LABEL maintainer="Hoplyra"

RUN apk add --no-cache iproute2 iptables bash dumb-init

COPY --from=builder-go /usr/local/bin/amneziawg-go /usr/local/bin/amneziawg-go
COPY --from=builder-tools /amneziawg-tools/src/wg /usr/bin/awg
COPY --from=builder-tools /amneziawg-tools/src/wg-quick/linux.bash /usr/bin/awg-quick

# Symlinks so awg-quick and generic wireguard tools find awg-go seamlessly
RUN chmod +x /usr/bin/awg /usr/bin/awg-quick && \
    ln -sf /usr/local/bin/amneziawg-go /usr/bin/amneziawg-go && \
    ln -sf /usr/local/bin/amneziawg-go /usr/bin/wireguard-go && \
    ln -sf /usr/bin/awg /usr/bin/wg && \
    ln -sf /usr/bin/awg-quick /usr/bin/wg-quick

COPY start.sh /opt/hoplyra/start.sh
RUN chmod +x /opt/hoplyra/start.sh

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/opt/hoplyra/start.sh"]
