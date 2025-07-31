Pour orchestrer deux appels à des services externes **sans tomber dans du simple « getData–persistData »**, on va
appliquer le pattern **Process Manager** (ou « Saga Orchestrator ») avec des **événements métiers**. Voici comment :

---

## 1. Définir des événements métiers, pas data-centrés

Plutôt que :

* ⚠️ `UserDataFetched`
* ⚠️ `PaymentDataReceived`

on utilisera des faits métier, par exemple :

* 🟧 **UserVerified**
* 🟧 **PaymentAuthorized**

Ces événements expriment un **état métier**, et non « j’ai reçu un JSON ».

---

## 2. Créer un **Process Manager**

Un composant unique (orchestrateur) qui :

1. **Sous‐crit** aux événements métiers
2. **Enchaîne** les appels externes
3. **Gère** l’échec / compensation

---

## 3. Orchestration pas à pas

1. **Déclencheur initial**

    * 🟦 `InitiateTransactionCommand` envoyé par l’application
    * 🟪 **ProcessManager** reçoit la commande

2. **Étape 1 : appel au Service A**

    * 🟪 **ProcessManager** publie 🟦 `CallServiceACommand`
    * **Service A** traite, renvoie 🟧 `ServiceAConfirmed` (événement métier)

3. **Étape 2 : appel au Service B**

    * À la réception de **ServiceAConfirmed**, **ProcessManager** émet 🟦 `CallServiceBCommand`
    * **Service B** traite et renvoie 🟧 `ServiceBConfirmed`

4. **Finalisation**

    * Sur 🟧 `ServiceBConfirmed`, **ProcessManager** publie 🟦 `TransactionCompletedEvent`
    * L’application ou d’autres handlers métiers (ex. notification) s’abonnent à cet événement

---

## 4. Gérer les erreurs / compensation

* Si **Service A** échoue (🟧 `ServiceAFailed`), le **ProcessManager** publie 🟦 `CancelTransactionCommand` ou 🟦
  `CompensateServiceBCommand` (si B avait déjà été appelé).
* Les handlers de ces commandes réalisent alors les **actions compensatoires**.

---

## 5. Avantages de cette approche

* **Faible couplage** : ni A ni B ne connaissent l’autre, ils n’échangent que via des événements métiers.
* **Visibilité** : vous avez une trace claire de l’enchaînement (d’un poste “post-it” à l’autre).
* **Extensibilité** : ajouter un 3ᵉ service ou une étape de notification ne nécessite que d’abonner un nouveau handler à
  l’événement adéquat.
* **Résilience** : en cas d’échec, le saga/Process Manager peut piloter les compensations automatiquement.

---

### Exemple minimal en pseudo-code

```java
class TransactionProcessManager {
  @HandleCommand InitiateTransactionCommand cmd {
    publish(new CallServiceACommand(cmd.txId));
  }

  @HandleEvent ServiceAConfirmed evt {
    publish(new CallServiceBCommand(evt.txId));
  }

  @HandleEvent ServiceAFailed evt {
    publish(new CancelTransactionCommand(evt.txId, "A failed"));
  }

  @HandleEvent ServiceBConfirmed evt {
    publish(new TransactionCompletedEvent(evt.txId));
  }

  @HandleEvent ServiceBFailed evt {
    publish(new CompensateServiceACommand(evt.txId, "B failed"));
  }
}
```

Avec ce **Process Manager**, chaque service **reste découplé**, vous pilotez le flux via des commandes et événements
métiers, et vous évitez les événements “data-centric” qui ne disent rien du contexte.
