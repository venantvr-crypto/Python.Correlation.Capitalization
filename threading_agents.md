# Architecture Threading et Agents

Ce document explique les subtilités de l'architecture multi-threadée de l'application, en se concentrant sur les agents (workers) et leur fonctionnement.

## Vue d'Ensemble de l'Architecture Threading

L'application utilise une architecture événementielle basée sur des threads multiples qui communiquent via un **ServiceBus**. Cette approche découple les composants et
permet un traitement parallèle efficace.

### Composants Principaux

L'application s'articule autour de plusieurs threads qui s'exécutent en parallèle :

1. **Thread Principal** : Orchestrateur (CryptoAnalyzer) qui coordonne le workflow et maintient l'état global
2. **ServiceBus Thread** : Gestionnaire central des événements qui reçoit et distribue tous les messages
3. **Worker Threads** : Agents spécialisés qui traitent les tâches en parallèle
    - **DataFetcher** : Récupère les données depuis les APIs externes (CoinGecko, Binance)
    - **RSICalculator** : Effectue les calculs mathématiques du RSI
    - **DatabaseManager** : Gère toutes les interactions avec SQLite
    - **DisplayAgent** : Affiche les résultats finaux
    - **StatusServer** : Serveur HTTP optionnel pour monitorer l'état des workers

Tous ces threads communiquent exclusivement via le ServiceBus, sans jamais s'appeler directement.

## Architecture de Base : QueueWorkerThread

### Principe du Pattern Producer-Consumer

Tous les agents héritent de `QueueWorkerThread`, qui implémente le pattern Producer-Consumer :

```python
class QueueWorkerThread(threading.Thread):
    def __init__(self, service_bus, name):
        super().__init__(name=name)
        self.service_bus = service_bus
        self.work_queue = queue.Queue()  # ← File thread-safe
        self._running = True
```

**Avantages de cette approche :**

- **Découplage** : Les producteurs (qui ajoutent des tâches) ne bloquent jamais
- **Thread-safe** : `queue.Queue` gère automatiquement la synchronisation
- **Backpressure naturel** : Si un agent est surchargé, sa queue s'allonge mais n'impacte pas les autres

### La Boucle de Traitement

Chaque agent exécute une boucle dans son propre thread :

```python
def run(self):
    logger.info(f"Thread '{self.name}' started.")
    while self._running:
        try:
            # Bloque jusqu'à 1 seconde en attendant une tâche
            task = self.work_queue.get(timeout=1)
            if task is None:  # Signal d'arrêt
                break

            # Décompose et exécute la tâche
            method_name, args, kwargs = task
            method = getattr(self, method_name)
            method(*args, **kwargs)

            self.work_queue.task_done()  # ← CRUCIAL pour queue.join()
        except queue.Empty:
            continue  # Timeout écoulé, reboucle
```

**Points clés :**

- `timeout=1` : Permet de vérifier régulièrement `_running` sans bloquer indéfiniment
- `task_done()` : Essentiel pour synchroniser avec `queue.join()` si utilisé
- Gestion d'erreur : Les exceptions ne tuent pas le thread

### Ajout de Tâches

Les tâches sont ajoutées via la méthode `add_task()` :

```python
def add_task(self, method_name: str, *args, **kwargs):
    self.work_queue.put((method_name, args, kwargs))
```

**Exemple d'utilisation dans DataFetcher :**

```python
def _handle_fetch_top_coins_requested(self, event):
    # Cette méthode est appelée par le ServiceBus
    # Elle délègue le travail lourd au thread du worker
    self.add_task("_fetch_top_coins_task", event.n)
```

## Le ServiceBus : Coordinateur Central

### Rôle du ServiceBus

Le `ServiceBus` est lui-même un thread qui :

1. Reçoit les événements publiés par les composants
2. Sérialise/désérialise les payloads (dataclasses → dict → dataclasses)
3. Distribue les événements aux handlers abonnés

```python
class ServiceBusBase(threading.Thread):
    def __init__(self, url, consumer_name):
        super().__init__(name=f"ServiceBus-{consumer_name}")
        self._handlers: Dict[str, list[HandlerInfo]] = defaultdict(list)
        self._event_schemas: Dict[str, type] = {}
```

### Pattern Pub/Sub et Thread-Safety

**Abonnement à un événement :**

```python
def subscribe(self, event_name: str, subscriber: Callable):
    self._topics.add(event_name)
    handler_info = HandlerInfo(handler=subscriber)
    self._handlers[event_name].append(handler_info)
```

**Publication d'un événement :**

```python
def publish(self, event_name: str, payload: Any, producer_name: str):
    message = self._prepare_payload(payload, event_name)
    self.client.publish(
        topic=event_name,
        message=message,
        producer=producer_name,
        message_id=str(uuid.uuid4())
    )
```

### Flow de Communication

Le flux de communication suit toujours le même pattern en 4 étapes :

**Étape 1 : Publication d'un événement**

- Un worker (ex: DataFetcher) termine une tâche et publie un événement
- Appel à `service_bus.publish("TopCoinsFetched", payload, producer_name)`

**Étape 2 : Réception par le ServiceBus**

- Le ServiceBus reçoit l'événement dans son thread dédié
- Sérialise le payload (dataclass → dict)
- Recherche tous les handlers abonnés à cet événement

**Étape 3 : Invocation des handlers**

- Le ServiceBus appelle chaque handler abonné (ex: `CryptoAnalyzer._handle_top_coins_fetched()`)
- **Important** : Les handlers s'exécutent dans le thread du ServiceBus, ils doivent être rapides

**Étape 4 : Délégation aux workers**

- Le handler ajoute une tâche à la queue d'un worker via `add_task()`
- Le worker traite la tâche dans son propre thread, de manière asynchrone

## Agents Spécialisés

### DataFetcher : I/O-Bound Worker

**Responsabilité** : Effectuer des appels réseau (API CoinGecko, Binance)

```python
class DataFetcher(QueueWorkerThread):

    def __init__(self, service_bus):
        super().__init__(service_bus=service_bus, name="DataFetcher")
        self.cg = CoinGeckoAPI()
        self.binance = ccxt.binance({"enableRateLimit": True})
```

**Gestion des erreurs réseau avec retry :**

```python
@retry(
    stop=stop_after_attempt(4),
    wait=wait_exponential(multiplier=2, min=5, max=30),
    retry=retry_if_exception_type(requests.exceptions.RequestException)
)
def fetch_one_page(page_num: int) -> List[dict]:
    return self.cg.get_coins_markets(...)
```

**Point important** : Les opérations I/O sont exécutées dans le thread du DataFetcher, ne bloquant jamais les autres composants.

### RSICalculator : CPU-Bound Worker

**Responsabilité** : Calculs mathématiques intensifs

```python
class RSICalculator(QueueWorkerThread):

    def _calculate_rsi_task(self, coin_id_symbol, data, timeframe):
        # Calculs pandas/numpy intensifs
        delta = valid_data.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=self.periods).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=self.periods).mean()
        rs = gain / loss
        rsi_series = 100 - (100 / (1 + rs))

        # Publication du résultat
        self.service_bus.publish("RSICalculated", event, self.__class__.__name__)
```

**Isolation CPU** : Les calculs se font dans un thread dédié, permettant à d'autres agents de continuer à travailler en parallèle.

### DatabaseManager : I/O-Bound avec Contrainte de Sérialisation

**Particularité** : SQLite nécessite une connexion par thread

```python
class DatabaseManager(QueueWorkerThread):
    def __init__(self, db_name="crypto_data.db", service_bus=None):
        super().__init__(service_bus=service_bus, name="DatabaseManager")
        self.db_name = db_name
        self.conn = None  # Sera créé dans run()
        self._initialized_event = threading.Event()  # Synchronisation

    def run(self):
        # Connexion créée dans le thread du worker
        self.conn = sqlite3.connect(self.db_name, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self._initialize_tables()
        self._initialized_event.set()  # Signale que la DB est prête

        # Appel de la boucle de traitement parente
        super().run()

        self._close()
```

**Pattern de synchronisation** :

- `_initialized_event` : Permet aux autres composants d'attendre que la DB soit prête
- Toutes les opérations SQL se font dans le thread du DatabaseManager
- Évite les problèmes de concurrence avec SQLite

### DisplayAgent : Worker Minimaliste

**Responsabilité** : Affichage final des résultats

```python
class DisplayAgent(QueueWorkerThread):
    def _display_results_and_publish(self, event: FinalResultsReady):
        self._display_results(event)
        # Signale la fin du traitement
        self.service_bus.publish("DisplayCompleted", DisplayCompleted(), self.__class__.__name__)
```

**Simplicité** : Peu de tâches, mais isolation importante pour ne pas bloquer l'orchestrateur.

## Gestion du Cycle de Vie

### Démarrage des Services

```python
def _start_services(self):
    all_services = self.services + [self.service_bus]
    for service in all_services:
        if not service.is_alive():
            service.start()  # ← Lance le thread

    time.sleep(0.5)  # Laisse le temps aux threads de s'initialiser
```

### Arrêt Gracieux

```python
def stop(self, timeout: Optional[float] = 30):
    logger.info(f"Stop requested for '{self.name}'.")

    # Attend que la queue se vide (avec timeout)
    start_time = time.time()
    while not self.work_queue.empty():
        if timeout and (time.time() - start_time) > timeout:
            logger.warning(f"Timeout reached. Force stopping...")
            break
        time.sleep(0.1)

    self._running = False
    self.work_queue.put(None)  # Signal d'arrêt

    if self.is_alive():
        self.join(timeout=5)  # Attend la fin du thread
```

**Ordre d'arrêt** : Les services sont arrêtés dans l'ordre inverse de leur démarrage.

## Synchronisation et Patterns Avancés

### Event Threading pour Coordination

```python
class CryptoAnalyzer:

    def __init__(self):
        self._processing_completed = threading.Event()

    def _on_display_completed(self, event):
        # Signale au thread principal que tout est terminé
        self._processing_completed.set()

    def run(self):
        self._start_services()
        self.start_workflow()
        self._processing_completed.wait()  # Bloque jusqu'au signal
        self._stop_services()
```

### Éviter les Deadlocks

**Problème potentiel** : Handler du ServiceBus qui bloque en attendant une réponse

**Solution** : Toujours déléguer le travail lourd via `add_task()`

```python
# ❌ MAUVAIS : Bloque le ServiceBus
def _handle_event(self, event):
    result = self.do_heavy_work()  # Opération longue
    self.service_bus.publish("Result", result)

# ✅ BON : Délègue au thread du worker
def _handle_event(self, event):
    self.add_task("_do_heavy_work_task", event)

def _do_heavy_work_task(self, event):
    result = self.do_heavy_work()
    self.service_bus.publish("Result", result)
```

### Gestion de la Backpressure

Si un agent accumule trop de tâches dans sa queue :

```python
def get_status(self) -> dict:
    return {
        "name": self.name,
        "tasks_in_queue": self.work_queue.qsize(),  # Monitoring
        "last_activity_time": self._last_activity_time
    }
```

**Solutions possibles** :

- Ajouter des threads supplémentaires pour l'agent surchargé
- Implémenter une limite de taille de queue avec blocage
- Ajuster les priorités de traitement

## Bonnes Pratiques

### 1. Handlers Légers

Les handlers d'événements doivent être **ultra-rapides** car ils s'exécutent dans le thread du ServiceBus :

```python
def _handle_fetch_requested(self, event):
    # ✅ Rapide : juste ajouter à la queue
    self.add_task("_fetch_task", event.url)

def _fetch_task(self, url):
    # ✅ Lent mais isolé : s'exécute dans le thread du worker
    response = requests.get(url, timeout=30)
```

### 2. Sérialisation des Données

Les événements passent par le ServiceBus sous forme de dictionnaires :

```python
@dataclass
class HistoricalPricesFetched:
    coin_id_symbol: Tuple[str, str]
    prices_df_json: Optional[str]  # ← Pandas DataFrame sérialisé en JSON
    timeframe: str

# Sérialisation
prices_json = prices_df.to_json(orient="split")

# Désérialisation
prices_df = pd.read_json(StringIO(prices_json), orient="split")
```

**Pourquoi ?** Les objets volumineux (DataFrames) ne peuvent pas être passés directement entre threads via le bus réseau.

### 3. Gestion d'Erreurs Robuste

Toujours publier un événement d'échec pour débloquer les compteurs :

```python
try:
    result = self.process_coin(coin)
    self.service_bus.publish("Success", result)
except Exception as e:
    logger.error(f"Failed: {e}")
    self.service_bus.publish("CoinProcessingFailed",
                             CoinProcessingFailed(coin_id_symbol=coin))
```

### 4. Éviter les Race Conditions

**Problème** : État partagé entre threads

```python
# ❌ DANGEREUX
class Agent:
    def __init__(self):
        self.counter = 0  # État partagé non protégé

    def task(self):
        self.counter += 1  # Race condition!
```

**Solution** : Utiliser des locks ou éviter l'état partagé

```python
# ✅ Option 1 : Lock
class Agent:
    def __init__(self):
        self.counter = 0
        self._lock = threading.Lock()

    def task(self):
        with self._lock:
            self.counter += 1

# ✅ Option 2 : État local (préféré)
class Agent:
    def task(self):
        # Pas d'état partagé, tout passe par événements
        result = self.compute()
        self.service_bus.publish("Result", result)
```

## Séquence Complète d'Exécution

Voici une séquence typique d'exécution pour analyser une cryptomonnaie :

**Phase 1 : Récupération des données de marché**

1. CryptoAnalyzer publie `FetchTopCoinsRequested` sur le ServiceBus
2. ServiceBus notifie DataFetcher via son handler `_handle_fetch_top_coins_requested`
3. DataFetcher ajoute la tâche `_fetch_top_coins_task` à sa queue
4. DataFetcher exécute la tâche dans son thread et appelle l'API CoinGecko
5. Une fois terminé, DataFetcher publie `TopCoinsFetched` avec les résultats
6. ServiceBus notifie CryptoAnalyzer qui traite la liste des cryptos

**Phase 2 : Récupération des prix historiques**

7. CryptoAnalyzer publie `FetchHistoricalPricesRequested` pour chaque crypto et timeframe
8. DataFetcher récupère les données OHLCV depuis Binance
9. DataFetcher publie `HistoricalPricesFetched` avec les données JSON sérialisées
10. ServiceBus notifie deux handlers en parallèle :
    - CryptoAnalyzer pour continuer le workflow
    - DatabaseManager pour sauvegarder les prix (via `_db_save_prices`)

**Phase 3 : Calcul du RSI**

11. CryptoAnalyzer publie `CalculateRSIRequested` avec les prix
12. RSICalculator reçoit l'événement et ajoute `_calculate_rsi_task` à sa queue
13. RSICalculator effectue les calculs pandas/numpy dans son thread
14. RSICalculator publie `RSICalculated` avec les résultats sérialisés
15. Deux handlers sont notifiés :
    - CryptoAnalyzer pour analyser la corrélation
    - DatabaseManager pour sauvegarder les RSI (via `_db_save_rsi`)

**Phase 4 : Analyse et affichage**

16. CryptoAnalyzer calcule les corrélations et publie `CorrelationAnalyzed`
17. DatabaseManager sauvegarde les corrélations dans SQLite
18. Quand tous les timeframes sont terminés, CryptoAnalyzer publie `FinalResultsReady`
19. DisplayAgent affiche les résultats et publie `DisplayCompleted`
20. CryptoAnalyzer reçoit le signal et arrête gracieusement tous les services

## Conclusion

L'architecture multi-threadée de cette application offre :

- **Performances** : Traitement parallèle des tâches I/O et CPU-bound
- **Résilience** : Isolation des erreurs par thread
- **Modularité** : Ajout/suppression de workers sans impact sur les autres
- **Clarté** : Communication explicite via événements typés

Le pattern QueueWorkerThread + ServiceBus est un excellent choix pour des applications événementielles nécessitant du traitement asynchrone distribué sur plusieurs
workers.
