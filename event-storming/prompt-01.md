Pour passer d’un monolithe opaque à une carte Event-Storming claire, voici une démarche **top-down** en 5 grandes
étapes :

1. **Définir le périmètre métier (le “Big Picture”)**

    * **Résultat attendu** : un mur vierge et la liste des **processus clés** (ex. « gestion des commandes », «
      facturation », « authentification »).
    * **Pourquoi ?** Ça isole les sous‐domaines métier, vous évite de tout prendre d’un coup et oriente votre
      exploration vers ce qui compte vraiment.

2. **Identifier les **Événements Métier** majeurs**

    * Pour chaque processus, interrogez-vous : « Qu’est-ce qui se passe réellement ? »
    * Notez ces faits passés, par exemple :

        * 🟧 **OrderPlaced**
        * 🟧 **PaymentSucceeded**
        * 🟧 **InvoiceGenerated**
    * **Astuce** : travaillez à partir des user stories, logs, ou des commentaires d’API.

3. **Lister les **Commandes** (**Actions Utilisateurs / Systèmes**)**

    * Pour chaque événement, demandez-vous : « quelle intention l’a déclenché ? »
    * Exemple :

        * 🟦 **PlaceOrderCommand** → 🟧 **OrderPlaced**
        * 🟦 **CapturePaymentCommand** → 🟧 **PaymentSucceeded**
    * Vous mettez en évidence le passage du “vouloir faire” (commande) au “c’est fait” (événement).

4. **Cartographier les **Agents / Agrégats / Classes****

    * Parcourez votre code monolithique pour repérer les **grandes responsabilités** :

        * Modules ou packages (UI, Domain, Infrastructure…)
        * Classes/Services qui exposent une interface métier (ex. `OrderService`, `PaymentGateway`)
    * Pour chacun, notez :

        * **Ce qu’il reçoit** (commande)
        * **Ce qu’il produit** (événement)
        * **Les règles métier** qu’il applique (politique / policy)
    * Sur votre mur, usez de post-it 🟪 pour ces agents.

5. **Tracer les **Flux et Politiques** (reactive policies)**

    * À chaque événement, identifiez la règle dite “When X then Y” :

        * 💡 **When** `OrderPlaced` **Then** `SendOrderConfirmationCommand`
        * 💡 **When** `PaymentSucceeded` **Then** `GenerateInvoiceCommand`
    * Ces flèches jaunes (politiques) dévoilent l’enchaînement et la découplage.

---

#### Exemple résumé sur un mini-module « Commande » :

1. **Domaines** : « Prise de commande »
2. **Événements** :

    * 🟧 OrderPlaced
    * 🟧 OrderCancelled
3. **Commandes** :

    * 🟦 PlaceOrderCommand
    * 🟦 CancelOrderCommand
4. **Agents** :

    * 🟪 OrderController (reçoit HTTP → publie commande)
    * 🟪 OrderService (traite commande → produit événement)
    * 🟪 NotificationService (sur `OrderPlaced` → envoie mail)
5. **Politiques** :

    * 💡 When `OrderPlaced` then `SendOrderConfirmationCommand`
    * 💡 When `OrderCancelled` then `ReimburseCustomerCommand`

---

En suivant ces 5 étapes, vous passerez d’un monolithe informe à un **Event-Storming** clair et collaboratif, où chaque
couleur de “post-it” révèle un pan essentiel de votre système.
