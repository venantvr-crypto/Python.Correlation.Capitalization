### Prompt : Conception d'une Architecture Asynchrone par Agents

**Objectif :** Tu dois concevoir une application Python multithreadée et pilotée par les événements en suivant une
architecture par agents. Le but est de créer un système robuste, découplé et non-bloquant, capable de gérer des flux de
travail complexes avec des opérations potentiellement longues (comme des appels API ou des calculs intensifs).

Pour cela, tu dois respecter rigoureusement les six principes de conception suivants :

**1. Découplage Radical par Agents**
Décompose l'application en composants indépendants appelés **Agents**. Chaque agent est une classe encapsulée dans son
propre thread (`threading.Thread`) et est responsable d'une unique tâche spécialisée (ex: `DataFetchingAgent`,
`CalculationAgent`, `PersistenceAgent`, `DisplayAgent`).

**2. Bus de Services Centralisé (ServiceBus)**
Toute communication entre les agents doit **exclusivement** passer par un `ServiceBus` central. Les agents ne doivent *
*jamais** s'appeler directement. Le `ServiceBus` fonctionne sur son propre thread, traitant une file d'événements (
`queue.Queue`) de manière séquentielle pour éviter les conditions de concurrence au niveau de la distribution des
messages.

**3. Événements Structurés et Immuables**
Chaque type de message transitant par le bus doit être une **classe de données immuable** (`@dataclass(frozen=True)`).
Cela garantit l'intégrité des données et sert de contrat clair entre les agents. Le `ServiceBus` doit intégrer un
mécanisme de **validation déclarative** qui s'assure que le payload d'un événement correspond bien à la structure de sa
classe avant de le dispatcher.

**4. Orchestration Réactive, non Impérative**
Un agent `Orchestrator` est responsable du flux de travail global. Cependant, il ne doit pas donner d'ordres directs.
Son rôle est de **réagir aux événements** en publiant de nouveaux événements. Par exemple, en recevant `DataReady`, il
publie `RequestCalculation`. Il agit comme une machine à états pilotée par le bus.

**5. Déport (Offloading) des Tâches Bloquantes**
Tout agent effectuant une opération longue (I/O réseau, accès à une base de données, calcul lourd) doit posséder sa *
*propre file d'attente interne** (`queue.Queue`). Lorsqu'il reçoit un événement du `ServiceBus`, il ne fait qu'ajouter
une tâche à sa file interne et retourne immédiatement le contrôle. Cela garantit que les gestionnaires d'événements du
`ServiceBus` restent **non-bloquants** et que le bus reste toujours réactif.

**6. Gestion Explicite des Dépendances et de la Synchronisation**
Le flux de travail ne doit pas reposer sur des hypothèses temporelles. Les dépendances doivent être gérées explicitement
par la séquence d'événements. Si une étape B dépend d'une étape A, l'agent responsable de B ne doit démarrer sa tâche
qu'à la réception d'un événement `A_Completed`. Utilise des mécanismes de synchronisation comme les `threading.Event`
uniquement pour les points critiques, comme signaler la fin complète du processus au thread principal.

**Livrable attendu :**
Fournis l'ensemble des fichiers Python nécessaires pour implémenter cette architecture, en appliquant ces principes.
Inclus les classes pour les agents, le service bus, les dataclasses d'événements et un script principal pour lancer l'
application. Le code doit être commenté pour expliquer comment chaque principe est appliqué.
