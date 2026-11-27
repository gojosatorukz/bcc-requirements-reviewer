"""Opt-in Docker bridge listener for an explicitly host-loopback-published port."""

from pathlib import Path
from tempfile import TemporaryDirectory

from reviewer.serve import Workbench, WorkbenchServer


class LocalDockerBridge(WorkbenchServer):
    def server_bind(self):
        # Docker forwards the host's published loopback port to the container interface.
        # The normal handler still verifies Host and Origin against localhost:8765.
        self.server_address = ("0.0.0.0", self.server_address[1])
        super().server_bind()


def main():
    with TemporaryDirectory(prefix="reviewer-workbench-") as directory:
        app = Workbench(Path(directory))
        try:
            with LocalDockerBridge(8765, app) as server:
                print("Локальное рабочее место: http://127.0.0.1:8765", flush=True)
                server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            app.wait()


if __name__ == "__main__":
    main()
