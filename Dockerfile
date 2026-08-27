FROM python:3.12-slim

WORKDIR /app
COPY . .
RUN pip install --no-cache-dir .

ENV CONFERENCE_SCOUT_HTTP_HOST=0.0.0.0
ENV CONFERENCE_SCOUT_HTTP_PORT=8765
EXPOSE 8765

CMD ["conference-scout-mcp", "--transport", "streamable-http"]

