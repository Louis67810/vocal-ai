# OneNote Voice Notes (prototype local)

Une petite application Windows qui enregistre votre voix, transcrit **en local** avec Whisper Tiny, puis colle le texte à l'emplacement courant dans OneNote.

Elle ne demande pas de droits administrateur : elle utilise uniquement le microphone, un raccourci clavier global, le presse-papiers et le raccourci `Ctrl+V`.

## Utilisation

1. Installez Python 3.10+ si besoin (depuis le Microsoft Store ou python.org).
2. Double-cliquez sur `install.bat`, une seule fois.
3. Lancez `start.bat`.
4. Dans OneNote, cliquez précisément à l'endroit où le texte doit être ajouté.
5. Faites `Ctrl+Alt+Espace` pour commencer à parler, puis le même raccourci pour terminer.

Le tag est la seule interface permanente : il apparaît juste au-dessus de la barre des tâches, puis se masque. `Ctrl+Alt+Maj+Espace` ferme totalement l'application. En cas d'erreur, cliquez sur le `?` du tag pour ouvrir le détail ; cliquez sur la croix ou à l'extérieur de l'overlay pour le fermer.

L'application mémorise la fenêtre active et son bureau virtuel au début de l'enregistrement. À la fin, elle bascule vers ce bureau, restaure la fenêtre si nécessaire et y insère le texte. Cela fonctionne également dans Word et dans la plupart des champs texte. Elle reste volontairement sur le bureau cible après le collage.

## Important : modèle local

Whisper Base est téléchargé une seule fois au premier usage (environ 145 Mo). Il est ensuite stocké localement dans le cache de l'utilisateur et la transcription fonctionne hors ligne. Il est plus précis que Tiny, mais un peu plus lent sur le processeur de la tablette. La fenêtre affiche l'avancement et les erreurs éventuelles.

Par défaut le calcul utilise le processeur (`CPU`) pour fonctionner sur une tablette sans configuration particulière. C'est volontairement compatible plutôt que très rapide.

## Dépannage

- La fenêtre **Journal de diagnostic** est gardée ouverte : copiez les dernières lignes pour me les envoyer si un test échoue.
- Si le raccourci est déjà pris, modifiez `HOTKEY` dans `app.py` (syntaxe `'<ctrl>+<alt>+<space>'`).
- L'application affiche maintenant le niveau sonore reçu. S'il est « quasi silencieux », ouvrez **Paramètres Windows > Confidentialité et sécurité > Microphone** et activez « Accès au microphone » ainsi que « Autoriser les applications de bureau à accéder à votre microphone ». Vérifiez aussi dans **Paramètres > Système > Son > Entrée** que le niveau de « Microphone Array » bouge lorsque vous parlez.
- Si l'insertion ne marche pas, regardez la ligne `Cible mémorisée` du journal. Elle doit nommer OneNote, Word ou l'application où vous aviez placé le curseur avant le premier raccourci.

## Étape 2 prévue

Le projet sépare la capture/transcription de la fonction `clean_text`. On pourra y brancher Gemma 3B localement (via Ollama ou llama.cpp) pour retirer les répétitions et reformuler, sans transmettre les notes à un service externe. La consigne prévue pour ce modèle sera :

> Corrige cette transcription vocale française en conservant exactement le style, l'ordre, le ton et les formulations de la personne qui parle. Ne résume pas, ne reformule pas et n'ajoute aucune information. Corrige uniquement les erreurs manifestes de reconnaissance vocale lorsque le contexte permet d'identifier avec certitude le mot voulu. Supprime uniquement les répétitions exactes, les mots clairement parasites ou les fragments provenant de paroles superposées. Conserve les hésitations, les phrases incomplètes et les passages ambigus s'ils font partie du discours. Si tu n'es pas certain d'une correction, garde le texte original.
