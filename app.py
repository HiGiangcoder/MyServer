from flask import Flask, jsonify, render_template

app = Flask(__name__)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/hello")
def hello():
    return jsonify(
        {
            "message": "Hello from my personal server!",
            "backend": "Flask",
            "status": "online",
        }
    )


@app.route("/api/info")
def info():
    return jsonify({"server": "My Linux Mint PC", "api_version": "1.0"})


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=8000, debug=False)
