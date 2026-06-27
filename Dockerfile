FROM python:3.11-slim AS builder
WORKDIR /app
COPY requirements.txt .
RUN pip install --upgrade pip && pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /install /usr/local
COPY main.py .
RUN useradd -m appuser && chown -R appuser /app
USER appuser

# Render מזריק PORT בזמן ריצה (בד"כ 10000). חשוב להאזין אליו ולא לפורט מקובע.
ENV PORT=8000
EXPOSE 8000

# שימוש ב-sh -c כדי שמשתנה הסביבה $PORT יורחב נכון בזמן ריצה
CMD ["sh", "-c", "uvicorn main:api --host 0.0.0.0 --port ${PORT:-8000} --workers 1"]
