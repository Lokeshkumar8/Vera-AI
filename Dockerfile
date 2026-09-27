FROM python:3.13-slim
WORKDIR /app
COPY bot.py server.py ./
ENV PORT=8080
EXPOSE 8080
CMD ["python", "server.py"]
