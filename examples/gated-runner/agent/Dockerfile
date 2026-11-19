# A stand-in agent: Python's standard library and two scripts, nothing
# installed from the network.
FROM python:3.12-slim-bookworm
RUN useradd -m -u 1000 agent
COPY gated.py attacks.py /app/
USER agent
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1
ENTRYPOINT ["sleep", "infinity"]
