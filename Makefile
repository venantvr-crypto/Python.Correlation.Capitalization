# Makefile pour Python.Correlation.Capitalization
# Gestion du projet et automatisation des tâches

.PHONY: help install install-dev test test-cov lint format clean run db-clean check all

# Variables
PYTHON := python3
PIP := pip3
PROJECT_NAME := crypto_analyzer
TEST_PATH := tests
SRC_PATH := .
COVERAGE_MIN := 70

# Couleurs pour l'affichage
GREEN := \033[0;32m
YELLOW := \033[0;33m
RED := \033[0;31m
NC := \033[0m # No Color

help: ## Afficher cette aide
	@echo "$(GREEN)Commandes disponibles:$(NC)"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  $(YELLOW)%-15s$(NC) %s\n", $$1, $$2}'

install: ## Installer les dépendances de production
	@echo "$(GREEN)Installation des dépendances de production...$(NC)"
	$(PIP) install -r requirements.txt

install-dev: ## Installer les dépendances de développement
	@echo "$(GREEN)Installation des dépendances de développement...$(NC)"
	$(PIP) install -r requirements-dev.txt
	@echo "$(GREEN)Configuration de pre-commit...$(NC)"
	pre-commit install

test: ## Lancer les tests unitaires
	@echo "$(GREEN)Exécution des tests unitaires...$(NC)"
	$(PYTHON) -m pytest $(TEST_PATH) -v --tb=short

test-cov: ## Lancer les tests avec couverture de code
	@echo "$(GREEN)Exécution des tests avec couverture...$(NC)"
	$(PYTHON) -m pytest $(TEST_PATH) --cov=$(SRC_PATH) --cov-report=html --cov-report=term --cov-fail-under=$(COVERAGE_MIN)
	@echo "$(GREEN)Rapport de couverture généré dans htmlcov/index.html$(NC)"

test-fast: ## Lancer les tests en parallèle (plus rapide)
	@echo "$(GREEN)Exécution des tests en parallèle...$(NC)"
	$(PYTHON) -m pytest $(TEST_PATH) -n auto -v

test-unit: ## Lancer uniquement les tests unitaires (sans intégration)
	@echo "$(GREEN)Exécution des tests unitaires seulement...$(NC)"
	$(PYTHON) -m pytest $(TEST_PATH) -v -m "not integration"

lint: ## Vérifier le code avec flake8 et pylint
	@echo "$(GREEN)Vérification du code avec flake8...$(NC)"
	flake8 $(SRC_PATH) --exclude=__pycache__,tests --max-line-length=120 --ignore=E501,W503
	@echo "$(GREEN)Vérification du code avec pylint...$(NC)"
	pylint *.py --max-line-length=120 --disable=C0114,C0115,C0116,R0902,R0913,W0212

mypy: ## Vérifier les types avec mypy
	@echo "$(GREEN)Vérification des types avec mypy...$(NC)"
	mypy $(SRC_PATH) --ignore-missing-imports --exclude 'tests/*'

format: ## Formater le code avec black et isort
	@echo "$(GREEN)Formatage du code avec isort...$(NC)"
	isort $(SRC_PATH) --profile black
	@echo "$(GREEN)Formatage du code avec black...$(NC)"
	black $(SRC_PATH) --line-length 120

format-check: ## Vérifier le formatage sans modifier
	@echo "$(GREEN)Vérification du formatage avec isort...$(NC)"
	isort $(SRC_PATH) --profile black --check-only
	@echo "$(GREEN)Vérification du formatage avec black...$(NC)"
	black $(SRC_PATH) --line-length 120 --check

clean: ## Nettoyer les fichiers temporaires et de cache
	@echo "$(RED)Nettoyage des fichiers temporaires...$(NC)"
	find . -type f -name '*.pyc' -delete
	find . -type d -name '__pycache__' -delete
	find . -type d -name '.pytest_cache' -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name '.mypy_cache' -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name 'htmlcov' -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name '.coverage' -delete
	find . -type f -name '*.log' -delete
	@echo "$(GREEN)Nettoyage terminé$(NC)"

db-clean: ## Supprimer la base de données
	@echo "$(RED)Suppression de la base de données...$(NC)"
	rm -f crypto_data.db
	@echo "$(GREEN)Base de données supprimée$(NC)"

run: ## Lancer l'application principale
	@echo "$(GREEN)Démarrage de l'analyse crypto...$(NC)"
	$(PYTHON) main.py

check: lint mypy test ## Vérification complète (lint, types, tests)
	@echo "$(GREEN)✓ Toutes les vérifications sont passées!$(NC)"

pre-commit: ## Lancer pre-commit sur tous les fichiers
	@echo "$(GREEN)Exécution de pre-commit...$(NC)"
	pre-commit run --all-files

setup: install-dev ## Configuration complète de l'environnement de développement
	@echo "$(GREEN)✓ Environnement de développement configuré$(NC)"

ci: format-check lint mypy test-cov ## Pipeline CI complet
	@echo "$(GREEN)✓ Pipeline CI terminé avec succès!$(NC)"

watch-test: ## Surveiller les changements et relancer les tests
	@echo "$(GREEN)Surveillance des fichiers pour relancer les tests...$(NC)"
	$(PYTHON) -m pytest_watch $(TEST_PATH) --clear

debug: ## Lancer l'application en mode debug
	@echo "$(GREEN)Démarrage en mode debug...$(NC)"
	PYTHONPATH=. $(PYTHON) -m pdb main.py

profile: ## Profiler l'application
	@echo "$(GREEN)Profilage de l'application...$(NC)"
	$(PYTHON) -m cProfile -s cumulative main.py > profile_output.txt
	@echo "$(GREEN)Résultats du profilage dans profile_output.txt$(NC)"

deps-check: ## Vérifier les dépendances obsolètes
	@echo "$(GREEN)Vérification des dépendances...$(NC)"
	pip list --outdated

deps-upgrade: ## Mettre à jour toutes les dépendances
	@echo "$(YELLOW)Mise à jour des dépendances...$(NC)"
	pip install --upgrade -r requirements.txt
	pip install --upgrade -r requirements-dev.txt

all: clean install-dev format lint mypy test-cov ## Tout nettoyer, installer, formater, vérifier et tester
	@echo "$(GREEN)✓ Build complet terminé avec succès!$(NC)"

.DEFAULT_GOAL := help