FROM alpine:3.20

LABEL maintainer="Hoplyra"

RUN apk add --no-cache openssl dumb-init ca-certificates curl unzip bash

COPY install-xray.sh /tmp/install-xray.sh
RUN chmod +x /tmp/install-xray.sh && /tmp/install-xray.sh && rm /tmp/install-xray.sh

RUN mkdir -p /opt/hoplyra/xray
COPY start.sh /opt/hoplyra/start.sh
RUN chmod +x /opt/hoplyra/start.sh

ENTRYPOINT ["/usr/bin/dumb-init", "--"]
CMD ["/opt/hoplyra/start.sh"]
