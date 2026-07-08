"""app.py — Voice Companion 後端（Flask）

・掛載語音 token + 會議 blueprint（routes.py）
・服務前端 PWA（frontend/index.html）

執行：
    cd backend
    pip install -r requirements.txt
    python app.py            # 預設 127.0.0.1:8080
"""
import os
import sys

from flask import Flask, send_from_directory

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from routes import yuling_bp  # noqa: E402

app = Flask(__name__, static_folder=None)
app.register_blueprint(yuling_bp)

_FRONTEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "frontend")


@app.route("/")
def index():
    return send_from_directory(_FRONTEND, "index.html")


@app.route("/<path:path>")
def frontend_files(path):
    return send_from_directory(_FRONTEND, path)


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    print(f"Voice Companion backend → http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, threaded=True)
