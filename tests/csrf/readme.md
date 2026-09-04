# Handling transactional GET-Requests

_Wie lässt sich im ZMS code am effektivsten verhindern, dass dessen verändernde API-Funktionen per GET-Request aufgerufen werden können. Zope erlaubt verändernde GET-Requests bzw. beschränkt transaktionale Requests nicht auf POST, selbst wenn im Web-GU die form-method auf "post" gesetzt ist. Ziel dabei ist, es zu vermeiden werden, dass auch GET-Requests mit CSRF-Token validiert werden müssen._

Die effektivste und wartungsfreundlichste Strategie unterscheidet zwischen zwei Ansätzen: 
1. Funktionsebene (Decorator `@post_only`): optional für gezielte Python-API-Methoden zur expliziten Signalisierung und für schnelles Ablehnung vor der Logikausführung.
2. Globaler Transaktions-Ebene (Event-Subscriber `IPubBeforeCommit`): dient als blockierendes Sicherheitsnetz für das gesamte System.

---

### 1. Bewertung des Decorator-Ansatzes (`@post_only`)

Ein Python-Decorator ist auf Funktionsebene sehr anschaulich und explizit.

#### Vorteile:
* **Fail Fast:** Der Request wird sofort zu Beginn der Funktionsausführung abgelehnt, bevor aufwendige Logik durchlaufen wird.
* **Explizite Dokumentation:** Im Code ist direkt sichtbar, dass eine API-Funktion nur per `POST` aufgerufen werden darf.

#### Nachteile:
* **Hoher Pflegeaufwand & Fehleranfälligkeit:** ZMS und Zope besitzen Hunderte verändernde Methoden (`manage_*`, API-Methoden, Metacmds etc.). Es ist sehr leicht, bei neuen oder bestehenden Funktionen den Decorator zu vergessen.

---

### 2. Einfachster & effektivster Lösungsansatz ist der globale Transaktions-Guard

Der sauberste Weg in Zope/ZMS ist die **zentrale Blockade auf Event-Ebene**, Mit `_csrf.py:65` besteht ein  Subscriber für `IPubBeforeCommit` an dem sich die Regel zentral durchsetzen lässt:

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

### Liste der transaktionalen Funktionen 

ZMS verfügt über 106 transaktionale "manage"-Funktionen in 37 Dateien:

**./Products/zms/_accessmanager.py**
* def manage_user(self, btn, lang, REQUEST, RESPONSE):
* def manage_roleProperties(self, btn, key, lang, REQUEST, RESPONSE=None):
* def manage_userProperties(self, btn, key, lang, REQUEST, RESPONSE=None):

**./Products/zms/_confmanager.py**
* def manage_customizeSystem(self, btn, key, lang, REQUEST, RESPONSE=None):
* def manage_customizeDesign(self, btn, lang, REQUEST, RESPONSE):

**./Products/zms/_copysupport.py**
* def manage_copyObject(self, ids=[], REQUEST=None, RESPONSE=None):
* def manage_cutObject(self, ids=[], REQUEST=None, RESPONSE=None):
* def manage_pasteObjs(self, REQUEST, RESPONSE=None):

**./Products/zms/_exportable.py**
* def manage_export(self, export_format, lang, REQUEST, RESPONSE):

**./Products/zms/_mediadb.py**
* def manage_addMediaDb(self, location, REQUEST=None, RESPONSE=None):
* def manage_structureMediaDb(self, structure, REQUEST=None, RESPONSE=None):
* def manage_packMediaDb(self, REQUEST=None, RESPONSE=None):
* def manage_delMediaDb(self, REQUEST=None, RESPONSE=None):
* def manage_index_html(self, filename, REQUEST=None):
* def manage_test(self, REQUEST, RESPONSE):
* def manage_changeProperties(self, submit, REQUEST, RESPONSE):

**./Products/zms/_multilangmanager.py**
* def manage_changeLanguages(self, lang, btn, REQUEST, RESPONSE):
* def manage_changeLangDictProperties(self, lang, btn, REQUEST, RESPONSE=None):

**./Products/zms/_objattrs.py**
* def manage_changeTempBlobjProperty(self, lang, key, form_id, action, REQUEST, RESPONSE=None):

**./Products/zms/_objchildren.py**
* def manage_initObjChild(self, id, type, lang, REQUEST, RESPONSE=None):

**./Products/zms/_sequence.py**
* def manage_changeProperties(self, submit, currentvalue, REQUEST, RESPONSE):

**./Products/zms/_versionmanager.py**
* def manage_UndoVersion(self, lang, btn, REQUEST):
* def manage_wfTransition(self, lang, custom, REQUEST, RESPONSE):
* def manage_wfTransitionFinalize(self, lang, custom, REQUEST, RESPONSE=None):

**./Products/zms/_zmsattributecontainer.py**
* def manage_addZMSAttributeContainer(self):
* def manage_changeProperties(self, REQUEST, RESPONSE):

**./Products/zms/IZMSConfigurationProvider.py**
* def manage_sub_options(self):

**./Products/zms/zms.py**
* def manage_addZMS(self, lang, manage_lang, REQUEST, RESPONSE):
* def manage_addMediaDb(self, location, REQUEST=None, RESPONSE=None):

**./Products/zms/ZMSCharformatManager.py**
* def manage_changeCharformat(self, lang, btn, REQUEST, RESPONSE):

**./Products/zms/zmscontainerobject.py**
* def manage_addZMSCustom(self, meta_id=None, values={}, REQUEST=None):
* def manage_addZMSObject(self, meta_id, values, REQUEST):
* def manage_eraseObjs(self, lang, ids, REQUEST, RESPONSE=None):
* def manage_undoObjs(self, lang, ids, REQUEST, RESPONSE=None):
* def manage_deleteObjs(self, lang, ids, REQUEST, RESPONSE=None):
* def manage_ajaxDragDrop( self, lang, target, REQUEST, RESPONSE):
* def manage_ajaxZMIActions(self, context_id, REQUEST, RESPONSE):
* def manage_addZMSCustomDefault(self, lang, id_prefix, _sort_id, REQUEST, RESPONSE):
* def manage_addZMSModule(self, lang, _sort_id, custom, REQUEST, RESPONSE):

**./Products/zms/zmscustom.py**
* def manage_addZMSCustom(self, meta_id, lang, _sort_id, btn, REQUEST, RESPONSE):
* def manage_options(self):
* def manage_changeRecordGrid(self, lang, btn, REQUEST, RESPONSE):
* def manage_changeRecordSet(self, lang, btn, action, REQUEST, RESPONSE):
* def manage_import(self, file, lang, REQUEST, RESPONSE=None):

**./Products/zms/ZMSFilterManager.py**
* def manage_options(self):
* def manage_sub_options(self):
* def manage_changeFilter(self, lang, btn='', key='', REQUEST=None, RESPONSE=None):
* def manage_changeProcess(self, lang, btn='', key='', REQUEST=None, RESPONSE=None):

**./Products/zms/ZMSFormatProvider.py**
* def manage_options(self):
* def manage_sub_options(self):

**./Products/zms/zmsindex.py**
* def manage_options(self):
* def manage_sub_options(self):
* def manage_reindex(self, regenerate_all=None, regenerate_duplicates=None, REQUEST=None):
* def manage_test(self):
* def manage_resync(self):

**./Products/zms/zmslinkelement.py**
* def manage_options(self):
* def manage_changeProperties(self, lang, REQUEST, RESPONSE):

**./Products/zms/ZMSLLMConnector.py**
* def manage_options(self):
* def manage_sub_options(self):
* def manage_changeConfig(self, btn, lang, REQUEST, RESPONSE=None):
* def manage_changeFeatures(self, btn, lang, REQUEST, RESPONSE=None):
* def manage_changeProperties(self, btn, lang, REQUEST, RESPONSE=None):
* def manage_addZMSLLMConnector(self, REQUEST, RESPONSE=None):

**./Products/zms/zmslog.py**
* def manage_options(self):
* def manage_index_html(self, REQUEST, RESPONSE):
* def manage_submit(self, REQUEST, RESPONSE):

**./Products/zms/ZMSMetacmdProvider.py**
* def manage_options(self):
* def manage_sub_options(self):
* def manage_changeMetacmds(self, btn, lang, REQUEST, RESPONSE):

**./Products/zms/ZMSMetadictManager.py**
* def manage_changeMetaProperties(self, btn, lang, REQUEST, RESPONSE=None):

**./Products/zms/ZMSMetamodelProvider.py**
* def manage_options(self):
* def manage_sub_options(self):

**./Products/zms/ZMSMetaobjManager.py**
* def manage_ajaxChangeProperties(self, id, REQUEST, RESPONSE=None):
* def manage_changeProperties(self, lang, btn='', key='all', REQUEST=None, RESPONSE=None):
* def manage_create_default_zpt(self, id, target_id='standard_html', attrs=None, REQUEST=None, RESPONSE=None):

**./Products/zms/zmsobject.py**
* def manage_changeProperties(self, lang, REQUEST, RESPONSE=None):
* def manage_moveObjUp(self, lang, REQUEST, RESPONSE):
* def manage_moveObjDown(self, lang, REQUEST, RESPONSE):
* def manage_moveObjToPos(self, lang, pos, fmt=None, REQUEST=None, RESPONSE=None):
* def manage_executeMetacmd(self, id, REQUEST, RESPONSE=None, context=None):

**./Products/zms/ZMSRepositoryManager.py**
* def manage_options(self):
* def manage_sub_options(self):
* def manage_change(self, btn, lang, REQUEST=None, RESPONSE=None):

**./Products/zms/zmssqldb.py**
* def manage_addZMSSqlDb(self, lang, _sort_id, REQUEST, RESPONSE):
* def manage_changeProperties(self, lang, REQUEST=None, RESPONSE=None):
* def manage_changeConfiguration(self, lang, btn='', key='all', REQUEST=None, RESPONSE=None):

**./Products/zms/ZMSTextformatManager.py**
* def manage_changeTextformat(self, lang, btn, REQUEST, RESPONSE):

**./Products/zms/zmstrashcan.py**
* def manage_options(self):
* def manage_changeProperties(self, lang, REQUEST=None):

**./Products/zms/ZMSWorkflowActivitiesManager.py**
* def manage_changeActivities(self, lang, btn='', REQUEST=None, RESPONSE=None):

**./Products/zms/ZMSWorkflowProvider.py**
* def manage_options(self):
* def manage_sub_options(self):
* def manage_changeWorkflow(self, lang, btn='', key='workflow_properties', REQUEST=None, RESPONSE=None):

**./Products/zms/ZMSWorkflowProviderAcquired.py**
* def manage_options(self):
* def manage_sub_options(self):
* def manage_changeWorkflow(self, lang, key='', btn='', REQUEST=None, RESPONSE=None):

**./Products/zms/ZMSWorkflowTransitionsManager.py**
* def manage_changeTransitions(self, lang, btn='', key='', REQUEST=None, RESPONSE=None):

**./Products/zms/ZMSZCatalogAdapter.py**
* def manage_options(self):
* def manage_sub_options(self):
* def manage_changeProperties(self, btn, lang, REQUEST, RESPONSE):

**./Products/zms/ZMSZCatalogConnector.py**
* def manage_init(self):
* def manage_objects_add(self, objects):
* def manage_objects_remove(self, nodes):
* def manage_objects_clear(self, home_id):
* def manage_destroy(self):
* def manage_changeProperties(self, btn, lang, REQUEST, RESPONSE):