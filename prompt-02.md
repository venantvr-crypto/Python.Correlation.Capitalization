C'est une excellente idée. Le résumé de nos interactions permet de consolider l'ensemble des leçons apprises et d'en
dégager une méthode claire pour la conception d'architectures pilotées par les événements.

### Résumé de nos interactions : Problème et Solution

Nous avons débuté avec un code monolithique simple, et notre objectif était de le transformer en une architecture
asynchrone et modulaire. Chaque itération a mis en lumière des défis de plus en plus complexes liés à la concurrence et
à la synchronisation.

#### 1. Découpage initial et introduction du ServiceBus

* **Problème initial :** Le code était un monolithe. Les classes dépendaient directement les unes des autres (
  `CryptoAnalyzer` appelait directement `DataFetcher`). La gestion des erreurs était centralisée et le traitement était
  synchrone.
* **Solution :** Introduction des **threads** pour chaque composant et d'un **ServiceBus**. Les composants principaux (
  `DataFetcher`, `DatabaseManager`, `RSICalculator`) sont devenus des threads indépendants, et leurs interactions ont
  commencé à passer par des événements métiers (ex. `TopCoinsFetched`).

#### 2. Problèmes de synchronisation et de blocage du thread principal

* **Problème :** Le `CryptoAnalyzer` agissait à la fois comme un orchestrateur et un exécutant. Sa méthode `run()`
  bloquait en attendant la fin des traitements, et les gestionnaires d'événements lançaient des tâches sans attendre
  leur achèvement, créant des "courses" (race conditions) où une étape pouvait démarrer avant que ses données requises
  ne soient prêtes (par exemple, la corrélation d'un altcoin était tentée avant que le RSI du Bitcoin ne soit calculé).
* **Solution :** Utilisation d'**objets de synchronisation** (`threading.Event`) pour s'assurer qu'une étape ne commence
  que lorsque la précédente est terminée. Le thread principal est devenu un simple lanceur de services, et les
  dépendances ont été gérées de manière explicite par des points de rendez-vous dans les gestionnaires d'événements.

#### 3. Erreurs de logique et de concurrence dans les threads

* **Problème :** Nous avons rencontré un **deadlock** potentiel dû à l'utilisation d'un verrou non réentrant (
  `threading.Lock`) dans une logique récursive. De plus, le thread `DatabaseManager` échouait en raison d'un accès
  concurrentiel à son curseur, car une partie du code l'utilisait directement au lieu de passer par sa file d'attente.
* **Solution :** Toutes les opérations de base de données (lecture et écriture) ont été centralisées dans la file
  d'attente du `DatabaseManager`. J'ai également corrigé la logique des verrous en m'assurant qu'ils ne sont pas acquis
  de manière récursive.

#### 4. Biais de conception et blocage de la boucle d'événements

* **Problème :** La boucle d'événements du `ServiceBus` se bloquait. Un appel d'API long dans le `DataFetcher` stoppait
  tout le système, car le `ServiceBus` est un thread séquentiel qui ne pouvait pas traiter d'autres messages en attente.
* **Solution :** **Offloading des tâches bloquantes**. Le `DataFetcher` et le `RSICalculator` sont redevenus des threads
  de travail avec leurs propres files d'attente. Le `ServiceBus` ne fait plus que transférer les requêtes à ces files,
  ce qui lui permet de rester réactif et non-bloquant.

#### 5. Structuration des données et validation

* **Problème :** L'absence de validation des messages a introduit des erreurs de type (passer un dictionnaire au lieu
  d'une classe d'événement) et des erreurs de logique. De plus, la "perte" d'informations critiques (comme le `coin_id`)
  dans le flux d'événements a causé un biais de données.
* **Solution :** Introduction de **classes de données (`dataclass`)** pour représenter chaque événement. Le `ServiceBus`
  a été mis à jour pour valider de manière déclarative le payload de chaque événement. Les gestionnaires d'événements
  ont été modifiés pour utiliser les attributs des objets, ce qui rend le code plus sûr et plus lisible.

### La Méthode à retenir

De tout cela, une méthode de conception émergente peut être formulée :

1. **Découplage radical** : Modélisez chaque fonctionnalité majeure comme un **agent spécialisé** (par ex.
   `DataFetcher`, `DatabaseManager`, `MarketCapAgent`). Chaque agent doit se concentrer sur une tâche unique.
2. **Communication par événements immuables** : Tous les agents ne communiquent qu'en publiant ou en s'abonnant à des *
   *événements nommés**. Ces événements doivent être représentés par des **classes de données immuables** (
   `@dataclass(frozen=True)`).
3. **Validation déclarative** : Implémentez un mécanisme pour valider les payloads des événements. C'est une première
   ligne de défense contre les erreurs de données et de type.
4. **Orchestration réactive** : L'orchestrateur (`CryptoAnalyzer`) ne doit pas appeler directement les agents. Il doit
   réagir aux événements en publiant de nouveaux événements, créant ainsi un flux de travail non-bloquant.
5. **Threads de travail pour les tâches bloquantes** : Les tâches longues, comme les appels API, les calculs intensifs
   ou les accès à la base de données, doivent être offloadées vers des **threads dédiés**. Chaque thread de travail doit
   avoir sa propre file d'attente pour traiter les requêtes de manière séquentielle, tout en laissant le `ServiceBus`
   libre et réactif.
6. **Points de synchronisation clairs** : Utilisez des mécanismes de synchronisation (`threading.Event`, `Queue`) pour
   signaler la fin des processus et permettre aux agents d'attendre les dépendances sans bloquer.

