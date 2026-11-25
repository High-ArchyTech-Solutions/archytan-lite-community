# The sandbox commands run in. Any Linux image works: add the tools your
# agent needs, then copy in the executor and make it the first process.
# It runs as an ordinary user; the compose file adds no network, a
# read-only root and no capabilities.
ARG LITE_IMAGE=ghcr.io/high-archytech-solutions/archytan-lite:2.9.0
FROM ${LITE_IMAGE} AS lite

FROM python:3.12-slim-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends git jq tree file \
 && rm -rf /var/lib/apt/lists/* \
 && useradd -m -u 1000 agent \
 # Owned by agent here, so the named volumes mounted on them start out
 # writable by it: Docker copies a fresh volume's owner from the image.
 && mkdir -p /work /run/archytan-runner \
 && chown agent:agent /work /run/archytan-runner
COPY --from=lite /usr/local/bin/archytan-lite-runner /usr/local/bin/archytan-lite-runner
USER agent
WORKDIR /work
ENV HOME=/home/agent
ENTRYPOINT ["/usr/local/bin/archytan-lite-runner", "exec"]
