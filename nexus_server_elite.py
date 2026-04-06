import os
from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

@app.route("/")
def index():
    return jsonify({"status": "NEXUS APEX online"})

@app.route('/health')
def health():
    return jsonify({"status": "ok"})

@app.route('/api/tickers')
def tickers():
    return jsonify({"BTCUSDT": {"price": 0, "change": 0}})

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5001))
    app.run(host='0.0.0.0', port=port, debug=False)
