FROM alpine:3.20
LABEL maintainer="Hoplyra"
RUN apk add --no-cache openssl dumb-init ca-certificates curl bash
COPY install-realm-server.sh /tmp/install-realm-server.sh
RUN sh /tmp/install-realm-server.sh && rm /tmp/install-realm-server.sh
RUN mkdir -p /etc/hysteria-realm /opt/hoplyra
COPY start.sh /opt/hoplyra/start.sh
RUN chmod +x /opt/hoplyra/start.sh
ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/opt/hoplyra/start.sh"]
