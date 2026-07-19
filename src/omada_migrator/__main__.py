"""Entry point: python -m omada_migrator (FR-12)."""

import uvicorn


def main():
    uvicorn.run(
        "omada_migrator.app:app",
        host="127.0.0.1",
        port=8888,
        reload=True,
    )


if __name__ == "__main__":
    main()
