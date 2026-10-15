FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY jobhunter ./jobhunter
RUN pip install --no-cache-dir .
ENV JOBHUNTER_HOME=/data
VOLUME /data
EXPOSE 8000
CMD ["python", "-m", "jobhunter", "web", "--host", "0.0.0.0", "--port", "8000", "--no-browser"]
