from flask import Flask, jsonify, request, render_template
import json
import os

app = Flask(__name__)
FILE = "ponte.json"

if not os.path.exists(FILE):
    with open(FILE, "w") as f:
        json.dump({"botoes": {"btc": False}}, f)
        
@app.route("/")
def index():
    return render_template("index.html")
def ler():
    with open(FILE, "r") as f:
        return json.load(f)

def salvar(dados):
    with open(FILE, "w") as f:
        json.dump(dados, f, indent=4)

@app.route("/estado", methods=["GET"])
def get_estado():
    return jsonify(ler())

@app.route("/estado", methods=["POST"])
def set_estado():
    dados = request.json
    salvar(dados)
    return {"status": "ok"}

@app.route("/toggle/<botao>", methods=["POST"])
def toggle(botao):
    dados = ler()
    atual = dados["botoes"].get(botao, False)
    dados["botoes"][botao] = not atual
    salvar(dados)
    return jsonify(dados)

app.run(host="0.0.0.0", port=5000)
