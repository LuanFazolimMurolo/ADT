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

@app.route("/login")
def login():
    return render_template("login.html")

@app.route("/entrar")
def entrar():
    return render_template("entrar.html")


if __name__ == "__main__":

    app.run(host="0.0.0.0", port=5000, debug=True)
