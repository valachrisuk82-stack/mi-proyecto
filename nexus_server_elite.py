import os
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route("/")
def index():
    return jsonify({"status": "NEXUS APEX online"})

@app.route("/health")
def health():
    return jsonify({"status": "ok"})
