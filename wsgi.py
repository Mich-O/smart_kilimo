"""
wsgi.py
Production entrypoint: gunicorn wsgi:app
"""
from api.app import create_app

app = create_app()
