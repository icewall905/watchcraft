# Forward proxy for the browser sidecar: public internet on ports 80/443 only, never LAN, loopback,
# link-local or Docker addresses (checked on the resolved IP, so DNS tricks do not get through).
FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends squid && rm -rf /var/lib/apt/lists/*
COPY squid.conf /etc/squid/squid.conf
RUN squid -k parse -f /etc/squid/squid.conf
USER proxy
EXPOSE 3128
CMD ["squid", "-N", "-f", "/etc/squid/squid.conf"]
