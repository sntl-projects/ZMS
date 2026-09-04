# Handling transactional GET-Requests

Wie lässt sich im ZMS code am effektivsten verhindern, dass dessen verändernde API-Funktionen per GET-Request aufgerufen werden können. Zope erlaubt verändernde GET-Requests bzw. beschränkt transaktionale Requests nicht auf POST, selbst wenn im Web-GU die form-method auf "post" gesetzt ist. Ziel dabei ist, es zu vermeiden werden, dass auch GET-Requests mit CSRF-Token validiert werden müssen.

Die effektivste und wartungsfreundlichste Strategie unterscheidet zwischen zwei Ansätzen: 
1. Funktionsebene (Decorator) und 
2. globaler Transaktions-Ebene (Event-Subscriber).

---

### 1. Bewertung des Decorator-Ansatzes (`@post_only`)

Ein Python-Decorator ist auf Funktionsebene sehr anschaulich und explizit.

#### Vorteile:
* **Fail Fast:** Der Request wird sofort zu Beginn der Funktionsausführung abgelehnt, bevor aufwendige Logik durchlaufen wird.
* **Explizite Dokumentation:** Im Code ist direkt sichtbar, dass eine API-Funktion nur per `POST` aufgerufen werden darf.

#### Nachteile:
* **Hoher Pflegeaufwand & Fehleranfälligkeit:** ZMS und Zope besitzen Hunderte verändernde Methoden (`manage_*`, API-Methoden, Metacmds etc.). Es ist sehr leicht, bei neuen oder bestehenden Funktionen den Decorator zu vergessen.

---

### 2. Der einfachste & effektivste Lösungsansatz: Der globale Transaktions-Guard

Der sauberste Weg in Zope/ZMS ist die **zentrale Blockade auf Event-Ebene**. Da ZMS in `_csrf.py:65` bereits einen Subscriber für `IPubBeforeCommit` besitzt, lässt sich die Regel dort zentral durchsetzen:

> **Regel:** Wenn ein HTTP `GET`-Request eine Transaktion auslöst, die Schreibzugriffe auf die ZODB vornimmt (`_is_transactional(t)` ist `True`), wird die Transaktion abgebrochen und eine `405 Method Not Allowed` oder `403 Forbidden` Exception geworfen.

#### Vorteile dieses globalen Ansatzes:
1. **100% Abdeckung:** Es muss keine einzige API-Funktion einzeln dekoriert werden.
2. **Koppelung an tatsächliche Mutation:** Nur wenn ein `GET`-Request wirklich ZODB-Schreibzugriffe verursacht, wird er blockiert. Reines Lesen per `GET` funktioniert weiterhin uneingeschränkt.
3. **Befreiung von CSRF-Tokens für GET:** `GET`-Requests benötigen damit garantiert **keine** CSRF-Token-Validierung mehr, da sie keine Zustandsänderungen bewirken können.

---

### Konkreter Code für den globalen Schutz

In `_csrf.py:65` kann der Subscriber `validate_csrf_token` erweitert werden:

```python
import zExceptions

@adapter(IPubBeforeCommit)
def validate_csrf_token(event):
  request = event.request
  method = str(getattr(request, 'method', '') or '').upper()

  t = transaction.get()
  is_mutating = _is_transactional(t)

  # 1. GET/HEAD-Requests dürfen niemals Persistent-State/ZODB verändern
  if method in {'GET', 'HEAD'} and is_mutating:
    raise zExceptions.Forbidden('State-changing operations are not allowed via GET requests.')

  # 2. CSRF-Token-Validierung für verändernde Formular-Submissions (POST, PUT, DELETE, etc.)
  if not is_mutating:
    return

  form = getattr(request, 'form', None) or {}
  if not form or all(key in ['lang', CSRF_FORM_KEY] for key in form.keys()):
    return

  session = getattr(request, 'SESSION', None)
  session_token = session.get(CSRF_SESSION_KEY) if session is not None else None
  submitted_token = form.get(CSRF_FORM_KEY)

  if CSRF_FORM_KEY not in form or not session_token or not submitted_token or not hmac.compare_digest(str(session_token), str(submitted_token)):
    response = getattr(request, 'response', None)
    if response is not None:
      response.setHeader('Content-Type', 'text/html;charset=utf-8')
      response.setStatus(503, lock=True)
    raise zExceptions.HTTPServiceUnavailable('Invalid CSRF token')
```

---

### Ergänzender Decorator für explizite API-Funktionen (Option)

Für öffentliche API-Endpunkte, bei denen schon vor der Transaktion ein klarer Fehler geworfen werden soll, kann zusätzlich ein schlanker Decorator bereitgestellt werden:

```python
from functools import wraps
import zExceptions

def post_only(func):
    @wraps(func)
    def wrapper(self, *args, **kwargs):
        request = getattr(self, 'REQUEST', None)
        if request and str(getattr(request, 'method', '') or '').upper() == 'GET':
            raise zExceptions.MethodNotAllowed('Method %s requires POST' % func.__name__)
        return func(self, *args, **kwargs)
    return wrapper
```

---

### Best Practice
1. **Globaler Subscriber (`IPubBeforeCommit`)**: Dient als unüberwindbares Sicherheitsnetz für das gesamte System.
2. **Decorator (`@post_only`)**: Optional für gezielte Python-API-Methoden zur expliziten Signalisierung und für schnelles Ablehnung vor der Logikausführung.