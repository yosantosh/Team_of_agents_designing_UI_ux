FROM python:3.13-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl git \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY org ./org
COPY ui_ux-agent_company.md ./

RUN pip install --no-cache-dir -e .

EXPOSE 8765

CMD ["uiux", "serve", "--host", "0.0.0.0", "--port", "8765"]
