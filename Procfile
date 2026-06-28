web: uvicorn api.main:app --host 0.0.0.0 --port ${PORT}
worker: python refresh_cache.py --loop --interval 24
