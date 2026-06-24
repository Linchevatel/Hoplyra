FROM docker.io/3proxy/3proxy:0.9.5

LABEL maintainer="Hoplyra"

COPY start.sh /opt/hoplyra/start.sh
RUN chmod a+x /opt/hoplyra/start.sh

ENTRYPOINT ["/bin/sh", "/opt/hoplyra/start.sh"]
