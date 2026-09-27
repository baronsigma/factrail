FROM python:3.12-slim

WORKDIR /usr/src/app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY .actor ./.actor
COPY src ./src
CMD ["python", "-m", "src.main"]
