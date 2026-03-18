FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends libmupdf-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for layer caching
COPY pyproject.toml README.md ./
RUN pip install --no-cache-dir $(python -c "import tomllib; f=open('pyproject.toml','rb'); deps=tomllib.load(f)['project']['dependencies']; print(' '.join(deps))")

COPY src/ src/
RUN pip install --no-cache-dir .

# Download DejaVu fonts for PDF export with Unicode support
RUN python -m slack_agents.scripts.download_fonts

ARG AGENT_PATH
COPY ${AGENT_PATH}/ /app/agent/

RUN useradd -r -s /bin/false agent
USER agent

CMD ["slack-agents", "run", "agent"]
