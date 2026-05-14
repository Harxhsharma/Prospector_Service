import logging

# Basic logging config (can be overridden by app entrypoint)
logging.basicConfig(
	level=logging.INFO,
	format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
"""CompanyParser — Japan luxury travel ebook data pipeline."""

__version__ = "0.1.0"
