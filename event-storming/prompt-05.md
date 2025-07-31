Non, l’usage d’**Event Sourcing** n’est pas systématique dès qu’un service A appelle un service B ; ce sont deux
préoccupations distinctes :

1. **Orchestration / Sagas (gestion du flux)**
2. **Event Sourcing (stockage de l’état)**

---

## 1. Orchestration vs. Choreography pour gérer l’appel A → B

### a. Choreography (chorégraphie)

* **Principe** : chaque service publie et réagit à des événements métier.
* **Flux** :

    1. A publie `OrderPlaced`
    2. B s’abonne à `OrderPlaced`, traite la demande et publie `PaymentAuthorized`
    3. C s’abonne à `PaymentAuthorized`, etc.
* **Avantages** : pas de composant central, très découplé.
* **Inconvénients** : la chaîne d’événements s’étend, le suivi du flux global devient complexe.

### b. Orchestration (Process Manager / Saga Orchestrator)

* **Principe** : un orchestrateur central (ou un petit ensemble de managers) pilote l’enchaînement.
* **Flux** :

    1. App appelle l’**Orchestrateur** : `InitiateOrderCommand`
    2. Orchestrateur envoie `CallPaymentCommand` à B
    3. B renvoie `PaymentSucceeded` ou `PaymentFailed`
    4. Orchestrateur, selon le résultat, envoie `CallShippingCommand` ou `CompensatePaymentCommand`
* **Avantages** : visibilité du flux, gestion centralisée des erreurs et compensation.
* **Inconvénients** : un petit couplage vers l’orchestrateur, point potentiel de saturation.

---

## 2. Event Sourcing : quand et pourquoi ?

### a. Qu’est-ce que l’Event Sourcing ?

Au lieu de stocker l’état courant dans une table, on stocke la **liste immuable** des événements qui l’ont généré :

```
OrderCreated → OrderItemAdded → OrderConfirmed → OrderShipped
```

* **Reconstruction** : on rejoue ces événements pour reconstituer l’état.

* **Avantages** :

    * Historique complet, audit infaillible
    * Facilité de régénérer de nouveaux **projections** / vues
    * Prise en charge naturelle des patterns CQRS

* **Inconvénients** :

    * Complexité accrue (gestion de schémas d’événements, versioning)
    * Nécessite un **Event Store** dédié

### b. Event Sourcing + Saga

Souvent on combine **Event Sourcing** pour le stockage du domaine ET un **Saga** pour l’orchestration.

1. **Stockage** : chaque service persiste ses événements métier localement dans un Event Store.
2. **Orchestration** : un Process Manager consomme le flux d’événements pour coordonner les appels externes et gérer les
   compensations.

---

## 3. Scénario « Service A → Service B » en « vrai »

Prenons l’exemple d’une commande et d’un paiement :

```
[Client] → POST /orders → OrderController → Publish OrderCreated(orderId)
```

1. **Stockage** (Event Sourcing)

    * **OrderAggregate** reçoit l’événement `OrderCreated`, l’ajoute dans son Event Store.
2. **Orchestration**

    * **OrderSaga** (Process Manager) s’abonne aux `OrderCreated`
    * Il émet `InitiatePaymentCommand(orderId)` vers le **PaymentService**
3. **PaymentService**

    * Reçoit la commande
    * Tente le paiement → publie `PaymentSucceeded` ou `PaymentFailed` dans son Event Store
4. **Saga**

    * Sur `PaymentSucceeded` → publie `ConfirmOrderCommand(orderId)` → **OrderAggregate** publie `OrderConfirmed`
    * Sur `PaymentFailed` → publie `CancelOrderCommand(orderId)` → **OrderAggregate** publie `OrderCancelled`
5. **Compensation**

    * Si une étape échoue après coup (ex. échec de shipping), le Saga peut publier `CompensatePaymentCommand` pour
      rembourser.

### Technologies courantes

* **Event Store DB**, **Apache Kafka** (avec Kafka Streams ou KSQL)
* Frameworks : **Axon Framework**, **Lagom**, **MassTransit** (pour .NET), **Camunda** (pour BPMN)
* Brokers/RPC : RabbitMQ, Kafka, Pulsar

---

## 4. Points clés pour réussir

| Aspect                  | Orchestration (Saga)                                                        | Event Sourcing                                                       |
|-------------------------|-----------------------------------------------------------------------------|----------------------------------------------------------------------|
| **But**                 | Coordonner les appels                                                       | Persister l’historique métier                                        |
| **Point de stockage**   | Tables/collections classiques + Event Store pour Saga (optionnel)           | Event Store (append-only)                                            |
| **Gestion des erreurs** | Saga : retry, timeout, compensation                                         | État reconstruit, events corrigés ou annulations via nouveaux events |
| **Complexité**          | Modérée à élevée                                                            | Élevée                                                               |
| **Quand l’utiliser ?**  | Dès que vous avez plusieurs services à coordonner et besoin de compensation | Quand vous avez besoin d’audit complet et de CQRS avancé             |

---

**En résumé**

* **Un simple flux A → B** peut se gérer par un **Process Manager** (Saga) sans Event Sourcing.
* **Event Sourcing** enrichit l’architecture quand vous voulez un **journal immuable** et des projections CQRS, mais ce
  n’est pas **obligatoire** pour l’orchestration.
* Dans le monde “vrai”, on mixe souvent les deux :

    1. **Sagas** pour la coordination et la résilience
    2. **Event Sourcing** pour la persistance et l’historique métier.

