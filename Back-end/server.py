# server.py
from flask import Flask, jsonify, request
import json
import os

app = Flask(__name__)

FILE = "ponte.json"

# garante que o arquivo exista
if not os.path.exists(FILE):
    with open(FILE, "w") as f:
        json.dump({}, f)

# -------- LER DADOS --------
@app.route("/estado", methods=["GET"])
def get_estado():
    with open(FILE, "r") as f:
        dados = json.load(f)
    return jsonify(dados)

# -------- ESCREVER DADOS --------
@app.route("/estado", methods=["POST"])
def set_estado():
    dados = request.json
    with open(FILE, "w") as f:
        json.dump(dados, f, indent=4)
    return {"status": "ok"}

app.run(host="0.0.0.0", port=5000)
