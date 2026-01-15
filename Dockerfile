FROM python:3.11-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir requests

# Copy application files
COPY cache_server.py .
COPY cabin_dashboard_v2.html .

# Expose the server port
EXPOSE 8080

# Environment variables (to be provided at runtime)
ENV OWM_API_KEY=""
ENV UDOT_API_KEY=""
ENV SENTINEL_HUB_CONFIG_ID=""

# Run the cache server
CMD ["python", "cache_server.py"]
