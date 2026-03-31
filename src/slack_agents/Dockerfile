FROM python:3.12-slim

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

# The agent directory is copied into /app/agent/<name>/ so the framework
# derives agent_name from the directory name (via config.load_agent_config).
# This ensures multiple agents sharing a database are distinguishable.
# At runtime, the glob agent/* resolves to the single subdirectory.
# "exec" replaces the shell so slack-agents receives signals directly.
ARG AGENT_PATH
ARG AGENT_NAME
COPY ${AGENT_PATH}/ /app/agent/${AGENT_NAME}/

RUN useradd -r -s /bin/false agent
USER agent

CMD ["sh", "-c", "exec slack-agents run agent/*"]
