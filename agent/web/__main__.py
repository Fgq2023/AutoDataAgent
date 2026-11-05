"""Launch the single-user local Web application."""
import argparse

from agent.core.config import load_config


def main():
    import uvicorn
    from agent.web.app import create_app

    parser = argparse.ArgumentParser()
    parser.add_argument("--data-root", default="data")
    parser.add_argument("--outputs-root", default="outputs/web")
    parser.add_argument("--port", type=int, default=8000)
    args = parser.parse_args()
    config = load_config(data_root=args.data_root, outputs_root=args.outputs_root,
                         sandbox_backend="docker")
    uvicorn.run(create_app(config), host="127.0.0.1", port=args.port, workers=1)


if __name__ == "__main__":
    main()
