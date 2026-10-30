FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
ENV PYTHONUNBUFFERED=1
# Im Container muss die Webansicht nach außen lauschen (Render/Railway prüfen den Port)
ENV RELAY_VIEW_BIND=0.0.0.0
EXPOSE 8080
CMD ["python", "relay.py"]
