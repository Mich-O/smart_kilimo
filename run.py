"""
run.py
Development entrypoint: python run.py
For production, use `gunicorn wsgi:app` instead (see wsgi.py).
"""
from api.app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
