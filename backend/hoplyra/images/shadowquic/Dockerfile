FROM alpine:3.20
LABEL maintainer="Hoplyra"
RUN apk add --no-cache dumb-init ca-certificates curl
COPY install-shadowquic.sh /tmp/install-shadowquic.sh
RUN sh /tmp/install-shadowquic.sh && rm /tmp/install-shadowquic.sh
RUN mkdir -p /etc/shadowquic /opt/hoplyra
COPY start.sh /opt/hoplyra/start.sh
RUN chmod +x /opt/hoplyra/start.sh
ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/opt/hoplyra/start.sh"]
