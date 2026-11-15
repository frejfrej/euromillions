"""Allow running the command-line interface with ``python -m euromillions``."""

from euromillions.cli import cli

if __name__ == "__main__":
    cli(prog_name="euromillions")
