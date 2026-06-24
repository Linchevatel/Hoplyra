FROM alpine:3.20

LABEL maintainer="Hoplyra"

RUN apk add --no-cache tor dumb-init su-exec

RUN mkdir -p /opt/hoplyra/tor /var/lib/tor \
    && chown -R tor:tor /var/lib/tor

COPY start.sh /opt/hoplyra/start.sh
RUN chmod +x /opt/hoplyra/start.sh

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/opt/hoplyra/start.sh"]
