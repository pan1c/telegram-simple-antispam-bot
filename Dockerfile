FROM python:3.11-slim

LABEL name="telegram-simple-antispam-bot" version="0.1.1"

WORKDIR /app

# install pip requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ /app/

CMD ["python", "main.py"]

