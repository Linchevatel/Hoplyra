FROM alpine:3.20

LABEL maintainer="Hoplyra"

RUN apk add --no-cache openssl dumb-init ca-certificates curl unzip bash \
    iptables iproute2 tor su-exec

COPY install-xray.sh /tmp/install-xray.sh
RUN chmod +x /tmp/install-xray.sh && /tmp/install-xray.sh && rm /tmp/install-xray.sh

RUN mkdir -p /opt/hoplyra/gateway /var/lib/tor \
    && chown -R tor:tor /var/lib/tor

COPY entrypoint.sh /opt/hoplyra/entrypoint.sh
RUN chmod +x /opt/hoplyra/entrypoint.sh

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/opt/hoplyra/entrypoint.sh"]
