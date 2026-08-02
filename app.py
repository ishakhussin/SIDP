"""SentryLab v123 launcher."""

from sentrylab import create_app
from sentrylab.runtime import runtime


app = create_app()


def main() -> None:
    runtime.start(app)
    try:
        app.run(
            host="127.0.0.1",
            port=5000,
            debug=False,
            threaded=True,
            use_reloader=False,
        )
    finally:
        runtime.stop()


if __name__ == "__main__":
    main()
