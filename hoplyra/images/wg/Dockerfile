FROM alpine:3.20

LABEL maintainer="Hoplyra"

RUN apk add --no-cache wireguard-tools iptables iproute2 dumb-init

COPY start.sh /opt/hoplyra/start.sh
RUN chmod +x /opt/hoplyra/start.sh

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/opt/hoplyra/start.sh"]
