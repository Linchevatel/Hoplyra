FROM alpine:3.20

LABEL maintainer="Hoplyra"

RUN apk add --no-cache openvpn easy-rsa bash iptables iproute2 openssl dumb-init netcat-openbsd

ENV EASYRSA_BATCH=1
ENV PATH="/usr/share/easy-rsa:${PATH}"

RUN mkdir -p /opt/hoplyra/openvpn

COPY start.sh /opt/hoplyra/start.sh
COPY setup-pki.sh /opt/hoplyra/setup-pki.sh
RUN chmod +x /opt/hoplyra/start.sh /opt/hoplyra/setup-pki.sh

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/opt/hoplyra/start.sh"]
