FROM alpine:3.20
LABEL maintainer="Hoplyra"
RUN apk add --no-cache openssl dumb-init ca-certificates curl bash
COPY install-hysteria.sh /tmp/install-hysteria.sh
RUN sh /tmp/install-hysteria.sh && rm /tmp/install-hysteria.sh
RUN mkdir -p /etc/hysteria /opt/hoplyra
COPY start.sh /opt/hoplyra/start.sh
RUN chmod +x /opt/hoplyra/start.sh
ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/opt/hoplyra/start.sh"]
