"""
_csrf.py - Generic CSRF token protection for Zope publishing.

Provides a session-backed CSRF token (L{getCSRFToken}) and a global
subscriber (L{register}) for C{ZPublisher.interfaces.IPubBeforeCommit}
that validates the token for any published Zope object - not just ZMS
content - whenever a submitted form carries a C{csrf_token} field and the
request actually results in ZODB writes (i.e. a transactional request).

Using a publish-event subscriber (rather than patching Zope's
C{__before_publishing_traverse__} machinery or individual classes) keeps
this protection independent of the class hierarchy of the published
object and avoids monkey-patching Zope core code.

License: GNU General Public License v2 or later,
Organization: ZMS Publishing
"""

import hmac
import secrets

import transaction
import ZODB.Connection
import zExceptions
from zope.component import adapter
from ZPublisher.interfaces import IPubBeforeCommit

# Session key holding the expected token and form/query field name
# expected to carry the token back with a submitted form.
CSRF_SESSION_KEY = '_csrft_'
CSRF_FORM_KEY = 'csrf_token'


def getCSRFToken(request):
  """
  Return the CSRF token stored in the given request's session, creating
  it if it does not yet exist.

  @param request: Active HTTP request.
  @type request: ZPublisher.HTTPRequest.HTTPRequest
  @return: CSRF token.
  @rtype: str
  """
  session = request.SESSION
  token = session.get(CSRF_SESSION_KEY)
  if not token:
    token = secrets.token_hex(20)
    session.set(CSRF_SESSION_KEY, token)
  return token


def _is_transactional(t):
  """Return C{True} if the given transaction registered ZODB writes."""
  return any(isinstance(resource, ZODB.Connection.Connection) for resource in t._resources)


def _is_submitted_form_request(request):
  """Return C{True} only for actual form submissions, not for query-string GETs."""
  method = str(getattr(request, 'method', '') or '').upper()
  if method not in {'POST', 'PUT', 'PATCH', 'DELETE', 'QUERY'}:
    return False

  form = getattr(request, 'form', None) or {}
  return bool(form)


@adapter(IPubBeforeCommit)
def validate_csrf_token(event):
  """
  Validate a submitted C{csrf_token} form field against the session
  before the transaction commits. Applies to any published Zope object,
  not just ZMS content.

  @param event: Publish event fired right before the transaction commits.
  @type event: ZPublisher.pubevents.PubBeforeCommit
  """
  request = event.request

  form = getattr(request, 'form', None) or {}
  # The form may be empty for a GET request with query parameters, 
  # so we only validate the token for actual form submissions 
  # that result in ZODB writes. This avoids breaking GET requests 
  # that are not intended to be CSRF-protected.
  # ---
  # if not _is_submitted_form_request(request):
  if not form or all(key in ['lang', CSRF_FORM_KEY] for key in form.keys()):
    return

  t = transaction.get()
  if not _is_transactional(t):
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


def register():
  """Register the CSRF validation subscriber for C{IPubBeforeCommit}."""
  from zope.component import provideHandler
  provideHandler(validate_csrf_token)


def _navigation_getCSRFToken(self, REQUEST=None):
  """C{getCSRFToken} accessor patched onto C{App.Management.Navigation}."""
  return getCSRFToken(REQUEST or self.REQUEST)


def patch_manage_page_footer():
  """
  Patch C{App.Management.Navigation} - the mix-in providing C{manage_page_footer}
  to virtually every manageable Zope object - so the Zope ZMI footer (not just
  ZMS's own C{zmi_html_foot}) also injects the CSRF token into every form.

  This overrides the class attribute with a ZMS-local copy of the template
  (L{Products.zms.dtml.manage_page_footer}) instead of editing Zope's own
  dtml file, so the override lives entirely in this add-on and is reverted
  simply by not calling this function.
  """
  import App.Management
  from App.special_dtml import DTMLFile

  Navigation = App.Management.Navigation
  Navigation.getCSRFToken = _navigation_getCSRFToken
  # ClassSecurityInfo is consumed by InitializeClass() at import time and no
  # longer available on the class, so declare access the old-style way (see
  # e.g. Navigation.manage_form_title__roles__, also None for a public method).
  Navigation.getCSRFToken__roles__ = None
  Navigation.manage_page_footer = DTMLFile('dtml/manage_page_footer', globals())

