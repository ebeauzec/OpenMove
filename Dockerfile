# Copyright (c) 2026 Eugene Beauzec and Obi1 - FZCO. All Rights Reserved.
# Proprietary software; see LICENSE.
#
# OpenMove as a container (requirement R11). Standard library only: nothing is installed from a package index.
#
#   docker build -t openmove .
#   docker run --rm -p 127.0.0.1:8765:8765 -v openmove-data:/data openmove
#
# Then open the address the container prints (it carries the access token). The data folder (journal, evidence) is the volume /data.
# The page listens on all addresses inside the container; publish it on 127.0.0.1 only, as above, unless you put your own access control in front.
#
# What this image does NOT do: it holds no data mover and no migration host tools (the staged method is not built). It moves class A and B
# volumes by Cinder's retype through the OpenStack APIs only, and needs no iSCSI or multipath stack.
FROM python:3.12-slim

LABEL org.opencontainers.image.title="OpenMove" \
      org.opencontainers.image.vendor="Obi1 - FZCO" \
      org.opencontainers.image.licenses="Proprietary"

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    OPENMOVE_HOME=/data

RUN useradd --create-home --uid 10001 openmove && mkdir /data && chown openmove /data
WORKDIR /app
COPY openmove /app/openmove
COPY version.json LICENSE /app/
USER openmove
VOLUME ["/data"]
EXPOSE 8765

# The interface checks the Host header. A client reaches the container as localhost or 127.0.0.1 through the published port; add other names after the
# image name, for example behind a reverse proxy:  docker run ... openmove --allow-host openmove.example.com
ENTRYPOINT ["python", "-m", "openmove", "--host", "0.0.0.0", "--port", "8765", "--no-browser"]
CMD []
