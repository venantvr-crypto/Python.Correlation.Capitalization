# ==============================================================================
# Makefile pour l'Analyseur Crypto avec Pub/Sub externe
# Inspiré par une structure client/serveur modulaire.
# Version améliorée avec purge du venv.
# ==============================================================================

# --- Variables ---
VENV_DIR      := .venv
PYTHON        := $(VENV_DIR)/bin/python
PIP           := $(VENV_DIR)/bin/pip
CLIENT_SCRIPT := main.py

# --- Cibles Principales ---
.PHONY: help all run run-server run-client stop-server force-update force-rebuild test clean

# Tâche par défaut
help:
	@echo "Commandes disponibles :"
	@echo ""
	@echo "--- Exécution de l'Application ---"
	@echo "  \033[0;32mrun\033[0m          : Lance le serveur en arrière-plan puis le client (tout-en-un)."
	@echo "  \033[0;32mrun-server\033[0m  : Lance UNIQUEMENT le serveur Docker en avant-plan."
	@echo "  \033[0;32mrun-client\033[0m  : Lance UNIQUEMENT le client Python (nécessite que le serveur tourne)."
	@echo "  \033[0;32mstop-server\033[0m   : Arrête et supprime le conteneur du serveur Docker."
	@echo ""
	@echo "--- Gestion de l'Environnement ---"
	@echo "  \033[0;33msetup\033[0m         : (Re)Crée l'environnement virtuel et installe toutes les dépendances."
	@echo "  \033[0;33mforce-update\033[0m  : Force la mise à jour des dépendances du client."
	@echo "  \033[0;33mforce-rebuild\033[0m : Force la reconstruction de l'image Docker sans cache."
	@echo ""
	@echo "--- Qualité du Code & Tests ---"
	@echo "  \033[0;34mtest\033[0m          : Lance les tests unitaires avec pytest."
	@echo "  \033[0;34mclean\033[0m         : Nettoie les fichiers temporaires, la DB ET l'environnement virtuel."
	@echo "  \033[0;34mall\033[0m           : Nettoie, installe, et teste le projet."


# --- Implémentation des Cibles ---

# Exécution de l'Application
run: stop-server
	@echo "-> Lancement du serveur Pub/Sub en arrière-plan..."
	@docker compose up -d --build
	@echo "-> Attente de 5 secondes que le serveur démarre..."
	@sleep 5
	@make run-client
	@echo "-> Analyse terminée. Arrêt automatique du serveur..."
	@make stop-server

run-server: stop-server
	@echo "-> Lancement du serveur Pub/Sub en avant-plan (Ctrl+C pour arrêter)..."
	@docker compose up --build

run-client:
	@echo "-> Lancement du client d'analyse crypto..."
	@$(PYTHON) $(CLIENT_SCRIPT)

stop-server:
	@echo "-> Arrêt et suppression du conteneur du serveur Pub/Sub..."
	@docker compose down --rmi local 2>/dev/null || true

# Gestion de l'Environnement
setup:
	@echo "-> Création de l'environnement virtuel dans [$(VENV_DIR)]..."
	@python3 -m venv $(VENV_DIR)
	@echo "-> Installation des dépendances de production..."
	@$(PIP) install -r requirements.txt
	@echo "-> Installation des dépendances de développement..."
	@$(PIP) install -r requirements-dev.txt
	@echo "-> Installation des hooks pre-commit..."
	@$(PYTHON) -m pre_commit install
	@echo "-> ✅ Environnement de développement prêt !"

# Force la mise à jour des dépendances en utilisant le pip du venv
force-update:
	@echo "-> Nettoyage du cache pip..."
	@$(PIP) cache purge
	@echo "-> Mise à jour des dépendances client depuis requirements.txt..."
	@$(PIP) install --no-cache-dir -r requirements.txt

# Pour forcer la reconstruction sans utiliser le cache
force-rebuild: stop-server
	@echo "-> Lancement d'une reconstruction complète SANS CACHE..."
	@docker compose build --no-cache
	@echo "-> Lancement du serveur en arrière-plan..."
	@docker compose up -d
	@echo "-> Serveur lancé. Vous pouvez maintenant utiliser 'make run-client'."

# Qualité du Code & Tests
test:
	@echo "-> Lancement des tests unitaires..."
	@$(PYTHON) -m pytest tests -v --tb=short

clean:
	@echo "-> Nettoyage des fichiers temporaires..."
	@find . -type f -name '*.pyc' -delete
	@find . -type d -name '__pycache__' -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name '.pytest_cache' -exec rm -rf {} + 2>/dev/null || true
	@find . -type d -name 'htmlcov' -exec rm -rf {} + 2>/dev/null || true
	@rm -f crypto_data.db
	@echo "-> Suppression de l'environnement virtuel..."
	@rm -rf $(VENV_DIR)
	@echo "-> ✅ Nettoyage complet terminé."

all: clean setup test
