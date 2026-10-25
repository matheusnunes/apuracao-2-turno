# Espelho online da tela: só a página e o servidor (stdlib); os dados chegam do Mac por cloud/sync.py.
FROM python:3.12-slim
WORKDIR /app
COPY site/ site/
COPY cloud/ cloud/
RUN rm -rf site/data && mkdir -p site/data/live
ENV PYTHONUNBUFFERED=1
CMD ["python", "cloud/server.py"]
