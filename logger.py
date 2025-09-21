import logging

logging.basicConfig(
    level=logging.INFO,  # INFO est un bon niveau pour la production, DEBUG pour le développement
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)
