FROM python:3.12-slim

WORKDIR /srv/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY workflow_engine ./workflow_engine
COPY examples ./examples

EXPOSE 8000
CMD ["uvicorn", "workflow_engine.api:app", "--host", "0.0.0.0", "--port", "8000"]
