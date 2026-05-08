import os

from app import createApp

app = createApp()

if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    # Werkzeug's debugger exposes a remote-code-execution surface, so it is
    # gated on an explicit FLASK_ENV=development opt-in (no truthy-string
    # heuristics). When debug is on, bind to loopback only so the debug
    # console is never reachable from the network.
    debug = os.environ.get("FLASK_ENV") == "development"
    host = '127.0.0.1' if debug else '0.0.0.0'
    app.run(host=host, port=port, debug=debug)