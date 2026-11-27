"""Opt-in Docker bridge listener for an explicitly host-loopback-published port."""

import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from reviewer.serve import Workbench, WorkbenchServer


class LocalDockerBridge(WorkbenchServer):
    def server_bind(self):
        # Docker forwards the host's published loopback port to the container interface.
        # The normal handler still verifies Host and Origin against its own port.
        self.server_address = ("0.0.0.0", self.server_address[1])
        super().server_bind()


def main():
    parser = argparse.ArgumentParser(description="Проброс рабочего места на локальный порт Docker")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("Порт должен быть от 1 до 65535.")
    with TemporaryDirectory(prefix="reviewer-workbench-") as directory:
        app = Workbench(Path(directory))
        try:
            with LocalDockerBridge(args.port, app) as server:
                print(f"Локальное рабочее место: http://127.0.0.1:{server.server_port}", flush=True)
                server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            app.wait()


if __name__ == "__main__":
    main()
