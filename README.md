# Simple Image Downloader

GET /?url=IMAGE_LINK  →  returns the image

## Render Deploy
1. Push to GitHub
2. New → Web Service → connect repo
3. Build: `pip install -r requirements.txt`
4. Start: `gunicorn app:app --bind 0.0.0.0:$PORT --workers 1 --threads 2 --timeout 30`
5. Free plan → Create
