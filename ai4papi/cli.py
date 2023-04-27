"""Command Line Interface to the API."""

# Implementation notes:
# Typer is not implement directly as a decorator around the run() function in main.py
# because it gave errors while trying to run `uvicorn main:app --reload`.  Just have it
# in a separate file, clean and easy.

import typer

from ai4papi import main


def run():
    """Run the API."""
    typer.run(main.run)


if __name__ == "__main__":
    run()
