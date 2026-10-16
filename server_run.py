"""仅启动服务端（不打开窗口），用于调试与截图。"""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server  # noqa: E402

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8721
    httpd = server.create_server(port)
    print(f"GameHub server on http://127.0.0.1:{port}/", flush=True)
    if not server.STATE.library.games:
        server.STATE.start_scan()
    httpd.serve_forever()
