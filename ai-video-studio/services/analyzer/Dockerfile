FROM python:3.12-slim

WORKDIR /app

COPY services/common ./common
COPY services/analyzer/requirements.txt ./requirements.txt
COPY offline/wheels /wheels

RUN pip install --no-cache-dir --no-index --find-links=/wheels -r requirements.txt \
    || pip install --no-cache-dir -r requirements.txt

COPY services/analyzer .

EXPOSE 8001
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8001"]
