FROM python:3.12-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends libmupdf-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY pyproject.toml ./
COPY src/ src/
# Some pyproject.toml configs reference README.md (hatchling readme field) or
# llms-full.txt (hatchling force-include). Create empty placeholders so
# pip install doesn't fail — their content isn't needed in the runtime image.
RUN touch README.md llms-full.txt
RUN pip install --no-cache-dir .

# Download DejaVu fonts for PDF export with Unicode support
RUN python -m slack_agents.scripts.download_fonts

ARG AGENT_PATH
COPY ${AGENT_PATH}/ /app/agent/

RUN useradd -r -s /bin/false agent
USER agent

CMD ["slack-agents", "run", "agent"]
