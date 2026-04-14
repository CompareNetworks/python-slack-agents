FROM python:3.12-slim

WORKDIR /app

# Install dependencies first so this layer caches well.
# Supports either requirements.txt (default from `slack-agents init`)
# or a pyproject.toml with PEP 735 [dependency-groups] (no [project]).
COPY requirements.txt* pyproject.toml* ./
RUN if [ -f requirements.txt ]; then \
        pip install --no-cache-dir -r requirements.txt ; \
    elif [ -f pyproject.toml ]; then \
        pip install --no-cache-dir --group default ; \
    else \
        echo "Error: overlay must have requirements.txt or a pyproject.toml with [dependency-groups]" >&2 ; \
        exit 1 ; \
    fi

# Download DejaVu fonts for PDF export with Unicode support
RUN python -m slack_agents.scripts.download_fonts

# Copy the overlay's custom providers (if any). The framework's CLI
# prepends ./src to sys.path at runtime so imports like
# `my_agents.tools.foo` resolve without installing the overlay.
COPY src/ /app/src/

# Copy only the selected agent into /app/agent/<name>/ so the framework
# derives agent_name from the directory name. Multiple agents sharing a
# database remain distinguishable. The glob agent/* resolves to the
# single subdirectory. `exec` replaces the shell so slack-agents
# receives signals directly.
ARG AGENT_PATH
ARG AGENT_NAME
COPY ${AGENT_PATH}/ /app/agent/${AGENT_NAME}/

RUN useradd -r -s /bin/false agent
USER agent

CMD ["sh", "-c", "exec slack-agents run agent/*"]
