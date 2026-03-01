FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir mcp
COPY mcp_guardrail_http.py /app/
ENV PORT=8200
EXPOSE 8200
CMD ["python", "mcp_guardrail_http.py", "--transport", "streamable-http"]
