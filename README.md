# Pawon Bunda

Aplikasi katalog Pawon Bunda dengan frontend statis, API FastAPI, dan Nginx sebagai reverse proxy.

## Jalankan lokal

1. Salin `.env.example` menjadi `.env` dan isi `QWEN_API_KEY` dengan secret lokal.
2. Jalankan `docker compose up --build`.
3. Buka `http://localhost`.

Preflight sebelum menjalankan Compose:

```powershell
Test-Path .env
docker --version
docker compose version
```

Jika `Test-Path .env` menghasilkan `False`, buat file environment terlebih dahulu:

```powershell
Copy-Item .env.example .env
notepad .env
```

Validasi kesehatan API:

```powershell
docker compose ps
Invoke-WebRequest http://localhost/health
```

Review UI/API tanpa Docker:

```powershell
python -m py_compile backend/main.py
python -c "import sys; sys.path.insert(0, 'backend'); from fastapi.testclient import TestClient; from main import app; client = TestClient(app); response = client.get('/health'); assert response.status_code == 200; print(response.json())"
```

## Konfigurasi produksi

- Jangan commit `.env` atau secret provider.
- Isi `CORS_ORIGINS` hanya dengan origin yang benar, dipisahkan koma. Untuk akses melalui Nginx pada origin yang sama, biarkan kosong.
- Pasang TLS di depan Nginx atau aktifkan konfigurasi Certbot sesuai domain deployment.
- Log API ditulis ke stdout container agar dapat dikumpulkan oleh Docker/host logging.
- Request API mengembalikan `X-Request-ID`; gunakan nilainya saat mencari kejadian di log.

## Operasional

```powershell
docker compose up -d --build
docker compose logs -f api
docker compose down
```
